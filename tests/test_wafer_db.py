import json

import pytest

from wafer_db import DatabaseFormatError, WaferDatabase, latest_value

from test_generate import setup  # noqa: F401  (fixture)
from database_4 import generate_from_config


@pytest.fixture
def db_path(setup):  # noqa: F811
    tmp, cfg_path, _ = setup
    generate_from_config(str(cfg_path))
    return tmp / "config_database.json"


def test_load_merges_sub_databases(db_path):
    db = WaferDatabase(db_path)
    assert db.types == ["sample", "tj_single"]
    assert len(db.dies_of_type("sample")) == 4
    assert len(db.dies_of_type("tj_single")) == 5


def test_add_remove_and_save_roundtrip(db_path):
    db = WaferDatabase(db_path)
    db.add_value("W1_A1", 1, 100.0)
    db.add_value("W1_A1", 1, 200.0)
    db.add_value("W1_A1_tj_single_1", 1, 5000.0)
    removed = db.remove_last("W1_A1", 1)
    assert removed["value_ohm"] == 200.0 and "removed_at" in removed
    db.set_notes("W1_A1", "hello")
    db.save()

    again = WaferDatabase(db_path)
    r = again.resistance("W1_A1", 1)
    assert latest_value(r) == 100.0
    assert r["removed"][0]["value_ohm"] == 200.0
    assert again.by_label["W1_A1"]["notes"] == "hello"
    assert latest_value(again.resistance("W1_A1_tj_single_1", 1)) == 5000.0
    assert db_path.with_name(db_path.name + ".bak").exists()
    # sub-database stays a separate file with only its type
    tj = json.loads(db_path.with_name("config_database_tj_single.json").read_text())
    assert {d["type"] for d in tj["dies"]} == {"tj_single"}


def test_value_range_and_outline(db_path):
    db = WaferDatabase(db_path)
    assert db.value_range("sample") == (None, None)
    db.add_value("W1_A1", 1, 10.0)
    db.add_value("W1_B2", 2, 30.0)
    assert db.value_range("sample") == (10.0, 30.0)
    xs, ys = db.wafer_outline()
    assert max(ys) < db.wafer_radius  # flat at the top
    assert min(ys) == pytest.approx(-db.wafer_radius, rel=1e-3)


def test_rejects_config_file(setup):  # noqa: F811
    _, cfg_path, _ = setup
    with pytest.raises(DatabaseFormatError):
        WaferDatabase(cfg_path)
