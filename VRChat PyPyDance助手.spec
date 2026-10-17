# -*- mode: python ; coding: utf-8 -*-
# Compatibility build entry. Prefer build_windows.ps1 to also collect notices.
# User configuration, custom themes/backgrounds, and cached videos are not bundled.
# The stable build starts with light/dark themes and bundles no personal images.

from pathlib import Path

project_root = Path(SPECPATH)

a = Analysis(
    [str(project_root / 'dance_cache_gui.py')],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / 'README.md'), '.'),
        (str(project_root / 'LICENSE'), '.'),
    ],
    hiddenimports=[],
    hookspath=[str(project_root / 'tools' / 'pyinstaller_hooks')],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PyPyDanceCache',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    uac_admin=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='PyPyDanceCache',
)
