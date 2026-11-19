"""Keithley 2450 source-meter driver (delta-method resistance) and a background measurement loop."""
from __future__ import annotations

import random
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

DEFAULT_RESOURCE = "USB0::0x05E6::0x2450::04644450::INSTR"


@dataclass
class SMUConfig:
    resource: str = DEFAULT_RESOURCE
    simulate: bool = False


def open_resource_manager():
    """System VISA (NI-VISA/Keysight) if installed, otherwise the pure-Python backend."""
    import pyvisa
    try:
        return pyvisa.ResourceManager()
    except Exception:
        return pyvisa.ResourceManager("@py")


def list_resources() -> List[str]:
    try:
        rm = open_resource_manager()
        try:
            return list(rm.list_resources())
        finally:
            rm.close()
    except Exception:
        return []


class Keithley2450:
    def __init__(self, cfg: SMUConfig):
        self.cfg = cfg
        self.rm = None
        self.inst = None
        self.lock = threading.RLock()
        self.idn = ""
        self._configured = False
        self._last_nplc: Optional[float] = None

    # -------------------------------------------------------------- session
    def connect(self):
        with self.lock:
            if self.cfg.simulate:
                self.idn = "SIMULATED 2450"
                return
            if self.inst is not None:
                return
            self.rm = open_resource_manager()
            self.inst = self.rm.open_resource(self.cfg.resource)
            self.inst.timeout = 5000
            try:
                self.idn = self.inst.query("*IDN?").strip()
            except Exception:
                self.idn = "UNKNOWN"

    def setup(self, nplc: float):
        with self.lock:
            self.connect()
            if self.cfg.simulate:
                self._configured = True
                self._last_nplc = nplc
                return
            if not self._configured:
                inst = self.inst
                inst.write("*RST")
                inst.write(":SOUR:FUNC CURR")
                inst.write(':SENS:FUNC "VOLT"')
                inst.write(":SENS:VOLT:RANG:AUTO ON")
                inst.write(":SOUR:CURR:RANG:AUTO ON")
                try:
                    inst.write(":FORM:ELEM VOLT")
                except Exception:
                    pass
                inst.write(":SOUR:CURR 0")
                inst.write(":OUTP OFF")
                self._configured = True
                self._last_nplc = None
            if self._last_nplc is None or abs(self._last_nplc - nplc) > 1e-12:
                self.inst.write(f":SENS:VOLT:NPLC {float(nplc)}")
                self._last_nplc = nplc

    def reconfigure(self, cfg: SMUConfig):
        with self.lock:
            if cfg == self.cfg:
                return
            self.close()
            self.cfg = cfg

    def output_off(self):
        with self.lock:
            if self.inst is not None:
                try:
                    self.inst.write(":OUTP OFF")
                    self.inst.write(":SOUR:CURR 0")
                except Exception:
                    pass

    def close(self):
        with self.lock:
            self.output_off()
            for obj in (self.inst, self.rm):
                try:
                    if obj is not None:
                        obj.close()
                except Exception:
                    pass
            self.inst = self.rm = None
            self._configured = False
            self._last_nplc = None
            self.idn = ""

    # -------------------------------------------------------------- measure
    @staticmethod
    def _first_float(resp: str) -> float:
        m = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", resp)
        if not m:
            raise ValueError(f"Could not parse numeric value from response: {resp!r}")
        return float(m.group(0))

    def measure_r_delta(self, current_a: float, settle_s: float = 0.02) -> float:
        """Delta method: R = (V(+I) - V(-I)) / 2I, which cancels thermal/offset voltages."""
        if current_a == 0.0:
            raise ValueError("Excitation current must be non-zero")
        if self.cfg.simulate:
            time.sleep(0.05)
            return 7000.0 * (1 + random.gauss(0, 0.002))
        with self.lock:
            self.connect()
            inst = self.inst
            try:
                inst.write(":OUTP ON")
                inst.write(f":SOUR:CURR {+current_a}")
                time.sleep(settle_s)
                vp = self._first_float(inst.query(":READ?"))
                inst.write(f":SOUR:CURR {-current_a}")
                time.sleep(settle_s)
                vm = self._first_float(inst.query(":READ?"))
            finally:
                try:
                    inst.write(":SOUR:CURR 0")
                    inst.write(":OUTP OFF")
                except Exception:
                    pass
            return abs((vp - vm) / (2 * current_a))


class MeasurementLoop:
    """
    Runs measurements in a background thread. Results are delivered through callbacks
    (the GUI connects them to Qt signals, which are thread-safe).
    """

    def __init__(self, smu: Keithley2450,
                 on_point: Callable[[float, float], None],
                 on_status: Callable[[str], None]):
        self.smu = smu
        self.on_point = on_point
        self.on_status = on_status
        self.current_ua = 0.02
        self.nplc = 1.0
        self.interval_s = 1.0
        self.settle_s = 0.02
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._t0 = 0.0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._t0 = time.time()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, wait: bool = True):
        self._stop.set()
        if wait and self._thread is not None:
            self._thread.join(timeout=10)
        self.smu.output_off()

    def measure_once(self) -> Tuple[float, float]:
        self.smu.setup(self.nplc)
        r = self.smu.measure_r_delta(self.current_ua * 1e-6, self.settle_s)
        return time.time() - self._t0, r

    def _run(self):
        try:
            self.smu.setup(self.nplc)
            self.on_status(f"Connected: {self.smu.idn}")
        except Exception as e:
            self.on_status(f"Connection error: {e}")
            return
        while not self._stop.is_set():
            start = time.time()
            try:
                t, r = self.measure_once()
                self.on_point(t, r)
                self.on_status(f"{self.smu.idn} · I = {self.current_ua:g} µA · NPLC {self.nplc:g}")
            except Exception as e:
                self.smu.output_off()
                self.on_status(f"Measurement error: {e}")
                self._stop.wait(1.0)
            self._stop.wait(max(0.0, self.interval_s - (time.time() - start)))
        self.smu.output_off()
