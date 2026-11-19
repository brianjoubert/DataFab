import sys
import os

# Patch for PyInstaller windowed mode (where sys.stdout/stderr are None)
# which causes loguru (used by gdsfactory/kfactory) to crash on import.
class DummyWriter:
    def write(self, *args, **kwargs): pass
    def flush(self): pass

if sys.stdout is None:
    sys.stdout = DummyWriter()
if sys.stderr is None:
    sys.stderr = DummyWriter()

import tkinter as tk
from tkinter import filedialog, messagebox
import json
import threading
import traceback
from pathlib import Path
from pathlib import Path

# Import the generation function
from database_4 import generate_from_config

class DatabaseGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Wafer Database Generator")
        self.geometry("600x700")
        
        self.config_path = ""
        self.config_data = {}
        
        self.create_widgets()
        
    def create_widgets(self):
        # Frame for file selection
        file_frame = tk.LabelFrame(self, text="File Selection", padx=10, pady=10)
        file_frame.pack(fill="x", padx=10, pady=5)
        
        # Config JSON
        tk.Label(file_frame, text="Config JSON:").grid(row=0, column=0, sticky="e")
        self.lbl_config = tk.Label(file_frame, text="No file selected", fg="blue", cursor="hand2", width=50, anchor="w", relief="sunken")
        self.lbl_config.grid(row=0, column=1, padx=5)
        self.lbl_config.bind("<Button-1>", lambda e: self.browse_config())
        tk.Button(file_frame, text="Browse", command=self.browse_config).grid(row=0, column=2)
        
        # The GDS file is automatically read from the config JSON.
        tk.Label(file_frame, text="GDS File (from config):").grid(row=1, column=0, sticky="e", pady=5)
        self.lbl_gds = tk.Label(file_frame, text="No file selected", fg="gray", width=50, anchor="w")
        self.lbl_gds.grid(row=1, column=1, padx=5, pady=5, columnspan=2)
        
        # Frame for Parameters
        self.param_frame = tk.LabelFrame(self, text="Parameters (Auto-loaded from Config)", padx=10, pady=10)
        self.param_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        self.param_vars = {}
        self.tj_vars = []
        tk.Label(self.param_frame, text="Please select a config.json to load parameters.").pack(pady=20)
        
        # Run button
        self.btn_run = tk.Button(self, text="Generate Database", command=self.run_generation, bg="#2ecc71", fg="white", font=("Arial", 12, "bold"))
        self.btn_run.pack(pady=10, ipadx=20, ipady=5)
        
        # Log console
        log_frame = tk.LabelFrame(self, text="Console Output")
        log_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        self.txt_log = tk.Text(log_frame, height=10, state="disabled", bg="#f4f4f4")
        self.txt_log.pack(fill="both", expand=True)
        
    def log(self, msg):
        self.txt_log.config(state="normal")
        self.txt_log.insert(tk.END, msg + "\n")
        self.txt_log.see(tk.END)
        self.txt_log.config(state="disabled")
        self.update()

    def browse_config(self):
        path = filedialog.askopenfilename(title="Select config.json", filetypes=[("JSON Files", "*.json")])
        if path:
            self.config_path = path
            self.lbl_config.config(text=path[-50:]) # Truncate for display
            self.load_config()



    def load_config(self):
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                self.config_data = json.load(f)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load JSON: {e}")
            return
        
        gds = self.config_data.get("script_parameters", {}).get("input_gds_file", "No file selected")
        self.lbl_gds.config(text=gds[-50:] if len(gds) > 50 else gds)
        
        self.build_param_ui()
        self.log(f"Loaded config: {Path(self.config_path).name}")

    def build_param_ui(self):
        # Clear existing
        for widget in self.param_frame.winfo_children():
            widget.destroy()
            
        params = self.config_data.get("script_parameters", {})
        
        row = 0
        
        # Label Prefix
        tk.Label(self.param_frame, text="Label Prefix:", font=("Arial", 10, "bold")).grid(row=row, column=0, sticky="e", pady=2)
        var_prefix = tk.StringVar(value=params.get("label_prefix", ""))
        tk.Entry(self.param_frame, textvariable=var_prefix, width=30).grid(row=row, column=1, sticky="w", padx=5, pady=2)
        self.param_vars["label_prefix"] = var_prefix
        row += 1
        
        tk.Label(self.param_frame, text="--- Layer Numbers (GDS Datatype is assumed 0) ---", fg="gray").grid(row=row, column=0, columnspan=2, pady=10)
        row += 1
        
        # Helper to extract the first int (the layer)
        def get_layer(key):
            v = params.get(key, [0, 0])
            return str(v[0]) if isinstance(v, list) and len(v) > 0 else "0"
            
        # Standard Layers
        for key, display in [("input_layer", "Input Layer (Samples):"), 
                             ("resistance_layer", "Resistance Layer:"), 
                             ("text_layer", "Text Layer:")]:
            tk.Label(self.param_frame, text=display).grid(row=row, column=0, sticky="e", pady=2)
            var = tk.StringVar(value=get_layer(key))
            tk.Entry(self.param_frame, textvariable=var, width=10).grid(row=row, column=1, sticky="w", padx=5, pady=2)
            self.param_vars[key] = var
            row += 1
            
        # Test Junction Layers
        tjs = params.get("test_junctions", [])
        self.tj_vars = []
        for i, tj in enumerate(tjs):
            name = tj.get("name", f"TJ_{i}")
            layer_val = str(tj.get("layer", [0,0])[0])
            
            tk.Label(self.param_frame, text=f"Test Junction '{name}':").grid(row=row, column=0, sticky="e", pady=2)
            var = tk.StringVar(value=layer_val)
            tk.Entry(self.param_frame, textvariable=var, width=10).grid(row=row, column=1, sticky="w", padx=5, pady=2)
            self.tj_vars.append((i, var))
            row += 1
            
        tk.Label(self.param_frame, text="--- Generation Options ---", fg="gray").grid(row=row, column=0, columnspan=2, pady=10)
        row += 1
        
        self.var_export_gds = tk.BooleanVar(value=params.get("export_gds_with_text", True))
        tk.Checkbutton(self.param_frame, text="Create new GDS with text labels", variable=self.var_export_gds, font=("Arial", 9, "bold")).grid(row=row, column=0, columnspan=2, sticky="w", padx=20, pady=5)
        row += 1

    def run_generation(self):
        if not self.config_path:
            messagebox.showwarning("Warning", "Please select a config.json first.")
            return
            
        # Update config dict from UI
        params = self.config_data.get("script_parameters", {})
        params["label_prefix"] = self.param_vars["label_prefix"].get()
        params["export_gds_with_text"] = self.var_export_gds.get()
        
        try:
            if "input_layer" in params:
                params["input_layer"][0] = int(self.param_vars["input_layer"].get())
            if "resistance_layer" in params:
                params["resistance_layer"][0] = int(self.param_vars["resistance_layer"].get())
            if "text_layer" in params:
                params["text_layer"][0] = int(self.param_vars["text_layer"].get())
            
            for i, var in self.tj_vars:
                params["test_junctions"][i]["layer"][0] = int(var.get())
        except ValueError:
            messagebox.showerror("Error", "All layer values must be integers.")
            return
            
        # Save back to file
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.config_data, f, indent=2)
            self.log("Config updated & saved.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save config: {e}")
            return
            
        self.btn_run.config(state="disabled", text="Generating...", bg="gray")
        self.log("-" * 40)
        self.log("Starting database generation...")
        
        # Run in thread so GUI doesn't freeze
        threading.Thread(target=self._run_thread, daemon=True).start()
        
    def _run_thread(self):
        class StdRedirector:
            def __init__(self, log_func):
                self.log_func = log_func
            def write(self, text):
                if text.strip():
                    self.log_func(text.strip())
            def flush(self):
                pass
                
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = StdRedirector(self.log)
        sys.stderr = StdRedirector(self.log)
        
        try:
            generate_from_config(self.config_path)
            self.log("\n✅ GENERATION COMPLETE")
        except Exception as e:
            self.log("\n❌ ERROR during generation:")
            self.log(traceback.format_exc())
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            # Re-enable button
            self.btn_run.config(state="normal", text="Generate Database", bg="#2ecc71")

if __name__ == "__main__":
    app = DatabaseGUI()
    app.mainloop()
