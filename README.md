# DataFab

Tools to build and use a measurement database for wafer fabrication.

| Tool | Source | What it does |
|------|--------|--------------|
| **DatabaseGenerator** | `database_gui.py` (GUI) + `database_4.py` (engine) | Reads a GDS layout, finds sample dies, measurement pads and test junctions on configurable layers, labels the dies on a grid (A1, B1, …, Z1, AA1, …), optionally writes a labelled GDS (`<name>_with_text.gds`), and exports JSON databases (`<config>_database.json` + one `<config>_database_<tj_type>.json` per test-junction type). Existing notes and resistance histories are preserved when you regenerate. |
| **ResistanceViewer** | `resistanceplotingV5.py` | Dash web app (opens in your browser) that shows the wafer map coloured by resistance, lets you edit values and notes, and measures live with a **Keithley 2450** (delta method) and registers the reading into the selected cell. Every change is saved with a timestamp. Works offline. |

## Download the executables

The `.exe` files are built automatically by GitHub Actions on every push to `main`:

* **Latest build:** *Actions* tab → latest "Build Windows executables" run → *Artifacts* → `DataFab-windows` (zip).
* **Releases:** push a tag such as `v1.0.0` and the executables are attached to a GitHub Release:
  ```
  git tag v1.0.0
  git push origin v1.0.0
  ```

## Usage

1. Copy [`examples/config_example.json`](examples/config_example.json), set `input_gds_file` and the layers to match your layout.
2. Run **DatabaseGenerator.exe**, choose the config, check the layers, click *Generate Database*.
3. Run **ResistanceViewer.exe** and pick the generated `*_database.json` (not the config file).

### Viewer tips

* **Wrong value?** Clear the cell: the latest value is removed and the previous one shows again. The removed value is kept under `"removed"` in the JSON (with the removal time).
* **Instrument:** set the VISA address or tick *Simulate instrument* in the live-measurement panel; it is applied when you press *Start*.
* **Closing:** use the **Quit viewer** button (top right). Closing the browser tab alone leaves the viewer running in the background; starting the exe again then offers to reopen it.
* Command-line options (also for shortcuts): `ResistanceViewer.exe [database.json] [--resource VISA] [--simulate] [--port 8050] [--no-browser]`

### Data safety

* All JSON files are written atomically (temporary file + replace), so a crash or a network-drive drop cannot leave a half-written database.
* Before overwriting, a copy of the previous version is kept as `<file>.bak` (generator: every run; viewer: first save of each session).
* If regenerating changes labels (e.g. new `label_prefix`) or measurement pads, notes/measurements that no longer match a die are not lost: they are listed in the console and saved to `<config>_database_orphaned.json`.
* When you change parameters in the generator GUI, it asks whether to save them to the config file or use them for this run only.

### Config parameters

| Key | Meaning |
|-----|---------|
| `input_gds_file` | Path to the layout (local or network share; it is copied locally before processing). |
| `input_layer` | Layer `[layer, datatype]` of the sample/die rectangles. |
| `resistance_layer` | Layer of the measurement pads/regions. |
| `test_junctions` | List of `{name, layer}`; each type gets its own database file. A layer missing from the GDS is skipped with a warning. |
| `text_layer`, `text_size_um` | Layer and size of the label text written in the GDS. |
| `relative_pos_x/y` | Label position as a fraction of the die width/height. |
| `mirror`, `mirror_offset_fraction` | With exactly two columns, place labels near the outer edges. |
| `tolerance_fraction` | Row-grouping tolerance as a fraction of the minimum row pitch. |
| `label_prefix` | Prefix for every die label (e.g. `20260526_`). |
| `export_gds_with_text` | Write the labelled GDS (`true`/`false`). |
| `wafer_metadata` | Material, diameter (`size_um`), thickness and recipe stored in the database. |

## Development

Requires [uv](https://docs.astral.sh/uv/) (Python 3.12 is installed automatically).

```
uv sync --group build                      # create .venv from uv.lock (incl. PyInstaller)
uv run pytest                              # run the tests
uv run python database_gui.py              # run the generator GUI
uv run python database_4.py config.json    # generator without GUI
uv run python resistanceplotingV5.py --simulate   # run the viewer without instrument
uv run pyinstaller --noconfirm DatabaseGenerator.spec   # local build -> dist/
uv run pyinstaller --noconfirm ResistanceViewer.spec
```

| File | Role |
|------|------|
| `database_4.py` | Generator engine (KLayout geometry, grid labelling, JSON export) |
| `database_gui.py` | Tkinter front-end for the generator |
| `resistanceplotingV5.py` | Viewer / measurement app |
| `datafab_io.py` | Shared helpers: lenient JSON loading, atomic writes, bundled resources |
| `assets/bootstrap.min.css` | Bootstrap 5.3.6 served locally by the viewer |
| `tests/` | pytest suite (uses a synthetic GDS, no real data needed) |

Fabrication data (`*.gds`, `config*.json`, `*_database*.json`, `*.bak`) is ignored by git on purpose.
