# DataFab

Desktop app to build and use a measurement database for wafer fabrication.

* **Generate database**: reads a GDS layout, finds the sample dies, measurement pads and test junctions
  on configurable layers, labels the dies on a grid (A1, B1, …, Z1, AA1, …), optionally writes a
  labelled GDS (`<name>_with_text.gds`), and exports JSON databases
  (`<config>_database.json` + one `<config>_database_<tj_type>.json` per test-junction type).
  Existing notes and measurements are preserved when you regenerate.
* **Wafer map**: interactive map coloured by resistance, a table of all values with their history
  and notes, and **live measurement with a Keithley 2450** (delta method) that registers each
  reading into the selected cell. Every change is saved immediately with a timestamp.

## Download

Go to **[Releases](../../releases)** and download:

| System | File | Install |
|--------|------|---------|
| Windows 10/11 | `DataFab.exe` | Nothing to install, just run it. SmartScreen may warn the first time (unsigned app): *More info → Run anyway*. |
| Ubuntu 22.04+ / Debian 12+ (x86-64) | `datafab_<version>_amd64.deb` | `sudo apt install ./datafab_<version>_amd64.deb`, then start **DataFab** from the applications menu or run `datafab`. |

The `.deb` also installs a udev rule so the logged-in user can talk to a Keithley over USB
(unplug/replug the instrument once after installing). On Linux, no NI-VISA is needed: the built-in
pure-Python VISA backend is used automatically.

## Using DataFab

### Generate a database
1. **Generate database** page → *Open…* a config (or *New*), set the GDS file and layers.
2. Click **Generate database**. Outputs are written next to the config file.
3. Click **Open result in wafer map →**.

You can also drop a config or a database `.json` file onto the window, or run without the window:
`DataFab.exe --generate config.json` / `datafab --generate config.json`.

### Wafer map & measurement
* **Zoom**: drag a box (it follows the mouse), mouse wheel zooms at the cursor, double-click or **Fit**
  shows the whole wafer. **Pan** switches dragging to moving the view. **1:1** keeps the wafer round.
* **Select**: click a die or a pad on the map, or a cell in the table; the selection panel shows the
  history and notes of that die/resistance.
* **Type a value** in the table: `7012`, `7.01k`, `1.2M` … **Clear a cell** to undo its latest value
  (the removed value is kept under `"removed"` in the JSON).
* **Measure**: set the current, NPLC, interval and the VISA address (↻ searches for instruments,
  or tick *Simulate*), press **Start**, then **Register value** (Ctrl+Enter) to store the latest reading
  in the selected cell; the cursor moves to the next resistance automatically.

### Data safety
* All JSON files are written atomically (temporary file + replace): a crash or a network-drive
  drop cannot leave a half-written database.
* A `<file>.bak` copy of the previous version is kept (generator: every run; wafer map: first save
  of each session).
* If regenerating changes labels (e.g. new prefix) or pads, data that no longer matches a die is
  listed in the log and saved to `<config>_database_orphaned.json` instead of being lost.

### Config parameters

| Key | Meaning |
|-----|---------|
| `input_gds_file` | Layout path (local or network share; it is copied locally before processing). |
| `import_topcell` | Optional top cell name (default: automatic). |
| `input_layer` | `[layer, datatype]` of the sample/die rectangles. |
| `resistance_layer` | Layer of the measurement pads/regions. |
| `test_junctions` | List of `{name, layer}`; each type gets its own database file. Missing layers are skipped. |
| `text_layer`, `text_size_um` | Layer and size of the label text written in the GDS. |
| `relative_pos_x/y` | Label position as a fraction of the die width/height. |
| `mirror`, `mirror_offset_fraction` | With exactly two columns, place labels near the outer edges. |
| `tolerance_fraction` | Row-grouping tolerance as a fraction of the row pitch. |
| `label_prefix` | Prefix for every die label (e.g. `20260526_`). |
| `export_gds_with_text` | Write the labelled GDS. |
| `wafer_metadata` | Material, diameter (`size_um`), thickness, recipe, optional `wafer_id`. |

See [`examples/config_example.json`](examples/config_example.json).

## Command line

```
DataFab.exe [file.json] [--simulate] [--resource VISA] [--generate CONFIG] [--version]
```
`file.json` can be a config (opens the Generate page) or a database (opens the wafer map).

## Development

Requires [uv](https://docs.astral.sh/uv/) (Python 3.12 is installed automatically).

```
uv sync --group build                    # .venv from uv.lock (incl. PyInstaller)
uv run pytest                            # tests (GUI tests run headless)
uv run python DataFab.py --simulate      # run the app without an instrument
uv run python tools/make_demo.py demo    # demo layout + config + database in ./demo
uv run python tools/make_icons.py        # icons for packaging (build/icons)
uv run pyinstaller --noconfirm DataFab.spec               # Windows: dist/DataFab.exe
bash packaging/linux/build_deb.sh 2.0.0                   # Linux: after PyInstaller, dist/*.deb
```

| Path | Role |
|------|------|
| `DataFab.py` | Entry point and command line |
| `gui/` | Qt (PySide6) interface: main window, pages, wafer map (pyqtgraph), theme, icon |
| `database_4.py` | Generator engine (KLayout geometry, grid labelling, JSON export) |
| `wafer_db.py` | Database model: load/save, history, undo, wafer outline |
| `keithley.py` | Keithley 2450 driver (delta method) and background measurement loop |
| `datafab_io.py` | Lenient JSON loading and atomic writes |
| `packaging/linux/` | `.deb` builder, desktop entry, udev rule |
| `tests/` | pytest suite (synthetic GDS, no real data needed) |

Releases: push a tag matching `version.py` (e.g. `v2.0.0`); GitHub Actions builds both packages,
tests them (the `.deb` is installed and run on Ubuntu), and attaches them to the release.

Fabrication data (`*.gds`, `config*.json`, `*_database*.json`, `*.bak`) is ignored by git on purpose.
