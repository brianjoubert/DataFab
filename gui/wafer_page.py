"""Wafer map & measurement page."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pyqtgraph as pg
from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QTableView, QTableWidget, QTableWidgetItem, QToolButton,
    QVBoxLayout, QWidget,
)

from keithley import DEFAULT_RESOURCE, Keithley2450, MeasurementLoop, SMUConfig, list_resources
from wafer_db import DatabaseFormatError, WaferDatabase, type_label
from gui.table_model import FilterProxy, ResistanceTableModel
from gui.wafer_plot import WaferPlot
from gui.widgets import Card, format_ohm, muted

MAX_POINTS = 5000


class _Bridge(QObject):
    """Carries results from the measurement thread to the GUI thread (queued signals)."""
    point = Signal(float, float)
    status = Signal(str)


class WaferPage(QWidget):
    message = Signal(str)          # status-bar text
    database_loaded = Signal(str)  # path

    def __init__(self, simulate: bool = False, resource: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        self.settings = QSettings()
        self.db: Optional[WaferDatabase] = None
        self.sel_label: Optional[str] = None
        self.sel_res: Optional[int] = None
        self.t_data: list = []
        self.r_data: list = []

        resource = resource or self.settings.value("visa_resource", DEFAULT_RESOURCE)
        self.smu = Keithley2450(SMUConfig(resource=resource, simulate=simulate))
        self.bridge = _Bridge()
        self.bridge.point.connect(self._on_point)
        self.bridge.status.connect(self._on_status)
        self.loop = MeasurementLoop(self.smu, self.bridge.point.emit, self.bridge.status.emit)

        self._build_ui(simulate)
        self._set_enabled(False)

    # ================================================================== UI
    def _build_ui(self, simulate: bool):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # ---- header
        head = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Wafer map")
        title.setObjectName("pageTitle")
        self.file_label = muted("No database loaded — open a generated *_database.json")
        title_box.addWidget(title)
        title_box.addWidget(self.file_label)
        head.addLayout(title_box, 1)
        head.addWidget(QLabel("View"))
        self.type_combo = QComboBox()
        self.type_combo.setMinimumWidth(160)
        self.type_combo.currentIndexChanged.connect(self._type_changed)
        head.addWidget(self.type_combo)
        self.open_btn = QPushButton("Open database…")
        self.open_btn.setObjectName("primary")
        self.open_btn.clicked.connect(self.open_dialog)
        head.addWidget(self.open_btn)
        root.addLayout(head)

        # ---- main splitter: (map | side panel) over measurement
        vsplit = QSplitter(Qt.Vertical)
        hsplit = QSplitter(Qt.Horizontal)

        # map card
        map_card = Card("Wafer")
        self.pan_btn = self._tool("Pan", "Drag to move the view (otherwise drag draws a zoom box)", True)
        self.pan_btn.toggled.connect(lambda on: self.plot.set_pan_mode(on))
        self.lock_btn = self._tool("1:1", "Keep true proportions (round wafer)", True, checked=True)
        self.lock_btn.toggled.connect(lambda on: self.plot.set_aspect_locked(on))
        self.labels_btn = self._tool("Labels", "Show die labels when there is room", True, checked=True)
        self.labels_btn.toggled.connect(lambda on: self.plot.set_labels_visible(on))
        fit_btn = self._tool("Fit", "Show the whole wafer (also: double-click)")
        fit_btn.clicked.connect(lambda: self.plot.fit_wafer())
        for b in (self.pan_btn, self.lock_btn, self.labels_btn, fit_btn):
            map_card.header.addWidget(b)
        self.plot = WaferPlot()
        self.plot.selected.connect(lambda lab, rid: self.select(lab, rid, from_map=True))
        self.plot.scene().sigMouseClicked.connect(self._plot_double_click)
        map_card.body.addWidget(self.plot)
        map_card.body.addWidget(muted("Drag: zoom box · Wheel: zoom at cursor · Click a die or pad to select it"))
        hsplit.addWidget(map_card)

        # side panel
        side = QSplitter(Qt.Vertical)
        sel_card = Card("Selection")
        self.sel_title = QLabel("—")
        self.sel_title.setObjectName("cardTitle")
        self.sel_info = muted("Click a die on the map or a cell in the table.")
        sel_card.body.addWidget(self.sel_title)
        sel_card.body.addWidget(self.sel_info)
        self.history = QTableWidget(0, 2)
        self.history.setHorizontalHeaderLabels(["Time", "Value"])
        self.history.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.history.verticalHeader().setVisible(False)
        self.history.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history.setMaximumHeight(96)
        sel_card.body.addWidget(self.history)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Notes for this die…")
        self.notes.setMaximumHeight(56)
        notes_row = QHBoxLayout()
        notes_row.addWidget(self.notes, 1)
        self.save_notes_btn = QPushButton("Save\nnotes")
        self.save_notes_btn.clicked.connect(self._save_notes)
        notes_row.addWidget(self.save_notes_btn)
        sel_card.body.addLayout(notes_row)
        side.addWidget(sel_card)

        table_card = Card("Resistances")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter…")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(180)
        table_card.header.addWidget(self.search)
        self.model = ResistanceTableModel(self)
        self.model.color_for = lambda v: self.plot._color(v)
        self.model.edited.connect(self._on_edited)
        self.proxy = FilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(-1, Qt.AscendingOrder)  # keep wafer order until a header is clicked
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                                   | QAbstractItemView.AnyKeyPressed)
        self.table.selectionModel().currentChanged.connect(self._table_current_changed)
        self.table.setMinimumHeight(180)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setToolTip("Type a value (7012, 7.01k, 1.2M) · clear a cell to undo its latest value")
        table_card.body.addWidget(self.table)
        side.addWidget(table_card)
        side.setStretchFactor(0, 0)
        side.setStretchFactor(1, 1)
        side.setSizes([230, 520])
        hsplit.addWidget(side)
        hsplit.setStretchFactor(0, 3)
        hsplit.setStretchFactor(1, 2)
        hsplit.setSizes([760, 480])
        vsplit.addWidget(hsplit)

        # measurement card
        meas = Card("Live measurement · Keithley 2450 (delta method)")
        self.status_pill = QLabel("Idle")
        self.status_pill.setObjectName("statusPill")
        meas.header.addWidget(self.status_pill)
        row = QHBoxLayout()
        self.live = pg.PlotWidget(background="w")
        self.live.setMenuEnabled(False)
        self.live.showGrid(x=True, y=True, alpha=0.15)
        self.live.setLabel("bottom", "Time", units="s")
        self.live.setLabel("left", "R", units="Ω")
        self.live_curve = self.live.plot([], [], pen=pg.mkPen("#2563eb", width=2))
        self.live.setMinimumHeight(170)
        row.addWidget(self.live, 3)

        ctrl = QVBoxLayout()
        self.big_value = QLabel("No reading")
        self.big_value.setObjectName("bigValue")
        ctrl.addWidget(self.big_value)
        btns = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.start_btn.setObjectName("success")
        self.start_btn.clicked.connect(self.toggle_measurement)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear_live)
        btns.addWidget(self.start_btn)
        btns.addWidget(clear_btn)
        ctrl.addLayout(btns)

        form = QGridLayout()
        form.setHorizontalSpacing(8)
        self.current = QDoubleSpinBox()
        self.current.setDecimals(3)
        self.current.setRange(0.001, 100.0)
        self.current.setSingleStep(0.01)
        self.current.setValue(float(self.settings.value("current_ua", 0.02)))
        self.current.setSuffix(" µA")
        self.current.valueChanged.connect(self._params_changed)
        self.nplc = QComboBox()
        for v in ("0.1", "1", "10"):
            self.nplc.addItem(v, float(v))
        self.nplc.setCurrentIndex(1)
        self.nplc.currentIndexChanged.connect(self._params_changed)
        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.2, 60.0)
        self.interval.setValue(1.0)
        self.interval.setSuffix(" s")
        self.interval.valueChanged.connect(self._params_changed)
        self.visa = QComboBox()
        self.visa.setEditable(True)
        self.visa.addItem(self.smu.cfg.resource)
        self.visa.setMinimumWidth(240)
        refresh = QToolButton()
        refresh.setText("↻")
        refresh.setToolTip("Search for connected instruments")
        refresh.clicked.connect(self._refresh_visa)
        self.simulate = QCheckBox("Simulate (no instrument)")
        self.simulate.setChecked(simulate)
        form.addWidget(QLabel("Current"), 0, 0)
        form.addWidget(self.current, 0, 1)
        form.addWidget(QLabel("NPLC"), 0, 2)
        form.addWidget(self.nplc, 0, 3)
        form.addWidget(QLabel("Interval"), 1, 0)
        form.addWidget(self.interval, 1, 1)
        form.addWidget(self.simulate, 1, 2, 1, 2)
        visa_row = QHBoxLayout()
        visa_row.addWidget(self.visa, 1)
        visa_row.addWidget(refresh)
        form.addWidget(QLabel("VISA"), 2, 0)
        form.addLayout(visa_row, 2, 1, 1, 3)
        ctrl.addLayout(form)

        self.register_btn = QPushButton("Register value → selected cell   (Ctrl+Enter)")
        self.register_btn.setObjectName("primary")
        self.register_btn.clicked.connect(self.register_value)
        ctrl.addWidget(self.register_btn)
        ctrl.addStretch(1)
        row.addLayout(ctrl, 2)
        meas.body.addLayout(row)
        vsplit.addWidget(meas)
        vsplit.setStretchFactor(0, 3)
        vsplit.setStretchFactor(1, 1)
        vsplit.setSizes([620, 260])
        root.addWidget(vsplit, 1)

        QShortcut(QKeySequence("Ctrl+Return"), self, self.register_value)
        QShortcut(QKeySequence("Ctrl+Enter"), self, self.register_value)

    def _tool(self, text, tip, checkable=False, checked=False):
        b = QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.setChecked(checked)
        return b

    def _set_enabled(self, on: bool):
        for w in (self.type_combo, self.table, self.search, self.notes, self.save_notes_btn,
                  self.register_btn, self.plot):
            w.setEnabled(on)

    # ================================================================== database
    def open_dialog(self):
        start = self.settings.value("last_database", "")
        path, _ = QFileDialog.getOpenFileName(self, "Open wafer database", start,
                                              "Wafer database (*_database*.json);;JSON (*.json)")
        if path:
            self.load_database(path)

    def load_database(self, path: str) -> bool:
        try:
            db = WaferDatabase(path)
        except DatabaseFormatError as e:
            QMessageBox.warning(self, "Not a wafer database", str(e))
            return False
        except Exception as e:
            QMessageBox.critical(self, "Could not open database", f"{path}\n\n{e}")
            return False
        self.db = db
        self.settings.setValue("last_database", str(path))
        self.file_label.setText(f"{Path(path).name}  ·  {len(db.dies)} entries  ·  "
                                f"{db.wafer_inches:.0f}-inch {db.metadata.get('material', '')} wafer")
        self.file_label.setToolTip(str(path))
        self.type_combo.blockSignals(True)
        self.type_combo.clear()
        for t in db.types:
            self.type_combo.addItem(f"{type_label(t)} ({len(db.dies_of_type(t))})", t)
        self.type_combo.blockSignals(False)
        self._set_enabled(True)
        self._type_changed()
        for w in db.warnings:
            self.message.emit(f"⚠ {w}")
        self.message.emit(f"Loaded {Path(path).name}")
        self.database_loaded.emit(str(path))
        return True

    @property
    def die_type(self) -> str:
        return self.type_combo.currentData() or "sample"

    def _type_changed(self, *_):
        if not self.db:
            return
        self.plot.load(self.db, self.die_type)
        self.model.load(self.db, self.die_type)
        self.table.resizeColumnsToContents()
        self.select(None, None)

    # ================================================================== selection
    def select(self, label: Optional[str], res_id: Optional[int], from_map=False, from_table=False):
        self.sel_label, self.sel_res = label, res_id
        self.plot.select(label, res_id, ensure_visible=from_table)
        if not from_table and label:
            src = self.model.find(label, res_id)
            idx = self.proxy.mapFromSource(src)
            if idx.isValid():
                self.table.selectionModel().blockSignals(True)
                self.table.setCurrentIndex(idx)
                self.table.selectionModel().blockSignals(False)
                self.table.scrollTo(idx)
        self._update_selection_panel()

    def _table_current_changed(self, current, _previous):
        label, rid = self.model.key(self.proxy.mapToSource(current))
        if label:
            self.select(label, rid, from_table=True)

    def _plot_double_click(self, ev):
        if ev.double():
            self.plot.fit_wafer()

    def _update_selection_panel(self):
        self.history.setRowCount(0)
        if not self.db or not self.sel_label:
            self.sel_title.setText("—")
            self.sel_info.setText("Click a die on the map or a cell in the table.")
            self.notes.setPlainText("")
            return
        d = self.db.by_label[self.sel_label]
        self.sel_title.setText(self.sel_label + (f"  ·  R{self.sel_res}" if self.sel_res else ""))
        self.sel_info.setText(f"{type_label(d.get('type', 'sample'))} · {d['width_um']:.0f} × {d['height_um']:.0f} µm "
                              f"at ({d['x_um']:.0f}, {d['y_um']:.0f}) µm · {d.get('num_measure_regions', 0)} pads")
        self.notes.setPlainText(d.get("notes", "") or "")
        if self.sel_res:
            r = self.db.resistance(self.sel_label, self.sel_res) or {}
            hist = list(reversed(r.get("history", [])))
        else:
            hist = []
            for r in d.get("resistances", []):
                for h in r.get("history", []):
                    hist.append({**h, "timestamp": f"R{r['id']} · {h['timestamp']}"})
            hist.sort(key=lambda h: h["timestamp"].split("· ")[-1], reverse=True)
        self.history.setRowCount(len(hist))
        for i, h in enumerate(hist):
            self.history.setItem(i, 0, QTableWidgetItem(h["timestamp"]))
            self.history.setItem(i, 1, QTableWidgetItem(format_ohm(h["value_ohm"])))

    # ================================================================== edits
    def _save(self) -> Optional[str]:
        """Save all files; returns the save time, or None after showing an error."""
        try:
            return self.db.save()
        except Exception as e:
            QMessageBox.critical(self, "Save failed",
                                 f"The change is kept in memory but could not be written:\n{e}")
            return None

    def _on_edited(self, label, rid, msg):
        if msg.startswith("⚠"):
            self.message.emit(msg)
            return
        ts = self._save()
        self.plot.refresh_colors()
        self.model.refresh()
        self._update_selection_panel()
        if ts:
            self.message.emit(f"{msg} · saved {ts}")

    def _save_notes(self):
        if not self.db or not self.sel_label:
            return
        self.db.set_notes(self.sel_label, self.notes.toPlainText())
        ts = self._save()
        self.plot.refresh_colors()
        if ts:
            self.message.emit(f"Notes saved for {self.sel_label} · {ts}")

    def register_value(self):
        if not self.db:
            return
        if not self.r_data:
            self.message.emit("⚠ No measurement yet — press Start first.")
            return
        idx = self.proxy.mapToSource(self.table.currentIndex())
        label, rid = self.model.key(idx)
        if not label or not rid or self.db.resistance(label, rid) is None:
            self.message.emit("⚠ Select a resistance cell (R#) first.")
            return
        value = self.r_data[-1]
        self.db.add_value(label, rid, value)
        self._on_edited(label, rid, f"Registered {format_ohm(value)} → {label} R{rid}")
        self._advance(idx)

    def _advance(self, src_idx):
        """Move to the next resistance of the same die, else R1 of the next row (view order)."""
        die = self.model.dies[src_idx.row()]
        n = len(die.get("resistances", []))
        if src_idx.column() < n:
            nxt = self.model.index(src_idx.row(), src_idx.column() + 1)
            view_idx = self.proxy.mapFromSource(nxt)
        else:
            cur = self.proxy.mapFromSource(src_idx)
            view_idx = self.proxy.index(cur.row() + 1, 1) if cur.row() + 1 < self.proxy.rowCount() else cur
        if view_idx.isValid():
            self.table.setCurrentIndex(view_idx)
            self.table.scrollTo(view_idx)

    # ================================================================== measurement
    def _params_changed(self, *_):
        self.loop.current_ua = self.current.value()
        self.loop.nplc = float(self.nplc.currentData())
        self.loop.interval_s = self.interval.value()
        self.settings.setValue("current_ua", self.current.value())

    def toggle_measurement(self):
        if self.loop.running:
            self.loop.stop(wait=False)
            self._set_running(False)
            self._on_status("Stopped")
            return
        resource = self.visa.currentText().strip() or DEFAULT_RESOURCE
        self.settings.setValue("visa_resource", resource)
        self.smu.reconfigure(SMUConfig(resource=resource, simulate=self.simulate.isChecked()))
        self._params_changed()
        self.loop.start()
        self._set_running(True)
        self._on_status("Connecting…")
        QTimer.singleShot(500, self._check_alive)

    def _check_alive(self):
        if not self.loop.running and self.start_btn.text() == "Stop":
            self._set_running(False)
        elif self.loop.running:
            QTimer.singleShot(500, self._check_alive)

    def _set_running(self, on: bool):
        self.start_btn.setText("Stop" if on else "Start")
        self.start_btn.setObjectName("danger" if on else "success")
        self.start_btn.style().unpolish(self.start_btn)
        self.start_btn.style().polish(self.start_btn)
        self.visa.setEnabled(not on)
        self.simulate.setEnabled(not on)

    def _on_point(self, t: float, r: float):
        self.t_data.append(t)
        self.r_data.append(r)
        if len(self.t_data) > MAX_POINTS:
            del self.t_data[:-MAX_POINTS], self.r_data[:-MAX_POINTS]
        self.live_curve.setData(self.t_data, self.r_data)
        self.big_value.setText(format_ohm(r))

    def _on_status(self, text: str):
        self.status_pill.setText(text)

    def _clear_live(self):
        self.t_data.clear()
        self.r_data.clear()
        self.live_curve.setData([], [])
        self.big_value.setText("No reading")

    def _refresh_visa(self):
        found = list_resources()
        current = self.visa.currentText()
        self.visa.clear()
        self.visa.addItems(found or [current])
        if current in found:
            self.visa.setCurrentText(current)
        self.message.emit(f"Found {len(found)} instrument(s)" if found else "No VISA instruments found")

    def shutdown(self):
        try:
            self.loop.stop(wait=True)
        finally:
            self.smu.close()
