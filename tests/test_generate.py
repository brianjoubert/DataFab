"""End-to-end test: synthetic GDS -> databases, then regenerate and check data preservation."""
import json

import klayout.db as pya
import pytest

from database_4 import generate_from_config

UM = 1000  # dbu per µm (dbu = 0.001)


def make_gds(path):
    layout = pya.Layout()
    layout.dbu = 0.001
    top = layout.create_cell("TOP")
    samples = layout.layer(201, 0)
    pads = layout.layer(202, 0)
    tj = layout.layer(250, 0)
    # 2 x 2 dies of 1000 x 500 µm, each with two pads and one test junction
    for r in range(2):
        for c in range(2):
            x0, y0 = c * 1500, -r * 1000
            top.shapes(samples).insert(pya.Box(x0 * UM, y0 * UM, (x0 + 1000) * UM, (y0 + 500) * UM))
            for k in range(2):
                px = x0 + 100 + k * 300
                top.shapes(pads).insert(pya.Box(px * UM, (y0 + 100) * UM, (px + 200) * UM, (y0 + 300) * UM))
            top.shapes(tj).insert(pya.Box((x0 + 800) * UM, (y0 + 100) * UM, (x0 + 900) * UM, (y0 + 200) * UM))
    # one test junction outside every die
    top.shapes(tj).insert(pya.Box(5000 * UM, 5000 * UM, 5100 * UM, 5100 * UM))
    layout.write(str(path))


@pytest.fixture
def setup(tmp_path):
    gds = tmp_path / "layout.gds"
    make_gds(gds)
    cfg = {
        "script_parameters": {
            "input_gds_file": str(gds),
            "input_layer": [201, 0],
            "resistance_layer": [202, 0],
            "test_junctions": [{"name": "tj_single", "layer": [250, 0]},
                               {"name": "tj_missing", "layer": [299, 0]}],
            "text_layer": [203, 0],
            "text_size_um": 50.0,
            "relative_pos_x": 0.1,
            "relative_pos_y": 0.5,
            "tolerance_fraction": 0.1,
            "label_prefix": "W1_",
            "mirror": True,
            "mirror_offset_fraction": 0.15,
            "export_gds_with_text": False,
        },
        "wafer_metadata": {"material": "Silicon", "size_um": 76200, "thickness_um": 385},
    }
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(cfg))
    return tmp_path, cfg_path, cfg


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_generate_and_preserve(setup, capsys):
    tmp, cfg_path, cfg = setup
    generate_from_config(str(cfg_path))
    assert "tj_missing" in capsys.readouterr().out  # missing layer is skipped with a warning

    main = load(tmp / "config_database.json")
    labels = sorted(d["label"] for d in main["dies"])
    assert labels == ["W1_A1", "W1_A2", "W1_B1", "W1_B2"]
    assert all(d["num_measure_regions"] == 2 for d in main["dies"])
    assert main["metadata"]["sub_databases"] == [
        {"type": "tj_single", "filename": "config_database_tj_single.json"}]
    tjs = load(tmp / "config_database_tj_single.json")
    assert sorted(d["label"] for d in tjs["dies"]) == [
        "W1_A1_tj_single_1", "W1_A2_tj_single_1", "W1_B1_tj_single_1", "W1_B2_tj_single_1", "tj_single_1"]

    # Add a measurement + note, then regenerate: they must survive.
    main["dies"][0]["resistances"][0]["history"].append({"timestamp": "t", "value_ohm": 123.0})
    main["dies"][0]["notes"] = "keep me"
    (tmp / "config_database.json").write_text(json.dumps(main))
    generate_from_config(str(cfg_path))
    again = load(tmp / "config_database.json")
    die = next(d for d in again["dies"] if d["label"] == main["dies"][0]["label"])
    assert die["notes"] == "keep me"
    assert die["resistances"][0]["history"][0]["value_ohm"] == 123.0
    assert (tmp / "config_database.json.bak").exists()
    assert not (tmp / "config_database_orphaned.json").exists()

    # Change the prefix: the old data no longer matches and goes to the orphaned file.
    cfg["script_parameters"]["label_prefix"] = "W2_"
    generate_from_config(str(cfg_path), config=cfg)
    orphaned = load(tmp / "config_database_orphaned.json")
    assert orphaned["entries"][0]["dies"][0]["notes"] == "keep me"
    assert "WARNING" in capsys.readouterr().out


def test_labelled_gds_export(setup):
    tmp, cfg_path, cfg = setup
    cfg["script_parameters"]["export_gds_with_text"] = True
    generate_from_config(str(cfg_path), config=cfg)
    out = tmp / "layout_with_text.gds"
    assert out.exists()
    layout = pya.Layout()
    layout.read(str(out))
    assert layout.find_layer(203, 0) is not None
