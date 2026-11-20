# Handoff: what's outstanding (written 2026-11-20)

For the next agent (Codex). Read `~/AGENTS.md` first. It covers the Qwen permission rules, the admin
boundary (no sudo, do not access the owner's separate admin login), 12-hour times when writing to Andy, and no AI attribution in commits
or PRs. BB12 is about two weeks out (early December 2026).

## Current state (verified 2026-11-20)

**Repo:** `/srv/projects/tekken-vod-helper`, branch `wip/devbox-migration-20261107T023740Z`, pushed to GitHub at
`b1a56ec`. Recent commits:
- `ac6ec78`: overlay history log. `overlay_server.py` writes `~/Videos/tvh-overlay-history/overlay-YYYY-MM-DD.jsonl`.
- `f2260ca`: Bob and ROGER Jr. added to `characters.txt`.
- `c656e8f`: start.gg fetch lists tournaments the token *administers*, not just ones it owns. This was rescued from
  a commit that only existed on the streaming PC.
- `b1a56ec`: `test_overlay_integration.py` paging test updated to the admin query shape.

**Devbox tests:** the full suite passes in the main checkout (1,605 passed). In a clean checkout, 7 test files error
at collection because they read gitignored `build/work/bb9/...` data. That's expected.

**Streaming PC** (`ssh kwtekken`; repo at `C:\Users\KWTekken\Documents\Apps\tekken-vod-helper`):
- It's now on the same WIP branch at `b1a56ec`, tracking origin. Its `.venv` is unchanged, and `requirements.txt`
  didn't change.
- The roster sync was run there: 43 characters, and all portraits were redownloaded, including Bob and ROGER Jr.
- Backups of its old state: branch `pc-main-backup-20261120` (old `main` at `c2298b0`), plus `stash@{0}`
  ("PC staged edits before switching to wip"). Everything in both is already on the WIP branch. Delete them once
  Andy is happy.
- PC test results: 877 passed. The 74 failures and 41 errors are boundary research tests that need devbox-only
  `build/work` data, plus one test-environment collision (see item 3).

## Outstanding, roughly in priority order

1. **Devbox work backed up in logical commits (2026-11-20).** The previously uncommitted boundary, review,
   export, thumbnail, archive, upload and Discord work is now committed. The event workflow and handoff docs
   are included in the final documentation commit. Local screenshots, credentials, OAuth files, upload sessions
   and generated media remain excluded. Private network details in these docs use placeholders; consult the
   local configuration for actual connection details. The streaming PC remains at `b1a56ec` until updated.
   Validation: see the commit preparation update below.
2. **App smoke test on the streaming PC (Andy, in person):** launch TVH, check the overlay works, and use the
   start.gg tournament fetch to confirm BB12 shows up (this tests `c656e8f`). Change a score once and check that
   `~/Videos/tvh-overlay-history/` gets a line. Not done yet. Only the automated tests have run there.
3. **Test collision on the PC (small, optional):**
   `tests/test_app_export_jobs.py::test_metadata_folder_loads_sidecars_after_export_folder_is_moved` hardcodes
   `C:/Users/KWTekken/Videos/oct 01_matches/...youtube.json`, and that real file exists on the PC, so the test reads
   the real BB8 title. Fix: use a path that can't exist (for example under `tmp_path / "gone"`).
4. **Live boundary detection:** see `docs/handoff-realtime-boundaries.md`. Andy ranks this above multi-source. The
   first step needs neither the PC nor Qwen: move `build/work/bb10/draft-detect/` (gitignored kNN classifier) into
   the repo, removing its hardcoded BB9 paths. Run it offline at 1 frame per second over the BB11 1080p original,
   and compare against the 12 owner-reviewed BB11 sets. Then do BB10 the same way. Report hits, misses and timing
   error. The classifier was trained on full composited program frames, so keep screenshotting the program feed,
   not a clean game source.
5. **Multi-source recording:** see `docs/handoff-multisource-recording.md`. The immediate webcam fix is to set the
   webcam source's Scale Filtering to Area in OBS. The renderer proof of concept is blocked on headless Chromium
   (`libatk` missing, and installing it needs admin) and on the loop VM's `media-ffmpeg-v1` being off (owner
   decision).
6. **One combined streaming-PC dry run (about 10 minutes, PC plugged in):** this serves both items 4 and 5. Get the
   GPU model and NVENC count (`nvidia-smi -q`). Check that OBS WebSocket on port 4455 is reachable from the devbox
   through the Windows firewall. Send one `GetSourceScreenshot` per second while streaming to a private target with
   all recordings on, and watch OBS Stats for "skipped frames due to encoding lag".
7. **Shared code to build once, not twice:**
   - A mapper from overlay-log wall-clock time to VOD offset. Live mode should use OBS `GetRecordStatus`
     `outputDuration`. Post-event mode uses the recording's file name, which is only accurate to the second and
     breaks if the recording was paused.
   - `obs_client.py` opens and authenticates a new WebSocket for every request. Add a persistent connection before
     polling once a second.
8. **Cleanup:** once Andy confirms, delete the PC's backup branch and stash. Later, fold the WIP branch into
   `main`. GitHub `main` (`c7428d3`) is well behind.

## Don't

- Don't switch Andy's sandbox architecture, enable VM profiles, or install system packages. Those are owner/admin
  decisions.
- Don't overwrite operator marks in a project JSON with detected boundaries. Detected boundaries are suggestions.
- On the PC, run long jobs through `schtasks`, not plain ssh. See `.claude/skills/event-vod/SKILL.md`.

## Commit preparation update (2026-11-20)

Andy approved the eleven commit groups and push. The original unsanitized working files are preserved
locally under gitignored `build/work/commit-prep-20261120/originals/`. These are private local reference
files, never part of the commits. The following implementation commits precede this documentation commit:

- `ea289e0`: Protect local credentials and generated artifacts
- `2daf241`: Reconcile recovery attempts using durable receipts
- `77ea3af`: Handle long local sampling and prose-wrapped vision responses
- `90e59e6`: Strengthen boundary evidence and operator review
- `34b6481`: Record export receipts and support frame-exact smart cuts
- `c4e7442`: Add mobile review and per-set character summaries
- `af79e83`: Improve thumbnails and background selection
- `0140f05`: Archive event recordings with end-to-end hash verification
- `f6c03dd`: Support resumable YouTube uploads and local credential storage
- `018d6a4`: Add Discord announcements and tournament commands

Validation: `1605 passed, 1 skipped, 2 xfailed, 124 warnings, 11 subtests passed in 250.16s (0:04:10)`. Mobile review JavaScript syntax and archive/review-card CLI help checks also passed.
This does not replace the in-person streaming-PC smoke test or validate live boundary accuracy.
