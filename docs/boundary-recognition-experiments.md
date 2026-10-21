# Boundary Recognition Experiments

## Status
Resumed work on 2026-10-19. Original implementation stopped too soon due to insufficient accuracy (0/1 BB7, 1/2 BB5).

## Visual Audit (Development)
Independent coordinator visual audit on 23 state-stratified images. 21 labelled (2 ambiguous initially excluded).
- **Label Agreement:** Exact 16/21 (2 errors due to OS assignment schema distinction previously missing).
- **Updated Scenes:** Full 18/21, Cropped 19/21, Final Calibrated Thinking-Enabled Crop 19/21.
- **Discrepancies:** Remaining 2 black frames labelled `stage_intro` rather than `unknown`.
- **Roster Issues:** Red placeholder character roster mistaken for `stage_select` originally. Crops rejected as `unknown`/`character_select`, v5 `stage_intro` (still wrong semantic class but not false start).
- **Note:** Audits are for development, not general accuracy.

## Thinking & Vision Trials
- **Thinking Disabled:** Vision trial produced malformed JSON on second batch; rejected for production profile.
- **Thinking Enabled:** Retained. Verified helpful for bounded implementation packets. No claim of coordinator token savings numeric.

## OCR Prototype
Built-in Windows OCR prototype on 46 regions from 23 frames. No API calls/node dependency install. Actual text recovery weak (some player names, 'Press', missing full control text/scores). Not adopted as boundary authority.
- **Ignored Artifacts:** `build/work/qwen/ocr_windows_frames.ps1`, `run_ocr_audit.py`, `build/work/boundary-evaluation/recognition-audit-v1/ocr-raw.json`.

## BB4 Classifier Audit (Unseen)
14 state-stratified frames from first 108 observations. Predictions hidden while coordinator labelled: **12/14**.
- **Errors:** READY HUD called intro, fading character select called VS.
- **Correct:** Sampled true stage-select/controller-prompt cases.
- **Constraint:** No tuning after BB4 profile freeze. No aggregate event accuracy claim yet.

## Backtest Performance (Fresh BB4 First 1800s)
Currently **RUNNING**. No invented sets/metrics.
- **Config:** 1s frame cache, 1799 actual PTS frames, 5s coarse, 1s refine, max 600 model frames, batch 12, max 100 shared model calls, reserve 1 temporal, crops=True, thinking=True, temporal=True.
- **Labels:** Loaded after matches persist.
- **Frame Cache Fix:** Real fractional NTSC test prevents discarding valid extraction when output-t drops last showinfo frame.
- **Format Measurements (Not General Benchmark):**
  - H264 HLS format: 230 seek 3660s BB7 in 2.05s; direct H264 134 timed out 25s. Prefer `avc1_m3u8_native`.
  - BB7 Fixed Cache: 1199 frames first 1200s completed 30.59 sec.
  - BB4 Cache: 1799 first 1800s completed 91.84 sec.

## Development Temporal Replay
Old classifier observations + new audit patches.
- **BB7:** Candidate 265.265..975.976, TP1/FN0. Historical start 45.596s late/end 13.599s late.
- **BB5:** Uncertain, all 0/2.
- **Note:** Not independent held-out trials.

## Rules & Artifacts
- Rules/prompt fingerprints archived in `rawrunartifacts`.
- Handoff predictions to `apply-matches` now tested. Evidence IDs in adjudication artifact, not `unknownmatch` fields.
- **Tests:** 225 tests passed, 2 known legacy xfail latest.
- **Baseline:** Existing detailed baseline `docs/boundary-backtest-results.md` must remain original baseline.
- **Links:** Relative files/artifacts linked. Distinction between calibration/fresh held-out preserved.

## Final Status
False-start unlocated: gap noplay at 20s sampling. Adjacent 2s diagnostic in progress (not proof). No YouTube upload.