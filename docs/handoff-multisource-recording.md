# Handoff: record game, webcam and overlay separately, composite in post

Written 2026-11-20 at the end of the BB11 pipeline session. Status: idea checked on paper. A proof of concept was
started and paused. Nothing has been changed on the streaming PC.

## Goal (Andy)

Fix scoreboard mistakes (names, scores, round) and the webcam after the event, instead of living with what went out
live. It was triggered by two things. First, the player cam looks jagged: a high-res webcam is shrunk with OBS's
default bilinear filter. Second, scoreboard and names are wrong now and then.

## Key idea

- **Don't record the overlay.** As of 2026-11-20, `tekken_vod_helper/overlay_server.py` appends every visible
  scoreboard change to `~/Videos/tvh-overlay-history/overlay-YYYY-MM-DD.jsonl` on the streaming PC. Each line has
  wall-clock `at`/`epoch` plus description, subtitle, names, teams and scores; changes to look only (font, colour)
  aren't logged. Tests: `tests/test_overlay_history.py`. **This is committed as `ac6ec78` and is included in the streaming PC's `b1a56ec` checkout.**
  The in-person smoke test still needs to confirm that a score change writes a history line before BB12. The OBS file name is the recording
  start (`2026-11-19 19-26-51.mkv`), so log time minus start time gives the VOD offset. In post, the scoreboard is
  re-rendered from the (corrected) log.
- **Record two extra sources:** a clean game feed (1080p60) and the webcam on its own (720p30 or 1080p30), using OBS's
  **Source Record** plugin. Keep the normal composited recording as a safety copy and for the current pipeline.
- **Composite in the existing re-encode.** The 1440p upscale (`scripts/upscale-to-1440p.ps1` settings) already
  re-encodes everything once. Doing it as game + webcam (scaled once, with a proper filter) + rendered scoreboard ->
  1440p master costs about the same hour of GPU time.

## Cost estimates (from BB11: 2 h 11 min, main recording 28.5 GB HEVC at ~29 Mbit/s)

| Extra | Size per event |
|---|---|
| Clean game 1080p60 | ~25-30 GB |
| Webcam 720p30/1080p30 | ~5-10 GB |
| Overlay | 0 (re-rendered from the log) |

That is about +30-40 GB per event, roughly double the archive, and about 25 more minutes of archive copying.

**Streaming PC load** (Andy's real concern). It's NVENC (video encoder) work, not CPU or 3D:
- Measured 2026-11-20: NVENC upscale ran at ~140 fps at 1440p60 (2.4x real time, including decode and scale), which
  is very roughly ~250 fps of 1080p capacity. **The GPU model is unknown.** Some cards have 2 NVENC engines and some
  have 1.
- Needed: stream and main recording **sharing one encode** (OBS "Same as stream") at 60, plus clean game at 60, plus
  webcam at about 13 1080p-equivalent. That's about **135 fps, roughly half**. Without sharing it's about 195, which
  is tight. Lighter presets for the extra recordings help.
- Disk writes are about 8 MB/s, which is trivial. The PC has ~3 TB free.

## Proof of concept: where it stopped

Working dir `build/work/composite-poc/` (gitignored). Plan: a 60 s stretch of the BB11 Grand Final from the 1440p
master (`/var/lib/ai-sandbox-media/2026-11-19 Basement Brawl 11 Cafe Edition/... [1440p].mp4`, while it still
exists). Render a *corrected* scoreboard (e.g. googy 1 instead of 0) and overlay it exactly over the baked one. Crop
the baked player cam (bottom centre, about x 1040-1520, y 1170-1440 at 1440p), move and resize it with a
Lanczos/area filter, and render.

Blockers hit:
- **Headless Chromium can't run on the devbox.** `~/.cache/ms-playwright/chromium_headless_shell-1254` needs
  `libatk-1.0.so.0`, which isn't installed, and installing it needs admin. The overlay is TVH's own HTML
  (`tekken_vod_helper/overlay_static/`, which polls `state.json` once a second). Options: ask the owner to install
  the Chromium deps; render on the streaming PC (it has a browser and is the source of truth for the look anyway);
  or reimplement the scoreboard in PIL using `overlay_static/assets/scoreboard.svg` and the CSS.
- **The loop VM can't do media work.** `sandbox-task capabilities` shows profile `media-ffmpeg-v1: false`. Enabling
  it is a sandbox architecture change, so it's an owner decision. Qwen there can only write code against
  synthetic tests.
- A true test of webcam quality needs a real separate webcam recording. BB11's cam is baked in at stream quality.

## Immediate webcam fix (no new pipeline needed)

In OBS on the streaming PC: right-click the webcam source -> **Scale Filtering -> Area** (best for big
reductions). Set the capture resolution near the displayed size (Properties -> Resolution/FPS Type: Custom ->
1280x720). Crop with Alt-drag instead of shrinking. TVH's `obs_client.py` could check or set this over OBS
WebSocket the next time the PC is on, if WebSocket is enabled.

## Next steps

1. With the PC plugged in: read the GPU model and NVENC sessions over `ssh kwtekken` (`nvidia-smi -q`). Run a
   10-minute dry run (stream to a private target, plus all recordings) with OBS **Stats** showing "skipped frames
   due to encoding lag", while watching `nvidia-smi`. Zero skipped frames means it's safe.
2. Install Source Record and set up the clean-game and webcam outputs. Check sync between the three files.
3. Build: an overlay-log -> VOD-offset mapper, a scoreboard renderer (choose one of the options above), and an
   ffmpeg compositing step folded into the upscale.

Access notes: the streaming PC is `ssh kwtekken` (<streaming-pc-address>, DHCP). Long jobs go through `schtasks`, not plain
ssh (see memory `streaming-pc-vod-pipeline` and `.claude/skills/event-vod/SKILL.md`).
