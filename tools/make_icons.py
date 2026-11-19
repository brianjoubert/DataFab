"""Render the app icon to build/icons/datafab.png (Linux) and datafab.ico (Windows)."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from gui.icon import render_icon  # noqa: E402

out = Path(__file__).resolve().parents[1] / "build" / "icons"
out.mkdir(parents=True, exist_ok=True)
app = QGuiApplication([])
png = out / "datafab.png"
render_icon(256).save(str(png))
Image.open(png).save(out / "datafab.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
print(f"Icons written to {out}")
