# -*- mode: python ; coding: utf-8 -*-
# Pulse-HWM — PyInstaller spec (onedir, windowed)
# Build:  .venv\Scripts\pyinstaller.exe pulse_hwm.spec

import os
from glob import glob
from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

datas = [
    ("pulse_hwm\\assets\\fonts", "pulse_hwm\\assets\\fonts"),
    ("pulse_hwm\\assets\\icons", "pulse_hwm\\assets\\icons"),
    ("pulse_hwm\\ui\\theme.qss", "pulse_hwm\\ui"),
]
if os.path.isdir("pulse_hwm\\assets\\lhm_runtime"):
    datas.append(("pulse_hwm\\assets\\lhm_runtime", "lhm_runtime"))

# pythonnet / .NET Framework runtime needs its native bootstrap pieces
clr_datas, clr_binaries, clr_hidden = collect_all("clr_loader")
py_datas, py_binaries, py_hidden = collect_all("pythonnet")
datas = datas + clr_datas + py_datas
extra_binaries = []
for entry in clr_binaries + py_binaries:
    if len(entry) == 2:
        extra_binaries.append((entry[0], entry[1], "BINARY"))
    else:
        extra_binaries.append(tuple(entry))
hiddenimports = [
    "wmi",
    "win32api",
    "win32con",
    "pywintypes",
    "winotify",
    "pynvml",
    "psutil",
    "httpx",
    "dotenv",
    "pyqtgraph",
    "clr",
    "clr_loader",
    "pythonnet",
] + clr_hidden + py_hidden

a = Analysis(
    ["pulse_hwm\\__main__.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pyqtgraph.opengl", "pyqtgraph.examples"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

binaries = a.binaries + extra_binaries

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PulseHWM",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="pulse_hwm\\assets\\icons\\pulse.ico",
)
coll = COLLECT(
    exe,
    binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PulseHWM",
)
