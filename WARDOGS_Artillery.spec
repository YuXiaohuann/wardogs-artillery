# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_dynamic_libs
from PyInstaller.utils.hooks import collect_submodules

binaries = []
hiddenimports = ['numpy._core._exceptions']
binaries += collect_dynamic_libs('numpy')
hiddenimports += collect_submodules('numpy')
hiddenimports += collect_submodules('cv2')


a = Analysis(
    ['gui_app.py'],
    pathex=[],
    binaries=binaries,
    datas=[('ocr_win.ps1', '.'), ('ocr_worker.ps1', '.'), ('samples', 'samples'), ('range_tables.json', '.'), ('config.json', '.'), ('app.ico', '.')],
    hiddenimports=hiddenimports,
    hookspath=[],
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
    a.binaries,
    a.datas,
    [],
    name='WARDOGS_Artillery',
    icon='app.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
