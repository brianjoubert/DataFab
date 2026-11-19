# viewer_time_history.py
import json
import sys
import datetime as dt
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
import dash
from dash import dcc, html, Input, Output, State, dash_table, no_update
import dash_bootstrap_components as dbc
import pandas as pd
from shapely.geometry import Point, Polygon

# ----------------------------- Utilities -----------------------------
def compact_now():
    # Local time, compact format (no seconds)
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")

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
if len(sys.argv) != 2:
    print("Usage: python viewer_time_history.py path_to_wafer_database_json")
    sys.exit(1)

json_file = sys.argv[1]
with open(json_file, "r", encoding="utf-8") as f:
    wafer_data = json.load(f)

wafer_info = wafer_data["metadata"]
dies = wafer_data["dies"]

# Try to load TJ databases (Multiple Support)
import pathlib
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

def create_wafer_figure(selection=None, dies_subset=None, view_mode="sample"):
    """
    selection: dict like {"sample": <label or None>, "res_id": <int or None>}
    Only the selected resistance rectangle gets a slightly larger red outline.
    """
    selected_sample = None
    selected_res_id = None
    if isinstance(selection, dict):
        selected_sample = selection.get("sample")
        selected_res_id = selection.get("res_id")

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

    fig.update_layout(
        title=f"{wafer_diameter_inch:.1f}-Inch {wafer_material} Wafer ({wafer_thickness} µm thick)",
        xaxis=dict(title="X (µm)", scaleanchor="y"),
        yaxis=dict(title="Y (µm)"),
        margin=dict(l=40, r=40, t=60, b=40),
        showlegend=False
    )
    return fig

# ----------------------------- Dash app -----------------------------
external_stylesheets = [dbc.themes.BOOTSTRAP]
app = dash.Dash(__name__, external_stylesheets=external_stylesheets)

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

app.layout = dbc.Container(fluid=True, children=[
    dcc.Store(id="table-store", data=df.to_dict("records")),      # previous table snapshot for diff
    dcc.Store(id="selection-store", data={"sample": None, "res_id": None}),
    dbc.Row([
        dbc.Col(
            dcc.Graph(
                id="wafer-plot",
                figure=create_wafer_figure(),
                style={"height": "80vh"}
            ),
            width=8
        ),
        dbc.Col([
            html.H4("Settings"),
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
                style={"width": "100%", "height": 100, "marginBottom": "0.5rem"},
                placeholder="Select a sample to add notes..."
            ),
            dbc.Button("Save Note", id="save-note-btn", color="primary", size="sm", style={"marginBottom": "1rem"}),
            html.Hr(),
            html.H4("Edit Resistance Values"),
            dash_table.DataTable(
                id="resistance-table",
                columns=table_columns,
                data=df.to_dict("records"),
                style_table={"overflowX": "auto"},
                editable=True
            ),
            html.Div(id="status", style={"marginTop": "0.5rem", "fontSize": "0.9rem"})
        ], width=4)
    ])
])

# ----------------------------- Save Logic -----------------------------
def save_wafer_data(updated_dies, metadata, main_json_path):
    """
    Saves the wafer dies to JSON files:
    1. Splits dies by type (sample vs others).
    2. Saves 'sample' type to main_json_path.
    3. Saves other types to their respective sub-database files defined in metadata.
    4. Supports legacy _TJ.json fallback.
    Returns a status string.
    """
    import pathlib
    import json
    
    timestamp = compact_now()
    status_msg = ""
    
    # 1. Save Samples
    sample_dies = [d for d in updated_dies if d.get("type", "sample") == "sample"]
    output_json_samples = {"metadata": metadata, "dies": sample_dies}
    
    try:
        with open(main_json_path, "w", encoding="utf-8") as f:
            json.dump(output_json_samples, f, indent=2)
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
                with open(tj_json_file, "w", encoding="utf-8") as f:
                        json.dump({"metadata": metadata, "dies": tj_dies}, f, indent=2)
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
            p = pathlib.Path(main_json_path)
            tgt_path = p.with_name(f_name)
            with open(tgt_path, "w", encoding="utf-8") as f:
                json.dump({"metadata": metadata, "dies": t_dies}, f, indent=2)
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
    prevent_initial_call=False
)
def update_from_table(ts, selection_data, view_mode, save_note_clicks, notes_value, current_data, previous_data):
    ctx = dash.callback_context
    trigger = ctx.triggered[0]["prop_id"].split('.')[0] if ctx.triggered else None

    if previous_data is None:
        previous_data = current_data

    changes_made = 0
    status = "Ready."
    
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

                    if cur_f != prev_f:
                        if cur_f is not None:
                            r.setdefault("history", []).append(
                                {"timestamp": timestamp, "value_ohm": cur_f}
                            )
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
        print(f"DEBUG: Save Button Clicked. Label: {label}")
        if label:
            die = die_by_label.get(label)
            if die:
                # Force update based on current Textarea value
                print(f"DEBUG: Updating note for {label} to: '{notes_value}'")
                die["notes"] = notes_value
                changes_made += 1
                # status update handled below

    # Save JSON if edits happened
    if changes_made > 0:
        print(f"DEBUG: Saving wafer data...")
        status = save_wafer_data(dies, wafer_info, json_file)
        print(f"DEBUG: Save Status: {status}")
        status = f"Saved {changes_made} change(s). " + status

    previous_data = current_data

    # Filter dies based on view_mode
    # Default to "sample" if type is missing (backward compatibility)
    filtered_dies = [d for d in dies if d.get("type", "sample") == view_mode]

    # Check if we need split view (only for TJs and > 35 items)
    use_split_view = (view_mode == "test_junction" and len(filtered_dies) > 35)

    new_rows = []
    new_columns = []

    if use_split_view:
        # Split into two columns
        import math
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

    fig = create_wafer_figure(selection_data, filtered_dies, view_mode)
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

    cur_selection = selection_data or {"sample": None, "res_id": None}
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
        
        needed_columns = []
        if is_split_mode:
            possible_target_cols = []
            if res_id:
                possible_target_cols = [f"R{res_id}_1", f"R{res_id}_2"]
            else:
                possible_target_cols = ["Sample_1", "Sample_2"]
            
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

        new_selection = {"sample": sample, "res_id": res_id}
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
                    except: pass
            elif col_id == "Sample_2" or ("_2" in col_id):
                sample = table_data[row_idx].get("Sample_2")
                if col_id.startswith("R") and "_2" in col_id:
                     try:
                        res_id = int(col_id.split("_")[0][1:])
                     except: pass
        else:
            # Check standard
            # Sample name or R column
            sample = table_data[row_idx].get("Sample")
            if col_id.startswith("R") and "(Ω)" in col_id:
                try:
                    res_id = int(col_id.split(" ")[0][1:])
                except: pass

        if sample:
            selected_rows = [row_idx]
            new_selection = {"sample": sample, "res_id": res_id}
            return new_selection, active_cell, selected_rows, selected_cells

    return cur_selection, active, selected_rows, selected_cells

if __name__ == "__main__":
    app.run(debug=True)
