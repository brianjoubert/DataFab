"""Main window: sidebar navigation + pages."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QStackedWidget,
    QVBoxLayout, QWidget,
)

from datafab_io import load_json_lenient
from gui.generator_page import GeneratorPage
from gui.icon import app_icon
from gui.wafer_page import WaferPage
from version import __version__


class MainWindow(QMainWindow):
    def __init__(self, simulate: bool = False, resource: str | None = None):
        super().__init__()
        self.settings = QSettings()
        self.setWindowTitle("DataFab")
        self.setWindowIcon(app_icon())
        self.setAcceptDrops(True)
        self.resize(1400, 900)

        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(210)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        t = QLabel("DataFab")
        t.setObjectName("appTitle")
        s = QLabel("Wafer database & measurement")
        s.setObjectName("appSubtitle")
        sl.addWidget(t)
        sl.addWidget(s)

        self.stack = QStackedWidget()
        self.generator = GeneratorPage()
        self.wafer = WaferPage(simulate=simulate, resource=resource)
        self.stack.addWidget(self.generator)
        self.stack.addWidget(self.wafer)

        self.nav = QButtonGroup(self)
        for i, text in enumerate(["⚙   Generate database", "◎   Wafer map"]):
            b = QPushButton(text)
            b.setObjectName("navButton")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            self.nav.addButton(b, i)
            sl.addWidget(b)
        self.nav.idClicked.connect(self.stack.setCurrentIndex)
        sl.addStretch(1)
        v = QLabel(f"Version {__version__}")
        v.setObjectName("versionLabel")
        sl.addWidget(v)

        lay.addWidget(side)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.generator.message.connect(self.show_message)
        self.wafer.message.connect(self.show_message)
        self.generator.open_database.connect(self.open_database)
        self.wafer.database_loaded.connect(lambda p: self.setWindowTitle(f"DataFab — {Path(p).name}"))
        self.show_page(1)

        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)

    def show_page(self, i: int):
        self.nav.button(i).setChecked(True)
        self.stack.setCurrentIndex(i)

    def show_message(self, text: str):
        self.statusBar().showMessage(text, 15000)

    def open_database(self, path: str):
        if self.wafer.load_database(path):
            self.show_page(1)

    def open_path(self, path: str):
        """Open a config (-> Generate page) or a database (-> Wafer page)."""
        try:
            data = load_json_lenient(path)
        except Exception as e:
            self.show_message(f"⚠ Cannot open {path}: {e}")
            return
        if "script_parameters" in data:
            if self.generator.load_config(path):
                self.show_page(0)
        else:
            self.open_database(path)

    # drag & drop JSON files onto the window
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            if url.toLocalFile().lower().endswith(".json"):
                self.open_path(url.toLocalFile())
                break

    def closeEvent(self, e):
        if self.generator.dirty:
            from PySide6.QtWidgets import QMessageBox
            r = QMessageBox.question(self, "Unsaved configuration",
                                     "The generator configuration has unsaved changes. Quit anyway?",
                                     QMessageBox.Yes | QMessageBox.No)
            if r != QMessageBox.Yes:
                e.ignore()
                return
        self.settings.setValue("geometry", self.saveGeometry())
        self.wafer.shutdown()
        super().closeEvent(e)
