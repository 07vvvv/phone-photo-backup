# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['app.pyw'],
    pathex=[],
    binaries=[('bin/adb.exe', 'bin'), ('bin/AdbWinApi.dll', 'bin'), ('bin/AdbWinUsbApi.dll', 'bin')],
    datas=[('bin/NOTICE.txt', 'bin'), ('assets/app.ico', 'assets')],
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
    a.binaries,
    a.datas,
    [],
    name='PhonePhotoBackup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir='.',
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/app.ico',
)
