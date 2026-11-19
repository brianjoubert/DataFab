# resistanceplotingV5.py — wafer resistance viewer + Keithley 2450 live measurement
import argparse
import json
import math
import os
import pathlib
import re
import socket
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from dataclasses import dataclass

import dash
import dash_bootstrap_components as dbc
from dash import dcc, html, Input, Output, State, no_update, callback_context, dash_table
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import pyvisa
import numpy as np
from shapely.geometry import Point, Polygon

from datafab_io import atomic_write_json, resource_path

DEFAULT_RESOURCE = "USB0::0x05E6::0x2450::04644450::INSTR"

# =========================
# Command line
# =========================
parser = argparse.ArgumentParser(description="Wafer resistance viewer with Keithley 2450 live measurement.")
parser.add_argument("json_file", nargs="?", help="Generated *_database.json (a file dialog opens if omitted)")
parser.add_argument("--resource", default=DEFAULT_RESOURCE, help="VISA resource of the Keithley 2450")
parser.add_argument("--simulate", action="store_true", help="Simulate the instrument (no hardware needed)")
parser.add_argument("--port", type=int, default=8050, help="Local web port (default 8050)")
parser.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically")
args, _unknown = parser.parse_known_args()


def tk_message(kind: str, title: str, text: str):
    """Show a Tk message box on top of other windows (used before the web UI exists)."""
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    root.call('wm', 'attributes', '.', '-topmost', True)
    try:
        return getattr(messagebox, kind)(title, text)
    finally:
        root.destroy()


# =========================
# Instrument wrapper (Keithley 2450)
# =========================
@dataclass
class SMUConfig:
    resource: str = DEFAULT_RESOURCE
    simulate: bool = False  # True to test without hardware


class Keithley2450:
    def __init__(self, cfg: SMUConfig):
        self.cfg = cfg
        self.rm = None
        self.inst = None
        self.lock = threading.Lock()
        self._connected = False
        self.idn = ""
        self._configured = False
        self._last_nplc = None

    def connect(self):
        if self.cfg.simulate:
            self._connected = True
            self.idn = "SIMULATED"
            return

        if self._connected and self.inst is not None:
            return

        self.rm = pyvisa.ResourceManager()
        self.inst = self.rm.open_resource(self.cfg.resource)
        try:
            self.inst.timeout = 5000  # ms
        except Exception:
            pass

        self._connected = True
        try:
            self.idn = self.inst.query("*IDN?").strip()
        except Exception:
            self.idn = "UNKNOWN"

    @staticmethod
    def _first_float(resp: str) -> float:
        m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", resp)
        if not m:
            raise ValueError(f"Could not parse numeric value from response: {resp!r}")
        return float(m.group(0))

    def setup_once(self, nplc_default=1.0):
        """Configure once: source current, sense voltage, voltage range AUTO."""
        if self.cfg.simulate:
            self._configured = True
            self._last_nplc = float(nplc_default)
            return

        with self.lock:
            self.connect()
            if self._configured:
                return

            inst = self.inst
            inst.write("*RST")

            inst.write(':SOUR:FUNC CURR')
            inst.write(':SENS:FUNC "VOLT"')

            # Keep voltage range AUTO
            inst.write(":SENS:VOLT:RANG:AUTO ON")

            # Let current range be auto as well
            inst.write(":SOUR:CURR:RANG:AUTO ON")

            # Set NPLC default (integration/averaging)
            try:
                inst.write(f":SENS:VOLT:NPLC {float(nplc_default)}")
                self._last_nplc = float(nplc_default)
            except Exception:
                self._last_nplc = None

            # Optional: request READ? return VOLT first (not required)
            try:
                inst.write(":FORM:ELEM VOLT")
            except Exception:
                pass

            inst.write(":SOUR:CURR 0")
            inst.write(":OUTP OFF")

            self._configured = True

    def set_nplc_if_needed(self, nplc: float):
        """Update NPLC only when it changes (avoid spamming config)."""
        if self.cfg.simulate:
            self._last_nplc = float(nplc)
            return

        with self.lock:
            self.connect()
            if self._last_nplc is not None and abs(self._last_nplc - float(nplc)) < 1e-12:
                return
            try:
                self.inst.write(f":SENS:VOLT:NPLC {float(nplc)}")
                self._last_nplc = float(nplc)
            except Exception:
                self._last_nplc = float(nplc)

    def reconfigure(self, resource: str, simulate: bool):
        """Switch to another instrument address / simulation mode (closes the current session)."""
        with self.lock:
            if resource == self.cfg.resource and simulate == self.cfg.simulate:
                return
            self._close_locked()
            self.cfg = SMUConfig(resource=resource, simulate=simulate)

    def close(self):
        with self.lock:
            self._close_locked()

    def _close_locked(self):
        if self.inst is not None:
            try:
                self.inst.write(":OUTP OFF")
                self.inst.write(":SOUR:CURR 0")
            except Exception:
                pass
            try:
                self.inst.close()
            except Exception:
                pass
        if self.rm is not None:
            try:
                self.rm.close()
            except Exception:
                pass
        self.inst = None
        self.rm = None
        self._connected = False
        self._configured = False
        self._last_nplc = None
        self.idn = ""

    def output_off(self):
        if self.cfg.simulate:
            return
        with self.lock:
            if not self._connected or self.inst is None:
                return
            try:
                self.inst.write(":OUTP OFF")
                self.inst.write(":SOUR:CURR 0")
            except Exception:
                pass

    def read_voltage(self) -> float:
        resp = self.inst.query(":READ?")
        return self._first_float(resp)

    def measure_R_delta(self, I_amps: float, settle_s: float = 0.02) -> float:
        """
        Delta method:
          +I -> Vp
          -I -> Vm
          R = (Vp - Vm) / (2I)
        """
        if I_amps == 0.0:
            return float("inf")

        if self.cfg.simulate:
            Rtrue = 7000.0
            offset = 200e-6
            Vp = Rtrue * I_amps + offset
            Vm = -Rtrue * I_amps + offset
            return abs((Vp - Vm) / (2 * I_amps))

        with self.lock:
            self.connect()
            inst = self.inst

            inst.write(":OUTP ON")

            inst.write(f":SOUR:CURR {+I_amps}")
            time.sleep(settle_s)
            Vp = self.read_voltage()

            inst.write(f":SOUR:CURR {-I_amps}")
            time.sleep(settle_s)
            Vm = self.read_voltage()

            inst.write(":SOUR:CURR 0")
            inst.write(":OUTP OFF")

            return abs((Vp - Vm) / (2 * I_amps))


smu = Keithley2450(SMUConfig(resource=args.resource, simulate=args.simulate))


# =========================
# Background worker state
# =========================
data_lock = threading.Lock()
t_data, r_data = [], []

params_lock = threading.Lock()
params = {
    "iex_ua": 0.02,        # DEFAULT = 0.02 µA = 20 nA
    "interval_s": 1.0,     # measurement interval
    "settle_s": 0.02,      # delta settle
    "nplc": 1.0,           # DEFAULT = 1
}

run_event = threading.Event()
single_event = threading.Event()
stop_event = threading.Event()

status_lock = threading.Lock()
status_text = "Idle."
idn_text = ""


def set_status(msg: str):
    global status_text
    with status_lock:
        status_text = msg


def get_status():
    with status_lock:
        return status_text


def clear_data():
    global t_data, r_data
    with data_lock:
        t_data, r_data = [], []


def append_point(t, r):
    with data_lock:
        t_data.append(t)
        r_data.append(r)
        if len(t_data) > 5000:
            t_data[:] = t_data[-5000:]
            r_data[:] = r_data[-5000:]


def read_data_copy():
    with data_lock:
        return list(t_data), list(r_data)


def worker_loop():
    global idn_text
    t0 = None

    while not stop_event.is_set():
        if not run_event.is_set() and not single_event.is_set():
            time.sleep(0.05)
            continue

        with params_lock:
            iex_ua = float(params["iex_ua"])
            interval_s = float(params["interval_s"])
            settle_s = float(params["settle_s"])
            nplc = float(params["nplc"])

        I = iex_ua * 1e-6

        try:
            with smu.lock:
                smu.connect()
            idn_text = smu.idn
            smu.setup_once(nplc_default=nplc)  # no-op once configured (until reconfigure)
            smu.set_nplc_if_needed(nplc)
        except Exception as e:
            set_status(f"Connect/setup error: {e}")
            run_event.clear()
            single_event.clear()
            time.sleep(0.2)
            continue

        if t0 is None or (not run_event.is_set() and single_event.is_set()):
            t0 = time.time()

        start = time.time()
        try:
            R = smu.measure_R_delta(I_amps=I, settle_s=settle_s)
            t = time.time() - t0
            append_point(t, R)
            set_status(f"OK — {R/1e3:.3f} kΩ  | I={iex_ua:.4f} µA | NPLC={nplc:g} | {idn_text}")
        except Exception as e:
            smu.output_off()
            set_status(f"Measurement error: {e}")

        if single_event.is_set():
            single_event.clear()

        if not run_event.is_set():
            time.sleep(0.05)
            continue

        elapsed = time.time() - start
        time.sleep(max(0.0, interval_s - elapsed))


threading.Thread(target=worker_loop, daemon=True).start()

# Helper
def compact_now():
    # Local time, compact format (no seconds)
    return datetime.now().strftime("%Y-%m-%d %H:%M")

# ----------------------------- Utilities -----------------------------
def latest_value(hist):
    return hist[-1]["value_ohm"] if hist else None

def history_values_only(hist):
    # Return list of just the values in chronological order
    return [h["value_ohm"] for h in hist]

def build_hover_text(sample_name, sample_dict, wafer_info):
    """
    Hover shows sample info + resistance histories with ONLY values (no timestamps).
    """
    lines = [f"Sample: {sample_name}"]
    for k, v in sample_dict.items():
        if k not in ("resistances", "measure_regions"):
            lines.append(f"{k}: {v}")

    for r in sample_dict.get("resistances", []):
        vals = history_values_only(r.get("history", []))
        if vals:
            lines.append(f"R{r['id']} history: " + ", ".join(str(v) for v in vals) + " Ω")
        else:
            lines.append(f"R{r['id']}: N/A")

    lines.append(f"Material: {wafer_info.get('material','')}")
    lines.append(f"Size µm: {wafer_info.get('size_um','')}")
    lines.append(f"Thickness µm: {wafer_info.get('thickness_um','')}")
    return "<br>".join(lines)

def normalize(v, vmin, vmax):
    if v is None or vmin == vmax:
        return 0.5
    return (v - vmin) / (vmax - vmin)

# ----------------------------- Argument -----------------------------
def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0


VIEWER_URL = f"http://127.0.0.1:{args.port}/"

if __name__ == "__main__" and is_port_in_use(args.port):
    # Checked before the file dialog so the user isn't asked for a file for nothing.
    if tk_message(
        "askyesno", "Viewer Already Running",
        "A Resistance Viewer is already running.\n\n"
        "Open it in the browser? (Use its 'Quit viewer' button before loading another database.)"
    ):
        webbrowser.open(VIEWER_URL)
    sys.exit(0)

if args.json_file:
    json_file = args.json_file
else:
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw() # Hide the main window
    root.call('wm', 'attributes', '.', '-topmost', True) # Bring dialog to front
    json_file = filedialog.askopenfilename(
        title="Select Wafer Database JSON",
        filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")]
    )
    root.destroy()
    if not json_file:
        print("No file selected. Exiting.")
        sys.exit(0)

try:
    with open(json_file, "r", encoding="utf-8") as f:
        wafer_data = json.load(f)
    wafer_info = wafer_data["metadata"]
    dies = wafer_data["dies"]
except (KeyError, TypeError, ValueError, OSError) as e:
    tk_message(
        "showerror", "Invalid File Format",
        "The selected JSON file does not appear to be a valid wafer database.\n\n"
        "Make sure you select the generated database file (e.g. 'config_ZenoSi_01_database.json') "
        f"and not the generator configuration file.\n\nDetails: {e}"
    )
    sys.exit(1)

# Try to load TJ databases (Multiple Support)
p = pathlib.Path(json_file)

sub_dbs = wafer_info.get("sub_databases", [])
loaded_extra_types = set()

if sub_dbs:
    for db_info in sub_dbs:
        t_type = db_info.get("type")
        f_name = db_info.get("filename")
        if not f_name: 
            continue
        
        path_aux = p.with_name(f_name)
        if path_aux.exists():
            try:
                with open(path_aux, "r", encoding="utf-8") as f:
                    aux_data = json.load(f)
                    dies.extend(aux_data.get("dies", []))
                    loaded_extra_types.add(t_type)
                    print(f"Loaded {t_type} from: {path_aux}")
            except Exception as e:
                print(f"Warning: Could not load {t_type} database: {e}")
else:
    # Legacy fallback: check for _TJ.json and assume type='test_junction'
    tj_json_file = p.with_name(f"{p.stem}_TJ{p.suffix}")
    if tj_json_file.exists():
        try:
            with open(tj_json_file, "r", encoding="utf-8") as f:
                tj_data = json.load(f)
                dies.extend(tj_data.get("dies", []))
                loaded_extra_types.add("test_junction")
                print(f"Loaded additional Test Junctions from: {tj_json_file}")
        except Exception as e:
            print(f"Warning: Could not load TJ database: {e}")

# ----------------------------- Wafer outline -----------------------------
wafer_size_um   = wafer_info["size_um"]
wafer_radius    = wafer_size_um / 2.0
wafer_material  = wafer_info["material"]
wafer_thickness = wafer_info["thickness_um"]

wafer_diameter_inch = wafer_size_um / (25.4 * 1000.0)
flat_length   = 22000 if abs(wafer_diameter_inch - 3) < 0.1 else 14000
flat_bottom_y = float(np.sqrt(max(wafer_radius**2 - (flat_length / 2)**2, 0.0)))

circle = Point(0, 0).buffer(wafer_radius, resolution=200)
cut_polygon = Polygon([
    (-flat_length / 2, flat_bottom_y),
    ( flat_length / 2, flat_bottom_y),
    ( flat_length / 2, wafer_radius),
    (-flat_length / 2, wafer_radius),
])
wafer_shape = circle.difference(cut_polygon)
wafer_x = list(wafer_shape.exterior.xy[0])
wafer_y = list(wafer_shape.exterior.xy[1])

# ----------------------------- Table model -----------------------------
rows = []
max_res_cols = max((len(d.get("resistances", [])) for d in dies), default=0)
for d in dies:
    row = {"Sample": d["label"]}
    for i, r in enumerate(d.get("resistances", []), start=1):
        row[f"R{i} (Ω)"] = latest_value(r.get("history", []))
    for i in range(1, max_res_cols + 1):
        row.setdefault(f"R{i} (Ω)", None)
    rows.append(row)
df = pd.DataFrame(rows)

# ----------------------------- Plot builder -----------------------------
colorscale = "Viridis"

def create_wafer_figure(selection=None, dies_subset=None, view_mode="sample", current_figure=None):
    """
    selection: dict like {"sample": <label or None>, "res_id": <int or None>}
    Only the selected resistance rectangle gets a slightly larger red outline.
    """
    selected_sample = None
    selected_res_id = None
    source = None
    if isinstance(selection, dict):
        selected_sample = selection.get("sample")
        selected_res_id = selection.get("res_id")
        source = selection.get("source")

    # Use subset if provided, else all
    target_dies = dies_subset if dies_subset is not None else dies

    # Calculate min/max for target_dies ONLY
    vals = []
    for d in target_dies:
        for r in d.get("resistances", []):
            lv = latest_value(r.get("history", []))
            if lv is not None:
                vals.append(lv)
    
    if not vals:
        gmin, gmax = 0, 1
    else:
        gmin, gmax = min(vals), max(vals)
        if gmin == gmax:
            gmax = gmin + 1.0

    # Select colorscale based on view_mode
    colorscale = "Viridis" if view_mode == "sample" else "Plasma"

    fig = go.Figure()

    # wafer outline
    fig.add_trace(go.Scatter(
        x=wafer_x,
        y=wafer_y,
        mode="lines",
        fill="toself",
        fillcolor="lightgray",
        line=dict(color="black", width=2),
        opacity=0.3,
        hoverinfo="skip"
    ))

    for d in target_dies:
        x0 = d["x_um"]; y0 = d["y_um"]
        w  = d["width_um"]; h = d["height_um"]
        x1 = x0 + w; y1 = y0 + h
        label = d["label"]

        hover_text = build_hover_text(label, d, wafer_info)

        # sample outline: always same style, CLICKABLE now
        sample_custom = {"sample": label, "res_id": None}
        fig.add_trace(go.Scatter(
            x=[x0, x1, x1, x0, x0],
            y=[y0, y0, y1, y1, y0],
            mode="lines",
            fill="toself",
            fillcolor="rgba(0,0,0,0)", # Transparent fill captures clicks
            line=dict(color="black", width=1.5),
            hoverinfo="text",
            text=hover_text,
            showlegend=False,
            name=label,
            customdata=[sample_custom] * 5
        ))

        # measurement rectangles
        for idx, (mr, r) in enumerate(zip(d.get("measure_regions", []), d.get("resistances", [])), start=1):
            mx0, my0, mx1, my1 = mr["x0_um"], mr["y0_um"], mr["x1_um"], mr["y1_um"]
            lv = latest_value(r.get("history", []))
            norm = normalize(lv, gmin, gmax)
            fill_color = px.colors.sample_colorscale(colorscale, [norm])[0]

            is_meas_selected = (label == selected_sample and selected_res_id == idx)

            # base rectangle (clickable)
            meas_custom = {"sample": label, "res_id": idx}
            fig.add_trace(go.Scatter(
                x=[mx0, mx1, mx1, mx0, mx0],
                y=[my0, my0, my1, my1, my0],
                mode="lines",          # clean outline
                fill="toself",
                fillcolor=fill_color,
                hoveron="fills",
                line=dict(color="black", width=0.5),
                hoverinfo="text",
                text=f"{label} – R{idx}: {lv if lv is not None else 'N/A'} Ω",
                customdata=[meas_custom] * 5,
                showlegend=False,
                name=f"{label} R{idx}"
            ))

            # highlight overlay: slightly larger red rectangle if selected
            if is_meas_selected:
                pad_x = (mx1 - mx0) * 0.05
                pad_y = (my1 - my0) * 0.05
                hx0, hx1 = mx0 - pad_x, mx1 + pad_x
                hy0, hy1 = my0 - pad_y, my1 + pad_y
                fig.add_trace(go.Scatter(
                    x=[hx0, hx1, hx1, hx0, hx0],
                    y=[hy0, hy0, hy1, hy1, hy0],
                    mode="lines",
                    fill=None,
                    line=dict(color="red", width=3),
                    hoverinfo="skip",
                    showlegend=False
                ))

        # center label
        cx = x0 + w/2; cy = y0 + h/2
        fig.add_trace(go.Scatter(
            x=[cx], y=[cy],
            text=[label],
            mode="text",
            textfont=dict(size=12, color="black"),
            showlegend=False,
            hoverinfo="none"
        ))

    current_uirevision = "constant"
    if current_figure and "layout" in current_figure and "uirevision" in current_figure["layout"]:
        current_uirevision = current_figure["layout"]["uirevision"]

    layout_kwargs = dict(
        title=f"{wafer_diameter_inch:.1f}-Inch {wafer_material} Wafer ({wafer_thickness} µm thick)",
        xaxis=dict(title="X (µm)", scaleanchor="y"),
        yaxis=dict(title="Y (µm)"),
        margin=dict(l=40, r=40, t=60, b=40),
        showlegend=False,
        uirevision=current_uirevision
    )

    if source == "table" and selected_sample:
        for d in target_dies:
            if d["label"] == selected_sample:
                w = d["width_um"]
                h = d["height_um"]
                cx = d["x_um"] + w / 2
                cy = d["y_um"] + h / 2
                span = max(w, h) / 0.3
                layout_kwargs["xaxis"]["range"] = [cx - span / 2, cx + span / 2]
                layout_kwargs["xaxis"]["autorange"] = False
                layout_kwargs["yaxis"]["range"] = [cy - span / 2, cy + span / 2]
                layout_kwargs["yaxis"]["autorange"] = False
                layout_kwargs["uirevision"] = str(uuid.uuid4())
                break

    fig.update_layout(**layout_kwargs)
    return fig

# ----------------------------- Dash app -----------------------------
# Bootstrap CSS is served from the local 'assets' folder (bundled in the exe), so the
# viewer also looks right on lab PCs without internet access.
app = dash.Dash(__name__, assets_folder=resource_path("assets"), title="Resistance Viewer")

table_columns = [{"name": "Sample", "id": "Sample"}]
for i in range(max_res_cols):
    table_columns.append({
        "name": f"R{i+1} (Ω)",
        "id":   f"R{i+1} (Ω)",
        "editable": True,
        "type": "numeric"
    })

# Dynamically determine View Mode options
all_types_set = set(d.get("type", "sample") for d in dies)
if "sample" not in all_types_set:
    all_types_set.add("sample")
    
# Sort: Samples first, then others
view_mode_options = []
# Samples
view_mode_options.append({"label": "Samples", "value": "sample"})
for t in sorted(list(all_types_set)):
    if t == "sample": continue
    # Format name nice
    label = t.replace("_", " ").title()
    view_mode_options.append({"label": label, "value": t})


# =============================================================================
#  LIVE MEASUREMENT CALLBACKS (Background Thread & Controls)
# =============================================================================

@app.callback(
    Output("store-running", "data"),
    Output("btn-start-stop", "children"),
    Output("btn-start-stop", "style"),
    Input("btn-start-stop", "n_clicks"),
    Input("btn-clear", "n_clicks"),
    Input("iex-ua", "value"),
    Input("nplc", "value"),
    State("store-running", "data"),
    State("visa-resource", "value"),
    State("simulate", "value"),
    prevent_initial_call=True,
)
def controls(n_start, n_clear, iex_ua, nplc, running_state, visa_resource, simulate):
    triggered = callback_context.triggered
    trig = triggered[0]["prop_id"].split(".")[0] if triggered else None

    # Update global params safely
    with params_lock:
        params["iex_ua"] = float(iex_ua or 0.02)
        params["nplc"]   = float(nplc or 1.0)
        # Fixed intervals for simplicity in this integration
        params["interval_s"] = 1.0
        params["settle_s"]   = 0.02

    running = bool((running_state or {}).get("running", False))

    if trig == "btn-clear":
        clear_data()
        set_status("Cleared.")
        return {"running": running}, ("Stop" if running else "Start"), (STOP_STYLE if running else START_STYLE)

    if trig == "btn-start-stop":
        if running:
            # STOP
            run_event.clear()
            smu.output_off()
            set_status("Stopped.")
            return {"running": False}, "Start", START_STYLE
        else:
            # START (apply instrument address / simulation choice first)
            smu.reconfigure((visa_resource or DEFAULT_RESOURCE).strip(), bool(simulate))
            run_event.set()
            set_status("Starting…")
            return {"running": True}, "Stop", STOP_STYLE

    return no_update

@app.callback(
    Output("live-resistance-graph", "figure"),
    Output("live-status-box", "children"),
    Output("live-last-readout", "children"),
    Input("live-update-tick", "n_intervals"),
    State("iex-ua", "value"),
)
def refresh_live_ui(_, iex_ua):
    # Fetch data copy
    t_list, r_list = read_data_copy()
    
    # Create lightweight figure
    fig = go.Figure()
    fig.add_trace(go.Scattergl(x=t_list, y=r_list, mode="lines", name="R (Ω)"))
    
    iex_val = float(iex_ua or 0.02)
    fig.update_layout(
        title=f"Live Resistance (Iexc={iex_val:.3f} µA, Delta Mode)",
        xaxis_title="Time (s)",
        yaxis_title="Resistance (Ω)",
        margin=dict(l=50, r=20, t=40, b=40),
        uirevision="keep-zoom" # prevents reset on update
    )

    stat = get_status()
    last_val = r_list[-1] if r_list else None
    
    last_text = f"Last: {last_val:.4f} Ω" if last_val is not None else "Last: —"

    return fig, stat, last_text


# =============================================================================
#  REGISTER VALUE CALLBACK
# =============================================================================
@app.callback(
    Output("resistance-table", "data", allow_duplicate=True),
    Output("table-store", "data", allow_duplicate=True),
    Output("resistance-table", "active_cell", allow_duplicate=True),
    Output("status", "children", allow_duplicate=True),
    Input("btn-register", "n_clicks"),
    State("resistance-table", "active_cell"),
    State("resistance-table", "data"),
    State("resistance-table", "columns"),
    prevent_initial_call=True
)
def register_value(n_clicks, active_cell, table_data, table_columns):
    """
    1. Get last measured value from r_data.
    2. Write it into active_cell.
    3. Update dies/JSON.
    4. Move active_cell to next logical cell.
    """
    if not active_cell:
        return no_update, no_update, no_update, "Create selection first!"

    # Get measurement
    _, r_list = read_data_copy()
    if not r_list:
        return no_update, no_update, no_update, "No data measured yet!"
    
    measured_val = r_list[-1]
    
    # Get Location
    row_idx = active_cell["row"]
    col_id  = active_cell["column_id"]
    
    # Determine which sample and which Resistance ID this is
    # We parse the row data or column name
    # Logic similar to update_from_table
    
    # We need to find the sample label from the table data row
    # AND the resistance ID from the col_id.
    
    if row_idx < 0 or row_idx >= len(table_data):
        return no_update, no_update, no_update, "Invalid row."

    row_data = table_data[row_idx]
    
    # Identify sample label for this cell
    target_sample = None
    target_res_id = None
    
    # Is split view?
    is_split = "Sample_1" in row_data
    
    if is_split:
        if "_1" in col_id:
            target_sample = row_data.get("Sample_1")
            try: target_res_id = int(col_id.split("_")[0][1:])
            except ValueError: pass
        elif "_2" in col_id:
            target_sample = row_data.get("Sample_2")
            try: target_res_id = int(col_id.split("_")[0][1:])
            except ValueError: pass
    else:
        target_sample = row_data.get("Sample")
        try: target_res_id = int(col_id.split(" ")[0][1:])
        except ValueError: pass
        
    if not target_sample or not target_res_id:
        return no_update, no_update, no_update, "Please select a resistance cell (R#)."

    # Update Data
    # 1. Update in memory (dies global)
    die = next((d for d in dies if d["label"] == target_sample), None)
    if die:
        # Update specific resistance
        for r in die.get("resistances", []):
            if r["id"] == target_res_id:
                r.setdefault("history", []).append({
                    "timestamp": compact_now(),
                    "value_ohm": measured_val
                })
                break
    
    # 2. Save
    save_status = save_wafer_data(dies, wafer_info, json_file)
    
    # 3. Update Table Data (for UI feedback immediately)
    table_data[row_idx][col_id] = measured_val
    
    # 4. Advance Selection (Simple logic: Next R in same row, or Next Row R1)
    # Strategy: Find index of current column in table_columns
    # Try next column. If not editable/numeric, skip ?
    # Actually simpler: R{i} -> R{i+1}. If R{max}, go to next row R1.
    
    next_cell = None
    
    col_idx = next((i for i, c in enumerate(table_columns) if c["id"] == col_id), -1)
    
    # Try to find next resistance column in same row
    next_col_idx = col_idx + 1
    found_next = False
    
    if next_col_idx < len(table_columns):
        # check if it is an R column
        next_c = table_columns[next_col_idx]
        if "R" in next_c["id"]:
            next_cell = {"row": row_idx, "column": next_col_idx, "column_id": next_c["id"]}
            found_next = True
            
    if not found_next:
        # Go to next row, first resistance column
        next_row_idx = row_idx + 1
        if next_row_idx < len(table_data):
            # Find first column that is R-like
            first_r_idx = -1
            first_r_id = None
            for i, c in enumerate(table_columns):
                if is_split:
                     # If we were in Sample_1 side, stay in Sample_1 side? Or simply first R?
                     # Let's just find first R column generally.
                     # Actually, if split, we want to stay in column 1 (left) or column 2 (right)?
                     # Let's try to stick to "current side"
                     if "_1" in col_id and "_1" in c["id"] and "R" in c["id"]:
                         first_r_idx = i; first_r_id = c["id"]; break
                     if "_2" in col_id and "_2" in c["id"] and "R" in c["id"]:
                         first_r_idx = i; first_r_id = c["id"]; break
                else:
                     if "R" in c["id"]:
                         first_r_idx = i; first_r_id = c["id"]; break
            
            if first_r_idx != -1:
                next_cell = {"row": next_row_idx, "column": first_r_idx, "column_id": first_r_id}
                
    # If still None (end of table), keep current
    if not next_cell:
        next_cell = active_cell

    return table_data, table_data, next_cell, f"Registered {measured_val:.2f} Ω for {target_sample}. {save_status}"

# --- Live Measurement Constants ---
START_STYLE = {"padding": "6px 12px", "borderRadius": "6px", "border": "0",
               "background": "#2ecc71", "color": "white", "fontWeight": "700", "fontSize": "14px", "marginRight": "10px"}
STOP_STYLE  = {"padding": "6px 12px", "borderRadius": "6px", "border": "0",
               "background": "#e74c3c", "color": "white", "fontWeight": "700", "fontSize": "14px", "marginRight": "10px"}
BTN_STYLE   = {"padding": "6px 12px", "borderRadius": "6px", "fontSize": "14px", "marginRight": "10px"}
NPLC_MARKS = {0.1: "0.1", 1.0: "1", 10.0: "10"}

app.layout = dbc.Container(fluid=True, children=[
    dcc.Store(id="table-store", data=df.to_dict("records")),
    dcc.Store(id="selection-store", data={"sample": None, "res_id": None}),
    # Store for live measurement running state (Start/Stop)
    dcc.Store(id="store-running", data={"running": False}),

    # UI Tick for Live Graph updates (500ms)
    dcc.Interval(id="live-update-tick", interval=500, n_intervals=0),

    # --- TOP ROW: Wafer Plot + Main Controls ---
    dbc.Row([
        dbc.Col(
            dcc.Graph(
                id="wafer-plot",
                figure=create_wafer_figure(),
                style={"height": "50vh"}
            ),
            width=8
        ),
        dbc.Col([
            html.Div([
                html.Button("Quit viewer", id="btn-quit", n_clicks=0,
                            title="Stop the viewer program (data is already saved)",
                            style={**BTN_STYLE, "float": "right", "marginRight": "0"}),
                html.H4("Settings"),
            ]),
            html.Div(id="quit-msg", style={"color": "#c0392b", "fontWeight": "bold"}),
            html.Label("View Mode:"),
            dcc.RadioItems(
                id="view-mode-selector",
                options=view_mode_options,
                value="sample",
                inline=True,
                style={"marginBottom": "1rem"}
            ),
            html.Hr(),
            html.H4("Sample Notes"),
            dcc.Textarea(
                id="sample-notes-input",
                style={"width": "100%", "height": 60, "marginBottom": "0.5rem"},
                placeholder="Select a sample to add notes..."
            ),
            dbc.Button("Save Note", id="save-note-btn", color="primary", size="sm", style={"marginBottom": "1rem"}),
            html.Hr(),
            html.H4("Edit Resistance Values"),
            dash_table.DataTable(
                id="resistance-table",
                columns=table_columns,
                data=df.to_dict("records"),
                style_table={"height": "20vh", "overflowY": "auto", "overflowX": "auto"},
                editable=True
            ),
            html.Div(id="status", style={"marginTop": "0.5rem", "fontSize": "0.9rem"})
        ], width=4)
    ], style={"marginBottom": "20px"}),

    # --- BOTTOM ROW: Live Measurement ---
    html.Hr(),
    html.H4("Live Resistance Measurement (Keithley 2450)"),
    dbc.Row([
        # Left: Live Graph
        dbc.Col(
            dcc.Graph(id="live-resistance-graph", style={"height": "30vh"}),
            width=8
        ),
        # Right: Controls
        dbc.Col([
            html.Div(id="live-status-box", style={"marginBottom": "10px", "fontSize": "16px", "fontWeight": "bold"}),
            html.Div(id="live-last-readout", style={"marginBottom": "15px", "fontSize": "20px", "color": "#007bff"}),
            
            html.Div([
                html.Button("Start", id="btn-start-stop", n_clicks=0, style=START_STYLE),
                html.Button("Clear", id="btn-clear", n_clicks=0, style=BTN_STYLE),
            ], style={"marginBottom": "15px"}),
            
            dbc.Card([
                dbc.CardBody([
                    html.Label("Iexc (µA)"),
                    dcc.Slider(
                        id="iex-ua",
                        min=0.001, max=5.0, step=0.001, value=0.02,
                        marks={0.001: "1n", 0.02: "20n", 1: "1µ", 5: "5µ"},
                        tooltip={"placement": "bottom", "always_visible": True},
                    ),
                    html.Br(),
                    html.Label("NPLC (Avg)"),
                    dcc.Slider(
                        id="nplc",
                        min=0.1, max=10.0, step=None, value=1.0,
                        marks=NPLC_MARKS,
                    ),
                    html.Br(),
                    html.Label("Instrument VISA address (applied on Start)"),
                    dbc.Input(id="visa-resource", value=smu.cfg.resource, size="sm", debounce=True),
                    dbc.Checkbox(id="simulate", value=smu.cfg.simulate,
                                 label="Simulate instrument (no hardware)", style={"marginTop": "6px"}),
                ])
            ], style={"marginBottom": "15px"}),
            
            dbc.Button("REGISTER VALUE", id="btn-register", color="success", size="lg", style={"width": "100%"}),
            html.Div("Inserts last measured value into selected cell and advances.", style={"fontSize": "12px", "color": "gray", "marginTop": "5px"}),

        ], width=4)
    ])
])


# ----------------------------- Save Logic -----------------------------
_backed_up = set()  # files already backed up to .bak during this session
save_lock = threading.Lock()


def save_wafer_data(*a, **kw):
    with save_lock:  # Dash may run callbacks concurrently
        return _save_wafer_data(*a, **kw)


def _save_wafer_data(updated_dies, metadata, main_json_path):
    """
    Saves the wafer dies to JSON files:
    1. Splits dies by type (sample vs others).
    2. Saves 'sample' type to main_json_path.
    3. Saves other types to their respective sub-database files defined in metadata.
    4. Supports legacy _TJ.json fallback.
    Writes are atomic (temp file + replace), and the first save of a session keeps
    the previous version of each file as '<name>.bak'.
    Returns a status string.
    """
    timestamp = compact_now()
    status_msg = ""

    def write(path, data):
        path = str(path)
        first = path not in _backed_up
        atomic_write_json(path, data, backup=first)
        _backed_up.add(path)

    # 1. Save Samples
    sample_dies = [d for d in updated_dies if d.get("type", "sample") == "sample"]
    output_json_samples = {"metadata": metadata, "dies": sample_dies}

    try:
        write(main_json_path, output_json_samples)
        status_msg = f"Saved at {timestamp}."
    except Exception as e:
        status_msg = f"❌ Error writing Samples JSON: {e}"

    # 2. Save TJs (Multiple types)
    sub_dbs = metadata.get("sub_databases", [])
    
    # If no sub_databases defined but "test_junction" present (legacy mode)
    legacy_mode = (not sub_dbs) and any(d.get("type") == "test_junction" for d in updated_dies)
    
    if legacy_mode:
        # Fallback to _TJ.json
        p = pathlib.Path(main_json_path)
        tj_json_file = p.with_name(f"{p.stem}_TJ{p.suffix}")
        tj_dies = [d for d in updated_dies if d.get("type") == "test_junction"]
        if tj_dies or tj_json_file.exists():
            try:
                write(tj_json_file, {"metadata": metadata, "dies": tj_dies})
                status_msg += " (Updated Legacy TJ)"
            except Exception as e:
                status_msg += f" ❌ Error writing TJ: {e}"

    # New Multi-TJ mode
    for db_info in sub_dbs:
        t_type = db_info.get("type")
        f_name = db_info.get("filename")
        if not f_name: continue
        
        t_dies = [d for d in updated_dies if d.get("type") == t_type]
        
        # Reconstruct full object
        try:
            tgt_path = pathlib.Path(main_json_path).with_name(f_name)
            write(tgt_path, {"metadata": metadata, "dies": t_dies})
            status_msg += f" (Updated {t_type})"
        except Exception as e:
            status_msg += f" ❌ Error writing {t_type}: {e}"
            
    return status_msg


# === 1) Edits: append timestamped entry ONLY for changed cells, save JSON, redraw

@app.callback(
    Output("wafer-plot", "figure"),
    Output("table-store", "data"),
    Output("status", "children"),
    Output("resistance-table", "columns"),
    Output("resistance-table", "data"),
    Input("resistance-table", "data_timestamp"),
    Input("selection-store", "data"),
    Input("view-mode-selector", "value"),
    Input("save-note-btn", "n_clicks"),
    State("sample-notes-input", "value"),
    State("resistance-table", "data"),
    State("table-store", "data"),
    State("wafer-plot", "figure"),
    prevent_initial_call=False
)
def update_from_table(ts, selection_data, view_mode, save_note_clicks, notes_value, current_data, previous_data, current_figure):
    ctx = dash.callback_context
    trigger = ctx.triggered[0]["prop_id"].split('.')[0] if ctx.triggered else None

    if previous_data is None:
        previous_data = current_data

    changes_made = 0
    status = "Ready."
    removed_msgs = []

    timestamp = compact_now()
    die_by_label = {d["label"]: d for d in dies}

    # --- Handle Table Edits ---
    if trigger == "resistance-table" and current_data is not None and previous_data is not None:
        for cur_row, prev_row in zip(current_data, previous_data):
            # Helper to process a specific sample in the row
            def process_sample_edit(sample_key, res_key_suffix):
                sample_label = cur_row.get(sample_key)
                if not sample_label:
                    return 0
                
                die = die_by_label.get(sample_label)
                if not die:
                    return 0

                local_changes = 0
                # loop over THIS die's resistances
                for i, r in enumerate(die.get("resistances", []), start=1):
                    # Construct column key: R{i} (Ω) OR R{i}_{suffix}
                    if res_key_suffix:
                        col = f"R{i}_{res_key_suffix}"
                    else:
                        col = f"R{i} (Ω)"

                    cur_val = cur_row.get(col, None)
                    prev_val = prev_row.get(col, None)

                    def as_float(x):
                        if x in ("", None):
                            return None
                        try:
                            return float(x)
                        except Exception:
                            return None

                    cur_f = as_float(cur_val)
                    prev_f = as_float(prev_val)

                    if cur_f == prev_f:
                        continue
                    if cur_f is not None:
                        r.setdefault("history", []).append(
                            {"timestamp": timestamp, "value_ohm": cur_f}
                        )
                        local_changes += 1
                    elif cur_val in ("", None) and r.get("history"):
                        # Cell cleared: undo the latest value. It is kept under "removed"
                        # (with the removal time) so nothing is ever lost.
                        removed = dict(r["history"].pop())
                        removed["removed_at"] = timestamp
                        r.setdefault("removed", []).append(removed)
                        removed_msgs.append(f"{sample_label} R{i}: removed {removed.get('value_ohm')} Ω")
                        local_changes += 1
                return local_changes

            # Check for standard "Sample" key
            if "Sample" in cur_row:
                changes_made += process_sample_edit("Sample", None)
            
            # Check for Split View keys "Sample_1" and "Sample_2"
            if "Sample_1" in cur_row:
                changes_made += process_sample_edit("Sample_1", "1")
            if "Sample_2" in cur_row:
                changes_made += process_sample_edit("Sample_2", "2")

    # --- Handle Manual Note Save ---
    if trigger == "save-note-btn" and selection_data:
        label = selection_data.get("sample")
        if label:
            die = die_by_label.get(label)
            if die:
                die["notes"] = notes_value or ""
                changes_made += 1

    # Save JSON if edits happened
    if changes_made > 0:
        status = save_wafer_data(dies, wafer_info, json_file)
        status = f"Saved {changes_made} change(s). " + status
        if removed_msgs:
            status += " | " + "; ".join(removed_msgs) + " (kept under 'removed' in the JSON)"

    previous_data = current_data

    # Filter dies based on view_mode
    # Default to "sample" if type is missing (backward compatibility)
    filtered_dies = [d for d in dies if d.get("type", "sample") == view_mode]

    # Two-column table for any test-junction type with many entries
    use_split_view = (view_mode != "sample" and len(filtered_dies) > 35)

    new_rows = []
    new_columns = []

    if use_split_view:
        split_idx = math.ceil(len(filtered_dies) / 2)
        left_dies = filtered_dies[:split_idx]
        right_dies = filtered_dies[split_idx:]

        # Determine max cols for left and right (though likely 1 for TJs)
        max_res_cols_left = max((len(d.get("resistances", [])) for d in left_dies), default=0)
        max_res_cols_right = max((len(d.get("resistances", [])) for d in right_dies), default=0)
        
        # We'll use the max of both to keep columns aligned, or just dynamic?
        # Let's use max of both for simplicity in column definition
        max_res_cols_local = max(max_res_cols_left, max_res_cols_right)

        for i in range(split_idx):
            row = {}
            
            # Left Die
            d_left = left_dies[i]
            row["Sample_1"] = d_left["label"]
            for k, r in enumerate(d_left.get("resistances", []), start=1):
                row[f"R{k}_1"] = latest_value(r.get("history", []))
            
            # Right Die (if exists)
            if i < len(right_dies):
                d_right = right_dies[i]
                row["Sample_2"] = d_right["label"]
                for k, r in enumerate(d_right.get("resistances", []), start=1):
                    row[f"R{k}_2"] = latest_value(r.get("history", []))
            else:
                row["Sample_2"] = "" # Empty placeholder

            new_rows.append(row)

        # Build Split Columns
        # Left Side
        new_columns.append({"name": "Sample", "id": "Sample_1"})
        for k in range(max_res_cols_local):
            new_columns.append({
                "name": f"R{k+1} (Ω)",
                "id":   f"R{k+1}_1",
                "editable": True,
                "type": "numeric"
            })
        
        # Spacer? Or just next column.
        # Right Side
        new_columns.append({"name": "Sample", "id": "Sample_2"})
        for k in range(max_res_cols_local):
            new_columns.append({
                "name": f"R{k+1} (Ω)",
                "id":   f"R{k+1}_2",
                "editable": True,
                "type": "numeric"
            })

    else:
        # Standard Single Column View
        max_res_cols_local = max((len(d.get("resistances", [])) for d in filtered_dies), default=0)
        for d in filtered_dies:
            row = {"Sample": d["label"]}
            for i, r in enumerate(d.get("resistances", []), start=1):
                row[f"R{i} (Ω)"] = latest_value(r.get("history", []))
            for i in range(1, max_res_cols_local + 1):
                row.setdefault(f"R{i} (Ω)", None)
            new_rows.append(row)
        
        new_columns = [{"name": "Sample", "id": "Sample"}]
        for i in range(max_res_cols_local):
            new_columns.append({
                "name": f"R{i+1} (Ω)",
                "id":   f"R{i+1} (Ω)",
                "editable": True,
                "type": "numeric"
            })

    fig = create_wafer_figure(selection_data, filtered_dies, view_mode, current_figure)
    # Return new_rows to BOTH table-store (for diffing next time) AND resistance-table (for display)
    return fig, new_rows, status, new_columns, new_rows

# === 2) Sync Notes Textarea with Selection
@app.callback(
    Output("sample-notes-input", "value"),
    Input("selection-store", "data"),
    prevent_initial_call=False
)
def sync_notes_to_selection(selection_data):
    if not selection_data:
        return ""
    
    label = selection_data.get("sample")
    if not label:
        return ""
    
    # search die
    found = next((d for d in dies if d["label"] == label), None)
    if found:
        return found.get("notes", "")
    return ""

# === 3) Unified selection sync: graph <-> table
# === 3) Unified selection sync: graph <-> table
@app.callback(
    Output("selection-store", "data"),
    Output("resistance-table", "active_cell"),
    Output("resistance-table", "selected_rows"),
    Output("resistance-table", "selected_cells"),
    Input("wafer-plot", "clickData"),
    Input("resistance-table", "active_cell"),
    State("resistance-table", "data"),
    State("resistance-table", "columns"),
    State("selection-store", "data"),
    prevent_initial_call=True
)
def sync_selection(clickData, active_cell, table_data, table_columns, selection_data):
    ctx = dash.callback_context
    trigger = ctx.triggered[0]["prop_id"].split('.')[0] if ctx.triggered else None

    # NO Auto-Save of old notes on navigation requested.
    # Manual save only.

    cur_selection = selection_data or {"sample": None, "res_id": None, "source": None}
    selected_rows = no_update
    selected_cells = no_update
    active = active_cell

    # Detect if we are in split mode based on columns or data keys
    # If "Sample_1" is in the first row keys, we are in split mode
    is_split_mode = False
    if table_data and "Sample_1" in table_data[0]:
        is_split_mode = True

    # --- 1) Graph click -> table selection ---
    if trigger == "wafer-plot" and clickData and "points" in clickData and clickData["points"]:
        cd = clickData["points"][0].get("customdata")

        # customdata can be dict or list-of-dicts
        info = cd
        if isinstance(cd, list):
            for el in cd:
                if isinstance(el, dict) and "sample" in el:
                    info = el
                    break

        if not isinstance(info, dict):
            return cur_selection, active, selected_rows, selected_cells

        sample = info.get("sample")
        res_id = info.get("res_id")

        # Allow clicks on RESISTANCE (int > 0) OR clicks on SAMPLE (res_id=None or whatever)
        if sample is None:
            return cur_selection, active, selected_rows, selected_cells

        # Find row and column in table
        row_idx = None
        col_idx = None
        target_col_id = None

        # Determine target column associated with selection
        # If res_id set -> that specific R column
        # If no res_id -> The "Sample" column
        if is_split_mode:
            # Find row
            for i, r in enumerate(table_data):
                if r.get("Sample_1") == sample:
                    row_idx = i
                    target_col_id = f"R{res_id}_1" if res_id else "Sample_1"
                    break
                if r.get("Sample_2") == sample:
                    row_idx = i
                    target_col_id = f"R{res_id}_2" if res_id else "Sample_2"
                    break
        else:
            row_idx = next((i for i, r in enumerate(table_data) if r.get("Sample") == sample), None)
            target_col_id = f"R{res_id} (Ω)" if res_id else "Sample"

        if row_idx is None:
            return cur_selection, active, selected_rows, selected_cells

        col_idx = next((i for i, c in enumerate(table_columns) if c["id"] == target_col_id), None)
        
        if col_idx is not None:
            active = {"row": row_idx, "column": col_idx, "column_id": target_col_id}
            selected_cells = [active]
            selected_rows = [row_idx] # Highlight row too

        new_selection = {"sample": sample, "res_id": res_id, "source": "graph"}
        return new_selection, active, selected_rows, selected_cells

    # --- 2) Table cell click -> selection ---
    if trigger == "resistance-table" and active_cell is not None:
        row_idx = active_cell.get("row")
        col_id  = active_cell.get("column_id")

        if row_idx is None or row_idx < 0 or row_idx >= len(table_data):
            return cur_selection, active, selected_rows, selected_cells
        
        sample = None
        res_id = None

        if is_split_mode:
            # Check left
            if col_id == "Sample_1" or ("_1" in col_id):
                sample = table_data[row_idx].get("Sample_1")
                # Parse res_id if available
                if col_id.startswith("R") and "_1" in col_id:
                    try:
                        res_id = int(col_id.split("_")[0][1:])
                    except ValueError: pass
            elif col_id == "Sample_2" or ("_2" in col_id):
                sample = table_data[row_idx].get("Sample_2")
                if col_id.startswith("R") and "_2" in col_id:
                     try:
                        res_id = int(col_id.split("_")[0][1:])
                     except ValueError: pass
        else:
            # Check standard
            # Sample name or R column
            sample = table_data[row_idx].get("Sample")
            if col_id.startswith("R") and "(Ω)" in col_id:
                try:
                    res_id = int(col_id.split(" ")[0][1:])
                except ValueError: pass

        if sample:
            selected_rows = [row_idx]
            new_selection = {"sample": sample, "res_id": res_id, "source": "table"}
            return new_selection, active_cell, selected_rows, selected_cells

    return cur_selection, active, selected_rows, selected_cells

# === 4) Quit: stop the background program (the exe has no console window to close)
@app.callback(
    Output("quit-msg", "children"),
    Input("btn-quit", "n_clicks"),
    prevent_initial_call=True,
)
def quit_viewer(n_clicks):
    if not n_clicks:
        return no_update
    run_event.clear()
    stop_event.set()

    def _shutdown():
        try:
            smu.close()  # output off + release the instrument
        finally:
            os._exit(0)

    threading.Timer(0.5, _shutdown).start()
    return "Viewer stopped. All data was already saved — you can close this tab."


if __name__ == "__main__":
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(VIEWER_URL)).start()

    # Run without debug to prevent duplicate tkinter dialogs from the reloader
    app.run(debug=False, port=args.port)
