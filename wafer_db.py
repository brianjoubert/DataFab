"""Wafer database model: load/save the generated JSON files and edit measurement history."""
from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from datafab_io import atomic_write_json, load_json_lenient

TIMESTAMP_FMT = "%Y-%m-%d %H:%M"


def now_stamp() -> str:
    return datetime.now().strftime(TIMESTAMP_FMT)


def latest_value(res: Dict) -> Optional[float]:
    hist = res.get("history") or []
    return hist[-1]["value_ohm"] if hist else None


def type_label(t: str) -> str:
    return "Samples" if t == "sample" else t.replace("_", " ").title()


class DatabaseFormatError(ValueError):
    pass


class WaferDatabase:
    """
    The main '<config>_database.json' (samples) plus the per-type sub-databases listed in
    metadata['sub_databases'] (or the legacy '<name>_TJ.json').
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        data = load_json_lenient(self.path)
        if not isinstance(data, dict) or "metadata" not in data or "dies" not in data:
            raise DatabaseFormatError(
                "This file is not a wafer database (no 'metadata'/'dies').\n"
                "Select the generated '*_database.json', not the generator config.")
        self.metadata: Dict = data["metadata"]
        self.dies: List[Dict] = list(data["dies"])
        for d in self.dies:
            d.setdefault("type", "sample")
        self.loaded_files = [self.path]
        self.warnings: List[str] = []
        self._backed_up: set = set()

        sub_dbs = self.metadata.get("sub_databases", [])
        if sub_dbs:
            for info in sub_dbs:
                fname = info.get("filename")
                if fname:
                    self._load_sub(self.path.with_name(fname))
        else:
            legacy = self.path.with_name(f"{self.path.stem}_TJ{self.path.suffix}")
            if legacy.exists():
                self._load_sub(legacy, default_type="test_junction")

        self.by_label: Dict[str, Dict] = {d["label"]: d for d in self.dies}

    def _load_sub(self, path: Path, default_type: Optional[str] = None):
        if not path.exists():
            self.warnings.append(f"Sub-database not found: {path.name}")
            return
        try:
            for d in load_json_lenient(path).get("dies", []):
                if default_type:
                    d.setdefault("type", default_type)
                self.dies.append(d)
            self.loaded_files.append(path)
        except Exception as e:  # keep going with what we have
            self.warnings.append(f"Could not load {path.name}: {e}")

    # ------------------------------------------------------------------ queries
    @property
    def types(self) -> List[str]:
        others = sorted({d.get("type", "sample") for d in self.dies} - {"sample"})
        return ["sample"] + others

    def dies_of_type(self, t: str) -> List[Dict]:
        return [d for d in self.dies if d.get("type", "sample") == t]

    def resistance(self, label: str, res_id: int) -> Optional[Dict]:
        die = self.by_label.get(label)
        if not die:
            return None
        return next((r for r in die.get("resistances", []) if r.get("id") == res_id), None)

    def value_range(self, t: str) -> Tuple[Optional[float], Optional[float]]:
        vals = [v for d in self.dies_of_type(t) for r in d.get("resistances", [])
                if (v := latest_value(r)) is not None]
        return (min(vals), max(vals)) if vals else (None, None)

    # ------------------------------------------------------------------ edits
    def add_value(self, label: str, res_id: int, value: float, timestamp: Optional[str] = None):
        r = self.resistance(label, res_id)
        if r is None:
            raise KeyError(f"{label} R{res_id} not found")
        if not math.isfinite(value):
            raise ValueError("Value must be a finite number")
        r.setdefault("history", []).append({"timestamp": timestamp or now_stamp(), "value_ohm": float(value)})

    def remove_last(self, label: str, res_id: int) -> Optional[Dict]:
        """Undo the latest value. It is kept under 'removed' with the removal time."""
        r = self.resistance(label, res_id)
        if not r or not r.get("history"):
            return None
        removed = dict(r["history"].pop())
        removed["removed_at"] = now_stamp()
        r.setdefault("removed", []).append(removed)
        return removed

    def set_notes(self, label: str, text: str):
        self.by_label[label]["notes"] = text or ""

    # ------------------------------------------------------------------ save
    def save(self) -> str:
        """Atomic save of every file; the first save of a session keeps a '.bak' copy."""
        def write(path: Path, dies: List[Dict]):
            key = str(path)
            atomic_write_json(path, {"metadata": self.metadata, "dies": dies}, backup=key not in self._backed_up)
            self._backed_up.add(key)

        write(self.path, self.dies_of_type("sample"))
        sub_dbs = self.metadata.get("sub_databases", [])
        for info in sub_dbs:
            if info.get("filename"):
                write(self.path.with_name(info["filename"]), self.dies_of_type(info.get("type")))
        if not sub_dbs and any(d.get("type") == "test_junction" for d in self.dies):
            write(self.path.with_name(f"{self.path.stem}_TJ{self.path.suffix}"), self.dies_of_type("test_junction"))
        return now_stamp()

    # ------------------------------------------------------------------ geometry
    @property
    def wafer_radius(self) -> float:
        return float(self.metadata.get("size_um", 76200)) / 2.0

    @property
    def wafer_inches(self) -> float:
        return float(self.metadata.get("size_um", 76200)) / 25400.0

    def wafer_outline(self, n: int = 400) -> Tuple[List[float], List[float]]:
        """Circle with the primary flat at the top (same convention as before)."""
        R = self.wafer_radius
        flat_len = 22000.0 if abs(self.wafer_inches - 3) < 0.1 else 14000.0
        flat_len = min(flat_len, 2 * R * 0.95)
        y_flat = math.sqrt(max(R * R - (flat_len / 2) ** 2, 0.0))
        a0 = math.asin(y_flat / R)              # angle of the right end of the flat
        start, end = math.pi - a0, 2 * math.pi + a0  # arc going under the wafer
        xs = [R * math.cos(start + (end - start) * i / n) for i in range(n + 1)]
        ys = [R * math.sin(start + (end - start) * i / n) for i in range(n + 1)]
        xs.append(xs[0]); ys.append(ys[0])     # closing segment = the flat
        return xs, ys
