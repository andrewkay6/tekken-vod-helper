# Boundary Workflow

## Current experimental profile

After the established upscale step, use the local 1440p recording directly:

```bash
.venv/bin/python -m tekken_vod_helper.boundary_cli detect recordings/input1440.mp4 \
  --output-dir build/work/my-boundary-run \
  --cache-fine --recognition-crops --temporal
```

This streams/decodes once into a one-second JPEG cache, classifies coarse frames,
refines from cached images in bounded batches, then asks Qwen to reason across
games and candidate sets. It retains original source timestamps. Cache size,
classified frames and total model calls have separate caps; temporal reasoning
reserves `--adjudication-requests` calls (default 4) from the total request budget.
For YouTube inputs, the resolver
prefers the tested low-resolution H.264 HLS path and uses no browser cookies.
A two-hour recording at the 5-second coarse interval is ~1386 frames, or at least
174 batched calls before refinement and retries. Set budgets for the intended
sampling plan; BB9 used 263 vision calls, so exceeding the default 300 is not
inherently required. Sampling writes `frames/sample-manifest.json`, which
`--samples-manifest` can reuse to skip extraction. It does not reuse classification
or resume adjudication. Temporal adjudication splits a timeline that cannot fit one
prompt into bounded windows, so the whole recording is decided rather than refused,
and a recovery can run from an already classified timeline file. The BB9 review and
recovery handoff that used to be linked here has been consolidated into
`docs/game-state-delivery-plan.md`, which is where the measured call counts and the
decisions they produced now live.

## Model endpoint

`--endpoint` and `--model` default to `tekken_vod_helper/local_model.py`
(`http://127.0.0.1:8000`, `qwen3.8-flash-next`) and can be overridden per flag or
for a whole shell with `QWEN_BASE_URL` / `QWEN_MODEL`. An endpoint may be written
as a bare origin, a `/v1` root, or a full chat-completions URL; all three resolve to
the same request path. When the server runs on another host behind a tunnel, check
the tunnel is still up before a long run.

`predictions.json` contains normal match fields; `project.tvh.json` can be opened
directly by the app. Observation IDs and reasoning remain in the adjudication/review
artifacts. Inspect `review.json` and `run.json`'s `refinement_status` before
exporting clips and preparing the YouTube proposal. Workflow completion does not
establish cut accuracy or bracket validity.

Vision thinking stays enabled. The optional `--no-vision-thinking` trial produced
malformed JSON; it is not part of this profile. Bounded implementation packets can
separately use `scripts/qwen_work_packet.py --no-thinking`.

### Prompt revisions

The cutting policy is sent inside every vision request (`## Cutting Policy` as the
first content part) and the revision is recorded in `prompt_version` of every batch
provenance file and usage record, and inside the prompt fingerprint (which also covers
the sent content, so a v1 and v2 fingerprint cannot collide). Revision
`vision-qwen-v1` hashed the
policy but omitted its text from the request, so a v1 batch proves the file was read,
never that the model saw it; `scripts/evaluate_boundary_window.py` refuses to replay
one as policy-aware output. Adjudication has its own revision marker:
`temporal-qwen-v1` is the single-shot prompt, `temporal-qwen-v2` the windowed one, and
saved chunks are keyed by a fingerprint of model, revision and exact prompt.

See [recognition and OCR experiments](boundary-recognition-experiments.md) for
measured development diagnostics and the separate fresh-footage evaluation.

## Overview

The boundary detection pipeline classifies video frames into screen states, optionally refines transitions, and proposes match boundaries.

## Pipeline Stages

1. **Frame Sampling** – Extract timestamped frames at a fixed interval.
2. **Classification** – Batch-classify frames using a local vision model.
3. **Refinement** – Re-sample around state transitions at finer intervals.
4. **Round and game tracking** – `boundary_games` follows the game's own sequence (round clock, ROUND
   callout, the READY/FIGHT start banner, K.O./TIME UP, result transition, win dots) into rounds and
   games, independent of the broadcast overlay, and queues whatever the frames in hand cannot settle.
5. **Boundary Proposal** – Determine match boundaries:
   - **Rule-based (default):** `propose_from_timeline` applies heuristic state-transition logic.
   - **Marking-driven:** `boundary_marks.recover_sets` groups play into sets using live markings as
     anchors only.
   - **Temporal adjudication (optional):** `TemporalAdjudicator` sends the classified timeline to a model in size-bounded, overlapping windows for cross-game reasoning, then reconciles the proposals into one chronology.

## Temporal Adjudication (`--temporal`)

Enabled via `detect(..., temporal=True)` or CLI `--temporal` flag.

- Runs **after** classification and refinement.
- Replaces rule-based grouping entirely.
- Records `proposal_algorithm: "temporal-qwen-v2"` in `run.json`. v1 sent the whole timeline in one
  prompt; v2 splits it into size-bounded windows. A one-window timeline produces the same prompt
  bytes and the same single call as v1.
- Saves adjudication provenance (per-window fingerprints, ranges, raw replies, validation status,
  attempted-call counts and accumulated usage) to `adjudication.json`, plus one
  `adjudication/chunk-NNNN.json` per window.

### Rules

Set edges follow one shared contract, `tekken_vod_helper/boundary_contract.py` (`edges-v2`). It is a
single source on purpose: the model's instructions, the validator's verdict and the run artefacts are
all generated from it, because two copies previously disagreed and the model was told to reject spans
the validator would have kept.

- The model infers tournament **sets** (not individual games). The round/game layer
  that decides rounds and games from the game's own HUD is a deliberately separate
  concern, documented in `docs/game-state-delivery-plan.md` and
  `docs/set-recovery-stage5-plan.md`.
- **An edge is the strongest evidence present, not a fixed screen.** A set may open at a confident
  `stage_select`, else at a confident `character_select`/`vs_screen`/`stage_intro`, else at the first
  observed gameplay; and may close at a confident `controller_prompt`, else at a confident
  `post_match_menu`/`controller_disconnected`, else at the last observed gameplay. Each inferred edge
  is published as `partial: ["start"]`/`["end"]` with `start_completeness`/`end_completeness` naming
  what it rests on.
  Rejecting a span for its endpoints was the old rule, and it was a bug: it deleted the two BB9 matches
  whose edges were never broadcast and published nothing in their place, while a wrong boundary
  elsewhere was still returned as a completed candidate.
- Play inside the span is still mandatory: a candidate with no observed `gameplay` between its ends is
  refused.
- Rematch menus, controller assignment dialogs and VS screens do not imply a set ended.
- Broadcast names and score boxes **corroborate an edge and never create one**, because they change
  mid-round: three such changes were confirmed against cached BB9 frames (round clock 43→42, 52→47,
  49→45 with the same stage and characters).
- **Absence of later observations is not proof a set concluded.** A disconnect, a recording end or a
  run of unknown footage leaves the end edge partial; it is not published as complete.
- Score resets with the same pairing (a Grand Final restart) are separate sets, from sequence evidence
  — concluded prompt, fresh episode, reset score — not from a name reading.
- Aborted attempts (no gameplay) go to review, not export.
- All proposals include `bracket_validity_unverified` review items.
- Player and character fields are bound to each other: a name is paired only with the character the
  same gameplay row reported beside it, and a pairing that gameplay rows never establish is reported
  as a `metadata_association_uncertain` review item instead of being guessed from screen slots.
- `review.json` records the contract version and its state lists under `edge_contract`, so a run can
  be re-read years later without guessing which rules produced it.

### Constraints

- Every prompt is bounded by `--adjudication-max-chars` (default 500,000), charged for the system
  prompt, the policy text and the JSON framing as well as the observations.
- Window cores partition the timeline exactly once. A boundary is proposed only at a **structural**
  candidate: a controller handover, a concluded screen followed by play of a different pairing, an
  episode opener after a stretch of non-play, a break longer than the set-gap window, or a visible
  scene change with no transition between two readings of play. A pairing change may **corroborate**
  such a candidate and never opens one alone. A `stage_select` on its own is not a boundary either —
  on the BB9 timeline the earlier "closing prompt then a grid" rule proposed 13 boundaries, none of
  which the adjudicating model treated as set ends, and 12 kept the identical pairing because Tekken 8
  re-votes a stage between games. Names are compared with an OCR tolerance (same player above 0.85
  similarity: BB9 read one tag as both `Spitfire2929` and `Spire2929`). Where no candidate exists the
  plan reports `unsnapped_boundaries` and the run records a `set_may_cross_window_boundary` review item.
- Adjacent windows share `--adjudication-overlap` observations of context. A proposal whose endpoint
  falls on the last observation its window could see is never published as a cut: it is adopted only
  when another window saw the set finish, otherwise it becomes `window_edge_endpoint_unverified`.
- Cross-window merging is by anchored global observation id, so an agreement counts once, a
  better-supported endpoint supersedes a weaker one (`superseded_endpoint`), and an unresolved tie
  becomes `conflicting_boundaries` or `conflicting_candidates`. A span one window called a candidate
  and another did not becomes `conflicting_dispositions` and stays in review. Nothing is silently
  dropped.
- `--adjudication-requests` is the temporal allowance, reserved out of `--max-requests` before
  classification starts. `run.json` reports the calls actually attempted
  (`adjudication_model_calls`) and accumulated token usage across all of them.
- A window whose reply arrives but cannot be used is retried inside the allowance — at most
  `DEFAULT_WINDOW_ATTEMPTS` (2) attempts per window, not configurable from the CLI — and is then
  recorded `failed`. Windows the allowance never reached are recorded `not_attempted`. A window whose
  reply **never arrived** (socket timeout, or a connection that died after the request was sent) is
  recorded `unresolved_timeout` after a single attempt and deliberately not retried: the endpoint may
  still be generating, and a second request would queue behind work nobody is waiting for. Any of
  these leaves the run `incomplete`: its chunks stay on disk for `--resume`, and no predictions or
  project are published.
- `--timeout` is the client's socket wait, not a job deadline. It does not cancel server-side work, so
  wall time is never `attempts × timeout`; after a timeout, check the endpoint before resuming.
- Play that no proposal mentions is reported as `unproposed_play` (timestamps, row count and observed
  pairings, grouped by elapsed time). Since `edges-v2` a set the stream joined mid-game is proposed as
  a partial rather than being impossible, so an `unproposed_play` entry now means the model left play
  unassigned, not that the contract refused it.
- Errors raise `TemporalAdjudicationError` (or `TemporalAdjudicationIncomplete` when partial results
  were saved) and are **never** silently swallowed.

## Recovering a saved timeline

`adjudicate-timeline` performs the temporal stage from a classified timeline file. It resolves no
source, extracts no frame and issues no vision request, so a run whose classification completed but
whose adjudication died at the size guard is finished by reading those files again:

```bash
# Offline first: windows, prompt sizes, coverage, split quality and call allowance.
.venv/bin/python -m tekken_vod_helper.boundary_cli adjudicate-timeline \
  --timeline build/work/bb9/detect-1440p-v2/timeline-refined.json \
  --output-dir build/work/bb9/recover-v1 \
  --source-id yt:QEWJ0UgVp4k --duration 6928.421136 --dry-run

# Then the real thing, into a fresh directory, with a bounded number of calls.
.venv/bin/python -m tekken_vod_helper.boundary_cli adjudicate-timeline \
  --timeline build/work/bb9/detect-1440p-v2/timeline-refined.json \
  --output-dir build/work/bb9/recover-v1 \
  --source-id yt:QEWJ0UgVp4k --duration 6928.421136 \
  --event "Basement Brawl #9" --adjudication-requests 4

# An interrupted recovery continues from its saved chunks without re-asking about finished windows.
.venv/bin/python -m tekken_vod_helper.boundary_cli adjudicate-timeline \
  --timeline build/work/bb9/detect-1440p-v2/timeline-refined.json \
  --output-dir build/work/bb9/recover-v1 \
  --source-id yt:QEWJ0UgVp4k --duration 6928.421136 --resume
```

`--resume` requires the same adjudication inputs — timeline bytes and row count, policy hash, model,
endpoint, prompt cap and overlap — which are recorded in `recovery-inputs.json` and compared before
any request. Each saved chunk also carries a fingerprint of its exact prompt, so a changed input
re-asks only the affected windows. Widening `--adjudication-requests` is deliberately allowed on
resume: it is the usual reason to resume.

Labels stay evaluator-only here too. The recovery's predictions score with `score-predictions`, and
an `incomplete` recovery refuses to score rather than reporting zero sets as a result.

## Recovering sets from live markings

Live markings are the least trustworthy input in this pipeline: made during the stream, from memory,
while a bracket runs. They land late, get duplicated, get skipped, and sometimes mark a match that was
never broadcast. `boundary_marks.recover_sets` treats them as **approximate anchors and nothing more** —
sets are grouped from the play itself, so unmarked matches are found and marking errors cannot move a
boundary.

```bash
# Recover sets and report what the markings did to them. No model, no network.
.venv/bin/python scripts/evaluate_marking_resilience.py \
  --timeline build/work/bb9/detect-1440p-v2/timeline-refined.json \
  --marks build/work/bb9/oct22.tvh.json --duration 6928.421136 \
  --pad-before 15 --pad-after 25
```

Rules the implementation is built around:

- **Stage grids and controller prompts are preferred evidence, not mandatory boundaries.** A set that
  the stream joined mid-game is still a set: it is recovered, marked `partial: ["start"]`, and kept.
- **A boundary needs a structural candidate.** Play is grouped into maximal **stretches** (one
  continuous view of one fight, broken by any screen, a long pause, or a visible scene change), and a
  stretch's pairing is the majority reading inside it. A candidate is a controller handover, a concluded
  screen followed by play of a different pairing, an episode opener after a stretch of non-play, a
  break longer than the set-gap window, a concluded prompt whose score reached the target followed by a
  fresh episode and a reset score, or a scene change with no transition. **A pairing change alone never
  cuts**: measured on BB9 the header changed players mid-round three times, and a name-driven cut would
  have invented boundaries at all three.
- **Score fields are corroboration only, and their meaning is per-frame.** `score1`/`score2` are not
  reliably round wins or games won: a cached frame at 4,524.5 s reads 3-0 and the frame 5 s later reads
  0-0 inside one continuous round. A scoreboard target therefore concludes nothing on its own.
- **A hard cut is a visible scene change, not a header change.** Adjacent confident play whose
  characters differ with no transition between them means the recording joined late or left early, and
  both sides are reported truncated. On BB9 this reports **zero** cuts: the three previously claimed
  ones were overlay lag, confirmed frame by frame.
- **Absence is not completion.** A concluding screen with no later play on record leaves the end edge
  partial; only a demonstrably different pairing following it, or a score that reached the target,
  makes it firm.
- **Possible play is retained, not averaged away.** Gameplay rows below the confidence floor become
  `investigation` residuals with the question that would settle them; they never raise
  `fraction_accounted`, which is a statement about confident rows only.
- **Degradation is measured, not promised away.** Blurring names cannot be forgiven, because counting
  sets is exactly what that signal does; the harness reports it as drift while coverage stays at 100%.
- **Nothing is silently omitted.** Windows tile the recording between candidates, so every confident
  gameplay row lands in exactly one set by construction; `overlaps`, `coverage`, `residuals` and
  `investigation` make the arithmetic checkable rather than trusted.
- **Clip ends come from evidence plus `--pad-before`/`--pad-after`**, never from where the next marking
  happens to sit. Padding is clamped so one clip cannot eat its neighbour.
- **Names are compared with tolerance** (`boundary_names`): similarity above 0.85, or one name wholly
  inside the other. On BB9 one player read as `Spitfire2929`/`Spire2929`/`PWDDA | Ariz`/`PWDDA | Az`,
  and exact comparison turned those flickers into invented sets — 31 instead of 9.

On BB9 (measured 2026-10-28, cached timeline only, no inference) the recovery returns **12 sets**, with
all 1,154 confident gameplay rows accounted for, no overlaps, **zero truncated edges and zero hard
cuts**, and all 12 preserved as partial on at least one edge. Each of the ten marking faults — unmarked,
shifted early and late, mid-set, triplicated, half-dropped, reordered, a bogus mark, and every mark
jammed into one set — produces the **same 12 boundaries with no play lost**. Under evidence damage
coverage stays 100% and no play is lost, while three of the six name-corruption cases move a boundary
(13, 16 and 9 sets), which the harness prints as drift rather than forgiving.

## Rounds and games: the layer underneath set boundaries

Set boundaries sit on top of a hierarchy the earlier pipeline skipped: a **round** is one fight, a
**game** is the first side to win three rounds, and a **set** is the first side to win three games
(both are configured, never inferred from labels). `boundary_games` recovers rounds and games from what
the game itself draws and ignores the broadcast header entirely:

- a **game boundary clears the win row**. A drawn row that reads the same on both sides of a proposed game start
  contradicts that start, whatever the clock did: measured 2026-11-04, where 2-2 at 6,158.152 and 6,160.154 sat on
  both sides of a `ROUND`-less reset at 6,161.155 and the split did not survive it. The converse also holds --
  the row *reaching* its target is the conclusion, and it can arrive at the +2 s or +4 s frame after the bound,
  so the frames beside a boundary are part of the evidence and not padding around it;
- a **planner's window list is not a work list.** Before buying pip crops, select on `round_end_basis` and on the
  saved rows that follow the bound: a crop at a round-end anchor can name a game winner only when that anchor is an
  **observed conclusion**, or sits within a couple of seconds of one. Measured 2026-11-04 on BB9: asking for a
  three-game candidate the ordinary way cost 30 requests across ten windows of which two could settle anything, and
  two of the 13 winner-less anchors lay 40 s and 90 s from any conclusion. Screening first bought 6 requests, and
  rejected a third anchor on saved evidence -- its row read 2-1 while the tracker counted four rounds there, which
  is a structural question no crop can answer;
- so the **round-count rule is a stopping signal, not evidence of a boundary**. `_group_into_games` used to
  close a game once `2 * rounds_per_game - 1` rounds had run without a conclusion, which is how a boundary
  appeared at 6,161.155 in front of a win row saying the game was still being played. The cap still fires and
  still forces a decision -- but where the win row is read, non-zero and unchanged across the candidate cut, the
  group stays open, the refused cut is recorded on the game as `round_count_beyond_target`, and the run reaches
  the queue as `game_incomplete` instead of a closed game. A cleared row, a missing reading, or a side that
  reached the target all keep the old behaviour: those are genuine resets and genuinely unresolved runs, and a
  guard that swallowed them would replace one fabrication with another;
- the **round clock** only ever falls inside a round, so any rise beyond reading jitter is the next
  round; a clock that fell *faster* than the recording advanced is a bad reading, reported and never
  acted on; the clock is read whichever way the sentence puts it -- `timer 29` and `29s timer`,
  `60-second timer` -- because the state route writes prose in both orders, and which wording produced a
  reading is recorded as `round_clock_wording`. Three things keep that from becoming a guess machine: a
  number is only a clock when a clock word is attached to it (combo counts, damage and set scores are
  not), a denial anywhere in the same clause (`no 30s timer readable`, `the 30s timer is not visible`)
  leaves the clock unread rather than invented, and a structured answer field always outranks the prose
  -- including when it states that it could not read one, which stays `None` instead of falling back to
  whatever the sentence happened to contain;
- a **ROUND n** callout opens a round, **K.O.** and **TIME UP** end one, and a result transition
  followed by a between-game screen closes a game -- with one continuity exception, because the announcement
  stays drawn for seconds: a banner read on a row whose digits are *still falling from the open round's own
  clock* is the same round being shown again, not a new one (`_banner_still_in_open_round`, recorded on the round
  as `banners_seen_inside`). The measured case is 60, 58, 53 across 6,071.065-6,081.075 with ROUND 2 on screen at
  all three: one round, cut in two, and the extra unit is what let the round count invent a game. The exceptions
  are deliberate and tested -- a banner on a row that never read the clock, a clock stalled at a full 60, a
  genuine rise, and a banner naming a different round than the one open all still open a round, the last of them
  with the disagreement kept on the record as `round_numbering_conflicts` rather than resolved by preferring the
  digits;
- **where a game stops is four claims, not one field**: `last_live_at` (the last instant the digits fell -- the
  lower bound, and never above an observed conclusion, because digits drawn after a watched K.O. are the victory
  camera), `conclusion_event_at` (the estimate the report publishes), `winner_confirmed_at` (the read naming the
  side, legitimately after the end, with `winner_confirmation_readings` carrying the confidence it came with), and
  `footage_after_conclusion_seconds` (play-labelled rows past the estimate). `clip_end_at` reaches the conclusion
  bound plus configured padding and stops at the next opening -- never drifts to a later marking, a next-game
  start, or the minutes of menu dwell a broadcast holds after a round;
- **a set's clip is its last game's clip, not that policy applied a second time.** `_set_record` inherits the
  game's `clip_end_at` and only falls back to conclusion (+confirmation) + padding when the game record carries
  none, so padding cannot be counted twice and the exported cut cannot land *before* the read that named the
  winner. It did: anchored on `conclusion_event_at` alone, four of BB9's ten published candidate clips ended
  1.001 s to 3.003 s short of their own `winner_confirmed_at`. Both the anchor and the uncertainty band are
  published beside the number (`clip_end_anchor`, `clip_uncertainty`) because `boundary_evidence.recovery()`
  forwards them to the export layer, and that layer is where a clip becomes a cut;
- **two positive readings that disagree are never ranked into silence.** A candidate game boundary announced by
  an explicit `ROUND 1` and denied by a win row that never cleared closes the game where the callout said -- and
  both games the cut separates are marked `contested`, the winner is withheld from both (`counts_toward_set:
  false`, with `winner_if_boundary_untouched` kept visible), the alternative single-game grouping is recorded on
  the second game as `start_is_disputed`, and the queue carries a `game_boundary_conflict` naming the frames
  that would settle it. A `ROUND 2`, no callout at all, a cleared row, or a side that reached the target behave
  exactly as they did before, which is what keeps the BB9 false-split repair intact;
- the game's own **READY**/**FIGHT** banner, and a full round clock arriving straight off a pre-round
  screen and then falling, establish where a round **began** when the clock never happened to be read at
  its first frame. Either one settles the start and neither names the round: a banner is not a number, so
  `number_observed` stays empty, and a frame that merely *reads* 60 proves nothing on its own.
- **win dots** are what distinguishes a game end from a set end, and the cached classification never
  recorded them — the gap the investigation loop exists to fill.
- a **win-dot count that falls inside a game** is a contradiction to adjudicate, never a round taken
  back. It resolves as one bad reading only when that side's *own neighbours* say so: a single low
  reading, the readings either side of it agreeing with the previous high within one round, and the low
  reading no more confident than both of them. That reading is then set aside with its provenance and
  the raw series survives untouched. Anything else — a run of low counts that climbs back, a fall never
  recovered, a dip bolder than the readings contradicting it, a side that contradicts itself twice — is
  the reset-and-rebuild hypothesis, indistinguishable here from a wrong game boundary, so the game is
  named contested, names no winner, counts toward nothing, and queues an item pointing at the frame
  that fell.

Observations (visual facts) are stored apart from events (what the sequence means), and both apart from
the overlay, which is carried with `trust: "untrusted"` purely so a reviewer can see it was
contradicted. Nothing that cannot be settled becomes a boundary: it gets an entry in the unresolved
queue naming the observation that would settle it, and incomplete games are kept, never discarded.

```bash
# Offline trace of one already-classified timeline: no source, no frames, no model.
env PYTHONPATH=. .venv/bin/python scripts/trace_game_detection.py \
  --timeline build/work/bb9/detect-1440p-v2/timeline-refined.json \
  --output-dir build/work/bb9/games-v1

# What the frames would have to answer, as an exact budgeted plan: still no model, and no spend.
# It reads the recording's own saved answers by default -- every crop-answer directory under
# build/work/bb9 for the pip evidence, the named band runs for identity -- so the plan is the
# recovery's equal, not a smaller claim than the report's.
env PYTHONPATH=. .venv/bin/python -m tekken_vod_helper.boundary_cli investigate-games \
  --timeline build/work/bb9/detect-1440p-v2/timeline-refined.json \
  --frame-manifest build/work/bb9/detect-1440p-v1/frames/sample-manifest.json \
  --duration 6928.421136 --region 30 600 --marks build/work/bb9/oct22.tvh.json \
  --max-requests 40 --output-dir build/work/bb9/games-pilot-v1 --dry-run

# The same command recovering sets from saved evidence: one complete candidate and one partial in
# [3200, 4000) with no target supplied, from 159 crop readings and 11 band readings already on disk.
env PYTHONPATH=. .venv/bin/python -m tekken_vod_helper.boundary_cli investigate-games \
  --timeline build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json \
  --frame-manifest build/work/bb9/detect-1440p-v1/frames/sample-manifest.json \
  --duration 6928.421136 --region 3200 4000 --step 2 --no-format \
  --header-responses build/work/bb9/header-trial-v1/unapproved-execute-2026-10-30,build/work/bb9/set-end2end-v1/attribution-run,build/work/bb9/set-end2end-v1/band-run \
  --header-ledger build/work/bb9/set-end2end-v1/attempts-shared-2026-10-31.json \
  --output-dir build/work/bb9/public-recovery-integration-v1/cli-3200-4000-noformat --dry-run

# Replay the three BB9 games whose crop readings are already on disk, under the win-dot rule above.
# Compares games 1 and 2 with their accepted reports and reports no comparison for the fixed interval.
env PYTHONPATH=. .venv/bin/python scripts/revalidate_temporal_rule.py

# The next bounded experiment, prepared without sending anything: one unread game after a real
# in-match game boundary, with its labels frozen before any output. See the plan for the budget
# (14 primary attempts, 3 retry reserve, 8 measured follow-up = 25 fresh attempts asked for).
env PYTHONPATH=. .venv/bin/python scripts/plan_decision_windows.py \
    build/work/bb9/consecutive-games-v2 --start 226 --end 430 --allowance 14
```

See `docs/game-state-delivery-plan.md` for the consecutive-game trial's frozen
interval, budget ask, evaluation criteria and the mocked-transport rehearsal that
proves cancellation and resume; the trial ran and its result is in that ledger.

The command **dispatches each question to the executor that can answer it**, and
`dispatch.routes` is where that is visible: `pip_crops` goes to
`PipReadClient(arm=crops)` with two labelled PNG crops per frame at the decision
instants (a round's end, +2 s, +4 s — the K.O. flash covers the third pip on the
frame the round ends on, and the victory close-up erases the row soon after),
`state` goes to `RoundFactsClient` in whole-frame batches on the `--step` grid,
and `header` goes to `HeaderReadClient` at two instants per disputed span. A
region that only needs the pip row produces no whole-frame batch at all: the
whole-frame pip question was the bulk of the old bill, and its answer was the
reading this project measured as unable to see the row.

Evidence grades follow the route, not the arrival order. Only a cache-arm crop
reading may occupy a row's `win_dots`; a whole-frame or band pip answer is written
to `win_dots_diagnostic`, which the tracker never reads, so it cannot create a
winner on an uncropped frame and cannot overwrite a crop value that already exists.
Refusals are recorded — `evidence.pip_readings_demoted`, and per-row
`pip_reading_conflicts` keeping both sides — because a rule that fires silently is
indistinguishable from one that never ran.

```bash
# Fund one route at a time. The routes you did not fund are still priced, in
# dispatch.routes[].requests_unfunded, and their requests appear as named deferrals.
env PYTHONPATH=. .venv/bin/python -m tekken_vod_helper.boundary_cli investigate-games \
  --timeline build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json \
  --frame-manifest build/work/bb9/detect-1440p-v1/frames/sample-manifest.json \
  --duration 6928.421136 --region 2400 3200 --step 2 --no-format --routes state \
  --output-dir build/work/bb9/state-only-plan --dry-run

# Two passes: pass one's answers merge back through the canonical loaders and the
# plan is recomputed, so a game that gains a winner can raise a band question that
# did not exist an hour ago. --passes 1 keeps a run to a single tranche.
env PYTHONPATH=. .venv/bin/python -m tekken_vod_helper.boundary_cli investigate-games \
  --timeline build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json \
  --frame-manifest build/work/bb9/detect-1440p-v1/frames/sample-manifest.json \
  --duration 6928.421136 --region 2400 3200 --step 2 --no-format --passes 2 \
  --header-responses build/work/bb9/header-trial-v1/unapproved-execute-2026-10-30,build/work/bb9/set-end2end-v1/attribution-run,build/work/bb9/set-end2end-v1/band-run \
  --budget-file build/work/bb9/unseen-set-structure-v1/attempts.json --budget-cap 66 \
  --budget-minutes 45 --output-dir build/work/bb9/unseen-set-structure-v1/run-attempt
```

A run stops for one of four reasons and says which in `stopped_reason`:
`supported_outcome` (nothing left to ask, or nothing left that a send can close),
`no_new_evidence` (a pass answered requests and closed no question), the ledger's
own reason, or `passes_exhausted`. Each route's deferral list survives a cap: an
allowance that runs out mid-crop still names the state batches and band readings it
never reached, by frame and instant.

Dropping `--dry-run` sends the requests, and **requires `--budget-file`**: the shared attempt ledger
carries one cap (`--budget-cap`) and one wall-time deadline (`--budget-minutes`, stamped at the first
send) across `run_pip_crop_probe.py`, `run_header_trial.py` and this command, and across every resume.
It refuses to start when the requests still owed — counted across all three routes — exceed the
attempts remaining, closes a hanging attempt only when an answer file can be attributed to *that* attempt,
and leaves an unknown spend charged and named. An answer on disk proves the **request** was satisfied; it
does not prove every attempt that carried it got a reply, so a retry that answered closes its own entry
and the earlier spend stays `uncertain` beside a `request_satisfied` flag, and `summary()` reports
`attempts_by_state`, `uncertain_requests` and `requests_satisfied_by_an_answer_file` rather than one
in-flight count that would answer both questions the same way. A send stopped by an interrupt is settled
`uncertain` by the process that saw it stop, which is the only one that can know. Every entry records its
route (`investigate-games:pip_crops`, `:state`, `:header`) and every attempt is named by route, because one
frame can be asked twice — cropped for its pip row and banded for its nameplate — and two attempts
sharing a name would let a resume close the band's hanging spend with the crop's answer file. A live
run with no ledger is refused before any client is built, and injecting a transport for one route
narrows the run to the routes injected rather than leaving the others live. `--dry-run` stays free and
needs none of that, because a guard on looking is a guard that gets dodged by pointing at somebody
else's ledger.

A run that does go on reuses the existing 1 s frame cache, persists every answer before sending the
next so an interrupted run re-asks nothing, treats the request allowance as a hard
stop whose exhaustion is reported as `not_attempted`, and never infers a frame from a neighbouring one
(an unread clock stays unread). Each route keeps its own answer store in the layout its own executor
reads (`requests/`, `pip/crops/groups/`, `header/groups/`), which is what lets a run's new crop answers
reach the tracker through the same loader that reads the saved corpus instead of a second one. A
`KeyboardInterrupt` leaves `investigation-interrupted.json` naming what was answered, what the ledger
still counts in flight, and the fact that the next invocation is a `--resume`. A resume whose evidence
base, corpus, band runs, funded routes or format policy differ from the run that produced the saved
groups is refused by name. The measured BB9 numbers, the acceptance evidence
and what the one-game pilot cannot prove are in `docs/game-state-delivery-plan.md`.

## Scoring against historical labels

Truth labels are evaluator-only: never give the project's names, characters, round
names or timestamps to detection. `backtests/annotations.json` records the same rule.

Score a finished run without spending inference again:

```bash
.venv/bin/python -m tekken_vod_helper.boundary_cli score-predictions \
  --run build/work/my-boundary-run/run.json \
  --truth backtests/projects/sep19.tvh.json \
  --output-dir build/work/my-boundary-run
```

`backtest-boundaries` bundles detection and scoring, and scores only after
predictions persist. Prefer `detect` then `score-predictions` so a label problem
costs no model calls. When the evaluated range is already known — a local file, or
an explicit `--duration` — `backtest-boundaries` validates the labels up front and
refuses before detecting; a remote source without `--duration` cannot be pre-checked
and still runs first.

A truth row may legitimately omit its final `end`; it is then derived from the next
row's start, or from the project's `duration`. Projects that report `duration: 0.0`
(BB5, BB9) cannot resolve a trailing null end this way, which used to surface only
after a full detection run. Pass the measured recording length as `--truth-duration`
to score them; it overrides the missing duration in the report
(`truth_duration_override`) and never edits the truth file. A row starting past the
evaluated range stays `unresolved_truth_ends` rather than becoming an error, which is
how clamped prefix runs are excluded.

Scoring reports `derived_truth_ends` and `unresolved_truth_ends`; a derived endpoint
is a hint from neighbouring labels, not an observed boundary.

## Files

| File | Purpose |
|------|--------|
| `tekken_vod_helper/boundary_contract.py` | The one set-edge contract: allowed edges, partial-vs-firm verdict, prompt rules |
| `tekken_vod_helper/boundary_games.py` | Round/game/set hierarchy: visual facts, rounds→games, candidate fusion from combined evidence (a between-games screen with no controller screen joins; a cleared station separates), the unresolved queue, and per-candidate set closure |
| `tekken_vod_helper/boundary_format.py` | The event's set format as data: stage text from the band → games-to-win target per candidate, with every admitted target kept when the label is stale or disputed. Format stays optional -- no target means nothing closes |
| `tekken_vod_helper/boundary_windows.py` | Window and frame identity: the cache index, decision-frame selection, saved-reading reuse, and `verify_annotations`, which refuses a frame/timestamp pair the index does not support before anything renders |
| `tekken_vod_helper/boundary_investigate.py` | Budgeted, resumable investigation of the unresolved queue, **dispatched by route**: crop, whole-frame state and band requests each go to their own client, one allowance and one ledger cover all three, answers merge back through `boundary_evidence`, and the plan re-runs until a stop it names |
| `tekken_vod_helper/boundary_marks.py` | Marking-driven set recovery: structural candidates, coverage, truncation |
| `tekken_vod_helper/boundary_names.py` | OCR-tolerant name clustering and pairing comparison |
| `tekken_vod_helper/boundary_pips.py` | Round-win pip arm: crop geometry, per-frame answers, validated win-dot evidence, and the three-way scoring denominator (visible count / known absent / unknown truth) |
| `tekken_vod_helper/boundary_header.py` | Broadcast scoreboard band route: pairing and displayed tally. Its pip counts are unvalidated diagnostics and never the crop arm's evidence. `saved_readings` can name the ledger that counted a run's answers, and matches each answer to a charged attempt |
| `tekken_vod_helper/boundary_bracket.py` | Optional event-bracket context: normalize, provenance, match-change/format/expected-game-count signals. Never decides a boundary alone |
| `tekken_vod_helper/boundary_budget.py` | Shared attempt ledger: one cap and one wall-time deadline across commands and resumes |
| `tekken_vod_helper/boundary_evidence.py` | The one canonical, recording-scoped evidence base and set-level summary: discovered per-recording crop runs, the merge with its framing and window guards, the three comparison passes (no band pairings / band pairings in / no target), and the candidate block every tool publishes. `scripts/pip_evidence.py` and `scripts/validate_game_one.py` delegate here so the command, the report and the planner cannot join the same recording twice |
| `scripts/pip_evidence.py` | Compatibility names for the canonical module above (`evidence`, `crop_runs`, `crop_run_dirs`, `framings`, `STATE_ONLY`). Import it if you are already a script; never reimplement the join it points at |
| `scripts/audit_recording_coverage.py` | Recording-wide coverage audit from saved evidence only, with nothing sent on any route: what the tape holds, what was read, which play rows sit inside no game, which candidate edges are limited by footage, and what every remaining read costs -- priced **on the window a run would execute after automatic widening**, not merely on the window asked for, and repeated under perturbed markings. It prices through `plan_saved_investigation` and scores through `boundary_evidence.adjudicate`, so it cannot disagree with the command it previews |
| `scripts/verify_live_plan_agreement.py` | Checks that claim: drives the real `investigate_saved_games` with every route's `_post` stubbed (replies that fail validation, so no answer is ever accepted and none is written) and compares the requests it plans against the audit's executable ones -- same executed window, same request identities. Its output is a verification, not an experiment: the run directory sits outside the recording's evidence root so it cannot become a corpus |
| `scripts/account_unplaced_play.py` | Every play row that sits inside no game, classified by the evidence around it -- post-conclusion footage, opening footage of the game that follows, a menu or transition mislabelled as gameplay, genuinely unassigned live play, or unresolved -- each verdict quoting the rows, the adjacent game bounds and the clock readings it read. Built so that neither failure mode is available: no frame after a K.O. counts as missing gameplay, and no unplaced round is excused as animation |
| `scripts/audit_crop_anchor_quality.py` | Asks the question a crop bill depends on and nobody had checked: does each round-end anchor sit on a conclusion or on a bound? Reports anchors on observed conclusions, bounds whose next conclusion is seconds-to-minutes away, and the cases where digits keep falling after the anchor inside the same round. Its first run found two of the latter, both prose-only, and closing that parsing gap (the clock bullet above) removed one of them -- the survivor is reported with `continued_only_in_prose_the_tracker_cannot_read: false`, which is the difference between a footage problem and a reading problem |
| `scripts/measure_clock_repair.py` | Before/after on identical inputs for a parser change that must not move a claim: switches one guarded pattern off to reproduce the old reading, then reports rows read, rounds, games, winners and bounds per region. Built to answer "did this change a count we already accepted?" -- on the clock-wording fix it answered no winner anywhere and 6 of 30 bounds, in `build/work/bb9/clock-repair-v1/before-after.json` |
| `scripts/price_bounded_investigation.py` | The exact frames and cost of one bounded interval: requested window, executable window after widening, requests / images / token ceilings per route, request identities, and every frame each route would read. Prepare-only; it writes no ledger and sends nothing |
| `tekken_vod_helper/boundary_reasoning.py` | TemporalAdjudicator: observations, window planning, reconciliation |
| `tekken_vod_helper/boundary_workflow.py` | detect(), finalize_run(), backtest(), score_from_run(), rule-based proposal |
| `tekken_vod_helper/boundary_recovery.py` | Adjudicate a saved timeline: no source, frames or vision calls |
| `tekken_vod_helper/boundary_cli.py` | CLI entry point: `detect`, `adjudicate-timeline`, `investigate-games`, `backtest-boundaries`, `score-predictions` |
| `tekken_vod_helper/local_model.py` | Shared model endpoint defaults and URL normalization |
| `docs/cutting-policy.md` | Rules-only policy context (not edited by this module) |
