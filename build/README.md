# Windows portable build

This folder contains the repeatable build setup for GitHub Releases.

## Prerequisites

- Python dependencies from `requirements.txt`
- PyInstaller:

```powershell
python -m pip install pyinstaller
```

- Optional: a local FFmpeg folder containing `ffmpeg.exe` and `ffprobe.exe`
- VLC media player remains a separate install for embedded playback with sound

## Build a portable single-exe zip

From the repository root:

```powershell
.\build\build.ps1 -Version 0.1.0 -FfmpegDir C:\tools\ffmpeg\bin
```

The output is:

```text
dist\TekkenVodHelper-v0.1.0-windows-portable\
dist\TekkenVodHelper-v0.1.0-windows-portable.zip
```

The zip is the file to upload to a GitHub Release. It contains a single `TekkenVodHelper.exe` plus a short README.

Portraits are embedded in `TekkenVodHelper.exe`. If you pass `-FfmpegDir`, `ffmpeg.exe` and `ffprobe.exe` are embedded in `TekkenVodHelper.exe` too. If you do not pass `-FfmpegDir`, the portable package is still built, but users must install FFmpeg separately or choose `ffmpeg.exe` and `ffprobe.exe` in Settings.

## GitHub Release

Create a tag and upload the generated zip as a release asset:

```powershell
gh release create v0.1.0 `
  dist\TekkenVodHelper-v0.1.0-windows-portable.zip `
  --title "Tekken VOD Helper v0.1.0" `
  --notes "Portable Windows build. Extract the zip and run TekkenVodHelper.exe."
```

You can also upload the zip manually from the GitHub Releases page.
