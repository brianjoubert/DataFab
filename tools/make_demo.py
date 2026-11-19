"""Create a demo layout, config and database in FOLDER (for trying the app and for CI)."""
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root), str(root / "tests")]

from database_4 import generate_from_config  # noqa: E402
from test_generate import make_gds  # noqa: E402


def main(folder: str) -> Path:
    d = Path(folder)
    d.mkdir(parents=True, exist_ok=True)
    make_gds(d / "demo_layout.gds")
    cfg = {
        "script_parameters": {
            "input_gds_file": str(d / "demo_layout.gds"),
            "input_layer": [201, 0], "resistance_layer": [202, 0],
            "test_junctions": [{"name": "tj_single", "layer": [250, 0]}],
            "text_layer": [203, 0], "text_size_um": 50.0,
            "relative_pos_x": 0.1, "relative_pos_y": 0.5, "tolerance_fraction": 0.1,
            "label_prefix": "DEMO_", "mirror": True, "mirror_offset_fraction": 0.15,
            "export_gds_with_text": False,
        },
        "wafer_metadata": {"material": "Silicon", "size_um": 76200, "thickness_um": 385, "recipe": "demo"},
    }
    (d / "demo.json").write_text(json.dumps(cfg, indent=2))
    out = Path(generate_from_config(str(d / "demo.json")))
    print(out)
    return out


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "demo")
