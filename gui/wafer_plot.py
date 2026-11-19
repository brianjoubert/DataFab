"""Interactive wafer map (pyqtgraph): dies, measurement regions coloured by resistance."""
from __future__ import annotations

from typing import Callable, Dict, Optional, Tuple

import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPen
from PySide6.QtWidgets import QGraphicsRectItem

from wafer_db import WaferDatabase, latest_value
from gui.widgets import format_ohm

EMPTY_FILL = QColor("#e5e7eb")
DIE_PEN = QColor("#334155")
SELECT_PEN = QColor("#ef4444")


def _esc(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_RULE = "<span style='color:#4b5563'>──────────────</span>"


def die_tooltip(d: Dict, wafer: Optional[Dict] = None, highlight_res: Optional[int] = None) -> str:
    """
    Full sample information (like the original viewer's hover): identity, grid position,
    geometry, every resistance with its history, notes and wafer details.
    `highlight_res` marks the pad under the mouse.
    """
    row, col = d.get("MAP_ROW"), d.get("MAP_COL")
    lines = [f"<b style='font-size:11pt'>{_esc(d['label'])}</b>",
             f"Type: {_esc(d.get('type', 'sample'))}"]
    if row is not None and col is not None:
        lines.append(f"Grid: row {row}, column {col}")
    lines += [f"Position: x = {d['x_um']:.1f} µm, y = {d['y_um']:.1f} µm",
              f"Size: {d['width_um']:.1f} × {d['height_um']:.1f} µm",
              f"Measurement regions: {d.get('num_measure_regions', len(d.get('resistances', [])))}"]

    lines.append(_RULE)
    for r in d.get("resistances", []):
        hist = r.get("history", [])
        if hist:
            vals = ", ".join(format_ohm(h["value_ohm"]) for h in hist[-8:])
            more = f" (+{len(hist) - 8} earlier)" if len(hist) > 8 else ""
            text = f"R{r['id']} history: {vals}{more} · last {hist[-1]['timestamp']}"
        else:
            text = f"R{r['id']}: N/A"
        if r.get("removed"):
            text += f" · {len(r['removed'])} removed"
        lines.append(f"<b style='color:#93c5fd'>▶ {text}</b>" if r.get("id") == highlight_res else text)

    if d.get("notes"):
        lines.append(_RULE)
        lines.append(f"<i>Notes: {_esc(d['notes'][:400])}</i>")

    if wafer:
        lines.append(_RULE)
        size = wafer.get("size_um")
        size_txt = f"{size} µm ({float(size) / 25400:.1f}\")" if size else "?"
        lines.append(f"Wafer: {_esc(wafer.get('material', ''))} · {size_txt} · "
                     f"{_esc(wafer.get('thickness_um', '?'))} µm thick")
        extra = [f"{k.replace('_', ' ').capitalize()}: {_esc(wafer[k])}"
                 for k in ("recipe", "wafer_id") if wafer.get(k) not in (None, "", "unknown")]
        lines += extra
    return "<br>".join(lines)


class ClickableRect(QGraphicsRectItem):
    def __init__(self, rect: QRectF, key: Tuple[str, Optional[int]], on_click: Callable):
        super().__init__(rect)
        self.key = key
        self._on_click = on_click

    # pyqtgraph's scene sends clicks (not drags) to items through this method
    def mouseClickEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            ev.accept()
            self._on_click(*self.key)


class WaferPlot(pg.PlotWidget):
    """Box zoom (rubber band follows the mouse), wheel zoom at cursor, round wafer (1:1)."""

    selected = Signal(str, object)  # label, res_id (int or None)

    def __init__(self, parent=None):
        super().__init__(parent, background="w")
        self.plotItem.setMenuEnabled(False)
        self.plotItem.hideButtons()
        self.plotItem.showGrid(x=True, y=True, alpha=0.12)
        self.plotItem.setLabel("bottom", "X", units="µm")
        self.plotItem.setLabel("left", "Y", units="µm")
        for ax in ("bottom", "left"):
            self.plotItem.getAxis(ax).enableAutoSIPrefix(False)
        self.vb = self.plotItem.getViewBox()
        self.vb.setAspectLocked(True, ratio=1.0)
        self.vb.setMouseMode(pg.ViewBox.RectMode)
        self.vb.sigRangeChanged.connect(self._update_label_visibility)

        self.cmap = pg.colormap.get("viridis")
        self.colorbar = pg.ColorBarItem(values=(0, 1), colorMap=self.cmap, interactive=False,
                                        width=12, label="Resistance (Ω)")
        self.colorbar.setVisible(False)
        self.plotItem.layout.addItem(self.colorbar, 2, 5)

        self.db: Optional[WaferDatabase] = None
        self.die_type = "sample"
        self.region_items: Dict[Tuple[str, int], ClickableRect] = {}
        self.die_items: Dict[str, ClickableRect] = {}
        self.labels: list = []
        self.show_labels = True
        self.highlight: Optional[QGraphicsRectItem] = None
        self.levels = (0.0, 1.0)

    # ------------------------------------------------------------ public API
    def set_pan_mode(self, pan: bool):
        self.vb.setMouseMode(pg.ViewBox.PanMode if pan else pg.ViewBox.RectMode)

    def set_aspect_locked(self, locked: bool):
        self.vb.setAspectLocked(locked, ratio=1.0)

    def set_labels_visible(self, show: bool):
        self.show_labels = show
        self._update_label_visibility()

    def fit_wafer(self):
        if self.db:
            r = self.db.wafer_radius
            self.vb.setRange(xRange=(-r, r), yRange=(-r, r), padding=0.04)

    def load(self, db: WaferDatabase, die_type: str):
        self.db = db
        self.die_type = die_type
        self._rebuild()

    def refresh_colors(self):
        """Recolour regions and update tooltips after values changed (no rebuild)."""
        if not self.db:
            return
        self._compute_levels()
        for (label, rid), item in self.region_items.items():
            r = self.db.resistance(label, rid)
            item.setBrush(QBrush(self._color(latest_value(r) if r else None)))
            item.setToolTip(die_tooltip(self.db.by_label[label], self.db.metadata, rid))
        for label, item in self.die_items.items():
            item.setToolTip(die_tooltip(self.db.by_label[label], self.db.metadata))

    def select(self, label: Optional[str], res_id: Optional[int], ensure_visible: bool = False):
        if self.highlight is not None:
            self.vb.removeItem(self.highlight)
            self.highlight = None
        if not label or not self.db or label not in self.db.by_label:
            return
        item = self.region_items.get((label, res_id)) if res_id else None
        item = item or self.die_items.get(label)
        if item is None:
            return
        rect = item.rect()
        self.highlight = QGraphicsRectItem(rect)
        pen = QPen(SELECT_PEN, 3)
        pen.setCosmetic(True)
        self.highlight.setPen(pen)
        self.highlight.setBrush(Qt.NoBrush)
        self.highlight.setZValue(50)
        self.vb.addItem(self.highlight)
        if ensure_visible:
            die = self.db.by_label[label]
            die_rect = QRectF(die["x_um"], die["y_um"], die["width_um"], die["height_um"])
            if not self.vb.viewRect().contains(die_rect):
                span = max(die_rect.width(), die_rect.height()) * 1.4
                c = die_rect.center()
                self.vb.setRange(xRange=(c.x() - span / 2, c.x() + span / 2),
                                 yRange=(c.y() - span / 2, c.y() + span / 2), padding=0)

    # ------------------------------------------------------------ drawing
    def _compute_levels(self):
        lo, hi = self.db.value_range(self.die_type)
        if lo is None:
            self.levels = (0.0, 1.0)
            self.colorbar.setVisible(False)
        else:
            if hi == lo:
                hi = lo + 1.0
            self.levels = (lo, hi)
            self.colorbar.setLevels(self.levels)
            self.colorbar.setVisible(True)

    def _color(self, v: Optional[float]) -> QColor:
        if v is None:
            return EMPTY_FILL
        lo, hi = self.levels
        return self.cmap.map(min(max((v - lo) / (hi - lo), 0.0), 1.0), mode="qcolor")

    def _rebuild(self):
        self.vb.clear()
        self.region_items.clear()
        self.die_items.clear()
        self.labels.clear()
        self.highlight = None
        db = self.db

        xs, ys = db.wafer_outline()
        outline = pg.PlotDataItem(xs, ys, pen=pg.mkPen("#94a3b8", width=2),
                                  fillLevel=None, brush=None)
        wafer_fill = pg.QtWidgets.QGraphicsPathItem()
        path = pg.QtGui.QPainterPath()
        path.moveTo(xs[0], ys[0])
        for x, y in zip(xs[1:], ys[1:]):
            path.lineTo(x, y)
        wafer_fill.setPath(path)
        wafer_fill.setBrush(QBrush(QColor("#f1f5f9")))
        wafer_fill.setPen(QPen(Qt.NoPen))
        wafer_fill.setZValue(-10)
        self.vb.addItem(wafer_fill)
        self.vb.addItem(outline)

        self._compute_levels()
        die_pen = QPen(DIE_PEN, 1.2)
        die_pen.setCosmetic(True)
        reg_pen = QPen(QColor("#0f172a"), 0.6)
        reg_pen.setCosmetic(True)
        font = QFont()
        font.setPointSizeF(8)

        for d in db.dies_of_type(self.die_type):
            label = d["label"]
            rect = QRectF(d["x_um"], d["y_um"], d["width_um"], d["height_um"])
            die_item = ClickableRect(rect, (label, None), self._clicked)
            die_item.setPen(die_pen)
            die_item.setBrush(QBrush(QColor(255, 255, 255, 120)))
            die_item.setToolTip(die_tooltip(d, db.metadata))
            die_item.setZValue(1)
            self.vb.addItem(die_item)
            self.die_items[label] = die_item

            for mr, r in zip(d.get("measure_regions", []), d.get("resistances", [])):
                rr = QRectF(mr["x0_um"], mr["y0_um"], mr["x1_um"] - mr["x0_um"], mr["y1_um"] - mr["y0_um"])
                item = ClickableRect(rr, (label, r["id"]), self._clicked)
                item.setPen(reg_pen)
                item.setBrush(QBrush(self._color(latest_value(r))))
                item.setToolTip(die_tooltip(d, db.metadata, r["id"]))
                item.setZValue(5)
                self.vb.addItem(item)
                self.region_items[(label, r["id"])] = item

            # rect.bottom() is the die's top edge (y grows upwards); anchor (0.5, 0) hangs
            # the text below that point on screen, i.e. inside the top of the die.
            text = pg.TextItem(label, color="#0f172a", anchor=(0.5, 0.0))
            text.setFont(font)
            text.setPos(rect.center().x(), rect.bottom())
            text.setZValue(20)
            self.vb.addItem(text)
            self.labels.append((text, rect.width(), len(label)))

        self.fit_wafer()
        self._update_label_visibility()

    def _update_label_visibility(self, *_):
        px = self.vb.viewPixelSize()[0] if self.vb.width() > 0 else 0
        for text, width_um, n in self.labels:
            fits = px > 0 and width_um / px >= n * 6.2 + 6
            text.setVisible(self.show_labels and fits)

    def _clicked(self, label: str, res_id: Optional[int]):
        self.selected.emit(label, res_id)
