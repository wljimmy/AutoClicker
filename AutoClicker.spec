# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['click_clicker.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
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
    [],
    exclude_binaries=True,
    name='AutoClicker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AutoClicker',
)
app = BUNDLE(
    coll,
    name='AutoClicker.app',
    icon='assets/AutoClicker.icns',
    bundle_identifier='com.bingostudio.autoclicker',
    version='0.5.0',
    info_plist={
        'CFBundleShortVersionString': '0.5.0',
        'CFBundleVersion': '0.5.0',
    },
)
