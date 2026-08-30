# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for IP_CHANGER.

Produces a single-file, windowed (no console) executable that requests
Administrator elevation through its manifest (specification section 48).

Build with:
    python build/build.py
or directly:
    pyinstaller --clean --noconfirm build/ip_changer.spec
"""

import sys
from pathlib import Path

# SPECPATH is injected by PyInstaller and points at this file's directory.
PROJECT_ROOT = Path(SPECPATH).resolve().parent

sys.path.insert(0, str(PROJECT_ROOT))
from app import __version__  # noqa: E402

ICON = PROJECT_ROOT / "assets" / "ip_changer.ico"
VERSION_FILE = PROJECT_ROOT / "build" / "version_info.txt"

a = Analysis(
    [str(PROJECT_ROOT / "app" / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[(str(ICON), "assets")] if ICON.exists() else [],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Qt modules the application never uses; excluding them keeps the
    # executable substantially smaller and the build reproducible.
    excludes=[
        "tkinter",
        "unittest",
        "pydoc",
        "pytest",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtQuick",
        "PySide6.QtQml",
        "PySide6.Qt3DCore",
        "PySide6.QtMultimedia",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtOpenGL",
        "PySide6.QtSql",
        "PySide6.QtTest",
        "PySide6.QtBluetooth",
        "PySide6.QtPositioning",
        "PySide6.QtSerialPort",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="IP_CHANGER",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    # No console window (section 48).
    console=False,
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Request elevation via the embedded manifest (section 21).
    uac_admin=True,
    uac_uiaccess=False,
    icon=str(ICON) if ICON.exists() else None,
    version=str(VERSION_FILE) if VERSION_FILE.exists() else None,
)
