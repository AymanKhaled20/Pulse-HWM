# -*- mode: python ; coding: utf-8 -*-
# Pulse-HWM — PyInstaller spec (onedir, windowed)
# Build:  .venv\Scripts\pyinstaller.exe pulse_hwm.spec

a = Analysis(
    ["pulse_hwm\\__main__.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("pulse_hwm\\assets\\fonts", "pulse_hwm\\assets\\fonts"),
        ("pulse_hwm\\assets\\icons", "pulse_hwm\\assets\\icons"),
        ("pulse_hwm\\ui\\theme.qss", "pulse_hwm\\ui"),
    ],
    hiddenimports=[
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
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pyqtgraph.opengl", "pyqtgraph.examples"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

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
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PulseHWM",
)
