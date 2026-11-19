"""Generate-database page: edit a config and run the GDS -> JSON generator."""
from __future__ import annotations

import copy
import sys
import threading
import traceback
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from datafab_io import atomic_write_json, load_json_lenient
from gui.widgets import Card, muted

DEFAULT_CONFIG = {
    "script_parameters": {
        "input_gds_file": "",
        "input_layer": [201, 0],
        "resistance_layer": [202, 0],
        "test_junctions": [{"name": "tj_single", "layer": [250, 0]}],
        "text_layer": [203, 0],
        "text_size_um": 100.0,
        "relative_pos_x": 0.5,
        "relative_pos_y": 0.02,
        "tolerance_fraction": 0.1,
        "label_prefix": "",
        "mirror": True,
        "mirror_offset_fraction": 0.15,
        "export_gds_with_text": True,
    },
    "wafer_metadata": {"material": "Silicon", "size_um": 76200, "thickness_um": 385, "recipe": ""},
}


class LayerEdit(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.layer = QSpinBox()
        self.layer.setRange(0, 65535)
        self.dtype = QSpinBox()
        self.dtype.setRange(0, 65535)
        lay.addWidget(self.layer)
        lay.addWidget(QLabel("/"))
        lay.addWidget(self.dtype)
        lay.addStretch(1)
        self.layer.valueChanged.connect(self.changed)
        self.dtype.valueChanged.connect(self.changed)
        self.setToolTip("GDS layer / datatype")

    def get(self):
        return [self.layer.value(), self.dtype.value()]

    def set(self, v):
        v = list(v or [0, 0]) + [0, 0]
        self.layer.setValue(int(v[0]))
        self.dtype.setValue(int(v[1]))


class _Writer:
    def __init__(self, emit):
        self.emit = emit

    def write(self, text):
        if text.strip():
            self.emit(text.rstrip())

    def flush(self):
        pass


class _Bridge(QObject):
    log = Signal(str)
    done = Signal(bool, str)


class GeneratorPage(QWidget):
    message = Signal(str)
    open_database = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        self.settings = QSettings()
        self.config_path: Optional[str] = None
        self.config: dict = copy.deepcopy(DEFAULT_CONFIG)
        self.dirty = False
        self.last_output: Optional[str] = None
        self.bridge = _Bridge()
        self.bridge.log.connect(self._log)
        self.bridge.done.connect(self._done)
        self._loading = False
        self._build_ui()
        self._fill_form(self.config)
        self._set_dirty(False)

    # ================================================================== UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        head = QHBoxLayout()
        tb = QVBoxLayout()
        title = QLabel("Generate database")
        title.setObjectName("pageTitle")
        self.path_label = muted("New configuration (not saved yet)")
        tb.addWidget(title)
        tb.addWidget(self.path_label)
        head.addLayout(tb, 1)
        for text, slot in (("New", self.new_config), ("Open…", self.open_dialog),
                           ("Save", self.save), ("Save as…", self.save_as)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            head.addWidget(b)
        root.addLayout(head)

        split = QSplitter(Qt.Horizontal)

        # ---------------- form (scrollable)
        form_host = QWidget()
        form_host.setObjectName("page")
        fl = QVBoxLayout(form_host)
        fl.setContentsMargins(0, 0, 8, 0)
        fl.setSpacing(12)

        c1 = Card("Layout")
        g = QFormLayout()
        g.setLabelAlignment(Qt.AlignRight)
        gds_row = QHBoxLayout()
        self.gds = QLineEdit()
        self.gds.setPlaceholderText("Path to the .gds layout")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_gds)
        gds_row.addWidget(self.gds, 1)
        gds_row.addWidget(browse)
        g.addRow("GDS file", gds_row)
        self.topcell = QLineEdit()
        self.topcell.setPlaceholderText("(automatic)")
        g.addRow("Top cell", self.topcell)
        self.l_sample = LayerEdit()
        self.l_res = LayerEdit()
        self.l_text = LayerEdit()
        g.addRow("Sample layer", self.l_sample)
        g.addRow("Measurement layer", self.l_res)
        g.addRow("Text layer", self.l_text)
        c1.body.addLayout(g)
        fl.addWidget(c1)

        c2 = Card("Test junctions")
        add = QPushButton("Add")
        rem = QPushButton("Remove")
        c2.header.addWidget(add)
        c2.header.addWidget(rem)
        self.tj = QTableWidget(0, 3)
        self.tj.setHorizontalHeaderLabels(["Name", "Layer", "Datatype"])
        self.tj.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tj.verticalHeader().setVisible(False)
        self.tj.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tj.setMinimumHeight(110)
        self.tj.setMaximumHeight(150)
        add.clicked.connect(self._add_tj)
        rem.clicked.connect(self._remove_tj)
        c2.body.addWidget(self.tj)
        c2.body.addWidget(muted("Each type gets its own database file. Layers missing in the GDS are skipped."))
        fl.addWidget(c2)

        c3 = Card("Labels")
        g3 = QFormLayout()
        g3.setLabelAlignment(Qt.AlignRight)
        self.prefix = QLineEdit()
        self.prefix.setPlaceholderText("e.g. 20260526_")
        g3.addRow("Label prefix", self.prefix)
        self.text_size = self._dspin(1, 100000, 1, " µm")
        g3.addRow("Text size", self.text_size)
        pos = QHBoxLayout()
        self.rel_x = self._dspin(0, 1, 3)
        self.rel_y = self._dspin(0, 1, 3)
        pos.addWidget(QLabel("x"))
        pos.addWidget(self.rel_x)
        pos.addWidget(QLabel("y"))
        pos.addWidget(self.rel_y)
        g3.addRow("Position (fraction of die)", pos)
        self.mirror = QCheckBox("Mirror labels when there are exactly two columns")
        g3.addRow("", self.mirror)
        self.mirror_off = self._dspin(0, 1, 3)
        g3.addRow("Mirror offset", self.mirror_off)
        self.tol = self._dspin(0, 1, 3)
        self.tol.setToolTip("Row grouping tolerance as a fraction of the row pitch")
        g3.addRow("Row tolerance", self.tol)
        self.export = QCheckBox("Write labelled GDS (<name>_with_text.gds)")
        g3.addRow("", self.export)
        c3.body.addLayout(g3)
        fl.addWidget(c3)

        c4 = Card("Wafer")
        g4 = QFormLayout()
        g4.setLabelAlignment(Qt.AlignRight)
        self.material = QLineEdit()
        self.size = self._dspin(1000, 500000, 0, " µm")
        self.size.setToolTip("Diameter: 2\" = 50800, 3\" = 76200, 4\" = 101600 µm")
        self.thick = self._dspin(1, 10000, 0, " µm")
        self.recipe = QLineEdit()
        self.wafer_id = QLineEdit()
        self.wafer_id.setPlaceholderText("(optional)")
        g4.addRow("Material", self.material)
        g4.addRow("Diameter", self.size)
        g4.addRow("Thickness", self.thick)
        g4.addRow("Recipe", self.recipe)
        g4.addRow("Wafer ID", self.wafer_id)
        c4.body.addLayout(g4)
        fl.addWidget(c4)
        fl.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(form_host)
        split.addWidget(scroll)

        # ---------------- run panel
        run = Card("Run")
        self.gen_btn = QPushButton("Generate database")
        self.gen_btn.setObjectName("primary")
        self.gen_btn.setMinimumHeight(40)
        self.gen_btn.clicked.connect(self.generate)
        run.body.addWidget(self.gen_btn)
        self.open_map_btn = QPushButton("Open result in wafer map →")
        self.open_map_btn.setEnabled(False)
        self.open_map_btn.clicked.connect(lambda: self.last_output and self.open_database.emit(self.last_output))
        run.body.addWidget(self.open_map_btn)
        self.console = QPlainTextEdit()
        self.console.setObjectName("console")
        self.console.setReadOnly(True)
        run.body.addWidget(self.console, 1)
        run.body.addWidget(muted("Outputs go next to the config file: <config>_database.json (+ one file per "
                                 "test-junction type). Existing notes and measurements are preserved; a .bak "
                                 "copy is kept."))
        split.addWidget(run)
        split.setSizes([560, 520])
        root.addWidget(split, 1)

        for w in (self.gds, self.topcell, self.prefix, self.material, self.recipe, self.wafer_id):
            w.textChanged.connect(self._changed)
        for w in (self.text_size, self.rel_x, self.rel_y, self.mirror_off, self.tol, self.size, self.thick):
            w.valueChanged.connect(self._changed)
        for w in (self.mirror, self.export):
            w.toggled.connect(self._changed)
        for w in (self.l_sample, self.l_res, self.l_text):
            w.changed.connect(self._changed)
        self.tj.itemChanged.connect(self._changed)

    def _dspin(self, lo, hi, decimals, suffix=""):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(decimals)
        s.setSingleStep(10 ** -decimals if decimals else 1)
        s.setSuffix(suffix)
        return s

    # ================================================================== form <-> dict
    def _fill_form(self, cfg: dict):
        self._loading = True
        p = cfg.get("script_parameters", {})
        m = cfg.get("wafer_metadata", {})
        self.gds.setText(p.get("input_gds_file", ""))
        self.topcell.setText(p.get("import_topcell") or "")
        self.l_sample.set(p.get("input_layer"))
        self.l_res.set(p.get("resistance_layer"))
        self.l_text.set(p.get("text_layer"))
        tjs = p.get("test_junctions") or ([{"name": "test_junction", "layer": p["tj_layer"]}] if "tj_layer" in p else [])
        self.tj.setRowCount(0)
        for t in tjs:
            self._add_tj(t.get("name", ""), t.get("layer", [0, 0]))
        self.prefix.setText(p.get("label_prefix", ""))
        self.text_size.setValue(float(p.get("text_size_um", 100)))
        self.rel_x.setValue(float(p.get("relative_pos_x", 0.5)))
        self.rel_y.setValue(float(p.get("relative_pos_y", 0.5)))
        self.mirror.setChecked(bool(p.get("mirror", False)))
        self.mirror_off.setValue(float(p.get("mirror_offset_fraction", 0.15)))
        self.tol.setValue(float(p.get("tolerance_fraction", 0.1)))
        self.export.setChecked(bool(p.get("export_gds_with_text", True)))
        self.material.setText(str(m.get("material", "")))
        self.size.setValue(float(m.get("size_um", 76200)))
        self.thick.setValue(float(m.get("thickness_um", 385)))
        self.recipe.setText(str(m.get("recipe", "")))
        self.wafer_id.setText(str(m.get("wafer_id", "") or ""))
        self._loading = False

    def form_config(self) -> dict:
        """The loaded config with the form values applied (unknown keys are preserved)."""
        cfg = copy.deepcopy(self.config)
        p = cfg.setdefault("script_parameters", {})
        m = cfg.setdefault("wafer_metadata", {})
        p["input_gds_file"] = self.gds.text().strip()
        if self.topcell.text().strip():
            p["import_topcell"] = self.topcell.text().strip()
        else:
            p.pop("import_topcell", None)
        p["input_layer"] = self.l_sample.get()
        p["resistance_layer"] = self.l_res.get()
        p["text_layer"] = self.l_text.get()
        tjs = []
        for row in range(self.tj.rowCount()):
            name = (self.tj.item(row, 0).text() if self.tj.item(row, 0) else "").strip()
            if not name:
                continue
            try:
                layer = [int(self.tj.item(row, 1).text()), int(self.tj.item(row, 2).text())]
            except (AttributeError, ValueError):
                raise ValueError(f"Test junction '{name}': layer and datatype must be integers")
            tjs.append({"name": name, "layer": layer})
        p["test_junctions"] = tjs
        p.pop("tj_layer", None)
        p["label_prefix"] = self.prefix.text()
        p["text_size_um"] = self.text_size.value()
        p["relative_pos_x"] = self.rel_x.value()
        p["relative_pos_y"] = self.rel_y.value()
        p["mirror"] = self.mirror.isChecked()
        p["mirror_offset_fraction"] = self.mirror_off.value()
        p["tolerance_fraction"] = self.tol.value()
        p["export_gds_with_text"] = self.export.isChecked()
        m["material"] = self.material.text()
        m["size_um"] = self.size.value()
        m["thickness_um"] = self.thick.value()
        m["recipe"] = self.recipe.text()
        if self.wafer_id.text().strip():
            m["wafer_id"] = self.wafer_id.text().strip()
        else:
            m.pop("wafer_id", None)
        return cfg

    def _add_tj(self, name=None, layer=None):
        if not isinstance(name, str):
            name, layer = f"tj_{self.tj.rowCount() + 1}", [250 + self.tj.rowCount(), 0]
        row = self.tj.rowCount()
        self.tj.insertRow(row)
        for col, val in enumerate([name, str(layer[0]), str(layer[1] if len(layer) > 1 else 0)]):
            item = QTableWidgetItem(val)
            if col:
                item.setTextAlignment(Qt.AlignCenter)
            self.tj.setItem(row, col, item)
        self._changed()

    def _remove_tj(self):
        rows = sorted({i.row() for i in self.tj.selectedIndexes()}, reverse=True)
        for r in rows:
            self.tj.removeRow(r)
        if rows:
            self._changed()

    def _changed(self, *_):
        if not self._loading:
            self._set_dirty(True)

    def _set_dirty(self, dirty: bool):
        self.dirty = dirty
        name = Path(self.config_path).name if self.config_path else "New configuration (not saved yet)"
        self.path_label.setText(name + ("  ·  modified" if dirty else ""))
        self.path_label.setToolTip(self.config_path or "")

    # ================================================================== files
    def _browse_gds(self):
        start = str(Path(self.gds.text()).parent) if self.gds.text() else self.settings.value("last_dir", "")
        path, _ = QFileDialog.getOpenFileName(self, "Select layout", start, "GDS (*.gds *.gds2 *.gdsii);;All files (*)")
        if path:
            self.gds.setText(path)

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(self, "Unsaved changes", "Discard the changes to the current configuration?",
                                 QMessageBox.Discard | QMessageBox.Cancel)
        return r == QMessageBox.Discard

    def new_config(self):
        if not self._confirm_discard():
            return
        self.config_path = None
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self._fill_form(self.config)
        self._set_dirty(False)

    def open_dialog(self):
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open configuration", self.settings.value("last_dir", ""),
                                              "Config (*.json)")
        if path:
            self.load_config(path)

    def load_config(self, path: str) -> bool:
        try:
            cfg = load_json_lenient(path)
        except Exception as e:
            QMessageBox.critical(self, "Could not open", f"{path}\n\n{e}")
            return False
        if "script_parameters" not in cfg:
            if "dies" in cfg:
                QMessageBox.information(self, "This is a database",
                                        "This file is a generated database. Opening it in the wafer map.")
                self.open_database.emit(path)
            else:
                QMessageBox.warning(self, "Not a config", "This file has no 'script_parameters' section.")
            return False
        cfg.setdefault("wafer_metadata", {})
        self.config_path = path
        self.config = cfg
        self.settings.setValue("last_dir", str(Path(path).parent))
        self.settings.setValue("last_config", path)
        self._fill_form(cfg)
        self._set_dirty(False)
        self.message.emit(f"Loaded {Path(path).name}")
        return True

    def save(self) -> bool:
        if not self.config_path:
            return self.save_as()
        try:
            cfg = self.form_config()
            atomic_write_json(self.config_path, cfg, backup=True)
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return False
        self.config = cfg
        self._set_dirty(False)
        self.message.emit(f"Saved {Path(self.config_path).name}")
        return True

    def save_as(self) -> bool:
        start = self.config_path or str(Path(self.settings.value("last_dir", "")) / "config.json")
        path, _ = QFileDialog.getSaveFileName(self, "Save configuration", start, "Config (*.json)")
        if not path:
            return False
        self.config_path = path
        self.settings.setValue("last_dir", str(Path(path).parent))
        return self.save()

    # ================================================================== generation
    def generate(self):
        try:
            cfg = self.form_config()
        except ValueError as e:
            QMessageBox.warning(self, "Invalid value", str(e))
            return
        if not cfg["script_parameters"]["input_gds_file"]:
            QMessageBox.warning(self, "No layout", "Choose the GDS file first.")
            return
        if not self.config_path:
            QMessageBox.information(self, "Save the configuration",
                                    "Save the configuration first: the database is written next to it.")
            if not self.save_as():
                return
        elif self.dirty:
            r = QMessageBox.question(
                self, "Save changes?",
                "Save your changes to the config file before generating?\n\n"
                "Yes: save and generate · No: generate with them, keep the file unchanged",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                return
            if r == QMessageBox.Yes and not self.save():
                return

        self.gen_btn.setEnabled(False)
        self.gen_btn.setText("Generating…")
        self.open_map_btn.setEnabled(False)
        self.console.appendPlainText("─" * 48)
        self.console.appendPlainText(f"Generating from {Path(self.config_path).name} …")
        threading.Thread(target=self._worker, args=(self.config_path, cfg), daemon=True).start()

    def _worker(self, path, cfg):
        writer = _Writer(self.bridge.log.emit)
        old = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = writer
        try:
            from database_4 import generate_from_config  # imported lazily (slow: KLayout)
            out = generate_from_config(path, config=cfg)
            self.bridge.done.emit(True, str(out))
        except Exception:
            self.bridge.log.emit("❌ ERROR\n" + traceback.format_exc())
            self.bridge.done.emit(False, "")
        finally:
            sys.stdout, sys.stderr = old

    def _log(self, text: str):
        self.console.appendPlainText(text)

    def _done(self, ok: bool, out: str):
        self.gen_btn.setEnabled(True)
        self.gen_btn.setText("Generate database")
        if ok:
            self.last_output = out
            self.open_map_btn.setEnabled(True)
            self.message.emit(f"Database generated: {Path(out).name}")
        else:
            self.message.emit("⚠ Generation failed — see the log")
