import sys

# Patch for PyInstaller windowed mode (sys.stdout/stderr are None there),
# which otherwise makes loguru (used by gdsfactory/kfactory) crash on import.
class DummyWriter:
    def write(self, *args, **kwargs): pass
    def flush(self): pass


if sys.stdout is None:
    sys.stdout = DummyWriter()
if sys.stderr is None:
    sys.stderr = DummyWriter()

import copy
import queue
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

from datafab_io import atomic_write_json, load_json_lenient
from database_4 import generate_from_config


class QueueWriter:
    """File-like object that forwards printed text to the GUI through a queue (thread-safe)."""

    def __init__(self, q: "queue.Queue"):
        self.q = q

    def write(self, text):
        if text.strip():
            self.q.put(("log", text.rstrip()))

    def flush(self):
        pass


class DatabaseGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Wafer Database Generator")
        self.geometry("600x700")

        self.config_path = ""
        self.config_data = {}
        self.param_vars = {}
        self.tj_vars = []
        self.var_export_gds = tk.BooleanVar(value=True)
        self.msg_queue: "queue.Queue" = queue.Queue()

        self.create_widgets()
        self.after(100, self._drain_queue)

    # ------------------------------------------------------------------ UI
    def create_widgets(self):
        file_frame = tk.LabelFrame(self, text="File Selection", padx=10, pady=10)
        file_frame.pack(fill="x", padx=10, pady=5)

        tk.Label(file_frame, text="Config JSON:").grid(row=0, column=0, sticky="e")
        self.lbl_config = tk.Label(file_frame, text="No file selected", fg="blue", cursor="hand2",
                                   width=50, anchor="w", relief="sunken")
        self.lbl_config.grid(row=0, column=1, padx=5)
        self.lbl_config.bind("<Button-1>", lambda e: self.browse_config())
        tk.Button(file_frame, text="Browse", command=self.browse_config).grid(row=0, column=2)

        tk.Label(file_frame, text="GDS File (from config):").grid(row=1, column=0, sticky="e", pady=5)
        self.lbl_gds = tk.Label(file_frame, text="No file selected", fg="gray", width=50, anchor="w")
        self.lbl_gds.grid(row=1, column=1, padx=5, pady=5, columnspan=2)

        self.param_frame = tk.LabelFrame(self, text="Parameters (Auto-loaded from Config)", padx=10, pady=10)
        self.param_frame.pack(fill="both", expand=True, padx=10, pady=5)
        tk.Label(self.param_frame, text="Please select a config.json to load parameters.").pack(pady=20)

        self.btn_run = tk.Button(self, text="Generate Database", command=self.run_generation,
                                 bg="#2ecc71", fg="white", font=("Arial", 12, "bold"))
        self.btn_run.pack(pady=10, ipadx=20, ipady=5)

        log_frame = tk.LabelFrame(self, text="Console Output")
        log_frame.pack(fill="both", expand=True, padx=10, pady=5)
        self.txt_log = tk.Text(log_frame, height=10, state="disabled", bg="#f4f4f4")
        self.txt_log.pack(fill="both", expand=True)

    def log(self, msg):
        """Append to the console. Must only be called from the Tk main thread."""
        self.txt_log.config(state="normal")
        self.txt_log.insert(tk.END, msg + "\n")
        self.txt_log.see(tk.END)
        self.txt_log.config(state="disabled")

    def _drain_queue(self):
        """Runs on the main thread: moves messages from the worker thread into the UI."""
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "done":
                    self.btn_run.config(state="normal", text="Generate Database", bg="#2ecc71")
                    if payload:
                        self.log("\n✅ GENERATION COMPLETE")
                    else:
                        messagebox.showerror("Generation failed", "See the console output for details.")
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    # -------------------------------------------------------------- config
    def browse_config(self):
        path = filedialog.askopenfilename(title="Select config.json", filetypes=[("JSON Files", "*.json")])
        if path:
            self.config_path = path
            self.lbl_config.config(text=path[-50:])
            self.load_config()

    def load_config(self):
        try:
            data = load_json_lenient(self.config_path)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load JSON: {e}")
            return
        if "script_parameters" not in data or "wafer_metadata" not in data:
            messagebox.showerror(
                "Invalid config",
                "This file has no 'script_parameters'/'wafer_metadata' sections.\n"
                "Did you select a generated *_database.json instead of a config file?")
            return
        self.config_data = data

        gds = data["script_parameters"].get("input_gds_file", "No file selected")
        self.lbl_gds.config(text=gds[-50:] if len(gds) > 50 else gds)
        self.build_param_ui()
        self.log(f"Loaded config: {Path(self.config_path).name}")

    def build_param_ui(self):
        for widget in self.param_frame.winfo_children():
            widget.destroy()

        params = self.config_data.get("script_parameters", {})
        self.param_vars = {}
        row = 0

        tk.Label(self.param_frame, text="Label Prefix:", font=("Arial", 10, "bold")).grid(row=row, column=0, sticky="e", pady=2)
        var_prefix = tk.StringVar(value=params.get("label_prefix", ""))
        tk.Entry(self.param_frame, textvariable=var_prefix, width=30).grid(row=row, column=1, sticky="w", padx=5, pady=2)
        self.param_vars["label_prefix"] = var_prefix
        row += 1

        tk.Label(self.param_frame, text="--- Layer Numbers (datatype is kept from the config) ---",
                 fg="gray").grid(row=row, column=0, columnspan=2, pady=10)
        row += 1

        def get_layer(key):
            v = params.get(key, [0, 0])
            return str(v[0]) if isinstance(v, list) and len(v) > 0 else "0"

        for key, display in [("input_layer", "Input Layer (Samples):"),
                             ("resistance_layer", "Resistance Layer:"),
                             ("text_layer", "Text Layer:")]:
            if key not in params:
                continue
            tk.Label(self.param_frame, text=display).grid(row=row, column=0, sticky="e", pady=2)
            var = tk.StringVar(value=get_layer(key))
            tk.Entry(self.param_frame, textvariable=var, width=10).grid(row=row, column=1, sticky="w", padx=5, pady=2)
            self.param_vars[key] = var
            row += 1

        self.tj_vars = []
        for i, tj in enumerate(params.get("test_junctions", [])):
            name = tj.get("name", f"TJ_{i}")
            layer_val = str(tj.get("layer", [0, 0])[0])
            tk.Label(self.param_frame, text=f"Test Junction '{name}':").grid(row=row, column=0, sticky="e", pady=2)
            var = tk.StringVar(value=layer_val)
            tk.Entry(self.param_frame, textvariable=var, width=10).grid(row=row, column=1, sticky="w", padx=5, pady=2)
            self.tj_vars.append((i, var))
            row += 1

        tk.Label(self.param_frame, text="--- Generation Options ---", fg="gray").grid(row=row, column=0, columnspan=2, pady=10)
        row += 1

        self.var_export_gds = tk.BooleanVar(value=params.get("export_gds_with_text", True))
        tk.Checkbutton(self.param_frame, text="Create new GDS with text labels", variable=self.var_export_gds,
                       font=("Arial", 9, "bold")).grid(row=row, column=0, columnspan=2, sticky="w", padx=20, pady=5)

    def _config_from_ui(self) -> dict:
        """Return a copy of the loaded config with the UI values applied (raises ValueError)."""
        cfg = copy.deepcopy(self.config_data)
        params = cfg["script_parameters"]
        params["label_prefix"] = self.param_vars["label_prefix"].get()
        export = bool(self.var_export_gds.get())
        if "export_gds_with_text" in params or not export:  # default is True: don't add it needlessly
            params["export_gds_with_text"] = export
        for key in ("input_layer", "resistance_layer", "text_layer"):
            if key in self.param_vars:
                params[key][0] = int(self.param_vars[key].get())
        for i, var in self.tj_vars:
            params["test_junctions"][i]["layer"][0] = int(var.get())
        return cfg

    # ----------------------------------------------------------- generation
    def run_generation(self):
        if not self.config_path or not self.config_data:
            messagebox.showwarning("Warning", "Please select a config.json first.")
            return

        try:
            cfg = self._config_from_ui()
        except ValueError:
            messagebox.showerror("Error", "All layer values must be integers.")
            return

        if cfg != self.config_data:
            answer = messagebox.askyesnocancel(
                "Save changes?",
                "You changed some parameters.\n\n"
                "Yes: save them to the config file and generate\n"
                "No: generate with them without changing the config file\n"
                "Cancel: do nothing")
            if answer is None:
                return
            if answer:
                try:
                    atomic_write_json(self.config_path, cfg, backup=True)
                    self.config_data = copy.deepcopy(cfg)
                    self.log("Config saved (previous version kept as .bak).")
                except Exception as e:
                    messagebox.showerror("Error", f"Failed to save config: {e}")
                    return

        self.btn_run.config(state="disabled", text="Generating...", bg="gray")
        self.log("-" * 40)
        self.log("Starting database generation...")
        threading.Thread(target=self._run_thread, args=(self.config_path, cfg), daemon=True).start()

    def _run_thread(self, config_path, cfg):
        """Worker thread: never touches Tk widgets, only posts to the queue."""
        writer = QueueWriter(self.msg_queue)
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = writer
        ok = False
        try:
            generate_from_config(config_path, config=cfg)
            ok = True
        except Exception:
            self.msg_queue.put(("log", "\n❌ ERROR during generation:"))
            self.msg_queue.put(("log", traceback.format_exc()))
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr
            self.msg_queue.put(("done", ok))


if __name__ == "__main__":
    app = DatabaseGUI()
    app.mainloop()
