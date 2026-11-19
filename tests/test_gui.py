"""Headless GUI tests (Qt offscreen): generate through the UI, edit, measure, register."""
import json
import time

import pytest

PySide6 = pytest.importorskip("PySide6")
from PySide6.QtCore import QCoreApplication, QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from test_generate import setup  # noqa: E402,F401


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    QCoreApplication.setOrganizationName("DataFabTest")
    QCoreApplication.setApplicationName("DataFabTest")
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path_factory.mktemp("settings")))
    a = QApplication.instance() or QApplication([])
    from gui.theme import apply_theme
    apply_theme(a)
    return a


def wait_until(app, cond, timeout=60.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def window(app):
    from gui.main_window import MainWindow
    w = MainWindow(simulate=True)
    w.show()
    yield w
    w.wafer.shutdown()
    w.generator.dirty = False
    w.close()


def test_generate_then_measure_and_register(app, window, setup):  # noqa: F811
    tmp, cfg_path, _ = setup
    gen = window.generator
    assert gen.load_config(str(cfg_path))
    gen.prefix.setText("GUI_")  # edit in the form, then save + generate
    assert gen.dirty
    assert gen.save()
    assert json.loads(cfg_path.read_text())["script_parameters"]["label_prefix"] == "GUI_"
    gen.generate()
    assert wait_until(app, lambda: gen.open_map_btn.isEnabled()), gen.console.toPlainText()
    gen.open_map_btn.click()
    page = window.wafer
    assert page.db is not None and window.stack.currentIndex() == 1
    assert [page.type_combo.itemData(i) for i in range(page.type_combo.count())] == ["sample", "tj_single"]
    assert page.model.rowCount() == 4 and page.model.n_res == 2

    # click on a pad in the map selects the matching table cell
    page.plot._clicked("GUI_B1", 2)
    assert page.model.key(page.proxy.mapToSource(page.table.currentIndex())) == ("GUI_B1", 2)
    assert page.sel_title.text().startswith("GUI_B1")

    # type a value with a unit suffix, then clear it again (undo)
    idx = page.proxy.mapFromSource(page.model.find("GUI_A1", 1))
    assert page.proxy.setData(idx, "7.5k")
    db_file = tmp / "config_database.json"
    saved = {d["label"]: d for d in json.loads(db_file.read_text())["dies"]}
    assert saved["GUI_A1"]["resistances"][0]["history"][-1]["value_ohm"] == 7500.0
    assert page.proxy.setData(idx, "")
    saved = {d["label"]: d for d in json.loads(db_file.read_text())["dies"]}
    assert saved["GUI_A1"]["resistances"][0]["history"] == []
    assert saved["GUI_A1"]["resistances"][0]["removed"][0]["value_ohm"] == 7500.0

    # simulated live measurement -> register into the selected cell, cursor advances
    page.table.setCurrentIndex(idx)
    page.toggle_measurement()
    assert wait_until(app, lambda: len(page.r_data) >= 1, 15)
    page.toggle_measurement()
    page.register_value()
    r = page.db.resistance("GUI_A1", 1)
    assert r["history"][-1]["value_ohm"] == pytest.approx(7000, rel=0.05)
    assert page.model.key(page.proxy.mapToSource(page.table.currentIndex())) == ("GUI_A1", 2)

    # notes
    page.select("GUI_A2", None)
    page.notes.setPlainText("scratch on pad")
    page._save_notes()
    saved = {d["label"]: d for d in json.loads(db_file.read_text())["dies"]}
    assert saved["GUI_A2"]["notes"] == "scratch on pad"

    # switch to the test-junction view
    page.type_combo.setCurrentIndex(1)
    assert page.model.rowCount() == 5


def test_open_path_dispatch(app, window, setup):  # noqa: F811
    _, cfg_path, _ = setup
    window.open_path(str(cfg_path))
    assert window.stack.currentIndex() == 0
    assert window.generator.config_path == str(cfg_path)


def test_hover_tooltips_show_full_sample_info(app, window, setup):  # noqa: F811
    tmp, cfg_path, _ = setup
    from database_4 import generate_from_config
    generate_from_config(str(cfg_path))
    page = window.wafer
    assert page.load_database(str(tmp / "config_database.json"))
    plot = page.plot
    pad_tip = plot.region_items[("W1_A1", 2)].toolTip()
    die_tip = plot.die_items["W1_A1"].toolTip()
    for tip in (pad_tip, die_tip):
        assert "W1_A1" in tip and "Grid: row 1, column 1" in tip and "Size:" in tip
        assert "R1" in tip and "R2" in tip and "Wafer: Silicon" in tip
    assert "▶ R2" in pad_tip and "▶" not in die_tip
    # tooltips follow edits
    page.db.add_value("W1_A1", 2, 1234.0)
    page.db.set_notes("W1_A1", "chipped corner")
    plot.refresh_colors()
    tip = plot.region_items[("W1_A1", 2)].toolTip()
    assert "1.234 kΩ" in tip and "chipped corner" in tip
