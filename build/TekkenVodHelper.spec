# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_root = Path(SPECPATH).parent
if project_root.name == "build":
    project_root = project_root.parent

datas = [
    (str(project_root / "tekken_vod_helper" / "characters.txt"), "tekken_vod_helper"),
]

portrait_dir = project_root / "portraits"
if portrait_dir.exists():
    datas.append((str(portrait_dir), "portraits"))

a = Analysis(
    [str(project_root / "tekken_vod_helper" / "__main__.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
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
    name="TekkenVodHelper",
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
