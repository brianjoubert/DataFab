"""Table model: one row per die, columns Sample, R1..Rn (latest values, editable)."""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor

from wafer_db import WaferDatabase, latest_value
from gui.widgets import format_ohm

_SUFFIX = {"": 1.0, "k": 1e3, "K": 1e3, "m": 1e-3, "M": 1e6, "G": 1e9}


def parse_ohm(text: str) -> float:
    """'7012', '7.01k', '1.2 MΩ', '350 ohm' -> value in Ω."""
    s = text.strip().replace("Ω", "").replace("ohm", "").replace("Ohm", "").replace(",", ".").strip()
    m = re.fullmatch(r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*([kKmMG]?)", s)
    if not m:
        raise ValueError(f"Not a resistance value: {text!r}")
    return float(m.group(1)) * _SUFFIX[m.group(2)]


class ResistanceTableModel(QAbstractTableModel):
    edited = Signal(str, int, str)  # label, res_id, message

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db: Optional[WaferDatabase] = None
        self.dies: List[dict] = []
        self.n_res = 0
        self.color_for = None  # callable(value) -> QColor

    def load(self, db: Optional[WaferDatabase], die_type: str):
        self.beginResetModel()
        self.db = db
        self.dies = db.dies_of_type(die_type) if db else []
        self.n_res = max((len(d.get("resistances", [])) for d in self.dies), default=0)
        self.endResetModel()

    def refresh(self):
        if self.dies:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.dies) - 1, self.n_res))

    # ------------------------------------------------------------------ helpers
    def key(self, index: QModelIndex) -> Tuple[Optional[str], Optional[int]]:
        if not index.isValid():
            return None, None
        die = self.dies[index.row()]
        return die["label"], (index.column() if index.column() > 0 else None)

    def find(self, label: str, res_id: Optional[int]) -> QModelIndex:
        for row, d in enumerate(self.dies):
            if d["label"] == label:
                return self.index(row, res_id if res_id and res_id <= self.n_res else 0)
        return QModelIndex()

    def _res(self, index: QModelIndex):
        if index.column() == 0:
            return None
        res = self.dies[index.row()].get("resistances", [])
        rid = index.column()
        return next((r for r in res if r.get("id") == rid), None)

    # ------------------------------------------------------------------ Qt API
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.dies)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 1 + self.n_res

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return "Sample" if section == 0 else f"R{section}"
        return str(section + 1)

    def flags(self, index):
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() > 0 and self._res(index) is not None:
            f |= Qt.ItemIsEditable
        return f

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        die = self.dies[index.row()]
        if index.column() == 0:
            if role in (Qt.DisplayRole, Qt.EditRole):
                return die["label"]
            if role == Qt.ToolTipRole and die.get("notes"):
                return die["notes"]
            return None
        r = self._res(index)
        if r is None:
            return None
        v = latest_value(r)
        if role == Qt.DisplayRole:
            return format_ohm(v) if v is not None else ""
        if role == Qt.EditRole:
            return "" if v is None else f"{v:.6g}"
        if role == Qt.UserRole:  # numeric value for sorting
            return v if v is not None else float("-inf")
        if role == Qt.DecorationRole and v is not None and self.color_for:
            return self.color_for(v)
        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole:
            hist = r.get("history", [])
            if not hist:
                return "No measurement yet. Type a value (e.g. 7012, 7.01k) or use Register."
            rows = [f"{h['timestamp']}  {format_ohm(h['value_ohm'])}" for h in hist[-10:]]
            return "History (latest last):\n" + "\n".join(rows) + "\n\nClear the cell to undo the latest value."
        if role == Qt.ForegroundRole and len(r.get("history", [])) > 1:
            return QColor("#1d4ed8")
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or index.column() == 0 or self.db is None:
            return False
        label, rid = self.key(index)
        text = "" if value is None else str(value).strip()
        if text == "":
            removed = self.db.remove_last(label, rid)
            if removed is None:
                return False
            msg = f"{label} R{rid}: removed {format_ohm(removed['value_ohm'])} (kept under 'removed')"
        else:
            try:
                v = parse_ohm(text)
            except ValueError as e:
                self.edited.emit(label, rid, f"⚠ {e}")
                return False
            if v == latest_value(self.db.resistance(label, rid)):
                return False
            self.db.add_value(label, rid, v)
            msg = f"{label} R{rid} = {format_ohm(v)}"
        self.dataChanged.emit(index, index)
        self.edited.emit(label, rid, msg)
        return True


class FilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFilterKeyColumn(0)
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.setSortRole(Qt.UserRole)

    def lessThan(self, left, right):
        if left.column() == 0:
            return str(left.data(Qt.DisplayRole)) < str(right.data(Qt.DisplayRole))
        return (left.data(Qt.UserRole) or float("-inf")) < (right.data(Qt.UserRole) or float("-inf"))
