# Handoff: streaming PC session (written 2026-11-23)

For a supervised Claude or Codex session. Andy is present and the streaming PC is on. Read `~/AGENTS.md`
first: no sudo, 12-hour times when writing to Andy, and no AI attribution in commits. BB12 is early December
2026, so the event-day items (A) come before the research items (B).

Background: `docs/handoff-outstanding-20261120.md` (items 2, 3, 8) and `docs/handoff-multisource-recording.md`
("Findings that remain open" and "Resume here").

## State at handoff (verified 2026-11-23 evening)

- `ssh kwtekken` works (host DESKTOP-KKB9BGD). The PC repo at `C:\Users\KWTekken\Documents\Apps\tekken-vod-helper`
  is on `wip/devbox-migration-20261107T023740Z` at `b1a56ec` and clean. OBS was not running.
- The devbox repo `/srv/projects/tekken-vod-helper` is on the same branch, with **uncommitted** work:
  - recording tools: `tekken_vod_helper/recording_clock.py`, `recording_media.py` and `recording_checkpoints.py`;
    `scripts/map_overlay_history.py`, `repair_recording_metadata.py` and `collect_recording_checkpoints.py`;
    and their three test files. `recording_checkpoints.py` and the collector came from the approved sandbox loop
    `review-runs/tvh-recording-checkpoints-20261121`, which Codex approved in round 2. They were applied on
    2026-11-23, and 138 of 138 tests pass on the devbox.
  - unrelated edits to the skill, the handoff docs and Discord notify.
  Ask Andy before committing. If he agrees, commit the recording tools as one commit and the Discord change
  separately, then push. The PC needs the push (or an scp of the files) before step B2 can run there.
- Synthetic OBS benchmark recordings are still on the PC under
  `C:\Users\KWTekken\tvh-jobs\obs-synthetic-stress-20261120\recordings\`. Devbox-side evidence is in
  `build/work/obs-synthetic-stress-20261120/` (gitignored).

## Rules for this session

- Production OBS: do not change the profile, scenes, encoders, plugins or streaming target without Andy saying
  so for that specific change. Back up `%APPDATA%\obs-studio` (a zip with a timestamp) before the first change.
- Never start a public broadcast. Any streaming test uses a private or unlisted destination that Andy chooses.
- Long jobs on the PC run through `schtasks`, not plain ssh (see `.claude/skills/event-vod/SKILL.md`).
- No credentials in the repo, logs or reports. The OBS WebSocket password comes from an environment variable.

## A. Event readiness (before BB12)

1. **App smoke test (Andy at the PC).** Launch TVH and check that the overlay renders in OBS. Use the start.gg
   fetch and confirm that BB12 is listed (this tests `c656e8f`, admin-tournament listing). Change a score once and
   confirm that a new line appears in `~/Videos/tvh-overlay-history/overlay-YYYY-MM-DD.jsonl`. Record the result
   in `handoff-outstanding-20261120.md` item 2.
2. **Webcam scaling fix.** With Andy's OK, set the webcam source's Scale Filtering to **Area** (or Lanczos) in
   OBS. Back up first. Andy judges the before/after by eye.
3. **PC test collision (optional, small).**
   `tests/test_app_export_jobs.py::test_metadata_folder_loads_sidecars_after_export_folder_is_moved` hardcodes a
   real `C:/Users/KWTekken/Videos/oct 01_matches/...` path. Change it to a path that cannot exist (under
   `tmp_path / "gone"`). Make the fix on the devbox, commit it, then pull on the PC.
4. **Cleanup, only once Andy confirms:** delete the PC's `pc-main-backup-20261120` branch and `stash@{0}`.
   Both are already contained in the WIP branch.

## B. Multi-source recording evidence (research; the PC must be on)

1. **Long-run sync evidence, from the existing recordings (no benchmark rerun).** From each full synthetic
   recording (main, clean game, camera, received stream), cut short late clips (for example 10 s near the end)
   with stream copy. Copy them, or the whole files, to the devbox under
   `build/work/obs-synthetic-stress-20261120/late-clips/`. Compare the late visible frame markers against decoded
   PTS, the same way the early clips were checked (`check_sample_clocks.py`, which wrote
   `short-sample-clocks.json`). Report the drift between early and late. Do not use the camera's WebSocket
   duration: with the frame-rate divisor it reports half time.
2. **Real checkpoint collection (read-only).** Start OBS (production install, unchanged). With Andy's OK, start a
   short local recording (a few minutes, not streaming). Run the collector from the PC checkout, with
   `.venv\Scripts\python` and `OBS_PASSWORD` set in the session:
   `scripts/collect_recording_checkpoints.py --output <new path> --recording-id <unique id> --interval 1 --max-samples 300 --password-env OBS_PASSWORD`
   It only sends `GetRecordStatus`. Then run `scripts/map_overlay_history.py` against that day's overlay history
   and check that the mapped offsets make sense against the recording. Also confirm that WebSocket port 4455 is
   reachable from the devbox through the Windows firewall. Report reachability only; do not change firewall rules.
3. **GPU facts for capacity planning:** record `nvidia-smi -q` (model, NVENC sessions, driver) in the
   multi-source handoff. The benchmark recorded a GTX 1660 Ti; confirm it.
4. **Source Record in production (only if Andy chooses to do it now).** Install Source Record 0.4.8, the version
   the benchmark used, in the production OBS. Configure clean game (HEVC p5, CQP 18) and camera (HEVC p5, CQP 20,
   frame-rate divisor 2), as benchmarked. Then do a real-device dry run with the capture card and webcam, all
   recordings on, streaming to Andy's private destination. Watch OBS Stats for "skipped frames due to encoding
   lag". Record the CPU, encoder and skip numbers in the multi-source handoff. If Andy defers, leave production
   untouched.

## Not in scope here

- The compositor and the persistent OBS client for the app. That is devbox and sandbox work and needs no PC.
- Live boundary detection (deferred by Andy behind separate feeds).
- Full-stack migration packets (`/srv/projects/tekken-vod-helper-fullstack`; they run through the sandbox
  loop queue).

## Report back

Update `docs/handoff-multisource-recording.md` (add a dated checkpoint at the top) and
`docs/handoff-outstanding-20261120.md` with what was done, the measurements, and anything left open. Tell Andy,
in a few lines, which steps were done and which were deferred.
