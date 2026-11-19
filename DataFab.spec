# -*- mode: python ; coding: utf-8 -*-
# Windows: one-file DataFab.exe   ·   Linux: one-folder dist/datafab (packaged as .deb)
import sys
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

datas, binaries, hiddenimports = [], [], []
for pkg in ("klayout", "gdsfactory", "kfactory"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
datas += collect_data_files("pyqtgraph")                 # colour maps etc.
hiddenimports += collect_submodules("pyvisa_py")         # VISA backend loaded by name
hiddenimports += ["database_4", "usb.backend.libusb1"]

a = Analysis(
    ["DataFab.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

if sys.platform == "win32":
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name="DataFab",
        console=False,
        icon="build/icons/datafab.ico",
        upx=False,
    )
else:
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name="datafab",
        console=False,
        upx=False,
    )
    coll = COLLECT(exe, a.binaries, a.datas, name="datafab", upx=False)
