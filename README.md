# Tekken VOD Helper

A small desktop app for turning a local Tekken tournament recording into match clips.

The first version is a manual-review workflow:

- Open a local video file, including MKV.
- Scrub through preview frames, or play the video with sound in the preview pane through VLC.
- Mark where each new match begins.
- Optionally mark explicit match ends.
- Assign player names and characters.
- Set a project event name for exported thumbnails and upload text.
- Export one folder per match with:
  - sliced `.mp4` clip,
  - generated `thumbnail.jpg`,
  - upload-ready `title.txt` and `description.txt`,
  - `match.json` metadata.

## Requirements

- Python 3.8+
- FFmpeg and ffprobe
- VLC media player, for embedded video playback
- Pillow

Install Python dependencies:

```powershell
pip install -r requirements.txt
```

Install FFmpeg separately and either add it to `PATH` or configure `ffmpeg.exe` and `ffprobe.exe` in **Settings**. Install VLC separately so `python-vlc` can load the VLC runtime for embedded playback.

## Run

```powershell
python -m tekken_vod_helper
```

App-level commands live in the menu bar:

- **File**: open video, load/save project, exit.
- **Settings**: configure output override, portraits, FFmpeg paths, and re-encoding.

Use the large **Export Clips** button under the match editor to export the current project.

Set **Event** above the match list to include the event name in thumbnails, metadata, titles, and descriptions. Choose an optional thumbnail background image in **Settings -> Thumbnail background**. Put reusable social links or other upload boilerplate in **Settings -> Description boilerplate**.

## Portable Windows Build

Build scripts live in `build/`. The release package is a portable zip, not an installer:

```powershell
.\build\build.ps1 -Version 0.1.0 -FfmpegDir C:\tools\ffmpeg\bin
```

That creates:

```text
dist\TekkenVodHelper-v0.1.0-windows-portable.zip
```

Upload that zip to a GitHub Release. Users extract it and run the single `TekkenVodHelper.exe`.

Character portraits are embedded in the executable. If `-FfmpegDir` is provided, `ffmpeg.exe` and `ffprobe.exe` are embedded too. VLC media player is still installed separately for embedded playback with sound.

## Portraits

By default, portraits load from the app's bundled `portraits` folder. In development, that is the repository-level `portraits` folder; in an executable build, the portraits are embedded into `TekkenVodHelper.exe`. You can override the folder in **Settings**. If a saved project points to a portrait folder that no longer exists, the app falls back to the bundled portraits.

Portrait filenames should match character names after simple normalization:

- `Jin.png`
- `Devil Jin.jpg`
- `jack-8.webp`

The character selectors are editable, so new characters or local aliases can be typed directly.

## Developer Roster Update

Roster sync is a developer-only workflow. Before building an executable, update the bundled character list and portraits from the command line:

You can also run it from the command line:

```powershell
python scripts/sync_roster.py --download-portraits --portraits portraits
```

The generated portrait filenames are normalized from the visible character names, such as `Devil_Jin.png` and `Armor_King.webp`, so the thumbnail exporter can match them automatically.

For offline use, `scripts/import_roster_html.py` still accepts a saved fighters HTML file.

## Export Notes

By default, clips export next to the source video in a folder named `<video name>_matches`. Set an output override in **Settings** if you want a fixed export location.

Explicit match ends are used when set. If a match has no end marked, export uses the next match start as its end, and the final match uses the video duration.

By default, clips export as `.mp4` using FFmpeg stream copy/remux for speed. Enable **Re-encode for more exact cuts** in **Settings** when starts need to be closer to the exact marked frame or the source codecs cannot be remuxed into MP4.

Each match folder also includes `title.txt` and `description.txt` for manual YouTube uploads without using the YouTube API.

## YouTube 1440p Upscale

To upscale exported clips to 1440p with NVIDIA NVENC before uploading to YouTube:

```powershell
.\scripts\upscale-to-1440p.ps1
```

By default, this reads videos from `C:\Users\ak\Videos` and writes `.mp4` files to `C:\Users\ak\Videos\youtube_1440p`.

Useful options:

```powershell
.\scripts\upscale-to-1440p.ps1 -InputDir "C:\path\to\clips"
.\scripts\upscale-to-1440p.ps1 -InputDir "C:\path\to\clips" -Recurse
.\scripts\upscale-to-1440p.ps1 -Cq 16
.\scripts\upscale-to-1440p.ps1 -NvencPreset p7
```

Lower `-Cq` values produce larger, higher-quality files. The default is `18`.
The default NVENC preset is `p5`, which is faster than `p7` and still a good fit for 1440p YouTube uploads that will be re-encoded.
