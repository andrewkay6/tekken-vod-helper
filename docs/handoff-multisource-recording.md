# Handoff: record game, webcam and overlay separately, composite in post

## Checkpoint — 2026-11-23 (streaming PC session, headless)

Done without anyone at the PC. Production OBS settings unchanged (backup taken first:
`C:\Users\KWTekken\tvh-jobs\obs-studio-backup-20261123-190955.zip`). Nothing was streamed.

- **Long-run sync (B1).** 12 s stream-copy clips cut with `-copyts` at 4 s and 586 s from the 600 s
  stream-and-separate-feeds run, so PTS stay on the original timeline. Script and results:
  `build/work/obs-synthetic-stress-20261120/late-clips/check_early_late.py` and `early-late-clocks.json`.
  Offset vs the main recording, early -> late: clean game +83.4 -> +83.4 ms; received stream +463.3 -> +463.5 ms;
  camera +167 -> +117 ms. So main, clean game and stream hold a constant offset over ~10 minutes (all three share
  a +33 ms common-mode shift against the synthetic source, which is the source's timing, not the outputs').
  The camera feed (frame-rate divisor 2) moved **-50 ms**, about 1.5 of its frames, with ~34 ms marker spread
  per clip. The compositor must re-anchor the camera periodically, or accept sub-2-frame lip-sync error per
  10 minutes. Early offsets match the 2026-11-21 short-sample check.
- **GPU (B3).** NVIDIA GeForce GTX 1660 Ti, Turing, 6 GB, driver 610.88 (CUDA 13.3), VBIOS 90.16.20.40.7b.
  Idle: 0 active encoder sessions.
- **WebSocket reachability (B2).** Production OBS WebSocket listens on 4455 with **authentication off**; TCP
  4455 is reachable from the devbox (192.168.0.199) through the Windows firewall. No firewall change made.
- **Real checkpoint collection (B2).** Production OBS launched in the console session via a one-shot schtasks
  /IT task. A 3-minute local recording only (`C:\Users\KWTekken\Videos\2026-11-23 19-15-42.mkv`, 180.4 s),
  started and stopped over WebSocket by a driver script; the collector itself only sent `GetRecordStatus`
  (180 samples, 1 s interval, exit 0, `sample_limit_reached`). Four scoreboard changes were written through the
  real `OverlayServer` history writer into a job folder (not the production history), 20/50/90/140 s after
  StartRecord was sent. Evidence: `build/work/b2-checkpoints-20261123/`.
  - `outputDuration` vs wall clock: rate 0.999999, residual max 18 ms (stdev 5 ms). The recording started
    0.40 s after StartRecord was sent.
  - Mapped score changes: 19.65 / 49.63 / 89.61 / 139.62 s, i.e. each wall time minus that 0.4 s start lag.
    Consistent.
  - Gap found: run without `--calibration-error-seconds`, the checkpoints carry no uncertainty and the mapper
    returns `capture_clock_not_calibrated` for every event (no seconds at all). The numbers above come from a
    re-map of a copy with a 0.05 s bound (`checkpoints-bound-0.05.json`, `mapped-bound-0.05.json`). For event
    use, pass `--calibration-error-seconds 0.05` (or a measured bound) to the collector.
  - Not covered: TVH itself was not running, so the overlay's appearance in OBS and a score change through
    the app UI remain for the in-person smoke test.
- **Deferred:** Source Record in production and the real-device dry run (B4). Andy noted streaming and
  recording will sometimes happen together; the synthetic benchmark covered that (82.5% mean / 85% peak
  encoder, 0 skips), but headroom is thin and B4 is the real test with the capture card, webcam and internet
  upload.

## Checkpoint — 2026-11-21

**The streaming PC can be powered down.** The synthetic OBS benchmark is complete. Required telemetry,
logs, frame samples and short video samples are on the devbox under
`build/work/obs-synthetic-stress-20261120/`. The portable OBS instance, temporary SSH tunnel, synthetic RTMP
receivers and test scheduled tasks were closed or removed. No live broadcast was sent. Six production OBS
configuration files were hash-checked with **zero changes**; Source Record was installed only in the portable
test copy, not in the production OBS installation.

Tested OBS 32.1.2 with Source Record 0.4.8 on the GTX 1660 Ti (6 GB). Sources were generated patterns with
frame numbers and flash/beep markers. Streaming used a 6 Mbps H.264 loopback RTMP receiver; the main recording
used the existing HEVC CQP 25 / p7 configuration. Extra recordings used HEVC p5, CQP 18 for clean game and
CQP 20 for camera, with the camera encoder set to a frame-rate divisor of two.

| Synthetic workload | Duration | Mean OBS CPU | Mean encoder utilization | OBS render/encode skips |
|---|---:|---:|---:|---:|
| Main recording only | 2 minutes | 5.1% | 51.6% | 0 / 0 |
| Main recording + stream | 2 minutes | 5.5% | 60.4% | 0 / 0 |
| Above + clean game + camera | 10 minutes | 8.5% | 82.5% | 0 / 0 |

Both streaming stages transmitted real bytes to local receivers, with no reconnecting state at the final
sample and zero reported network drops. The full stage held 59.94 fps; steady-window encoder utilization
peaked at 85%, GPU render utilization at 18%, and temperature at 58 C. This measures synthetic-scene capacity,
not capture-card/USB reliability or internet upload stability. The two baselines were retained across a
test-only watchdog restart; the final ten-minute stage was uninterrupted.

### Qwen loop queued — 2026-11-21

Andy authorized proceeding with FFmpeg in the Qwen loop. **No sandbox architecture, host package,
service or global profile change was needed.** The existing `repo-python-v1` path supports native guest
packages through `dependencies.debian: ["ffmpeg"]`. Declaring this also makes the `ffmpeg` capability
available for that job. The separate `media-ffmpeg-v1: false` flag does not prohibit this supported path;
previous notes treating that flag as a blanket FFmpeg blocker were incorrect.

- Task: `task-2a6c3e73eabd40b58606687c`.
- Saved loop: `/srv/projects/ai-sandbox-dev/review-runs/tvh-recording-checkpoints-20261121`.
- Qwen implements; Codex reviews. It is queued after the active `tvh-fullstack-packet11-20261121` loop,
  using the existing FIFO and shared Qwen lease. No second inference job was started.
- First scope: persistent, read-only OBS status polling and checkpoint collection directly consumable by
  the offline mapper, with explicit calibration uncertainty, safe partial output, restart/clock guards and
  synthetic tests. Existing captured files must stay byte-identical. Compositing is a later task.
- The sandbox accepted the reviewed plan as ready, with no missing capabilities. **Native FFmpeg execution
  in this new job is still pending.** Baseline/final/fresh-verifier checks require FFmpeg, ffprobe, libx265,
  actual synthetic HEVC repair tests and zero skipped media tests; acceptance is not decode proof.
- Source packet: `build/work/multisource-qwen-loop-20261121/`. Twelve selected source/test files were
  attached after checking configured secret values; no credentials, footage, private logs or host settings
  were attached. Public internet is off; the configured package gateway handles the declared guest dependency.
- Outputs remain sandbox review artifacts. There is no automatic host apply, commit, push or deployment.
  Existing local work and unrelated skill/Discord changes remain untouched. The streaming PC can stay off.

Inspect with:

```bash
python3 -B /srv/projects/ai-sandbox-dev/review_loop.py status /srv/projects/ai-sandbox-dev/review-runs/tvh-recording-checkpoints-20261121
```

### Local follow-up completed — 2026-11-21

- **Reusable metadata repair:** `scripts/repair_recording_metadata.py` inspects or writes a separate HEVC/MKV
  copy, with an explicitly supplied intended rate. It verifies every decoded picture, per-stream presentation
  timestamps, non-video packet payloads, and the unchanged original before publishing the copy and a JSON receipt.
  It refuses existing output/receipt paths and rejects timestamp rounding by FFmpeg. The original sample's 421
  pictures passed, with corrected `30000/1001` rate fields. The container duration changes by about 17 ms because
  the nominal final-frame duration is corrected. This is an offline tool; it is not yet called by the event pipeline.
- **One pre-existing camera gap:** the copied camera sample has one unfilled presentation slot at its intended
  rate. The repair preserves and reports it. This is separate from OBS's zero render/encoding skip counters;
  the sample does not establish the gap's cause.
- **Timestamp-based short-sample check:** `short-sample-clocks.json` compares visible synthetic frame markers
  against decoded presentation timestamps, with no output-frame duplication from the incorrect camera header.
  In the approximately fourteen-second prefixes, main, clean game and received stream each have less than 1 ms
  of marker-origin range. Camera has about 50 ms. Relative median marker phases are approximately +83 ms clean,
  +167 ms camera and +463 ms received stream versus main. These are phases of independently looping synthetic
  sources, modulo their 10.01-second loop; they are **not calibrated production source offsets**.
- **Shared offline time mapper:** `tekken_vod_helper/recording_clock.py` and `scripts/map_overlay_history.py`
  map overlay epochs using saved checkpoints with explicit recording IDs and calibration uncertainty. They
  preserve an interval when a pause's location is unknown, refuse extrapolation/restarts, and leave uncalibrated
  clocks unknown. Independent sources require measured offsets; mapping after a possible main-recording pause
  is refused unless shared pause behavior is explicitly established. A constant offset does not correct drift.
  Checkpoint collection and OBS/app integration remain to be built.
- **Validation:** 33 targeted tests pass, including real FFmpeg repair, picture/timestamp preservation,
  timestamp-rounding rejection, overwrite/publication races, pause ambiguity, restart guards and the mapper CLI.
  The source and tests are local, uncommitted work. Unrelated skill/Discord edits were left untouched.

Example inspection and repair (only after the recording has finished):

```bash
.venv/bin/python scripts/repair_recording_metadata.py camera.mkv --expected-fps 30000/1001 --inspect
.venv/bin/python scripts/repair_recording_metadata.py camera.mkv camera-repaired.mkv --expected-fps 30000/1001
```

The mapper CLI takes checkpoint JSON, overlay JSONL, a new output report path and `--recording-id`.
Its module/CLI docstrings describe the checkpoint and offset schema. An unknown capture/encoder latency is
not replaced with zero or bounded by WebSocket round-trip time alone. It writes an offline report and does
not alter overlay history, operator marks, recordings, or app state.

### Findings that remain open

- **Synchronization is not accepted.** The previous early/late PNG calculation mixed seeking and container
  endpoint assumptions; its 0.1–0.4 second residuals are not a validated ten-minute drift measurement. The new
  actual-PTS check covers only short prefixes and still shows camera timing variation. Next time the PC is on,
  export precise late clips with their original timestamps from the existing full recordings, or copy the full
  files, and compare them with the early clips. No benchmark rerun is needed just to obtain that evidence.
  Do not use the camera's WebSocket duration directly: with the frame-rate divisor it reports half time.
- **Quality is not certified.** The sampled files decode, but the experimental SSIM comparisons include
  alignment and color-conversion effects. Raw values are in `media-analysis.json`; they are not a gameplay
  quality acceptance test. Real game/camera capture and perceptual review remain necessary.

### Resume here

1. Review the local repair/mapper code, integrate a checkpoint collector and persistent OBS client, then build
   the compositing command builder. This code work needs no streaming PC. Establish capture-clock and source
   offset calibration before using mapped times for final output.
2. The client/collector loop above is queued with guest FFmpeg; the compositor remains a later VM task.
   Hardware benchmarks stay on the streaming PC. Long-run sync needs precise late media from its existing
   recordings; no private footage was attached to the queued job.
3. Install/configure Source Record in the production OBS setup only when ready, then run a real-device dry
   run with the capture card and webcam, plus an explicitly chosen private streaming destination. The existing
   production profile and webcam scale filtering have not been changed by this benchmark.
4. Live boundary work and the HUD-masking backtest are deferred behind separate feeds, per Andy's direction.

The small CPU-only analysis packet is `build/work/obs-synthetic-stress-20261120/vm-packet/packet.json`.
It includes sanitized counters, GPU measurements, analysis code and six passing unit tests; no credentials,
media, host connection details, or network access are required. That earlier analysis packet was not
submitted. The new source-only checkpoint loop above is queued separately; the analysis is already complete.

Primary evidence: `run.json`, `analysis.json`, `pc-results/file-checks/files.json`, `media-analysis.json`,
`camera-metadata-repair.json`, `camera-verified-tool-repair-report.json`, `short-sample-clocks.json`,
and `pc-results/production-config-check.json` in the run directory.
Benchmark scripts/evidence are gitignored. Unrelated in-progress skill and Discord changes were left untouched.

## Earlier planning notes (2026-11-20)

The older estimates and blockers below are retained as history; the measurements above supersede the GPU
capacity estimates. Production deployment and real-device validation remain unfinished.

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
- **Superseded FFmpeg blocker:** `media-ffmpeg-v1: false` was previously read as a blanket media limitation.
  The existing repository profile can declare FFmpeg as a guest dependency; see the queued-loop checkpoint above.
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
