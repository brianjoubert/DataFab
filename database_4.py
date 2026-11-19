# extract_and_export_full.py
import os
import sys
import json
import re
import pathlib
from pathlib import Path
from typing import Tuple, List, Dict

import numpy as np
import klayout.db as pya
import gdsfactory as gf
from datetime import datetime

# =========================================================================
# 🔧 HELPERS
# =========================================================================

def load_config_lenient(json_path: str) -> dict:
    r"""
    Loads JSON but tolerates Windows-style paths with single backslashes.
    It escapes backslashes that appear inside quoted strings before json.loads.

    This allows both of the following to work:
      "C:\\Users\\you\\Documents\\GitHub\\New folder\\input.gds"   (standard JSON)
      "C:\Users\you\Documents\GitHub\New folder\input.gds"         (lenient input)
    """
    text = Path(json_path).read_text(encoding="utf-8")
    try:
        # Works if the JSON already uses proper escaping or forward slashes
        return json.loads(text)
    except json.JSONDecodeError:
        # Escape backslashes ONLY within quoted string literals,
        # keeping existing escape sequences intact.
        def _escape_in_string(m: re.Match) -> str:
            s = m.group(0)
            # Turn single "\" into "\\" but avoid doubling already escaped "\\"
            # First, make all "\" doubled, then collapse quadruples back to doubles
            return s.replace("\\", "\\\\").replace("\\\\\\\\", "\\\\")
        fixed = re.sub(
            r'("(?:[^"\\]|\\.)*")|(\'(?:[^\'\\]|\\.)*\')',
            lambda m: _escape_in_string(m) if m.group(0) else m.group(0),
            text,
        )
        return json.loads(fixed)


def calculate_grid_position(
    rects: List[Tuple[float, float, float, float]],
    tolerance_fraction: float,
) -> Dict[Tuple[float, float, float, float], str]:
    """
    Assign grid labels A1, B1, ... row-by-row from top to bottom (y),
    and left-to-right (x) within each row.
    Row is represented by a letter (A, B, C...) and column by a number (1, 2, 3...).
    Returns { (xmin,ymin,xmax,ymax): "A1", ... }.
    """
    if not rects:
        return {}

    centroids = []
    for (xmin, ymin, xmax, ymax) in rects:
        cx = (xmin + xmax) / 2.0
        cy = (ymin + ymax) / 2.0
        centroids.append((cx, cy, (xmin, ymin, xmax, ymax)))

    # Sort by y descending (rows top to bottom)
    sorted_centroids = sorted(centroids, key=lambda c: (-c[1], c[0]))

    # Estimate row tolerance from min vertical pitch
    y_coords = np.array([c[1] for c in sorted_centroids], dtype=float)
    unique_y = np.sort(np.unique(np.round(y_coords, 3)))

    row_tol = 0.001
    if len(unique_y) > 1:
        diffs = np.diff(unique_y)
        diffs = diffs[diffs > 0]
        if len(diffs):
            min_pitch = float(np.min(diffs))
            row_tol = max(min_pitch * float(tolerance_fraction), 0.0)

    # Group into rows
    rows_list = []
    current_row_elements = [sorted_centroids[0]]
    start_y_of_row = sorted_centroids[0][1]

    for c in sorted_centroids[1:]:
        cy = c[1]
        if abs(cy - start_y_of_row) <= row_tol:
            current_row_elements.append(c)
        else:
            # Sort the finished row from left to right (x ascending)
            current_row_elements.sort(key=lambda item: item[0])
            rows_list.append(current_row_elements)
            current_row_elements = [c]
            start_y_of_row = cy
    
    # Sort and append the last row
    current_row_elements.sort(key=lambda item: item[0])
    rows_list.append(current_row_elements)

    # Label dies
    labels_map: Dict[Tuple[float, float, float, float], str] = {}
    for i_row, row in enumerate(rows_list):
        row_char = chr(ord('A') + i_row)
        for i_col, (cx, cy, rect) in enumerate(row):
            col_num = i_col + 1
            label = f"{row_char}{col_num}"
            labels_map[rect] = label

    return labels_map


def get_rect_bounds_with_klayout(input_gds_path: str, layer: Tuple[int, int]) -> List[Tuple[float, float, float, float]]:
    """
    Returns list of bounding boxes (xmin, ymin, xmax, ymax) in microns for all shapes on the given layer.
    Accepts boxes and polygons; uses shape.bbox() for robustness.
    """
    input_gds_path = os.path.normpath(input_gds_path)
    if not os.path.exists(input_gds_path):
        raise FileNotFoundError(f"Input file not found: {input_gds_path}")

    layout = pya.Layout()
    layout.read(input_gds_path)

    top_cell = layout.top_cell()
    if top_cell is None:
        raise RuntimeError("GDS contains no top-level cell.")

    dbu = layout.dbu
    layer_id = layout.find_layer(layer[0], layer[1])
    if layer_id == -1:
        raise ValueError(f"Layer {layer[0]}:{layer[1]} not found in layout.")

    rects: List[Tuple[float, float, float, float]] = []
    it = top_cell.begin_shapes_rec(layer_id)
    while not it.at_end():
        s = it.shape()
        if s.is_box() or s.is_polygon():
            b = s.bbox()
            rects.append((b.left * dbu, b.bottom * dbu, b.right * dbu, b.top * dbu))
        it.next()

    return rects


def _has_exactly_two_columns(labels: List[str]) -> bool:
    """
    True if the set of column numbers is exactly {'1','2'} (for mirror placement).
    """
    cols = {lbl[1:] for lbl in labels if lbl}
    return cols == {"1", "2"}


def load_existing_data(json_path: str) -> Dict[str, Dict]:
    """
    Loads an existing database JSON and returns a map of:
    label -> { 
        "notes": str, 
        "resistance_history": dict(id -> history_list) 
    }
    """
    data_map = {}
    path_obj = Path(json_path)
    if not path_obj.exists():
        return data_map
    
    try:
        with open(path_obj, "r", encoding="utf-8") as f:
            data = json.load(f)
            for d in data.get("dies", []):
                label = d.get("label")
                if label:
                    # Map res_id to history
                    res_hist = {}
                    for r in d.get("resistances", []):
                        if "history" in r:
                            res_hist[r["id"]] = r["history"]
                    
                    data_map[label] = {
                        "notes": d.get("notes", ""),
                        "resistance_history": res_hist
                    }
    except Exception as e:
        print(f"Warning: Could not read existing data from {path_obj.name}: {e}")
    
    return data_map


# =========================================================================
# 🧠 CORE: label & export
# =========================================================================

def write_labels_and_export_json(
    params: Dict,
    metadata: Dict,
    sample_map: Dict[Tuple[float, float, float, float], str],
    measure_rects: List[Tuple[float, float, float, float]],
    tj_data_map: Dict[str, List[Tuple[float, float, float, float]]],
    config_json_path: str,
) -> Tuple[str, str]:
    """
    - Imports the GDS (gdsfactory), overlays polygonal text labels at the requested positions
      (with optional mirroring behavior for A/B columns),
    - Flattens the result (best effort),
    - Writes labeled GDS to disk as <input_gds_base>_with_text.gds (same folder as input),
    - Exports wafer JSON to <config_json_base>_database.json with:
        - sample rectangles (bottom-left origin, width/height),
        - measurement rectangles absolute coords,
        - resistance entries with empty 'history' lists (for time series).
    - Exports additional JSONs for test junctions (one per type).
    Returns (output_gds_path, output_json_path).
    """
    input_gds     = params["input_gds_file"]
    text_layer    = tuple(params["text_layer"])
    rel_x         = float(params["relative_pos_x"])
    rel_y         = float(params["relative_pos_y"])
    text_size_um  = float(params["text_size_um"])
    label_prefix  = params["label_prefix"]
    mirror        = bool(params.get("mirror", False))
    mirror_offset = float(params.get("mirror_offset_fraction", 0.15))
    topcell       = params.get("import_topcell", None)

    # ---------------------------------------------------------------------
    # Derive output names
    #   • GDS: same folder/name as input with suffix "_with_text"
    #   • JSON: next to the config file with suffix "_database"
    # ---------------------------------------------------------------------
    input_gds_path = Path(input_gds)
    output_gds_path = str(input_gds_path.with_stem(f"{input_gds_path.stem}_with_text"))

    cfg_base_noext = os.path.splitext(config_json_path)[0]
    output_json_path = cfg_base_noext + "_database.json"

    # --- LOAD EXISTING DATA (Notes & History) ---
    existing_data_map = {}
    
    # 1. Load from main file
    main_existing = load_existing_data(output_json_path)
    existing_data_map.update(main_existing)
    
    # 2. Load from potential existing sub-files (predicting names)
    # TJs could be "test_junction" or named "tj_single", etc.
    # We check all keys in tj_data_map as potential types
    p = pathlib.Path(output_json_path)
    potential_types = set(tj_data_map.keys()) | {"test_junction"}
    
    for t in potential_types:
        if t == "test_junction":
            fname = f"{p.stem}_TJ{p.suffix}"
        else:
            fname = f"{p.stem}_{t}{p.suffix}"
        sub_path = p.with_name(fname)
        sub_existing = load_existing_data(str(sub_path))
        existing_data_map.update(sub_existing)
    # --------------------------------------------
    export_gds = params.get("export_gds_with_text", True)

    if export_gds:
        import gdsfactory as gf
        # Import base GDS into gdsfactory and create a wrapper component for labels
        base = gf.import_gds(input_gds, cellname=topcell)
        top  = gf.Component(f"{input_gds_path.stem}_labeled")
        _    = top << base

    # Mirror mode?
    labels_list = list(sample_map.values())
    use_mirror  = mirror and _has_exactly_two_columns(labels_list)

    dies: List[Dict] = []
    
    # Track which TJs have been assigned to a sample for EACH type
    # used_tj_indices = { "tj_type_name": set(indices), ... }
    used_tj_indices = {name: set() for name in tj_data_map}

    # For each sample rectangle, place text + gather measurement regions
    for (xmin, ymin, xmax, ymax), label in sample_map.items():
        w = xmax - xmin
        h = ymax - ymin

        # Text position: mirror-aware X anchor
        if use_mirror:
            col_part = label[1:]
            if col_part == "1":
                px = xmin + w * mirror_offset
            elif col_part == "2":
                px = xmax - w * mirror_offset
            else:
                px = xmin + w * rel_x
        else:
            px = xmin + w * rel_x

        py = ymin + h * rel_y

        full_label = f"{label_prefix}{label}"

        # Insert polygonal text label
        if export_gds:
            txt = gf.components.text(text=full_label, size=text_size_um, layer=text_layer)
            ref = top << txt
            ref.center = (px, py)

        # Identify measurement rectangles wholly inside this sample
        measure_regions = []
        region_id = 1
        for (mx0, my0, mx1, my1) in measure_rects:
            if (mx0 >= xmin and my0 >= ymin and mx1 <= xmax and my1 <= ymax):
                measure_regions.append({
                    "id":   region_id,
                    "x0_um": mx0, "y0_um": my0,
                    "x1_um": mx1, "y1_um": my1
                })
                region_id += 1

        # Resistance entries with empty time-series history
        resistances = [{"id": mr["id"], "history": []} for mr in measure_regions]
        
        # --- PRESERVE DATA ---
        existing = existing_data_map.get(full_label)
        preserved_notes = ""
        if existing:
            preserved_notes = existing["notes"]
            # Restore history
            for r in resistances:
                rid = r["id"]
                if rid in existing["resistance_history"]:
                     r["history"] = existing["resistance_history"][rid]
        # ---------------------

        # Row/Col indices from label (Row: A->1, B->2...; Col: 1->1, 2->2...)
        row = ord(label[0]) - ord('A') + 1
        try:
            col = int(label[1:])
        except ValueError:
            col = None

        # Store sample as bottom-left origin (xmin, ymin) + width/height
        dies.append({
            "label":                 full_label,
            "MAP_ROW":               row,
            "MAP_COL":               col,
            "x_um":                  xmin,
            "y_um":                  ymin,
            "width_um":              w,
            "height_um":             h,
            "num_measure_regions":   len(measure_regions),
            "measure_regions":       measure_regions,
            "resistances":           resistances,
            "type":                  "sample",
            "notes":                 preserved_notes
        })
        
        # --- Handle Test Junctions (TJ) inside this sample ---
        # Iterate over each type of Test Junction defined
        for tj_name, tj_rects_list in tj_data_map.items():
            tjs_in_sample = []
            for idx, (tx0, ty0, tx1, ty1) in enumerate(tj_rects_list):
                # Check if TJ is inside Sample
                if (tx0 >= xmin and ty0 >= ymin and tx1 <= xmax and ty1 <= ymax):
                    tjs_in_sample.append(((tx0, ty0, tx1, ty1), idx))
                    used_tj_indices[tj_name].add(idx)
            
            # Sort TJs (e.g. by y then x)
            tjs_in_sample.sort(key=lambda item: (item[0][1], item[0][0]))

            for i_tj, ((tx0, ty0, tx1, ty1), original_idx) in enumerate(tjs_in_sample, start=1):
                # Naming strategy:
                # Legacy "test_junction": _TJ_{i} (or just _TJ if only one)
                # Specific named types: _{tj_name}_{i}
                
                if tj_name == "test_junction":
                    if len(tjs_in_sample) > 1:
                        suffix = f"_TJ_{i_tj}"
                    else:
                        suffix = "_TJ"
                else:
                    suffix = f"_{tj_name}_{i_tj}"
                
                tj_full_label = f"{full_label}{suffix}"

                # Find measure regions inside this TJ
                tj_measure_regions = []
                tj_r_id = 1
                for (mx0, my0, mx1, my1) in measure_rects:
                    if (mx0 >= tx0 and my0 >= ty0 and mx1 <= tx1 and my1 <= ty1):
                        tj_measure_regions.append({
                            "id":   tj_r_id,
                            "x0_um": mx0, "y0_um": my0,
                            "x1_um": mx1, "y1_um": my1
                        })
                        tj_r_id += 1
                
                # If NO measure regions found inside TJ, use the TJ itself as the measure region
                if not tj_measure_regions:
                    tj_measure_regions.append({
                        "id": 1,
                        "x0_um": tx0, "y0_um": ty0,
                        "x1_um": tx1, "y1_um": ty1
                    })

                tj_resistances = [{"id": mr["id"], "history": []} for mr in tj_measure_regions]
                
                # --- PRESERVE DATA (TJ) ---
                existing_tj = existing_data_map.get(tj_full_label)
                tj_preserved_notes = ""
                if existing_tj:
                    tj_preserved_notes = existing_tj["notes"]
                    for r in tj_resistances:
                        rid = r["id"]
                        if rid in existing_tj["resistance_history"]:
                             r["history"] = existing_tj["resistance_history"][rid]
                # --------------------------

                dies.append({
                    "label":                 tj_full_label,
                    "MAP_ROW":               row,
                    "MAP_COL":               col,
                    "x_um":                  tx0,
                    "y_um":                  ty0,
                    "width_um":              tx1 - tx0,
                    "height_um":             ty1 - ty0,
                    "num_measure_regions":   len(tj_measure_regions),
                    "measure_regions":       tj_measure_regions,
                    "resistances":           tj_resistances,
                    "type":                  tj_name,
                    "notes":                 tj_preserved_notes
                })

    # --- Handle Orphan Test Junctions (not inside any sample) ---
    for tj_name, tj_rects_list in tj_data_map.items():
        orphan_tjs = []
        for idx, (tx0, ty0, tx1, ty1) in enumerate(tj_rects_list):
            if idx not in used_tj_indices[tj_name]:
                orphan_tjs.append((tx0, ty0, tx1, ty1))
        
        # Sort orphans for stable naming
        orphan_tjs.sort(key=lambda b: (b[1], b[0]))

        for i_orphan, (tx0, ty0, tx1, ty1) in enumerate(orphan_tjs, start=1):
            if tj_name == "test_junction":
                tj_full_label = f"TJ_{i_orphan}"
            else:
                tj_full_label = f"{tj_name}_{i_orphan}"

            # Find measure regions inside this TJ
            tj_measure_regions = []
            tj_r_id = 1
            for (mx0, my0, mx1, my1) in measure_rects:
                if (mx0 >= tx0 and my0 >= ty0 and mx1 <= tx1 and my1 <= ty1):
                    tj_measure_regions.append({
                        "id":   tj_r_id,
                        "x0_um": mx0, "y0_um": my0,
                        "x1_um": mx1, "y1_um": my1
                    })
                    tj_r_id += 1
            
            # Fallback
            if not tj_measure_regions:
                tj_measure_regions.append({
                    "id": 1,
                    "x0_um": tx0, "y0_um": ty0,
                    "x1_um": tx1, "y1_um": ty1
                })

            tj_resistances = [{"id": mr["id"], "history": []} for mr in tj_measure_regions]

            # --- PRESERVE DATA (Orphan TJ) ---
            existing_orphan = existing_data_map.get(tj_full_label)
            orphan_preserved_notes = ""
            if existing_orphan:
                orphan_preserved_notes = existing_orphan["notes"]
                for r in tj_resistances:
                    rid = r["id"]
                    if rid in existing_orphan["resistance_history"]:
                            r["history"] = existing_orphan["resistance_history"][rid]
            # ---------------------------------

            dies.append({
                "label":                 tj_full_label,
                "MAP_ROW":               None,
                "MAP_COL":               None,
                "x_um":                  tx0,
                "y_um":                  ty0,
                "width_um":              tx1 - tx0,
                "height_um":             ty1 - ty0,
                "num_measure_regions":   len(tj_measure_regions),
                "measure_regions":       tj_measure_regions,
                "resistances":           tj_resistances,
                "type":                  tj_name,
                "notes":                 orphan_preserved_notes
            })

    # Try to flatten hierarchy for a clean labeled GDS
    if export_gds:
        try:
            top.flatten()
        except Exception:
            try:
                from gdsfactory.utilities import flatten as _flatten
                top = _flatten(top)
            except Exception:
                pass

        # Write labeled GDS
        top.write_gds(output_gds_path)
        print(f"✅ Labeled GDS saved to: {output_gds_path}")
    else:
        output_gds_path = None

    # --- Export to JSON (Split into Samples and TJs) ---
    
    sample_dies = [d for d in dies if d.get("type") == "sample"]
    
    # Identify all TJ types present
    all_types = set(d["type"] for d in dies)
    tj_types_found = sorted(list(all_types - {"sample"}))

    # Prepare list of extra DB files for metadata
    extra_databases = []
    p = pathlib.Path(output_json_path)

    for t in tj_types_found:
        if t == "test_junction":
            fname = f"{p.stem}_TJ{p.suffix}"
        else:
            fname = f"{p.stem}_{t}{p.suffix}"
        extra_databases.append({"type": t, "filename": fname})

    # 1. Export Samples to the main json_path
    meta_copy = metadata.copy()
    meta_copy["created_at"] = datetime.now().isoformat()
    meta_copy["wafer_id"] = metadata.get("wafer_id", "unknown")
    meta_copy["sub_databases"] = extra_databases # Inform viewer about other files

    output_json_samples = {
        "metadata": meta_copy,
        "dies": sample_dies
    }

    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(output_json_samples, f, indent=2)
    print(f"✅ Wafer JSON (Samples) saved to: {output_json_path}")

    # 2. Export each TJ type to its own file
    for db_info in extra_databases:
        t_type = db_info["type"]
        f_name = db_info["filename"]
        
        tj_dies = [d for d in dies if d.get("type") == t_type]
        
        # TJ metadata
        # We can include sub_databases there too, or just basic info
        tj_meta = meta_copy.copy()
        
        tj_output = {
            "metadata": tj_meta,
            "dies": tj_dies
        }
        
        out_tj_path = p.with_name(f_name)
        with open(out_tj_path, "w", encoding="utf-8") as f:
            json.dump(tj_output, f, indent=2)
        print(f"✅ Wafer JSON ({t_type}) saved to: {out_tj_path}")

    return output_gds_path, output_json_path


# =========================================================================
# 🎬 MAIN
# =========================================================================

def generate_from_config(config_json_path: str):
    import tempfile
    import shutil
    
    # Load config (lenient loader allows Windows-style single-backslash paths)
    config = load_config_lenient(config_json_path)

    params         = config["script_parameters"]
    wafer_metadata = config["wafer_metadata"]

    original_gds = params["input_gds_file"]
    
    temp_dir = tempfile.mkdtemp()
    local_gds = os.path.join(temp_dir, os.path.basename(original_gds))
    print("Copying GDS locally to bypass KLayout network restrictions...")
    shutil.copy2(original_gds, local_gds)
    params["input_gds_file"] = local_gds
    
    try:
        sample_layer  = tuple(params["input_layer"])       # e.g. [201, 0]
        measure_layer = tuple(params["resistance_layer"])  # e.g. [202, 0]

        # 1) Read rectangles from both layers with KLayout
        sample_rects  = get_rect_bounds_with_klayout(params["input_gds_file"], sample_layer)
        measure_rects = get_rect_bounds_with_klayout(params["input_gds_file"], measure_layer)
    
        # Handle multiple Test Junction types
        # "test_junctions": [ {"name": "tj_single", "layer": [250, 0]}, ... ]
        # Fallback to legacy "tj_layer" if new key not present
        tj_configs = params.get("test_junctions", [])
        if not tj_configs and "tj_layer" in params:
            tj_configs = [{"name": "test_junction", "layer": params["tj_layer"]}]

        tj_data_map = {}
        total_tjs = 0
        
        for cfg in tj_configs:
            name = cfg["name"]
            layer = tuple(cfg["layer"])
            rects = get_rect_bounds_with_klayout(params["input_gds_file"], layer)
            tj_data_map[name] = rects
            total_tjs += len(rects)
            print(f"Found {len(rects)} '{name}' rectangles on layer {layer[0]}:{layer[1]}.")

        print(f"Found {len(sample_rects)} sample rectangles on layer {sample_layer[0]}:{sample_layer[1]}.")
        print(f"Found {len(measure_rects)} measurement rectangles on layer {measure_layer[0]}:{measure_layer[1]}.")

        # 2) Compute grid labels A1, B1, ...
        sample_map = calculate_grid_position(sample_rects, float(params["tolerance_fraction"]))
        print("Computed grid labels (A1, B1, ...).")

    # 3) Write labeled GDS + wafer JSON (time-series ready)
        out_gds, out_json = write_labels_and_export_json(
            params        = params,
            metadata      = wafer_metadata,
            sample_map    = sample_map,
            measure_rects = measure_rects,
            tj_data_map   = tj_data_map,
            config_json_path = config_json_path,
        )
        
        if out_gds:
            final_output_gds = str(Path(original_gds).with_stem(f"{Path(original_gds).stem}_with_text"))
            print(f"Copying finished GDS back to network: {final_output_gds}")
            shutil.copy2(out_gds, final_output_gds)
        print("✅ Generation completely finished!")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

def main():
    if len(sys.argv) != 2:
        print("Usage: python extract_and_export_full.py path\\to\\config.json")
        sys.exit(1)
    generate_from_config(sys.argv[1])

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)
