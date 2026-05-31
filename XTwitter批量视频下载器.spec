# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['x_twitter_downloader.py'],
    pathex=[],
    binaries=[],
    datas=[('推特下载器图标.ico', '.'), ('推特下载器图标_透明.png', '.')],
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
    name='XTwitter批量视频下载器',
    icon='推特下载器图标.ico',
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
