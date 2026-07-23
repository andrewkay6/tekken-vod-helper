# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import os


project_root = Path(SPECPATH).parent
if project_root.name == "build":
    project_root = project_root.parent

datas = [
    (str(project_root / "tekken_vod_helper" / "characters.txt"), "tekken_vod_helper"),
    (str(project_root / "tekken_vod_helper" / "kwtekken-icon.ico"), "tekken_vod_helper"),
    (str(project_root / "tekken_vod_helper" / "kwtekken-icon.png"), "tekken_vod_helper"),
    (str(project_root / "tekken_vod_helper" / "kwtekken-icon-16.png"), "tekken_vod_helper"),
    (str(project_root / "tekken_vod_helper" / "kwtekken-icon-32.png"), "tekken_vod_helper"),
    (str(project_root / "tekken_vod_helper" / "kwtekken-icon-48.png"), "tekken_vod_helper"),
    (str(project_root / "tekken_vod_helper" / "kwtekken-icon-256.png"), "tekken_vod_helper"),
    (str(project_root / "tekken_vod_helper" / "overlay_static"), "tekken_vod_helper/overlay_static"),
]

portrait_dir = project_root / "portraits"
if portrait_dir.exists():
    datas.append((str(portrait_dir), "portraits"))

ffmpeg_dir_value = os.environ.get("TEKKEN_VOD_HELPER_FFMPEG_DIR", "").strip()
if ffmpeg_dir_value:
    ffmpeg_dir = Path(ffmpeg_dir_value)
    datas.extend(
        [
            (str(ffmpeg_dir / "ffmpeg.exe"), "ffmpeg"),
            (str(ffmpeg_dir / "ffprobe.exe"), "ffmpeg"),
        ]
    )

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
    icon=str(project_root / "tekken_vod_helper" / "kwtekken-icon.ico"),
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
