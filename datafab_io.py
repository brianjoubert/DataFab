"""Shared JSON helpers for DataFab: lenient config loading and crash-safe writes."""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


def load_json_lenient(json_path: str | os.PathLike) -> dict:
    r"""
    Load JSON, tolerating Windows paths written with single backslashes.

    Both of these work:
      "C:\\Users\\you\\Documents\\input.gds"   (standard JSON)
      "C:\Users\you\Documents\input.gds"       (lenient input)
    """
    text = Path(json_path).read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        def _escape_in_string(m: re.Match) -> str:
            s = m.group(0)
            # Double every backslash, then collapse already-escaped ones back.
            return s.replace("\\", "\\\\").replace("\\\\\\\\", "\\\\")

        fixed = re.sub(r'"(?:[^"\\]|\\.)*"', _escape_in_string, text)
        return json.loads(fixed)


def backup_path(path: str | os.PathLike) -> Path:
    p = Path(path)
    return p.with_name(p.name + ".bak")


def atomic_write_json(path: str | os.PathLike, data: Any, backup: bool = False) -> None:
    """
    Write JSON so that a crash or a dropped network drive never leaves a half-written file:
    the data goes to a temporary file in the same folder, which then replaces the target.
    With backup=True, the previous version is first copied to '<name>.bak'.
    """
    path = Path(path)
    if backup and path.exists():
        shutil.copy2(path, backup_path(path))

    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        # On Windows, antivirus/indexers can briefly lock the target: retry a few times.
        for attempt in range(10):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.2)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def resource_path(relative: str) -> str:
    """Path to a bundled resource, both when run from source and from a PyInstaller exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)
