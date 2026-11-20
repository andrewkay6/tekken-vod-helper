# Handoff: detect set boundaries live during the stream and correct the project JSON

Written 2026-11-20 at the end of the BB11 pipeline session. Status: feasibility worked out. Nothing built yet.

## Goal (Andy, higher priority than multi-source recording)

Have set starts and ends detected **while streaming**, so the live TVH project JSON gets corrected during the event
instead of a 2-hour post-event analysis plus review. Andy's concern is **streaming-PC compute** (it's streaming and
may also run multi-source recording), not Qwen compute.

## What exists

- **Post-event analysis:** `tekken_vod_helper.boundary_cli detect` (Qwen vision on the Mac Studio via the devbox
  tunnel at `127.0.0.1:8000`). BB11 rerun stats: 1,566 coarse frames at 5 s intervals, batches of 8, about 20 s per
  batch, so about 2x real time with the GPU about half busy. 344 calls in total including the fine boundary zoom
  (`--max-frames 4000`) and 3 set-reasoning calls. The fine zoom and whole-event reasoning are what make it take
  about 2 hours.
- **Cheap screen-state classifier:** `build/work/bb10/draft-detect/` (`classify.py`, `segment.py`, `run_bb10.py`,
  `extract.py`; **gitignored, so move it into the repo before relying on it**). A kNN over small normalised thumbnails
  (full frame plus the HUD band), trained on BB9 keyframes (`bb9-keyframes.npz`) labelled from BB9's timeline.
  Classes: play / stage / vs / other. On BB9 it matched all 10 owner sets, with starts within ±2 s and ends 2-11 s
  early (BB10 applied a +5 s end offset). It runs in milliseconds on a CPU.
- **Overlay history log** (added 2026-11-20, `overlay_server.py`): the operator's names, round and score changes with
  wall-clock times. That's live, exact metadata, so names don't need OCR.
- **Ground truth for testing:** owner-reviewed boundaries for BB9, BB10 and **BB11**:
  `build/work/bb11/owner-reviewed-20261120/2026-11-19 Basement Brawl 11 Cafe Edition.owner-reviewed.tvh.json`
  (12 sets, owner-confirmed 2026-11-20). BB11's 1080p original is archived at
  `/var/lib/ai-sandbox-media/2026-11-19 Basement Brawl 11 Cafe Edition/recordings/2026-11-19 19-26-51 [original].mkv`.
- **Edge contract** used by the analysis (`review.json` `edge_contract`): strong start = `stage_select`, strong end =
  `controller_prompt`; supporting states are character_select, vs_screen, stage_intro, post_match_menu.

## Proposed design: keep the streaming PC nearly idle

1. The **devbox** polls OBS on the streaming PC through OBS WebSocket `GetSourceScreenshot` (640x360 JPEG, **once
   per second**). That's the only work on the streaming PC: a small readback of a frame OBS already renders, well
   under 1% of a core. TVH already has `tekken_vod_helper/obs_client.py`. Check that WebSocket is enabled and
   reachable from the devbox over the LAN (port 4455 by default, behind a password).
2. The devbox runs the kNN classifier on each frame (milliseconds), then a small state machine: stage select ->
   set start; controller prompt or results -> set end. It debounces over a few seconds.
3. Names, round and score come from the overlay history (pull it from the PC, or have TVH push it), cross-checked
   against the start.gg bracket (`startgg_client.fetch_bracket_sets`, using TVH's own `STARTGG_TOKEN` in `.env`).
4. Qwen is called only for ambiguous moments (for example two stage selects close together, or a long "other"
   stretch).
5. Results go into the live project as **suggested** boundaries (a separate field or decision journal; never
   overwrite operator marks silently). They get confirmed in the TVH app or the existing post-event review page
   (`scripts/build_review_cards.py` + `prototypes/mobile-review/server.py`).

Latency target: under a minute behind live, which is plenty for correcting a JSON.

## First step (no streaming PC needed, no Qwen)

Run the kNN classifier offline over BB11's 1080p original, faster than real time on the devbox, through the same
1-frame-per-second path a live loop would see. Compare the starts and ends it detects with the 12 owner-confirmed
BB11 sets. Report hits, misses and timing error. Then do BB10 the same way (cross-event). If it's good, build the
live loop. If the layout makes it fail (BB11 is "Café Edition"), retrain on BB9+BB10+BB11 keyframes and recheck
with blocked cross-validation.

## Open questions

- Is OBS WebSocket enabled on the streaming PC, and is the devbox allowed through the Windows firewall?
- Does a screenshot every second affect the stream? Expected no, but measure it in the same dry run as the
  multi-source test (OBS Stats "skipped frames").
- Where do suggestions show up live: in the TVH app on the PC, or only in the post-event review?

Related: `docs/handoff-multisource-recording.md` (shares the streaming-PC dry run), skill
`.claude/skills/event-vod/SKILL.md`, memory `streaming-pc-vod-pipeline`.
