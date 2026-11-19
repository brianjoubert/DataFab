# database_4.py — GDS -> labelled GDS + JSON wafer databases
import copy
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

import numpy as np
import klayout.db as pya

from datafab_io import atomic_write_json, load_json_lenient

Rect = Tuple[float, float, float, float]  # (xmin, ymin, xmax, ymax) in µm

# Kept for backward compatibility with older imports.
load_config_lenient = load_json_lenient


# =========================================================================
# 🔧 HELPERS
# =========================================================================

class GridPos(NamedTuple):
    label: str  # e.g. "A1", "AA3"
    row: int    # 1-based (A -> 1, Z -> 26, AA -> 27)
    col: int    # 1-based


def row_letters(index: int) -> str:
    """0 -> 'A', 25 -> 'Z', 26 -> 'AA', 27 -> 'AB' (spreadsheet-style)."""
    s = ""
    n = index + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(ord("A") + r) + s
    return s


def calculate_grid_position(rects: List[Rect], tolerance_fraction: float) -> Dict[Rect, GridPos]:
    """
    Assign grid labels row-by-row from top to bottom (y) and left-to-right (x).
    Rows are letters (A, B, ..., Z, AA, AB, ...), columns are numbers (1, 2, 3...).

    Rows are grouped with a tolerance of `tolerance_fraction` x the row pitch. The pitch is the
    smallest vertical gap between die centres that is larger than half a die height, so tiny
    misalignments between dies of the same row cannot be mistaken for a row pitch.
    """
    if not rects:
        return {}

    centroids = [((x0 + x1) / 2.0, (y0 + y1) / 2.0, (x0, y0, x1, y1)) for (x0, y0, x1, y1) in rects]
    sorted_centroids = sorted(centroids, key=lambda c: (-c[1], c[0]))

    heights = np.array([y1 - y0 for (_, y0, _, y1) in rects], dtype=float)
    median_h = float(np.median(heights)) if len(heights) else 0.0

    unique_y = np.unique(np.round([c[1] for c in sorted_centroids], 3))
    gaps = np.diff(unique_y)
    significant = gaps[gaps > 0.5 * median_h] if median_h > 0 else gaps[gaps > 0]
    if significant.size:
        row_tol = float(significant.min()) * float(tolerance_fraction)
    else:
        # Everything within half a die height: a single row.
        row_tol = max(median_h, 0.001)
    row_tol = max(row_tol, 0.001)

    rows_list: List[list] = []
    current = [sorted_centroids[0]]
    start_y = sorted_centroids[0][1]
    for c in sorted_centroids[1:]:
        if abs(c[1] - start_y) <= row_tol:
            current.append(c)
        else:
            rows_list.append(sorted(current, key=lambda item: item[0]))
            current = [c]
            start_y = c[1]
    rows_list.append(sorted(current, key=lambda item: item[0]))

    labels_map: Dict[Rect, GridPos] = {}
    for i_row, row in enumerate(rows_list):
        letters = row_letters(i_row)
        for i_col, (_, _, rect) in enumerate(row, start=1):
            labels_map[rect] = GridPos(f"{letters}{i_col}", i_row + 1, i_col)
    return labels_map


def read_layout(input_gds_path: str) -> pya.Layout:
    input_gds_path = os.path.normpath(input_gds_path)
    if not os.path.exists(input_gds_path):
        raise FileNotFoundError(f"Input file not found: {input_gds_path}")
    layout = pya.Layout()
    layout.read(input_gds_path)
    if layout.top_cell() is None:
        raise RuntimeError("GDS contains no top-level cell.")
    return layout


def get_rect_bounds(layout: pya.Layout, layer: Tuple[int, int]) -> Optional[List[Rect]]:
    """
    Bounding boxes (µm) of all boxes/polygons on `layer` (hierarchy flattened).
    Returns None if the layer does not exist in the layout.
    """
    layer_id = layout.find_layer(layer[0], layer[1])
    if layer_id is None or layer_id == -1:
        return None
    dbu = layout.dbu
    rects: List[Rect] = []
    it = layout.top_cell().begin_shapes_rec(layer_id)
    while not it.at_end():
        s = it.shape()
        if s.is_box() or s.is_polygon():
            # Transform to top-cell coordinates (shapes may sit in placed sub-cells).
            b = s.bbox().transformed(it.trans())
            rects.append((b.left * dbu, b.bottom * dbu, b.right * dbu, b.top * dbu))
        it.next()
    return rects


def get_rect_bounds_with_klayout(input_gds_path: str, layer: Tuple[int, int]) -> List[Rect]:
    """Backward-compatible wrapper: raises ValueError if the layer is missing."""
    rects = get_rect_bounds(read_layout(input_gds_path), layer)
    if rects is None:
        raise ValueError(f"Layer {layer[0]}:{layer[1]} not found in layout.")
    return rects


def _inside(inner: Rect, outer: Rect) -> bool:
    return inner[0] >= outer[0] and inner[1] >= outer[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def _regions_inside(measure_rects: List[Rect], box: Rect) -> List[Dict]:
    regions = []
    for (mx0, my0, mx1, my1) in measure_rects:
        if _inside((mx0, my0, mx1, my1), box):
            regions.append({"id": len(regions) + 1, "x0_um": mx0, "y0_um": my0, "x1_um": mx1, "y1_um": my1})
    return regions


def sub_db_filename(main_json_path: str, tj_type: str) -> str:
    p = Path(main_json_path)
    suffix = "TJ" if tj_type == "test_junction" else tj_type
    return f"{p.stem}_{suffix}{p.suffix}"


def load_existing_data(json_path: str) -> Dict[str, Dict]:
    """
    Load an existing database JSON and return {label: {"notes", "resistances": {id: dict}, "die"}}.
    """
    data_map: Dict[str, Dict] = {}
    path_obj = Path(json_path)
    if not path_obj.exists():
        return data_map
    try:
        data = load_json_lenient(path_obj)
        for d in data.get("dies", []):
            label = d.get("label")
            if label:
                data_map[label] = {
                    "notes": d.get("notes", "") or "",
                    "resistances": {r["id"]: r for r in d.get("resistances", []) if "id" in r},
                    "die": d,
                }
    except Exception as e:
        print(f"⚠️ Warning: Could not read existing data from {path_obj.name}: {e}")
    return data_map


def _has_data(res: Dict) -> bool:
    return bool(res.get("history")) or bool(res.get("removed"))


# =========================================================================
# 🧠 CORE: label & export
# =========================================================================

def write_labels_and_export_json(
    params: Dict,
    metadata: Dict,
    sample_map: Dict[Rect, GridPos],
    measure_rects: List[Rect],
    tj_data_map: Dict[str, List[Rect]],
    config_json_path: str,
) -> Tuple[Optional[str], str]:
    """
    - Optionally writes <input_gds>_with_text.gds with polygon text labels,
    - Exports <config>_database.json (samples) and <config>_database_<tj_type>.json files,
      preserving notes and resistance histories from previous runs,
    - Saves any data that no longer matches a die to <config>_database_orphaned.json.
    Returns (output_gds_path or None, output_json_path).
    """
    input_gds = params["input_gds_file"]
    text_layer = tuple(params["text_layer"])
    rel_x = float(params["relative_pos_x"])
    rel_y = float(params["relative_pos_y"])
    text_size_um = float(params["text_size_um"])
    label_prefix = params["label_prefix"]
    mirror = bool(params.get("mirror", False))
    mirror_offset = float(params.get("mirror_offset_fraction", 0.15))
    topcell = params.get("import_topcell", None)
    export_gds = bool(params.get("export_gds_with_text", True))

    input_gds_path = Path(input_gds)
    output_gds_path: Optional[str] = str(input_gds_path.with_stem(f"{input_gds_path.stem}_with_text"))
    output_json_path = os.path.splitext(config_json_path)[0] + "_database.json"
    out_dir = Path(output_json_path).parent

    # --- LOAD EXISTING DATA (notes & history) from main file and every known sub-file ---
    existing_data_map: Dict[str, Dict] = {}
    existing_data_map.update(load_existing_data(output_json_path))
    sub_files = {sub_db_filename(output_json_path, t) for t in set(tj_data_map) | {"test_junction"}}
    try:
        old_meta = load_json_lenient(output_json_path).get("metadata", {}) if Path(output_json_path).exists() else {}
        sub_files |= {s["filename"] for s in old_meta.get("sub_databases", []) if s.get("filename")}
    except Exception:
        pass
    for fname in sorted(sub_files):
        existing_data_map.update(load_existing_data(str(out_dir / fname)))

    if export_gds:
        import gdsfactory as gf
        base = gf.import_gds(input_gds, cellname=topcell)
        top = gf.Component(f"{input_gds_path.stem}_labeled")
        _ = top << base

    use_mirror = mirror and {g.col for g in sample_map.values()} == {1, 2}

    dies: List[Dict] = []
    used_tj_indices = {name: set() for name in tj_data_map}

    def make_entry(label, row, col, box: Rect, regions, die_type) -> Dict:
        resistances = [{"id": mr["id"], "history": []} for mr in regions]
        notes = ""
        existing = existing_data_map.get(label)
        if existing:
            notes = existing["notes"]
            for r in resistances:
                old = existing["resistances"].get(r["id"])
                if old:
                    r.update({k: v for k, v in old.items() if k != "id"})
        x0, y0, x1, y1 = box
        return {
            "label": label,
            "MAP_ROW": row,
            "MAP_COL": col,
            "x_um": x0,
            "y_um": y0,
            "width_um": x1 - x0,
            "height_um": y1 - y0,
            "num_measure_regions": len(regions),
            "measure_regions": regions,
            "resistances": resistances,
            "type": die_type,
            "notes": notes,
        }

    def tj_regions(box: Rect) -> List[Dict]:
        regions = _regions_inside(measure_rects, box)
        if not regions:  # no pads inside: the TJ itself is the measurement region
            x0, y0, x1, y1 = box
            regions = [{"id": 1, "x0_um": x0, "y0_um": y0, "x1_um": x1, "y1_um": y1}]
        return regions

    for (xmin, ymin, xmax, ymax), pos in sample_map.items():
        w, h = xmax - xmin, ymax - ymin
        if use_mirror and pos.col == 1:
            px = xmin + w * mirror_offset
        elif use_mirror and pos.col == 2:
            px = xmax - w * mirror_offset
        else:
            px = xmin + w * rel_x
        py = ymin + h * rel_y
        full_label = f"{label_prefix}{pos.label}"

        if export_gds:
            txt = gf.components.text(text=full_label, size=text_size_um, layer=text_layer)
            ref = top << txt
            ref.center = (px, py)

        sample_box = (xmin, ymin, xmax, ymax)
        dies.append(make_entry(full_label, pos.row, pos.col, sample_box,
                               _regions_inside(measure_rects, sample_box), "sample"))

        # Test junctions inside this sample
        for tj_name, tj_rects in tj_data_map.items():
            inside = [(r, i) for i, r in enumerate(tj_rects) if _inside(r, sample_box)]
            used_tj_indices[tj_name].update(i for _, i in inside)
            inside.sort(key=lambda item: (item[0][1], item[0][0]))
            for i_tj, (box, _) in enumerate(inside, start=1):
                if tj_name == "test_junction":
                    suffix = f"_TJ_{i_tj}" if len(inside) > 1 else "_TJ"
                else:
                    suffix = f"_{tj_name}_{i_tj}"
                dies.append(make_entry(f"{full_label}{suffix}", pos.row, pos.col, box, tj_regions(box), tj_name))

    # Test junctions outside every sample
    for tj_name, tj_rects in tj_data_map.items():
        orphans = sorted((r for i, r in enumerate(tj_rects) if i not in used_tj_indices[tj_name]),
                         key=lambda b: (b[1], b[0]))
        for i_orphan, box in enumerate(orphans, start=1):
            label = f"TJ_{i_orphan}" if tj_name == "test_junction" else f"{tj_name}_{i_orphan}"
            dies.append(make_entry(label, None, None, box, tj_regions(box), tj_name))

    # --- Detect measurements/notes that no longer match any die (label or pad changed) ---
    new_by_label = {d["label"]: d for d in dies}
    orphaned: List[Dict] = []
    for label, ex in existing_data_map.items():
        new_die = new_by_label.get(label)
        if new_die is None:
            if ex["notes"] or any(_has_data(r) for r in ex["resistances"].values()):
                orphaned.append(ex["die"])
        else:
            new_ids = {r["id"] for r in new_die["resistances"]}
            lost = [r for rid, r in ex["resistances"].items() if rid not in new_ids and _has_data(r)]
            if lost:
                partial = dict(ex["die"])
                partial["resistances"] = lost
                orphaned.append(partial)

    if orphaned:
        orphan_path = Path(os.path.splitext(output_json_path)[0] + "_orphaned.json")
        previous = []
        if orphan_path.exists():
            try:
                previous = load_json_lenient(orphan_path).get("entries", [])
            except Exception:
                pass
        previous.append({
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "reason": "These dies/resistances had notes or measurements but no longer match the "
                      "regenerated layout (label prefix, grid or measurement pads changed).",
            "dies": orphaned,
        })
        atomic_write_json(orphan_path, {"entries": previous})
        print(f"⚠️ WARNING: {len(orphaned)} die(s) with existing data no longer match the layout:")
        for d in orphaned[:20]:
            print(f"     - {d.get('label')}")
        if len(orphaned) > 20:
            print(f"     ... and {len(orphaned) - 20} more")
        print(f"   Their data was saved to: {orphan_path}")

    # --- Write labelled GDS ---
    if export_gds:
        try:
            top.flatten()
        except Exception:
            pass
        top.write_gds(output_gds_path)
        print(f"✅ Labeled GDS saved to: {output_gds_path}")
    else:
        output_gds_path = None

    # --- Export JSON (samples + one file per TJ type) ---
    sample_dies = [d for d in dies if d["type"] == "sample"]
    tj_types_found = sorted({d["type"] for d in dies} - {"sample"})
    extra_databases = [{"type": t, "filename": sub_db_filename(output_json_path, t)} for t in tj_types_found]

    meta_copy = dict(metadata)
    meta_copy["created_at"] = datetime.now().isoformat()
    meta_copy["wafer_id"] = metadata.get("wafer_id", "unknown")
    meta_copy["sub_databases"] = extra_databases

    atomic_write_json(output_json_path, {"metadata": meta_copy, "dies": sample_dies}, backup=True)
    print(f"✅ Wafer JSON (Samples) saved to: {output_json_path}")

    for db_info in extra_databases:
        out_tj_path = out_dir / db_info["filename"]
        tj_dies = [d for d in dies if d["type"] == db_info["type"]]
        atomic_write_json(out_tj_path, {"metadata": dict(meta_copy), "dies": tj_dies}, backup=True)
        print(f"✅ Wafer JSON ({db_info['type']}) saved to: {out_tj_path}")

    return output_gds_path, output_json_path


# =========================================================================
# 🎬 MAIN
# =========================================================================

def generate_from_config(config_json_path: str, config: Optional[Dict] = None):
    """
    Run the generation. `config` lets a caller (the GUI) pass edited settings without
    writing them to disk; outputs are still placed next to `config_json_path`.
    """
    _safe_console()
    config =copy.deepcopy(config) if config is not None else load_json_lenient(config_json_path)
    params = config["script_parameters"]
    wafer_metadata = config["wafer_metadata"]
    original_gds = params["input_gds_file"]
    if not os.path.exists(original_gds):
        raise FileNotFoundError(f"Input GDS not found: {original_gds}")

    temp_dir = tempfile.mkdtemp()
    try:
        local_gds = os.path.join(temp_dir, os.path.basename(original_gds))
        print("Copying GDS locally to bypass KLayout network restrictions...")
        shutil.copy2(original_gds, local_gds)
        params["input_gds_file"] = local_gds

        layout = read_layout(local_gds)
        sample_layer = tuple(params["input_layer"])
        measure_layer = tuple(params["resistance_layer"])

        sample_rects = get_rect_bounds(layout, sample_layer)
        if sample_rects is None:
            raise ValueError(f"Sample layer {sample_layer[0]}:{sample_layer[1]} not found in the GDS.")
        measure_rects = get_rect_bounds(layout, measure_layer)
        if measure_rects is None:
            print(f"⚠️ Measurement layer {measure_layer[0]}:{measure_layer[1]} not found — "
                  "dies will have no measurement regions.")
            measure_rects = []

        tj_configs = params.get("test_junctions", [])
        if not tj_configs and "tj_layer" in params:  # legacy single-TJ config
            tj_configs = [{"name": "test_junction", "layer": params["tj_layer"]}]

        tj_data_map: Dict[str, List[Rect]] = {}
        for cfg in tj_configs:
            name, layer = cfg["name"], tuple(cfg["layer"])
            rects = get_rect_bounds(layout, layer)
            if rects is None:
                print(f"⚠️ Test junction '{name}': layer {layer[0]}:{layer[1]} not found — skipped.")
                continue
            tj_data_map[name] = rects
            print(f"Found {len(rects)} '{name}' rectangles on layer {layer[0]}:{layer[1]}.")

        print(f"Found {len(sample_rects)} sample rectangles on layer {sample_layer[0]}:{sample_layer[1]}.")
        print(f"Found {len(measure_rects)} measurement rectangles on layer {measure_layer[0]}:{measure_layer[1]}.")

        sample_map = calculate_grid_position(sample_rects, float(params["tolerance_fraction"]))
        n_rows = max((g.row for g in sample_map.values()), default=0)
        print(f"Computed grid labels ({n_rows} rows).")

        out_gds, out_json = write_labels_and_export_json(
            params=params,
            metadata=wafer_metadata,
            sample_map=sample_map,
            measure_rects=measure_rects,
            tj_data_map=tj_data_map,
            config_json_path=config_json_path,
        )

        if out_gds:
            final_output_gds = str(Path(original_gds).with_stem(f"{Path(original_gds).stem}_with_text"))
            print(f"Copying finished GDS back to: {final_output_gds}")
            shutil.copy2(out_gds, final_output_gds)
        print("✅ Generation completely finished!")
        return out_json
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _safe_console():
    """Windows consoles/pipes often use cp1252, which cannot print the ✅/⚠️ status symbols:
    replace unprintable characters instead of crashing."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass  # e.g. the GUI's own writer: nothing to do


def main():
    if len(sys.argv) != 2:
        print("Usage: python database_4.py path\\to\\config.json")
        sys.exit(1)
    generate_from_config(sys.argv[1])


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)
