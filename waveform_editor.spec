# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from PyInstaller.utils.hooks import collect_all

# Icon is pre-converted by the build workflow (docs/logo.ico / .icns)
_icon = None
if sys.platform == "win32":
    _icon = os.path.join("docs", "logo.ico")
elif sys.platform == "darwin":
    _icon = os.path.join("docs", "logo.icns")

datas    = [("firmware", "firmware")]
binaries = []
hiddenimports = []

for pkg in ("customtkinter", "esptool"):
    d, b, h = collect_all(pkg)
    datas         += d
    binaries      += b
    hiddenimports += h

a = Analysis(
    ["waveform_editor.py"],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="waveform_editor",
    icon=_icon,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=False,   # no terminal window on Windows
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
