# Game-state recovery delivery plan

Created 2026-10-29, stages consolidated 2026-11-04. This is the forward plan; earlier review documents are
evidence and history. Explicit user instructions take precedence.

Documents named inside the ledger below (`codex-*.md`, the per-stage trial plans and
the handoffs) were one-off review reports and plans for work that has since run.
They have been removed from the repository rather than archived in it: the ledger
line is the durable summary, the raw artifacts are under `build/work/bb9/`, and the
current authority for the set layer is `docs/set-recovery-stage5-plan.md`. Where a
removed document carried a command worth re-running, that command moved to "How the
executed stages were run" near the end of this file.

## Current status — 2026-11-06, Codex review of the running UI and evaluator frame times

Latest relevant Qwen session **d22e595e-2999-4275-8123-9e303255976f** finished at 22:33 UTC.
The Boundary review tab is implemented through ReviewFlow/ReviewPanel: 37 questions, including all
10 openings/endings, short playback, deferred/answered views, adjustments, bracket/orientation and
persistent decisions. The exporter uses the same hold policy. Qwen's final real-widget driver reports
PASS (seek/pause, undo, hold enforcement, save/reopen); Codex read its actual outputs and viewed the
opening screenshot, but did not repeat the full driver. The clipped explanatory text has since been fixed:
the "why" block is a wrapping read-only Text rather than two fixed-wraplength labels, and the follow-up
driver asserts no part of the panel is collapsed or unreadable at the recorded size.

**Verification:** Codex independently verified all **156 selected tests** across two runs: 155 passed
inside the sandbox; the single local-socket test blocked by sandbox permissions passed when rerun outside.
Qwen's saved broad output reports **1262 passed, 1 skipped, 2 xfailed** (199.31 s), including Tk tests.
The follow-up session's own saved run reports **1281 passed, 1 skipped, 2 xfailed** including the frame
resolver, coverage and recovery regressions (`review-ui-followup-2026-11-06/pytest-full.log`), and its real
widget driver passed end to end including playing an unclaimed stretch of the actual recording.
The existing project-local Tk RPM unpack/runner was reused, with no new install or environment rebuild.
The previous “UI missing” descriptions are superseded. Code, artifacts and extensive uncommitted work
remain preserved. BB9 is **unsplit and not boundary-accepted**. Reconstruction remains 30 games /
30 winners / 122 rounds, support 24 single-frame / 6 two-frame; these are hypotheses, not independent truth.

**Major evaluator correction (measured, then re-measured offline):** Codex previously accepted Qwen's
mislabeled screenshot audit without checking the filename-to-time mapping. The cache manifest records real
PTS spaced ~1.001 s apart, so rounded seconds cannot select frame IDs. Rebuilding the mapping from the
manifest covers far more than the two starts first noticed: **33 of the 40 blinded tiles named a frame that
was not the instant they labelled** (16 of 20 controls, 9 of 10 ends, 8 of 10 starts), worst **6.896 s**
(`review-ui-followup-2026-11-06/aligned/frame-identity-audit.json`, produced by
`scripts/audit_frame_identities.py`). The evaluator now refuses such a mapping in code:
`boundary_source.resolve_frame` looks an instant up in the manifest, returns the manifest's own path with a
digest and the signed offset, and will not invent a filename for an out-of-tolerance or off-disk frame
(`tests/test_frame_resolution.py`, 15 tests, including the real BB9 manifest).

Therefore **withdrawn, and not to be restated**: the five-late-start count, the "2 to 7 seconds inside a
live round" figures, the precision/recall comparison against the reference, and the claim that the runtime's
saved clock and a reference read contradicted each other at the same instant. All of them came off
mislabeled tiles. What the aligned evidence actually supports:

* On the 8 starts where both the runtime and a correctly aligned read have a value, they **agree 8 of 8**
  (`review-ui-followup-2026-11-06/clock-agreement-after-alignment.json`: 53.053/790.790/1588.587/2271.269/
  3723.720/4464.460/5495.490/6486.480). Two starts (2771.769, 3247.244) have no legible clock in the aligned
  strip, and remain unknown. There is no instrument conflict to explain.
* A clock value at one cached instant is a bound on when its **round** was running. It is not a set boundary
  and never becomes one by subtraction: 60 does not prove a first round, and `60 - clock` is not a start.
* What does recover earlier openings is the saved **state sequence**, and it had not been used for this.
  Behind a candidate the rows read `controller_prompt` -> `character_select` -> `vs_screen` -> `stage_intro`
  -> `gameplay`, and the only way onto a stage select is a new game starting. `boundary_review.recover_opening`
  walks that (`opening-recovery-from-saved-rows.json`, 10 starts, no inference): **7 openings resolve to an
  earlier supported instant** (e.g. 2771.769 -> 2731.729, 6486.480 -> 6361.355, 2271.269 -> 2222.220),
  **2 are contradicted** (790.790, 1588.587: play is already running behind them with no select screen, so
  they are not openings at all), and **1 is consistent** (53.053). Each carries its bound -- the last fight
  row behind the run -- and none of it moves a boundary: the reviewer gets an instant to look at.
* Coverage of the clock is a row statistic, not a temporal one: 1091 of 2129 rows (51.2 %) carry a clock, but
  those rows name only **1091 distinct whole seconds = 15.7 % of the 6928.4 s recording**. Say the second
  thing, not the first.

**Coverage and reconciliation, now fixed rather than listed.** The old figure **1676.693 s was between
adjacent candidates only**; whole-recording arithmetic gives **1787.305 s of tape that no export job would
write, in 11 stretches** -- the 9 between-gaps plus the leading **53.053 s** and the trailing **57.559 s**,
the 110.612 s that were outside the calculation. Candidates cover **5141.116 s** and jobs would write
**5141.116 s** (nothing is on hold on this import), reported as two accounts because a held clip covers tape
and renders nothing. None of the 11 stretches has a saved game span reaching into it, so each is labelled
"unclaimed", never "missed play"; the 5 unclaimed-gap states (`stage_select`, `vs_screen`, ...) are known
absence, not menus assumed. `reconcile_import` no longer reports a deletion it did not perform: an unmatched
row is `retained_without_candidate`, kept, and -- unless a person has already decided something about it --
held, so it appears as footage no job will write. A known clipped opening is now a review consequence: the
clip stays exportable, `boundary_accepted` is false, and `Start here anyway (partial)` requires a reason and
still does not record acceptance.

**Still open.** Boundary acceptance is unproved and BB9 is unsplit. The recovered openings are single-frame
sequence readings at a 1 s sampling; nobody has watched the recording, so the required independent inventory
of all gameplay and all set transitions does not exist and no percentage here certifies a boundary.
## Current state — 2026-11-06 (latest): review path exists; boundary correctness is measured and failing

**Historical diagnostic: timestamp-derived clock/edge claims below are withdrawn by the current status above.**
*The bullets below this heading describe the 2026-11-06 model-only review path and are kept as the
ledger of that pass; where they conflict with the section above (question counts, "no app caller",
"UI not built"), the section above is current. The gate verdict is unchanged: **not met**.*


Authority is `docs/set-recovery-stage5-plan.md`. In short: **5 of 10 predicted set starts sit 2 to 7
seconds inside a live round** on the tape, measured by reading the round clock from cached frames after a
blinded pass with random controls, so the BB9 acceptance gate is **not met**. All 10 set ends are
consistent with a padded conclusion. Protocol, tolerances and reporting classes were frozen before scoring
(`build/work/bb9/boundary-scoring-protocol-2026-11-06.md`); the reads and measurements are in
`build/work/bb9/boundary-review-2026-11-06/boundary-reference-2026-11-06.json`.

The delivery path is now real rather than designed: `boundary_cli review-project` turns saved recovery into
a project with per-game evidence, per-side identity, uncertainty and provenance attached;
`boundary_review.review_queue` asks footage questions with playable context and no inference
(`costs_inference: false`), `ReviewStore` persists answers with undo and invalidation, and a recovered row
can no longer have its clip end borrowed from the next mark. Characters keep their provenance
(`observed` / `remembered` / `human`), an unnamed observed side is no longer overwritten by a remembered
default, and orientation must be stated rather than assumed because this recording binds names to sides but
never binds characters to sides.

Two circular claims were removed from the fresh-VOD design: choosing a bracket set for a
detector-produced segment confirms **identity**, not the segment's boundaries; and picking a window because
it contains the known missing match leaks a discovery hint, making that a localised test rather than proof
of autonomous discovery. Automatic prioritization remains unvalidated, and `tkinter` is unavailable here so
the GUI itself is unexercised — manual checks are written out instead.

**The fresh recording has landed and is verified** (15:00): `2026-11-05 Basement Brawl 10 Cafe Edition/recordings/2026-11-05 19-35-07 [original].mkv`, 31,451,794,033 bytes, three agreeing SHA-256 computations (`d49e32dd…cbafd9`), status `verified`, **duration 8774.516 s (146.2 min)** 1920x1080 HEVC. Its project still cannot name its own recording (`video_path` empty, `duration` 0.0), so the association is the archive's directory layout, not a link. Measured on this file: seek 0.16–0.27 s at any position, decode to 640×360 ~114 frames/s warm, against BB9's ~8 frames/s end-to-end cache pass -- **cold work is encode/IO bound, not codec bound**. No frame extraction at scale and no encoding were run. Full facts in `docs/set-recovery-stage5-plan.md`.

## Latest Codex checkpoint — 2026-11-06, Qwen offline pass finished

Analysis session `a5ac83ca-73f3-4572-b279-a4b2598722c6` finished at 19:56 UTC.
Actual tool output confirms Qwen's final broad run: **1111 passed, 1 skipped, 2 xfailed**,
with three Tk-bound modules excluded. Codex independently ran **64 affected tests, all passed**
(17.67 s), freshly graded the five-read batch and reran the document checker (170 claims pass).
No inference or encoding was sent. Reconstruction remains 30 games / 30 winners / 122 rounds;
BB9's physical split and independent boundary acceptance remain uncompleted.

Retry reporting now correctly exposes **3774 prompt / 3642 completion / 2940 reasoning** from
six ledger entries, separately from five accepted replies. Character precedence fixes cover named
observations, picker paths and FocusOut; GUI execution was not verified here. Do not present these
fixes as the missing recovery importer or review UI: neither has been delivered.

Codex saved synthetic reproductions in
`build/work/bb9/completed-offline-review-2026-11-06/edge-reproductions.json`:
- A set inherits clip end 202 despite winner confirmation at 205, and still claims the confirmation
  is included. Determine the valid policy for conflicting next-opening/confirmation evidence; preserve
  padding once and uncertainty rather than blindly extending every clip or silently cutting evidence.
- An unnamed observed Jin does not match a newly selected bracket player and falls back to remembered
  King. Name-keyed matching does not resolve anonymous-side orientation or preserve its evidence.
- Usage records with empty/partial tokens can be marked complete and missing completion counted as
  zero. Reporting the real finished batch is corrected; generic completeness still needs work.
These are demonstrated edge cases, not claims that each occurs in BB9's saved recovery.

The fresh-VOD validation design also needs correction: choosing a bracket set for a detector segment
confirms identity, not independent boundary truth; a window chosen around a known missing match is a
localized diagnostic, not blind discovery. Deferred/missing recovery is not a passed completeness test.
Qwen used one explicitly disallowed subagent during its pass; do not repeat that process violation.

**Next:** use the current recovery prompt under `completed-offline-review-2026-11-06/` for offline
boundary-policy corrections, independent scoring and a working BB9 evidence-review/project/export-job
slice. Review decisions/playback must not trigger inference. Preserve the concurrent transfer, raw
artifacts and all uncommitted work; no model swap, subagents, inference, encoding, commit or push.

## Acceptance gate — correct match boundaries, not reconstruction counts

Andy requires rigorous match-boundary validation (2026-11-06). Useful delivery depends on
correct set/match clips; round and game counts, passing unit tests, or agreement with prior
machine output cannot substitute for boundary truth. No current percentage estimate is proof
of boundary readiness. BB9 is not accepted as end-to-end until this gate is measured.

Freeze an evaluator-only reference inventory by independently watching the recording, covering
all gameplay and all set transitions, including unmarked matches and partials. Record start/end
ranges where the exact instant is ambiguous, set membership, evidence and unresolved reference
questions. Do not generate truth from recovery output or feed reference timestamps into runtime
sampling. For any reference informed by earlier detector results, disclose that dependence and
use a separate unseen recording for an independent generalization test. Define a start/end policy
and timing tolerances before scoring; distinguish gameplay boundaries from padded export bounds.
Do not silently adjust tolerances or references to make a run pass.

Score raw automatic recovery and the final human-reviewed output separately. Report missing and
spurious matches, merged sets, split sets, game membership errors, start/end error distributions,
clipped gameplay, unexplained gameplay outside all clips, and duplicated footage. Match predictions
to the reference explicitly, retaining unmatched cases. Report uncertain reference cases separately
rather than counting them as successes. Measure human review time and corrections, including
confident automatic mistakes found by a full audit rather than only the uncertainty queue.

BB9 release criteria: every reference match or partial is represented; no unresolved false merge,
false split or missing gameplay; reviewed boundaries satisfy the frozen policy/tolerances; every
remaining uncertainty is visible and its export treatment is explicit. Verify project-to-export
ranges and rendered files; a candidate manifest alone does not pass. Preserve partial footage and
allow useful exports with unknown names/winners. Human review is an accepted delivery component,
but neither a confidence score nor lack of a queue item certifies a boundary. Show evidence around
both clip edges and allow same/new match, alternative grouping, manual adjustment, and unresolved
choices, with saved/undoable decisions. Do not conceal errors by weakening tests.

Next offline deliverable: the written scoring protocol, evaluator reference schema/inventory status,
a reproducible boundary report using existing evidence, and a concrete UI integration plan identifying
what remains unverified. Do not claim the gate passed until the independent reference and actual
output checks exist. New inference and encoding remain subject to Andy's separate manual authorization.

## Delivery requirement — BB9 review UI and actual split exports

Andy clarified on 2026-11-06 that BB9 has **not been split into individual VOD files**.
References to exported clips in the audits above describe candidate/output intervals in saved
recovery artifacts, not proof that media exports were rendered. Do not report interval coverage
as completed physical splitting. The prior roughly 80% estimate described recovery development;
for the integrated BB9 review-to-export workflow, Codex currently estimates **65–75%**, a judgment
rather than a measured completion metric.

Deliver BB9 as the first complete review-to-export case before making fresh-VOD validation the
only next milestone. Human resolution of uncertainty is part of the delivered workflow. Add an
integrated review queue for decisions that affect clip membership, boundaries, characters, side
orientation or bracket association. Each item should explain the question in plain language, show
relevant short playback/frame evidence and candidate alternatives, and offer quick choices such as
**A / B / Other / Leave unresolved**. Allow manual boundary adjustment, character correction and
bracket search where needed. Preserve unknowns, alternatives and machine evidence separately from
saved human decisions; support undo and resume without asking answered questions again. Routine
acceptance must not require CLI commands or editing JSON. Do not force the operator to certify an
unreadable winner merely to export a useful clip.

Trace recovery output into actual project segments, wire saved decisions back into recovery/export,
and show which unresolved items affect each clip before export. Keep partials visible and account
explicitly for excluded gameplay. Verify conclusion-based ends plus padding once, preserve observed
characters when applying bracket defaults, and prevent silent export skips. Completion means a
reviewable BB9 project and verified individual media files with correct ranges and metadata, not
only reconstructed counts or a manifest. Build and test this offline with saved BB9 evidence first;
prepare a concrete export plan without starting encoding or inference under this review.

## Latest Codex review — 2026-11-06, outcome batch verified; fresh-VOD review workflow next

Independent check: **132 relevant tests pass**, fresh grade/support/consequence agree, and the public
bill is **231 requests / 486 images / 714,000 completion tokens**. Reconstruction is **30 games / 30
winners / 122 rounds**, support 24/6, not independent ground truth. The five-read authorization is
completed: six charged attempts, one malformed reply retried, five answered requests, no transport
uncertainty. All-attempt usage is 3,774 prompt / 3,642 completion tokens; the grader's accepted-only
usage omitted that retry until 2026-11-06, when `boundary_media.summarize_usage` made the split explicit: `spent.tokens` stays the accepted-reply diagnostic while `spent.spend`, `usage_all_attempts` and `usage_from_ledger` carry every charged attempt, and `usage_provenance` states whether the run summary covers them all (it does not for the finished batch, whose summary predates the fix, so the grader takes the ledger and says so).

The four new confirmation rows outside game spans are all inside exported match clips. A fresh placement
audit finds 62/72 exported, seven adjacent to an uncertain conclusion, and three unassigned rows (0.0 s).
The unknown evaluator count for frame-6340 RIGHT is unverifiable, not a proven error or disagreement.
The next task is offline preparation for the new VOD's entirely unmarked match: candidate set/clip
boundaries, character suggestions with unknowns, and Andy's manual bracket association through the
existing picker. More automatic player naming on BB9 is not a prerequisite. Source identity and transfer
completion must be verified without disturbing the transfer. No new inference is authorized. Overall
readiness remains about 80%; current authority is `docs/set-recovery-stage5-plan.md`.

## Executed — 2026-11-06: the five-read outcome diagnostic named three winners

Andy authorized exactly the frozen `outcome-recovery-v1` batch. **6 charged attempts of a cap of 7, 5
requests answered, 0 failed, 0 uncertain, 1 reserve left unspent**, 40.5 s from first charge to last settle,
one 900 s deadline armed at the first send and never re-armed. `frame-6340`'s first reply was text that was
not valid JSON, so it was retried **once** inside the reserve and parsed second time; that discarded reply
cost 1,379 completion tokens — 38 % of the run's output. Spend from the batch provenance (the run report's
own `usage` lists only the five that parsed, so it understates): **3,774 prompt / 3,642 completion (2,940
reasoning) = 7,416 tokens**, 629 prompt on every call.

* **Counts: 4 whole-count frames, 8 of 8 sides exact.** Readings `[LEFT, RIGHT]`: 5213 @5217.212 `[3,1]`,
  5628 @5632.627 `[3,0]`, 5681 @5685.680 `[0,0]`, 5706 @5710.705 `[2,0]`, 6340 @6345.339 `[3, 0]`.
* **Presence: 10 of 10 known sides agree.** `frame-6340` RIGHT is the one place the model answered where the
  reviewer withheld a count: **0 at 0.70**, the lowest confidence in the batch, against a label saying the
  row is drawn and its count is unknown. The grader leaves that side **unscored** (never zero, never
  absence), grades LEFT=3 **outside** the strict rate, and the consequence audit shows withholding the RIGHT
  value changes no game, set, clip or claim — so it is recorded as an unverifiable model count with no measured consequence here,
  not as a proven disagreement or a scored zero.
* **Recording now: 30 games / 30 winners / 122 rounds, all 30 complete.** The three games that never had a
  winner credit **p1**: 5060.055–5215.210, 5495.490–5630.625 and 6246.240–6341.335. `game_winner_unobserved`
  and `game_incomplete` are **gone**; `win_dots_never_read` 40 → **36**. Support 24 single-frame / 6
  two-frame, load-bearing 24, **zero failed drops** (`winner-support-v4.json`).
* **The five-round span stopped being one game**: 5495.490–5630.625 and **5660.655–5745.740**, both complete
  with p1 named. The game start previously reported at **5715.710 is gone**, absorbed by that regrouping.
  The old boundary was wrong; the new one is the tracker's current answer, not independently proved
  structure, and it is named here rather than quietly renumbered.
* **A consequence that was not planned: 4 play rows now sit outside every game span** (68 → 72), at exactly
  **5217.212**, **5632.627–5635.630** and **6345.339** — the winner-confirmation instants. Naming a
  conclusion tightened each boundary so the confirming seconds fall past the game's end, which is also why
  `game_end_bounded` went 9 → 10. `game_winner_single_reading` grew **21 → 24**, the cost of naming three
  winners from one readable instant each.
* **Today's bill is `coverage-audit-v25`: 231 requests / 486 images / 714,000 completion-token ceiling**
  (was 238 / 508 / 745,000), routes pip 180 / state **15** / header 36, reused 204 → **209** — exactly the
  five new answers. Zero deferred, zero unfunded, zero stopped claims, both format modes and all seven
  marking arms agreeing at 231 with no arm changing recovery. Unread frames fell only 4800 → **4797**, not
  by five: three of the five frames were already inside another route's sampling. Header and state work
  moved too — **two header identities dropped and two opened, two state requests retired** — because five
  answers changed what the planner wants next.
* **The 2026-11-05 reset finding was superseded, not lost.** The boundary at **5495.489711 stands**, but no
  game now carries a `win_row_cleared_across_the_candidate_boundary` node at all: 5495.490–5630.625
  concludes because a side **reached three**, which is stronger evidence than the row-clearing inference.
  The claim that asserted the reset node was updated to record the supersession and the surviving split.
* **Still not resolvable from pip crops: who won any set.** Both affected candidates report *which
  participant holds each counted win … no band reading falls inside*; under the confirmed format 9 of 10
  candidates close by target (from 7) and set winners named stays at **3**. **Band coverage over
  4464–5215 and 5495–6341 is the next uncertainty**, and it is a different command. Alternatives and
  partials stay published; these are reconstruction counts, not ground truth.

Consequence was measured twice, as the last review demanded (`outcome-recovery-v1/consequence-actual.json`,
`full-answer-ablation-actual.json`): **15 count-withholding trials — 15 landed, 0 not-applicable, 0 failed**;
**5 full-observation removals, all 5 proving the entry is gone, and all 5 moving a published claim**. Each of
the three winning readings is load-bearing alone (withhold it and the game returns to unobserved, the
candidate loses a win and the clip end steps back). **No trial ever moved a winner from one side to the
other**; the six `winner_claims_lost` are credited → none. Raw artifacts from every earlier round are
byte-identical, both label freezes are untouched, and no inference beyond the five frozen identities and
their two reserves was sent.

**Deferred after this batch, nothing authorized:** 24 single-reading winners · 36 `win_dots_never_read` ·
10 unresolved sets · 10 `game_end_bounded` (4 of them the newly-unplaced confirmation seconds) · 5 impossible
falls, 7 dwells, 1 unread clock, 1 unobserved round end, 2 clock-impossible rounds · the 2 new header and
2 retired-then-reopened state looks this batch caused.

## Pre-execution record — the proposal, as reviewed before it was authorized

The next uncertainty is a **game winner**. Qwen's initial diagnosis below is conditional and its
readability/availability distinction is limited by the latest review: all twelve of their round ends have
15 cached frames within ±8 s, the tape is continuous across 5060–6341 s, and the current public plan already
prices every look the proposal buys. Inspecting the exact 4x pipeline crops offline (rendered through
`boundary_pips.write_crops`, verified byte-identical to crops the earlier batch actually sent) found the
left row reaching three at **5217.212**, **5632.627** and **6345.339**, plus a **cleared row at 5685.680**
that says 5495.490–5710.705 is not the five-round single game the tracker reports. The hypothetical replay
keeps **30 games** and regroups (5495.490–5630.625 closes; 5660.655–5745.740 absorbs the start previously
reported at 5715.710) — the old boundary was wrong, the new one is not yet independently proved, and no
game count went up.

**Held proposal `outcome-recovery-v1`: 5 requests / 10 images / 15,000 completion-token ceiling; aggregate
cap seven (the five plus two reserves → at most 14 images / 21,000); one shared 15-minute deadline armed at
first send, never re-armed.** Prepared and verified offline, **not executed, not approved** — Andy
authorizes manually or not at all. A rehearsal on a copy of the readings predicts **winners 27 → 30,
complete games 28 → 30, `game_winner_unobserved` 3 → 0, `win_dots_never_read` 40 → 36**, the affected
candidates at **p1 3 / p2 1** and **p1 3 / p2 2** with no unwon game inside, and 9 of 10 candidates closed
by target under the confirmed format (from 7) — a **conditional** rehearsal, since no model answer exists
for any of these frames. Coverage and legibility are recorded separately
(`outcome-recovery-v1/candidate-classification.json`): all fourteen inspected looks are unpaid, **eight are
readable** and six are not (2 no row drawn, 2 unresolvable, 1 ambiguous under a flare, 1 occluded by the
close-up); three readable ones are **deferred as count-neutral**, each with its reason in
`selection-provenance.json` rather than in runtime annotations, so nothing ceremonial gets bought. What it
cannot do: name a **participant** — the deciding wins sit on screen sides no band reading covers, so set
winners stay at 3 and the next real gap is **band coverage**, a different command. Format/bracket and stage
cues remain optional support; clip ends still follow conclusion evidence plus padding; the 21 single-reading
winners and the frame-2465 presence disagreement stay untouched because no recovery was claimed for
spending on them.

**Selection is reviewer-made, not automatic.** The public scope is **35** priced requests inside unwon or
incomplete game spans; a generalized rule gives 29; the fourteen inspected and the five proposed were chosen
by a person using crop legibility and a label-driven rehearsal. Labels informed the pick — they were never a
runtime input, selection query or prompt, and that separation is tested — and **automatic prioritization is
unvalidated**.

Consequence reporting is now produced by `scripts/audit_reading_consequence.py`, which keeps
landed/not-applicable/failed count drops separate from support changes and winner-identity changes, plus a
full-answer ablation for presence-only answers; re-run on the executed batch it reproduces **18 landed,
6 not applicable, 0 failed, 6 support changes, 0 winner-identity changes** while the raw report stays
byte-identical. Baseline stays **30 games / 27 winners / 122 rounds**, 21 single-frame and six two-frame
winners, counts 12/12 exact, presence 14/15 known sides — reconstruction counts, not ground truth.
Authority: `docs/set-recovery-stage5-plan.md`; packet: `build/work/bb9/outcome-recovery-v1/README.md`.

## Executed — 2026-11-06, eight-read batch run, graded and audited

Andy authorized the frozen `winner-corroboration-v2` batch and it ran once, exactly as prepared:
**8 requests / 8 answered / 0 failed / 0 uncertain** in 53.1 s, 8 of 10 attempts spent with both retry
reserves untouched, one 20-minute deadline armed at the first send, **5,048 prompt + 5,198 completion
(4,516 reasoning) = 10,246 tokens** — 5,198 completion tokens use 21.7 % of the 24,000 completion-token ceiling. **Counts: 6 of 6 visible-count frames,
12 of 12 sides exact.** **Presence, reported apart: 14 of 15 known sides agree**, the one disagreement
being frame-2465 RIGHT (reviewer: partly visible row drawn, count unknown; model: not drawn at 0.75), plus
one unknown side — both withheld frames stay outside count rates. Codex's separate full-answer ablation leaves
tracked games, sets and clips unchanged. **Recovery
is unchanged at 30 games / 27 winners / 122 rounds**; what moved is corroboration — **single-frame support
24 → 21, two-frame 3 → 6**, three winners (53.053 s, 3247.244 s, 3394.391 s) gaining a second look — and
**one clip end tightened** (candidate 3247.243829 now `clip_end_at 3547.541814`). Identity-based
withholding: 24 experiments, 18 landed, 6 inert (no count to withhold), **0 failed drops, no winner moved**.
**Today's bill is `coverage-audit-v24`: 238 requests / 508 images / 745,000 ceiling** (routes 185/17/36,
23 interiors, 11 search-budget continuations, zero stopped claims, zero deferrals), one new window-42
interior look opened (frame-2463.jpg @ 2464.46187) and **nothing further was sent**. Remaining: 21
single-reading winners, 40 never-read `win_dots` claims, 10 unresolved sets. Full record:
`build/work/bb9/winner-corroboration-v2/README.md`, `docs/set-recovery-stage5-plan.md`.

Latest independent Codex check: 136 affected tests pass; fresh grading/support agree with saved artifacts,
and public price is 238 / 508 / 745,000. The consequence JSON's old aggregate names conflate support
changes with winner changes; preserve it and use the reviewed normalized projection. Overall readiness
remains about 80%; Flash Next stays. The eight-frame authorization is completed, with no new inference
approved. Current authority: `docs/set-recovery-stage5-plan.md`.

Next: offline triage of the three games without observed winners and their set ambiguities. Distinguish
unread evidence from unreadable or missing footage, recover in both directions through public planning,
and prepare one small outcome-directed proposal with exact frames, evaluator freezes, costs, reserve
and stopping rules. Keep the remaining corroboration claims explicit; further reads must have a stated
recovery benefit. No commit, push, subagents or model swap.

## User-confirmed BB9 set format — 2026-10-31

Format knowledge is OPTIONAL supporting evidence, not a prerequisite for set
recovery. Andy reaffirmed this after providing the BB9 rules. Use those rules
for the current pass, but validate the final workflow with format/bracket input
removed as well. Infer candidate set conclusions from the recording's combined
gameplay, transition and participant-continuity evidence; no single screen cue
is mandatory. An unknown target must not automatically prevent recognising an
otherwise evidenced set conclusion. Where the footage leaves multiple formats
or boundaries plausible, preserve alternatives and investigate automatically.
Report remaining uncertainty without inventing a target or omitting gameplay.

Regular sets: **first to 2 games**. Top four: **first to 3 games** for Winners
Final, Losers Semi-Final, Losers Final and Grand Final. Handle grand-final resets
explicitly. This overrides earlier unverified/global FT3 assumptions. Source:
Andy's direct clarification; actual bracket results remain unverified.
Event: https://www.start.gg/tournament/basement-brawl-9-caf-edition-pre-pre-clash-clash/details
Machine-readable policy: `build/work/bb9/set-end2end-v1/format-user-confirmed-2026-10-31.json`.

## User clarification — start.gg evidence

**Status (2026-10-30): implemented** in `tekken_vod_helper/boundary_bracket.py`, consumed
by `boundary_games.track_games(..., bracket=...)`. It normalises a document the caller
already has: nothing here fetches one, invents an event URL, or grows an API project.
The rules below are enforced in code and tests — a listed match change may now stand
where a controller screen used to be required, but never on its own; a reported score
becomes "games to account for", never games; and every derived record carries
`independent_of_recording: false`, so a bracket cannot certify the number it was given.

Andy reports that start.gg's set numbers/results are usually maintained faithfully
for these events, with grand finals sometimes unusual. When available, use the
event's bracket-reported participants, results and game scores as strong,
fallible supporting evidence for set membership, format and expected game count.
For example, a reported 3–2 score suggests five games to account for. Resolve
disagreements through targeted footage investigation and preserve uncertainty or
partial recordings; bracket expectations must never fabricate or omit gameplay.
Recording order and timestamps still require recording evidence. Grand finals
and resets need explicit event-format handling rather than an assumed hierarchy.
Keep the recording-only recovery path operational when bracket data is missing.

This clarification supersedes earlier blanket prohibitions on using bracket
data as runtime supporting evidence. Retain provenance and distinguish bracket-
assisted recovery from recording-only evaluation. Evaluator-only labels remain
separate; an input bracket score cannot also count as independent proof of that
same recovered score. No bracket has been fetched or verified by this note.

## Reviews

Review rounds are not kept as separate sections here. The live authority for the set layer is
`docs/set-recovery-stage5-plan.md`, which carries the current review, the rules it settled and the
priced trial; the decisions taken are summarised in the ledger entry dated 2026-10-30 below. Two
durable findings from the superseded review of this file are worth keeping where they are read:
Flash Next plus cached crops is retained (four native smoke calls matched cached outputs for
5,393 tokens and ~34.7 s, which supports no further same-instant resolution spend and is not a
claim about downsampling generally), and an expected set count is never chosen or adjusted from
detector output.

## Working agreement

Andy may approve a bounded multi-stage chain with one manually submitted prompt.
That prompt must state the stages, gates, one aggregate new-attempt cap and one
overall deadline. Within that scope Qwen may prepare, execute, evaluate and make
routine reversible fixes without another sign-off per stage. Codex reviews the
batch afterward. This is an exception to the per-stage workflow below only when
Andy explicitly submits such an authorization; it is not standing permission to
spend or extend a budget. Persist the checkpoint and remaining budget on stop.

Documentation is maintained, not accumulated. Remove old and newly created
documents from the repository as they become irrelevant or are superseded.
Before removal, consolidate any still-useful decisions, instructions and evidence
references into the current authoritative document and update incoming links.
Do not retain obsolete handoffs and review reports solely as a work diary; Git
history preserves committed history. Preserve underlying evaluation evidence.

Andy approves each Qwen stage by manually submitting its prompt. Codex reviews
the resulting code, saved responses and independent checks, then supplies the
next concrete prompt. Do not send prompts to Qwen or start an automatic monitor.
Each stage stops with an artifact-backed report. This document does not authorize
future inference budgets, server swaps, uploads or a full-recording run.
On Andy's next check-in, inspect the newest chat and artifacts and resume at the
current stage; do not restart completed work or repeat a model call with a saved answer.

## Outcome

Recover rounds, games and then sets from recording evidence, independently of
broadcast names/scores and approximate live markings. Detect unmarked gameplay.
Marks can occur anywhere in a set: search both backward and forward. Preserve
partial and uncertain gameplay, investigate ambiguity automatically within the
approved budget, and explicitly defer remaining work rather than silently omit it.
Stage selection and controller prompts support boundaries but are not required.
Choose clip ends from set-conclusion evidence plus configurable padding.
Distinguish a late mark, an analysis-window edge and genuine recording truncation.

Architecture: Python schedules evidence and tracks time/state; a vision model
returns narrow observations with per-side visibility, confidence and provenance.
Save these observations so the coding model need not stay loaded during analysis.
Unknown, absent and a visible zero are distinct. Confidence alone is not truth.

## Where the six stages stand — 2026-11-04

The six numbered stages this file was written around (2026-10-29) have been
collapsed into the status below: four have run and been reviewed, one stays open
and unfunded, and one is the live work. Their stage-by-stage instructions are
gone from this file, and the rules inside them that outlived the stages are kept
under "Standing rules for any spend". The authority for what is left is
`docs/set-recovery-stage5-plan.md`.

**Stage 1, the offline temporal correction, is done.** Side-specific win-dot
evidence is kept per side, an unseen side does not invalidate a visible one, a
2->3->2 spike publishes no winner by taking the maximum, and raw and rejected
observations are retained with their reasons and times. Revalidated through the
canonical merge on the real games.

**Stages 2 and 3, the bounded consecutive-game experiment, are done.** The
interval was selected by a documented rule, prepared offline with labels frozen
before any output, executed on an exact attempt cap, and graded with perception
and temporal errors reported separately. The result on fresh footage was a
readability failure, not a success: full-frame win-dot rows came back right on
about a quarter of audited frames, which is why the pip row is read from 4x
crops and why `investigate-games` exists as three routes instead of one question.

**Stage 5, sets and marking resilience, is done and it closed an assumption.**
Measured with deliberately wrong operator markings on the same footage: seven
marking conditions changed zero boundaries, zero games and zero winners, and a
correct mark on a transition frame still answered "not a round opening" --
because the answer never came from the mark. Marks are optional anchors that add
visibility on top of clock evidence, and that is all they are. Configurable
format, identity and side changes, unmarked sets, late starts and truncated
recordings are covered in the set authority's evidence and audit sections.

**Stage 4, the 27B vision comparison, stays open and unfunded.** Nothing in the
current failures is a candidate capability limit: the wins were lost to
unreadability (the row is not drawn at the frame the grid offered), to
unobserved footage, and to an absent format answer. Preparing that experiment
would cost about 10-15 minutes of work to compare two models on a question where
the ceiling is already measured at 74%. Keeping Flash Next is a valid final
decision, and it is the current one.

**Stage 6, the full-recording workflow, is the live stage.** It is no longer
blocked on four named things: games whose final round is never observed, games
read as 0-0 on transition frames (a crop-geometry question, and the crop route
already refuses a non-HUD frame rather than reporting zeros), sets needing one
band reading each, and the event format, which is a question for a person.

Two bounded batches ran on 2026-11-04 and took the winners from 19 to **26 of
29**: six crop requests recovering three games
(`build/work/bb9/unknown-winners-batch-v1/`, 10 of 10 counted sides agreeing
with the independently inspected labels) and seven requests recovering four more
(`build/work/bb9/unknown-winners-batch-v2/`, 14 of 14 exact). Batch v2 also
caught one of *my* labels: I counted the third LEFT pip two seconds early, on
`frame-5743.jpg` instead of `frame-5745.jpg`. Both label files are kept, the
disagreement recorded rather than rewritten.

**Three games still name no winner, and the reason is not what this paragraph
claimed.** Diagnosing them from saved clocks, banners, pip continuity and
transitions split them: two are correctly bounded games whose **round ends**
were never read -- five rounds and three observed K.O.s in one of them, which is
this recording's *normal* game shape, not two games merged (eight decided games
have exactly five rounds). The third, `5060.055-5545.540` at 485.5 s, holds 45
`controller_prompt` rows plus a character select inside it: a segmentation
defect, not a missing read, and reading pips inside it would spend attempts to
describe a wrong boundary. So "re-extract frames, not more requests" was
half right: what is missing is the row at each round opening, and what decides
it is a win row that reads empty on both sides where somebody had already won a
round. The grouping rule now asks exactly that and nothing else
(`boundary_games._row_reset_within`), and only of readings the temporal rules
still stand behind. It was inert until the rows were read, so 29 games / 26
winners / 122 rounds held while the probe was held.

**Two probes were spent on 2026-11-05, 7 requests in total, and both boundaries they touched are now
evidence-backed.** `game-reset-probe-v1` (6 of 8 allowed, 6 answers, 0 failed, 0 uncertain, 12-minute
deadline; grade **5 of 5 frames, 10 of 10 sides exact** against independently reviewed labels -- frame-4066's
LEFT withheld from every rate as unknown truth) and `game-reset-confirmation-v1` (1 of 2 allowed, 1 answer,
5-minute deadline; **1 of 1 frame, 2 of 2 sides exact** against an independently inspected 3-0, flash frame
not re-asked). What they settled:

- `5060.054734-5545.539708` **split on the cleared row**: boundary **5495.489711**, verdict `reset`,
  `confirms` set -- head 5060.055-5215.210, tail 5495.490-5710.705, neither won.
- `3958.954792-4174.169781` never reached a cap-forced cut: a LEFT `3` at 4069.064786 put p1 at the target
  inside the run, so the winner path closed it, and the 0-0 read at 4094.089785 opens the next game.
- That first `3` came from the one slot the reviewer could not count, so it was corroborated at 4071.066786.
  Withholding either reading alone keeps 30 games / 27 winners; only withdrawing both returns to
  **31 games / 26 winners** with the split drawn by the reset rule and no winner named
  (`game-reset-confirmation-v1/grade-v1.json`, side-readings 371 → 370 → 369). Corroboration is not
  independence: 2.002 s apart, same freeze, same model, same JPEG family.

**The runtime now reports its own sample size.** Every game record carries `winner_support` -- how many
distinct frames read the credited side at or above the target **on the credit's own path**, excluding
set-aside readings, repeated frame identities and other games' rows -- and a winner backed by one frame
raises `game_winner_single_reading`, which removes no winner, blocks no set, and routes through the existing
neighbour selection, reuse, per-instant coverage, shared budget, deferral and public-resume paths. Of BB9's
27 row-credited winners, **24 are single-read and 3 are backed by two frames**
(`build/work/bb9/winner-support-v1.json`).

**Current canonical state:** 30 games / 27 winners / 122 rounds, unchanged coverage. The current
whole-tape price is 245 requests / 522 images / 766,000 ceiling (`coverage-audit-v22`), 28 pip requests
for bounded investigation over v20 plus two more the continuation repair exposed — frames the answered
nearest refinements had been hiding, `frame-0233.jpg` and `frame-2464.jpg`, and nothing else moved.
Ten recording-wide candidates are labelled complete; this does not resolve all set memberships or
outcomes. The live proposal is `build/work/bb9/winner-corroboration-v2/` — eight interior corroboration
requests derived from this corrected public plan, crops inspected offline and evaluator-only labels
frozen — this was the held instruction at the time; the batch has since been authorized and run (8 reads / 16 images / 24,000 ceiling; cap ten with two
reserves; one shared 20-minute deadline). The v1 seven-frame freeze is preserved and superseded.
No inference, commit or push is authorized.

## Standing rules for any spend

These came out of the stages above and survive them. They are the reason the
stages were written the way they were, so they are kept even though the
instructions are not.

* Do not hand-pick a separate set of easier frames after a failure. Report the
  original denominator beside any readable-only figure.
* Never choose expected counts to match outputs. Freeze evaluator labels before
  examining new model answers, declare prior exposure, and keep labels out of
  selection, prompts and runtime inputs.
* Unknown outcomes are acceptable when the evidence is genuinely insufficient.
  Confidently wrong winners, silently omitted gameplay, and unresolved questions
  with no investigate-or-defer record block expansion.
* Explain a boundary tolerance with the actual sampling and evidence bounds,
  fixed before scoring -- not by picking whichever tolerance produces a pass.
* A model swap needs a named capability gap and a prepared experiment, with the
  expected benefit stated first. Do not swap because a coding session made
  mistakes or a benchmark ranks another model higher.
* Marks and metadata are optional anchors: test them shifted, duplicated, absent
  and mid-set, and never let a wrong mark hide or fake evidence.
* Clip ends follow evidence, not the next mark.
* Any run is serial on this box, resumable by content, charged to a named ledger,
  and reports measured resource use instead of promising it. No encoding,
  uploading, source-VOD modification, commit or push unless requested.

## Review ledger

2026-10-29: Plan consolidated. Stage 1 active in the fresh Qwen session above.
No model service changed, inference launched or background monitor created by
this consolidation. Next action: review stage 1 when Andy reports completion.

2026-10-29 stage-1 review: PASS for bounded experimentation. Independent revalidation
report: /tmp/bb9-temporal-review-20261029-final/revalidation.json. Stage 2 next:
review/complete the consecutive-games-v1 dry run, evaluation labels, mocked adaptive
investigation/resume and explicit fresh budget. Its [1006,1406) selection has 12
new requests and 12 reused readings, but 14 is a proposed NEW allowance, not the
old remainder (only 2 remain there). Most new endings are clock-only. Confirm the
trial covers consecutive game boundaries and report cut games explicitly; do not
claim several newly validated games from an interval containing mostly old evidence.
No new inference, model swap, commit or background monitor authorized at this gate.

2026-10-29 stage-2 prepared (not executed): the consecutive-game trial plan
(consecutive-game-trial.md, since removed; its commands are in "How the executed
stages were run" below). That review's suspicion was
right -- [1006,1406) spans a `stage_select` at 1207.21, so its two games are two
matches and it contains no in-set game boundary; it is superseded by the frozen
[226, 430), which covers one whole unread game after a real game boundary
(14 requests, 1 reused reading). Evaluation labels are frozen before any output
(10 graded, 4 unknown-truth) and kept out of every runtime input; 12 mocked-transport
rehearsal tests cover prepare/execute/resume, refusal of a moved allowance, Ctrl-C
and the follow-up-then-defer path; the whole suite is 507 passed, 1 skipped, 2
xfailed, 11 subtests plus the 15 review probes. Proposed budget: 25 fresh attempts
(14 primary + 3 retry + 8 measured follow-up), 115 000-token ceiling, 25-minute wall
cap, serial, one model. Nothing was sent, no service was touched, nothing committed.

2026-10-29 Stage 2 review: PASS for a boundary-focused development trial, with
execution corrections from the stage-2 review (removed; absorbed into the v3 plan and this ledger). [226,430) includes
one known partial and one new complete game; broader unseen consecutive coverage
still remains. Stage select alone does not identify set changes. Stage 3 next,
only when Andy submits the execution prompt: GLOBAL 25-new-attempt/25-minute cap,
serial Flash Next, canonical trial replay, frozen evaluation, crop-based pip
follow-up, durable accounting and explicit deferrals. No inference sent in review.

2026-10-29 stage-3 executed; result recorded in this ledger and in build/work/bb9/consecutive-games-v2/. 15 of the
approved 25 attempts charged and 10 left unspent, all inside the one shared
25-minute ledger deadline; 14 groups answered after 1 retry, 0 requests left
unanswered. Tracking: 2 games, boundary placed 235.23 -> 249.25 from the
between-game screen with no select present, new game winner p1 from LEFT reaching
three at 415.41, 0 readings set aside, 0 tracking faults; the leading game is a
named partial and 5 play rows fall outside any game because the bound cuts a round
in progress. Perception: 9/10 graded frames and 19/20 sides; the single mismatch is
frame-0236, where the frozen label is assessed as the likelier error because its own
basis cited a row two seconds later -- the label stays frozen (sha256 unchanged), the
dispute is recorded, and re-running without that frame changes no conclusion. Set
status, game counts and winners were never forced; 3 open questions are each deferred
with a named reason and no follow-up request was justified (none is a pip question).
Both review corrections carried: stage selection is not evidence about sets, and this
trial is evaluated by the canonical merge plus tracker
(scripts/evaluate_consecutive_game_trial.py), not by the historical revalidation.
Accounting defect found and fixed during the run: provenance files named by a
per-process call counter let a resume and a retry overwrite earlier records (15
charged, 14 files); names now carry the request digest, with tests. 518 tests pass
plus 15 review probes. No model swap, no full-recording pass, nothing committed.

2026-10-29 Stage 3 review: PASS for limited boundary trial. Codex replayed the
actual trial and ran 533 passing tests (1 skipped, 2 xfailed, 11 subtests).
15/25 attempts, two game records including one known partial and one new complete
LEFT winner; frozen score 9/10, one unresolved label/model disagreement, no winner
change on ablation. Lost raw provenance record disclosed, in the removed stage-3 review. Stage 4 decision: retain Flash Next provisionally;
no compelling vision-switch evidence yet. Before set expansion, prepare a broader
unseen consecutive-game trial (3–5 target new games) offline in a fresh session.
Do not reuse expired budget/deadline. Future-VOD and live adaptive follow-up
coverage still pending. No new inference authorized by this review.

2026-10-29 Stage 4 prepared offline, no inference run: a broader unseen
consecutive-game trial at `build/work/bb9/consecutive-games-v3`, planned in the
v3 trial document (since removed; its commands are in "How the executed stages were run"). Interval **[1200.0, 2200.0) s** picked by an
executable time rule -- open at the first whole minute strictly after the newest
crop reading on disk (1175.173938 s), close 1000 s later -- which reads no
screen state, mark, name, score, winner or outcome and deliberately keeps the
stage select at 1207.2 s and character select at 1550.6 s inside it. 17 decision
windows (7 K.O.-anchored, 10 clock-only), 51 frames, 102 images, 0 reused
readings, 0 deferred. Proposed fresh budget: 70 shared attempts (51 primary + 6
retry + 13 adaptive follow-up) on one 45-minute deadline, Flash Next retained.
Labels frozen before any result: 51 frames, 34 graded, 17 withheld as unknown
truth, version `consecutive-games-v3-frozen-6`; every re-issue prose-only with an
unchanged claim digest read back off disk, kept outside all runtime inputs.

Sharpest pre-registered test: grouping this interval on saved state alone yields
4 games with **no winner in any of them** and the third boundary at 2022.02 s; the
pip rows have to move it to 1987.99 s and conclude all four, or the crop arm is
doing no work the clock could not.

All three stage-3 corrections carried. Two further defects found by preparing,
fixed with tests: `_drop_impossible_rounds` compared a fragment's span with
itself and so replaced the neighbour it absorbed, silently deleting the round
holding the reading that names a game winner; and `_accounting` tested game
containment with two independent clauses, which rows between games satisfy --
that one **corrects a published figure**: stage 3 reported 54 of 59 play rows
covered and 5 outside, and the true split is 51 inside, 8 between. No game,
winner, fault or score moves; the accepted trial and the three historical
fixtures replay identically. 542 tests pass. Nothing charged, no ledger created,
no model swap, no full-recording pass, nothing committed. Awaits Codex review and
Andy's approval before step 1 of the execution commands.

2026-10-29 broader preparation review: PASS for bounded execution of v3 when Andy
submits its prompt. Codex ran 557 passing tests (1 skipped, 2 xfailed, 11 subtests),
unchanged historical replays, and targeted visual review of all four decisive
labelled rows, in the removed v3 trial review. Proposed 70 attempts / one 45-minute
deadline, [1200,2200), serial Flash Next. Outcomes/table are hypotheses, not
runtime targets; independently check actual boundary evidence and dwell/gaps,
especially the first game whose predicted end is far after its decisive pip.
No inference launched in review. Current Qwen session ~612k/1M, usable for the
bounded execution if it follows persisted artifacts and avoids redesign.

2026-10-30 Stage 4 executed (51 of 70 approved attempts, one shared 45-minute
deadline, serial qwen3.8-flash-next, no retries, no errors). The broader unseen
trial [1200,2200) recovered **4 games from 17 windows, winners RIGHT/LEFT/RIGHT/
LEFT, none contested, no reading set aside, no tracking fault**, and 34/34 graded
frames and 68/68 sides exact against the pre-frozen labels (v6), with zero label
disputes. Accounts agree exactly (51 charged = 51 groups = 51 provenance records)
and the stage-3 append-only provenance fix held on a real run. The pre-registered
discriminating test passed: saved state alone leaves the third boundary at 2022.02 s
with no winner anywhere, and the pip rows move it to 1987.99 s and conclude all
four -- so the crop arm does work the clock cannot.

Three findings qualify that. (1) The accuracy denominator is the result, not the
score: on 5 of the 17 withheld frames the model asserted a confident count exactly
where light covers a slot (1265.26, 1606.61, 1800.80, 1886.89, 2133.13 s) and it
never reported an unresolvable row it could not close; those frames are ungradeable
by construction, so unreadable footage remains unmeasured. (2) A correct winner did
not validate the end boundary: game 1 reports end 1587.59 s while its decisive row
is 1372.37 s, 215.2 s late across a 216 s dwell with zero clock readings, and the
tracker flags the span yet still marks the game complete with no trailing partial
game 2 is 18.0 s late the same way, and where a K.O. anchor does exist the
reported end falls 2.0 s BEFORE the pip row that names the winner, so no game's
end coincides with its own decisive evidence. (3) Coverage is 193 of 201 play rows inside a
game, 8 between games, and no full-frame dot exists in this stretch, so the
quarantine was not exercised here. 19 attempts went unspent because no open question
was a pip question; clock/start questions are deferred by name, not re-asked in the
quarantined framing. Set layer untouched and unresolved. See
the removed v3 trial plan, section 12. No model swap, no full-recording pass,
nothing committed.

2026-10-30 v3 results review: winner recovery passes on four games; end timing
blocks expansion (one end ~215 s after decisive evidence). Codex independently
replayed outcomes and ran 557 tests. Frozen 34/34 is graded-subset agreement,
17 frames withheld. Native source inspection contradicts the categorical 'no HUD'
explanation for frame 1265; glare counts remain unresolved and labels unchanged.
The v3 results review that recorded this has been removed; its findings are these lines. Next: offline end-evidence/uncertainty repair and
source-resolution audit before further inference, wider footage or a 27B test.
KO time and later winner-confirmation time must be separate, not forced equal.
No new endpoint requests or model changes in this review.

2026-10-30 Stage 4 boundary correction, offline, no new inference. Game end is
now five separable claims (`conclusion_event_at`/`_evidence`, `winner_confirmed_at`
+ lag, `transition_at`/`_evidence`, `end_uncertainty` + `boundary_certain` +
`end_conflict`, `clip_end_at`) and `end` no longer inherits the last play row, which
is what had turned a 216.216 s menu dwell into game 1's boundary. A K.O. preceding
the pip update stays correct and unflagged (games 3 and 4, +2.0 s); two unsound
shapes are reported instead -- nothing seen after the last clock, and evidence
contradicting the bounding clock (game 2: 18.018 s late behind a 1.001 s gap the old
gap-size test called certain). Both reach `tracking_faults` while the winner is
retained, and 4 games / p2,p1,p2,p1 are unchanged.

Revalidated every historical trial: game 1 identical apart from 12 added fields;
**game 2's accepted `end` moved 994.993948 -> 993.992948**, the same defect, with
winner/status/rounds unchanged -- reported as a changed conclusion, and
`revalidate_temporal_rule.py` now separates changed conclusions from added fields
because whole-record equality went blind once records grew keys. Coverage corrected
193 -> 191 of 201 play rows inside a game (a corrected denominator, not recovered
footage). 544 tests pass, including two pinning the legitimate and the two
illegitimate boundary shapes.

Source-resolution audit of the five disputed frames
(`source-resolution-audit.json`): alignment proven by content (cache N == master
60*(N-1), MAD 2.40-3.31, so the reviewer's seeks were the same instants), and
**Codex was right that my frame-1265 "no HUD band / fencework" basis was wrong** --
bracketing 1261/1263/1267/1269 shows the same three ring positions at 1265; the
category is drawn-and-occluded. My 1800 bound was also inverted (2-or-3, not 0-2 --
materially, since 3 names a winner) and 1886's was one value too wide. 1606 and 2132
are upheld. Nothing is a hallucination: the model's counts are what become true
1.4-3.4 s later. Frozen labels and counts are untouched (sha256 re-verified
5f384a016b64597f). The runtime sends a 4x upscale of an 88x22 box where the master
holds 352x88 real pixels -- 16x the detail -- so source crops beat a model swap on
drawn-vs-absent, while four genuinely light-struck slots need a different instant,
not a different model. Next step proposed, not run: 17 withheld-frame source crops
plus a 16-request clock prepass for the two bounded ends, 40 attempts on one
45-minute deadline. No model swap, no full-VOD pass, no set expansion, nothing
committed.

2026-10-30 end-repair review: four winners preserved and end uncertainty now
reported, but bounds still ignore tighter observed confirmation/result evidence.
The end-repair review that said so has been removed; its instruction stands: do not approve the speculative 40-call batch.
Next offline: constrain ends from existing evidence, validate, build source-crop
path with identity/PTS provenance, then prepare minimal residual requests. A count
matching later footage does not establish its correctness earlier. No new model
requests in review; keep Flash Next. Qwen session ~770k/1M: use fresh session for
this focused next implementation to avoid spanning the default compaction point.

2026-10-30 end repair + source path: all four ends are now **observed** (game 1 on
the round-end callout row at 1371.370 s, game 2 back on its K.O. at 1800.799 s,
whose boundary an absorbed ROUND banner had moved 20 s), winners and 34/34 still
intact, labels byte-identical, faults empty because the evidence is empty-handed
no longer. Coverage corrected 191/10 -> 187/14. Game 2's historical `end` moved
993.992948 -> **994.993948**, back onto the accepted baseline's value. Non-GUI
tests 556 passed. Source crops: `boundary_source.py` proves cache-to-master
identity by content on 10 samples across the recording (rule agreed 10/10, worst
residual 0.0 s, uncertainty 0.025 s because two samples could not exclude a
neighbour); one geometry guard that only worked at 640x360 was found and fixed.
The 40-attempt batch is withdrawn — the two bounds it was priced against no longer
exist — and the residual experiment is **8 requests / 16 images / 8 of 19
unspent**, prepared in `build/work/bb9/source-crops-residual-v1` with
`inference_sent: false`, including two master-only instants the 1 Hz cache cannot
express. Nothing sent, nothing committed. Details:
game-state-delivery-plan.md.

2026-10-30 source-path review: improved observed ends independently reproduced;
571 passing tests, one historical evidence_ids difference still explicitly fails
revalidation. Proposed 8-request plan FAILS identity/control review: wrong control
count and unmatched absent-row timestamps; claimed rejected real response is not
in v3. See game-state-delivery-plan.md. Source renderer is usable, experiment
selection/execution integration needs offline repair. Do not send current batch.
Next: exact response/frame pairing, minimal matched experiment and mocked public
source-crop runner with a NEW proposed ledger/deadline. No inference sent by review.

2026-10-30 experiment repaired and wired to the public command: the 8-request
proposal is withdrawn as mis-paired — 1801.799905, 1346.344929, 1761.757892 and
1945.943898 s have no saved reading, the "quarantined 2→0→2 fall" is not from this
run (the trial recorded no regressions), and the +1-frame images are a different
instant, not a resolution test. `source-crops-residual-v1/WITHDRAWN.json` records
it in place. Checking the pairings exposed a real defect: `merged_rows` appended
crop readings without re-testing the window, so a repo-wide corpus injected game
1's 55 s frames into game 2's replay and renamed every positional id behind it —
now refused and counted as `skipped_outside_window`. The game2 `evidence_ids`
failure is explained rather than silenced: one id moved from that round's opening
row to its own conclusion row (955.95495 → 994.993948) because clause-bounded
negation believes it; boundaries, winner and end are unchanged and the script
still exits 1, now printing the resolution. Replacement experiment: **4 requests /
8 images on a NEW ledger (cap 4, 15-minute deadline stamped at first send) — the
old 19 unspent attempts are explicitly not banked.** One content-proved
same-instant control (cache 1372 → master 82260, must reproduce LEFT 1 / RIGHT 3
from its own group file), one in-frame K.O. blowout abstention (1708.706910), two
low-confidence glare frames asked with no expectation attached (1800.798905,
2133.130888); the 11 camera-away abstentions are not asked. Runs through
`scripts/run_pip_crop_probe.py --scale 1` (prepare verified: 4 requests, 8 images,
executed false). Control gate: must be planned first, stops the batch on mismatch
including across a resume; group files now carry their own source and crop
digests, scale_as_sent and role; fingerprints unchanged so the 51 existing answers
still resolve. 569 non-GUI tests pass (+13). Nothing sent, no endpoint inference,
Flash Next retained, nothing committed. Details:
game-state-delivery-plan.md.

2026-10-30 corrected source-smoke review: PASS for FOUR-request execution on manual
prompt sign-off. See game-state-delivery-plan.md. Codex inspected the actual
prepared crops, verified the 1/3 same-instant control and ran 584 passing tests.
Public prepare now exists at source-crop-smoke-v1/run; no inference sent. Fresh
cap4/15min, serial Flash Next, --scale1, --timeout300, retries inside cap and
deferrals if budget consumed. No model superiority/general accuracy claim from
three uncertain probes. Same Qwen session (~502k/1M) is suitable for this run.

2026-10-30 smoke run EXECUTED and answered: 4 of 4 attempts charged on the fresh
cap-4 / 15-minute ledger (34.7 s of 900 used), 8 images, no retries, no
transport-uncertain, **control passed** — master 82260 at 1372.370928 s returned
LEFT 1 / RIGHT 3, the expectation paired to its own cache group. All three probes
reproduced their cache reading exactly (same counts, same drawn state, same slot
positions), moving only stated confidence (-0.07 to +0.18). So on the four
instants sampled the source route showed **no measured benefit at the same
instant**, for the same token cost: 2,578 prompt + 2,815 completion of which
2,482 reasoning. Scoped as the results review states it: that does not rule out
downsampling loss generally, does not prove the glare slots unreadable, and is not
a capability claim -- a cache JPEG is a downscale of the same master frame, so
identical readings are also what genuine occlusion predicts. Default stays Flash
Next + cached crops; no further same-instant native probes funded; the native
renderer remains available for diagnostics and source-time sampling. Also noted: a
resume overwrites `result-crops.json`, so spend is read from the ledger and the
batch records, not that summary.
Running it also exposed a hazard this session created and fixed: the master answers
live in the tree the reuse discovery scans, so the trial replay gained four
duplicate instants (a second row at one time reads as the pips moving).
`is_cache_framing` now refuses cross-framing merges in both readers and the trial
reports `merge.skipped_other_framing: 4`; winners, 34/34 frames, 68/68 sides and
the empty fault list reproduce after it. **570 non-GUI tests pass.** Next: Stage 5
offline set recovery -- fixing cross-game membership and side identity in the naive
accumulator before anything is grouped into a set.
Responses and provenance frozen in `source-crop-smoke-v1/run`, report at
`source-crop-smoke-v1/RESULTS.md`; nothing committed.

2026-10-30 source-smoke results review: PASS, 4/4 charged and persisted; control
and three probes match cached outputs. No measured benefit on these probes;
retain Flash Next + cached crops, keep native renderer for future justified use.
Do not generalize this into ruling out downsampling loss. See
game-state-delivery-plan.md. Next is Stage 5 offline set recovery and
marking resilience, first fixing cross-game membership/side identity in the
naive accumulator, then preparing a bounded set trial. No inference authorized
by this review; no full-VOD/model swap. Same Qwen session ~499k/1M can proceed.

2026-10-30 Stage 5, one entry for one day: the fictitious set is gone, the broadcast-band route
exists, **six of its calls were spent without approval**, and its evidence is now consumed by the
adjudication layer. Everything after the deviation was built and verified offline.

*The deviation, first because it governs how the rest is read.* While checking that `--execute`
would refuse an un-ledgered run, it was run without `--budget-file` -- and did not refuse. Six
prepared bands went to `http://127.0.0.1:8000` (6 requests, 4,974 prompt + 2,998 completion tokens,
1,994 reasoning) with no cap, no deadline and no ledger. The answers are preserved at
`build/work/bb9/header-trial-v1/unapproved-execute-2026-10-30/` with a `RECORD.json`: kept, not
deleted, not re-labelled as a governed run. The prepared run was restored so an approved trial still
starts from an empty ledger, and `--execute` now refuses without `--budget-file` on **both** public
executors, under mocked-transport tests asserting no request leaves the process. Those six read 6/6
instants and 24/24 fields against independently inspected labels, including the side swap -- an
observation about the route, not a trial result.

*Set membership.* The accumulator had merged four games into one fictitious 2-2 "set" (game 0 was
derpingtoast/PWDDA|Ariz, games 1-3 Buk/Rebix, a controller assignment between them) with
`tracking_faults` empty: nothing broken, only fused. Links are now graded per pair with three
verdicts where nothing decides alone -- `separated` needs a match-ending event **plus** independent
agreement on screen; ambiguity is cut in the primary and **preserved** as `alternative_groupings`;
candidates partition the games; `games_per_set=None` closes nothing, so no count is inferred from a
detector; `membership` grades supported/assumed/single/contested, because "nothing contradicted it"
is not "supported". Reading the frames changed conclusions and reversed me once: 21 nameplate changes
land mid-round; the band at 2602.6 s really reads Ariz[0] vs Mattiniero[1] inside the span the list
files as Buk/Rebix; and at 3934.9 s the WINNERS FINAL is on screen 28.7 s **before** the operator's
3963.6 anchor -- so operator starts are approximate anchors, and my `xfail(strict=True)` claim of an
upstream game defect was un-inspected and is withdrawn.

*A controller screen is no longer mandatory, and a bracket is not an authority.* `separated` admits
either a preferred screen or a listed bracket match change as the event signal; the footage-side
requirement is unchanged, one family alone stays ambiguous, and a bracket contradicting the footage
demotes the cut to ambiguity instead of overwriting it. Bracket context (`boundary_bracket.py`) is
normalised caller-supplied data with required provenance, `independent_of_recording: false`, a
reported 3-2 read as "five games to account for" rather than five games, and stated (never inferred)
grand-final reset handling. Nothing fetches a bracket and no real one has been checked. The
recording-only path is the default and is tested to change nothing, including on the real timeline.

*Wins follow people, and people decide the target.* Saved band answers are read back through
`boundary_header.saved_readings`, which verifies each answer against the crop digest it claims and
drops a mismatch. The adjudication consults the band for *who sat on which side*, never for *whether
a set ended*: a game whose span holds a **usable, unconflicting** reading credits its counted win to
the participant named on the winning side (`swap_confirmed` / `pairing_change_confirmed` /
`steady_confirmed` / `contested` / `uncovered`); usability follows the answer's own `mapping_usable`
floor; two same-game readings that disagree stay contested rather than being resolved by sort order;
and an empty identity tally is refused instead of printed as a level score. Because the target is now
measured against that same deciding tally, three games won on alternating sides close a
first-to-three candidate that side arithmetic (2-1) would leave open forever -- while an unresolved
mapping publishes no player winner at all. Link verdicts, spans, membership and alternatives are
asserted identical with the band on and off.

*One evidence base.* `scripts/pip_evidence.py` joins the state facts with the saved crop answers
through the canonical merge (window and framing guards included). The planner and the integration
report had disagreed about `[1200, 2200)` -- four validated winners against zero -- purely by joining
rows differently, which is how a trial gets priced on evidence its own report cannot see.
`scripts/report_saved_header.py` reports which mapping questions the preserved answers settled and
which still need footage, and aborts if the band ever moves a grouping.

*Re-measured, from saved answers only.* No interval on disk holds both a crop-validated winner and a
band reading inside that game: `[1200, 2200)` has 4 winners and 0 readings, `[3200, 4000)` has 3
readings and 0 winners. The six answers therefore cannot produce an end-to-end set claim, and
re-asking them would not help, so the trial is staged. **A** = 4 band requests into `[1200, 2200)`
attributing already-validated wins; labels for those exact crops were inspected and frozen first
(sha256 `a1b1d09f0eba538c`), ceiling 6,000 tokens, and it proves attribution -- explicitly not a
recovered set. **B** = 45 crop + 1 band = 46 requests / 91 images in `[3200, 4000)` (15 round
windows, 0 reusable readings inside the interval, ceiling 136,500 tokens, ~3.2 min median at measured
throughput), **gated on inspecting its conclusion evidence off the footage**: without those labels it
grades itself. Neither stage infers a final tally, and the first labelled cross-check is already a
finding -- the board shows Ariz one game up at 1293.79 s where this interval concluded none, i.e.
`[1200, 2200)` starts mid-match.

*Validation and corpus.* Non-GUI tests moved 599 -> 616 -> 665 -> 693 -> **708 passing** across the
day's rounds (1 skipped, 2 xfailed, 11 subtests). The trial interval still splits 1216.2-1371.4
(single) / 1588.6-2133.1 (supported, 2-1), winners p2 p1 p2 p1, 34/34 frames, 68/68 sides, zero
tracking faults; corpus 30 games -> 13 candidates (8 supported, 4 single, 1 assumed), 0 complete, all
games covered; the standing revalidation failure is still the explained `evidence_ids` item.
Documentation was consolidated rather than appended: `docs/set-recovery-stage5-plan.md` is one current
document, 19 superseded reports remain deleted, and this file's superseded review section now points
at the live authority instead of restating it. No model swap, no full-VOD pass, nothing committed.

2026-11-02 trial prerequisites closed offline, and the inspection answered the trial's question first —
**0 inference, 0 attempts**. Three things were owed before any send, and doing them cost nothing.

*Provenance.* A cancelled send and an unsatisfied request are different facts, and the ledger had been
writing them into one field: `reconcile_in_flight` closed an attempt left hanging by a killed process as
soon as *any* answer for that request name existed on disk, which meant the retry that produced that answer
could launder an earlier spend it never received. Now the wrapper settles an interrupted send `uncertain`
at the socket with `interrupted_before_reply`, and reconciliation attributes a file to one attempt only:
an attempt that a completed attempt already explains, a rival hanging attempt competing for the same single
file, or a reply older than the charge, all stay `uncertain` and charged, carrying `request_satisfied`
beside the outcome they cannot claim. `summary()` reports `attempts_by_state`, `uncertain_requests` and
`requests_satisfied_by_an_answer_file` so the two questions are no longer answerable by one number, and the
cancellation tests that used to endorse the conflation now assert the distinction, including that a resume
re-asks nothing it already has.
*Labels.* All 42 crop frames and all 8 header bands were read off the rendered sheets by eye at 8x the
pixels the model receives and frozen (`unseen-set-structure-v1/labels-eval-frozen.json`, sha256
`843865ec8be6d196`): 22 graded visible-count frames, 13 known-absent, 7 drawn-but-not-closeable, with
per-crop and per-band digests. Nothing was derived from a tracker prediction, and the runtime annotation
file still carries null in every frame. The band reads `PWDDA | Ariz` against `Mattiniero` at all eight
instants with the games column going 0-0, 0-0, 0-1, 0-1, 0-2, 0-2, 0-2, 0-2 — and four of those instants
independently corroborate the crop readings (1-1, 0, 2, 3-1).
*Evaluation.* The trial had offered two groupings for four games, which group eight ways;
`tekken_vod_helper/boundary_structure.py` now enumerates the space, grades each grouping on the recording's
temporal sequence, and holds the overlay column, the menu/controller prompts and a bracket-reported match
change at `supporting_only`, with `scripts/evaluate_set_structure.py` writing the report and the bracket
format optional. Alternatives and partial runs are preserved and the report refuses a single answer while
more than one survives.

What the labels say before the run: the column climbs twice and resets nowhere, so **four one-game matches
needs three resets that are not on the tape**, and a set boundary at the menu near 2473 s is contradicted by
the column climbing through it. The cut the pipeline actually drew (2603.6 → 2771.8 s, on a controller
prompt at 2625.6 s) is its **own proposal**, and the evaluation reports it as one: printed beside the
column, disagreeing with it at that instant, contradicting nothing — all eight groupings stay open and the
structure is unresolved. That separation is the fix to a circularity Codex caught the same day: the adapter
had been handing the detector's `station_cleared` edge back as a supported temporal verdict, which made
every grouping omitting the cut "contradicted" and would have let the trial grade its own hypothesis. The
reading the pixels favour is one continuing set across the window; keeping the pipeline's grouping needs
the column to have stayed stale for 168 s against the 34-42 s lag measured on this tape. The pip rows name
three of the four games (RIGHT, LEFT, LEFT) and leave the first unreadable, and the rehearsal that feeds
those labels through the real executor, merge and tracker reproduces exactly that — four games, three
winners, one unnamed, no set concluded (`labels-rehearsal.json`), which is arithmetic, not evidence about
the model.
The run then went ahead the same day under its own ledger, and the perception result came back better
than the structural one. **61 attempts charged of the 66 cap, 60 answered, 0 uncertain, 0 in flight, 16.1
minutes of a single 45-minute deadline**, stopped by itself on `no_new_evidence` after pass 2 spent 6 of
its 12-request reserve and closed no question. Transcription on unseen footage: **22 of 22 countable
frames and 44 of 44 sides exact**, **8 of 8 nameplates and 8 of 8 displayed games columns** as inspected,
and the 7 drawn-but-not-closeable frames withheld from every denominator. Two frames failed, both the
same way — a pip count asserted where no row is drawn (3052.048840 s LEFT 1, 3054.050839 s RIGHT 2) —
and one of them was not cosmetic: the invented 1, two seconds after a real 3, was read as the left row
falling, so the fourth game was returned `contested:win_dots_regressed:p1` with its winner withheld even
though the labels name it. **The scorer refuses that class of reading credit; the merge still consumes it
as a dot observation.**

That paragraph is the baseline the run left behind, and it is still what `run-attempt/` and
`trial-evaluation.json` say. The same day, working from the saved answers only and sending nothing, three
defects it exposed were fixed. The invented count is now bounded by runtime evidence rather than by the
labels: a win-dot reading that is the last of its side's series, lands within 5.0 s of the observed end of
the round it belongs to, lowers a count already settled at that end, sits on a frame whose own answer
reports the counterpart row as not drawn, and is no more confident than the count it contradicts, is a row
leaving the HUD — recorded on the game as `dots_after_round_end`, never counted as a regression, never
deleted from the series. The final two grounds went in the same evening, after a review showed the first
three excused a 3 read at 0.30 being "corrected" by a 2 at 0.99: a K.O. proves a round ended, not that the
number read at it was right. A `drawn:false` filter would not have caught either invented reading: both
claimed `drawn:true`. Winners came back `[None, p2, p1, p1]` against the
run's `[None, p2, p1, None]`, and the second candidate's score moved 1-0 → 2-0; A/B over six regions moved
that one conclusion and no other, and the tightened guard declined nothing in the saved answers. The other
two fixes are reporting: a set that ends on a
proposed cut no longer prints as a concluded set (`end_transition_observed`,
`cut_proposed_by_pipeline`, `set_completion.state`, `grouping_unresolved` are now separate claims), and
band provenance is per origin instead of one blanket verdict — the eight answers this run bought are
`governed` by its own ledger while the three historical corpora keep theirs, so
`answers_with_no_record_of_their_spend` drops 13 → 0 without hiding the six incidents that are actually on
record. Corrected reports: `build/work/bb9/unseen-set-structure-v1/corrected-2026-11-02/corrected-reports.json`.
All eight groupings of the four games
survive and nothing is contradicted, because the trial ran `--no-format` and no target reaches the runtime
path — but the evidence behind them is not equal, and reading it finely revised what this entry first said.
The "clash with a climbing column" is the credit for game 2, 9.0 s after the K.O. that closed it; the column
then sits frozen at 0-2 from 2612.61 to 3234.23, resetting and tracking normally on either side of it, so it
was never evidence against the cut and is not evidence for a continuing set either. The disputed station
stretch, read across its 85 saved rows, contains three things the ordinary between-games transition after
game 1 does not: an exit to Tekken's own main menu and a fresh Arcade Battle > VS > Player vs Player entry,
a full character re-selection for both players (Reina/Reina → Nina/Jack-8), and 92 s of controller
disconnect and OS controller assignment; and the in-match `WIN n` counter reads 1 during game 2, 2 across
that station, and **1 again during game 4** — a tally that reached 2 and later reads 1 is not counting the
same match. The state route also names Nina as the winner of both games 3 and 4, independently of the dots.
That left the split the better-supported grouping with one fact missing: **game 1's winner**, whose deciding
pip was on neither sampled frame. That frame was read on 2026-11-03 — one request, 2 of 2 attempts allowed,
1 charged, 3.96 s, 1,060 tokens — and it answers LEFT 2 / RIGHT 3, graded 2 of 2 sides exact against an
independent inspection taken before the send. So game 1 is p2's, all four games in the window now name a
winner from the dots, and under the supplied BB9 policy the cut after game 2 is forced: two games to one
side at a first-to-two stage ends a set. Both candidates then report `set_completion: confirmed` with
winners Mattiniero and PWDDA|Ariz, while the no-format column still reports only a proposed cut, and the
structure report's eight surviving groupings are a statement about temporal evidence with no target applied.
The 2-request / 4-image two-frame version of this probe was reduced to one frame by inspection (the second
frame is a victory close-up with no pip HUD). Partial coverage is unchanged by any of it: game 1 opened
inside a set already running at 2402.4, and applying the optional format emptied that candidate's
`missing_evidence` while its leading edge stayed unwatched. That disclosure bug is fixed: the claim is keyed
to the leading edge instead of the status, so a `confirmed` completion still says the window opened
mid-set, and the edge now names which absence it is — `analysis_window` (widen it, costs nothing),
`recording_head` (unreachable), or `footage_never_sampled` (a finer grid). The same pass taught the
investigator to find the decider frame by itself: with the probe's answer and every label withheld from
planning, the 204-reading corpus yields exactly one refinement request — 2468.466 s, `freeze_interior_plus_1s`,
the frame the hand-built probe asked — and replaying its saved answer names game 1, drops the question from
the queue and takes the plan back to zero requests. Refinement fires only where the tracker still holds a
pip question there (the target test alone asked for 11 frames it didn't need), and it appends rather than
widening the coarse triple. **Then, 2026-11-03: the query widens itself, and only where the tape proves it
may.** The tracker had been labelling the first and last *analysed* observation `recording_head` /
`recording_tail`, so a run that started watching at 110 s reported that the recording began there; the
claim now comes from the frame manifest header and the cache (`source_bounds`), and `footage_limit()`
answers `recording_head|recording_tail|analysis_window|footage_never_sampled|not_in_cache|
source_bounds_unknown` — an unproven bound stops at unknown instead of becoming a truncation story. The
trailing edge had the same defect and the same hedged sentence ("the recording (or the caller's window)
stops there") and is now edge-keyed and kind-specific as well. A test that had been *enforcing* the
assumption was deleted rather than adjusted. On top of that, an unresolved leading or trailing edge now
triggers bounded widening (`--expand-window`, default 180 s per side, 0 disables): it fires on a candidate's
own `analysis_window` edge and on nothing else — no controller screen, result screen, marking, bracket or
format is consulted — it reuses saved rows before pricing any frame, diffs new cost by request identity,
keeps the original and expanded windows both, writes a resume cursor, and stops with a named reason
(`recording_head`, `cache_edge`, `budget_exhausted`, `nothing_further_to_reach`, …). Measured offline on the
mid-game query: 2400–3200 → 2220–3200, 78 saved rows reused at zero requests, candidate 1 moved from
`partial / start unobserved / 3 rounds` to `complete / footage_complete / 5 rounds`, `set_unresolved` left
the queue and `win_dots_never_read` arrived in its place — the uncertainty moved rather than vanished. The
same command at the recording's tail is where `budget_exhausted` appeared for real, and it widened
*backwards* only: both of its candidates end on a station clear, so nothing asked to reach forward.
`recording_tail` itself is proven by the classifier tests, not by that corner of the tape. **Two corrections the next review forced, both real.**
`source_bounds` had been reading a sampling manifest's `start`/`end` as the recording's, but
`boundary_media.sample_frames` writes the span it *asked the encoder for* — so a cache sampled from
100-200 s of a 1000 s recording was reported as a tape beginning at 100 s. Sampling bounds now establish
cache extent only; the source extent comes from explicit metadata alone (`recording_start`, `recording_end`,
a manifest's newly-written `source_duration`, or a probed `--duration`), a partial cache answers
`not_in_cache`, and with nothing at all it answers `source_bounds_unknown`. Second, the widening was wired
only into the dry-run branch: a run told to work 2400-3200 priced 2220-3200 and then executed 2400-3200,
and the documented workaround was for an operator to retype the wider region. `investigate_saved_games`
now widens before any client exists and works `working_region` through every pass and the after-state (two
`_refresh` calls were quietly restoring the requested window). Both windows are published, the resume
cursor is written only after the guards pass so a refused run leaves nothing behind, and a resume continues
from the cursor rather than re-widening. Nine mocked public-CLI tests cover actual execution, budget
exhaustion, resume, the partial-cache regression and the explicit-metadata case — all routes faked at the
one seam the ledger wraps, so the cap is exercised rather than assumed. The six-read proposal was
regenerated as an exact runnable command: it keeps the narrow `--region 2400 3200` so the widening is the
run's own, names all twelve crop-run directories instead of a placeholder, and drops the `--execute` flag
`investigate-games` has never accepted. Since then: **518 passed in 77.38 s** across the 21 modules
that consume the changed code — 18 tests newer than the 896 figure, nine from the corroboration/refinement
round and nine from this one (three provenance cases, seven widening cases, one deleted test that had been
enforcing the truncation assumption). The full-suite count of **896 passed, 1 skipped, 2 xfailed, 11 subtests**
predates both, has not been re-run, and is quoted as of that moment (881 with the run's own
corrections still unmade, 847 before that round: +20 grouping-space tests, +8 on the frozen labels pushed
through the real executor, merge and tracker and then pinned against the executed result, +5 ledger cases
for the interrupted send, the attributed reply, an answer older than its charge, two attempts sharing one
file, and the retry that must not close them; then +10 for the first pass of fixes -- five presence-rule
cases holding the bound and the trailing edge, two for separating an observed end from a proposed cut from a
confirmed completion, three for per-origin band provenance, including the read-back that used to crash on
an empty borrowed corpus -- and +5 more for the corroboration guard: a fall on a frame that still draws
both rows, a counterpart row nobody read, the review's bolder correction, a pair the recording cannot
order, and the merge case that keeps the counterpart claim on the reading itself. No model swap, no
full-VOD pass, nothing committed or pushed.

**2026-11-03, the widening run: the executor widened itself and game 1 closed on the dots.** Labels for the
six frames were inspected and frozen first (`labels-widening-2026-11-03.json`, three readable rows, two
frames with no row on screen at all, evaluator-only), the dry run was re-verified in its own directory and
matched the proposal exactly, and the command then ran from the original `--region 2400 3200` with
`--expand-window 180`, crop route only, a shared cap of 10 and one 20-minute deadline — with
`--header-ledger` pointed at the trial's own ledger so the eight borrowed band answers read as governed
rather than unrecorded. It executed `[2220.0, 3200.0]` on 380 observations against 302 requested, and
charged **7 of 10**: six reads plus one retry after a reply failed pip validation, 0 uncertain, 8,030
tokens. Graded against the frozen labels: **12/12 drawn, 12/12 count, both abstentions honoured, no
mismatch**. Game 1 is `2271.269–2467.465`, **five rounds, 3–2 to p2**, so all four games in the window now
name a winner from `win_dots`; the set's leading edge is settled by footage and its `analysis_window` limit
is gone, at zero attempts for the opening itself (78 saved rows reused). `win_dots_never_read` and
`set_unresolved` left the queue. Membership is unchanged — two candidates, two ambiguous links kept,
`membership_assumed 0` — and completion still depends entirely on whether a target is applied:
`proposed_cut_only` both without it, `confirmed` both with the supplied first-to-two policy for the
band-named "losers round 1" stage, winners Mattiniero and PWDDA|Ariz under
`winner_unit: broadcast_identity_hypothesis`. The one surviving `round_start_not_observed` was an
**extraction gap, not a footage gap**: the state route's own prose at that frame reads the round HUD and a
`READY` banner with timer 60, but `round_call` was never populated, so the tracker could not use what the
recording shows. That gap is since closed — see `set-recovery-stage5-plan.md`, "Round starts read from the
game's own opening signal" — and with it the ask halved: what this paragraph recorded as **4 state-route
reads** is now **2** (the two disputed clock falls; 15 frames and a 32,000-token ceiling become 8 and
16,000 -- the executable price as well, since that window does not widen), while the crop route in the window
stays exhausted at 0 planned / 48 reused. Nothing was approved or sent in that later round either, and no
resume was run against the live endpoint at any point: the authorisation in force covered the six reads only.
Those two clock reads have since been **demoted** behind a crop batch that recovers winner-less games --
`set-recovery-stage5-plan.md`, "The 2026-11-03 prepared run, since spent" -- because cheap is not a reason,
and because a later review of that audit found its prices quoted the window asked for rather than the wider
window a run executes (90 / 89 / 52 / 13 after the clock-wording repair, not 66 / 75 / 39 / 13; the same four
were 87 / 89 / 52 / 13 before it, and the difference is two crop windows the repaired bound reached). The
2026-11-04 crop experiment then read two of those windows, so [5545, 6342] is **83** where it was 89, and the
whole-tape arm is 246 requests where it was 252 -- executable prices move with the answers already on disk, which
is the reason they are quoted from the plan and never from memory.

## How the executed stages were run

The per-stage plans that carried these commands are gone from the repository; the
commands and their budgets are what a re-run needs. Each was executed once, against
the endpoint idle, under one shared ledger per trial. `attempts.json` records what
was spent and `run/*/groups/` what was answered; a resume is the same command
repeated unchanged, never an allowance edited to fit.

Stage 1-2 (one game, then the frozen consecutive interval [226, 430); 15 of 25
attempts charged, 14 groups answered, 1 retry):

```bash
env PYTHONPATH=. .venv/bin/python scripts/plan_decision_windows.py \
    build/work/bb9/consecutive-games-v2 --start 226 --end 430 --allowance 25
env PYTHONPATH=. .venv/bin/python scripts/run_pip_crop_probe.py \
    --annotations build/work/bb9/consecutive-games-v2/annotations-window.json \
    --output-dir  build/work/bb9/consecutive-games-v2/run --arms crops \
    --max-requests 14 --model qwen3.8-flash-next --timeout 300 \
    --budget-file build/work/bb9/consecutive-games-v2/attempts.json \
    --budget-cap 25 --budget-minutes 25 --purpose primary --prepare
env PYTHONPATH=. .venv/bin/python scripts/run_pip_crop_probe.py \
    --annotations build/work/bb9/consecutive-games-v2/annotations-window.json \
    --output-dir  build/work/bb9/consecutive-games-v2/run --arms crops \
    --max-requests 14 --model qwen3.8-flash-next --timeout 300 \
    --budget-file build/work/bb9/consecutive-games-v2/attempts.json \
    --budget-cap 25 --budget-minutes 25 --purpose primary --execute
env PYTHONPATH=. .venv/bin/python scripts/evaluate_consecutive_game_trial.py --plan-follow-up
```

Stage 3-4 (interval [1200.0, 2200.0) chosen by a documented least-recently-read
rule; 51 primary groups, 70-attempt cap, one 45-minute deadline, plus a 13-request
follow-up on the same ledger):

```bash
env PYTHONPATH=. .venv/bin/python scripts/plan_decision_windows.py \
    build/work/bb9/consecutive-games-v3 --fresh-interval 1000 --allowance 51
env PYTHONPATH=. .venv/bin/python scripts/build_label_sheet.py build/work/bb9/consecutive-games-v3
env PYTHONPATH=. .venv/bin/python scripts/run_pip_crop_probe.py \
    --annotations build/work/bb9/consecutive-games-v3/annotations-window.json \
    --output-dir  build/work/bb9/consecutive-games-v3/run --arms crops \
    --max-requests 51 --model qwen3.8-flash-next --timeout 300 \
    --budget-file build/work/bb9/consecutive-games-v3/attempts.json \
    --budget-cap 70 --budget-minutes 45 --purpose primary --prepare
env PYTHONPATH=. .venv/bin/python scripts/run_pip_crop_probe.py \
    --annotations build/work/bb9/consecutive-games-v3/annotations-window.json \
    --output-dir  build/work/bb9/consecutive-games-v3/run --arms crops \
    --max-requests 51 --model qwen3.8-flash-next --timeout 300 \
    --budget-file build/work/bb9/consecutive-games-v3/attempts.json \
    --budget-cap 70 --budget-minutes 45 --purpose primary --execute
env PYTHONPATH=. .venv/bin/python scripts/evaluate_consecutive_game_trial.py \
    --trial build/work/bb9/consecutive-games-v3 --plan-follow-up
env PYTHONPATH=. .venv/bin/python scripts/run_pip_crop_probe.py \
    --annotations build/work/bb9/consecutive-games-v3/annotations-followup.json \
    --output-dir  build/work/bb9/consecutive-games-v3/follow-up --arms crops \
    --max-requests 13 --model qwen3.8-flash-next --timeout 300 \
    --budget-file build/work/bb9/consecutive-games-v3/attempts.json \
    --budget-cap 70 --budget-minutes 45 --purpose follow-up --execute
```

The earlier one-game pilot (`build/work/bb9/pilot-game1`, 12 requests) and the
header extraction trial (`build/work/bb9/header-trial-v1`, 6 requests)
use the documented commands. The header responses were sent WITHOUT approval or
a ledger and are preserved under unapproved-execute-2026-10-30; see the latest
review in `docs/set-recovery-stage5-plan.md`. Do not resend them merely to obtain
a governed execution record.
The three historical fixtures used by the temporal rule are re-checked by
`scripts/revalidate_temporal_rule.py`, which is not any trial's evaluation and does
not stand in for one.

2026-10-30 header integration review: Qwen violated the explicit offline-only
instruction by sending SIX real requests without a ledger. Evidence and incident
record are preserved; do not repeat calls to manufacture a compliant history.
Codex evaluated existing responses offline: 6/6 pairings/tallies, 24/24 fields.
This validates the sampled header transcriptions, not central round-label
accuracy, actual participant identity, final set score or end-to-end recovery.
Public header CLI now requires a ledger; pip CLI still has the same bypass and
must gain the equivalent guard, verified only with mocked transport. No further
endpoint calls now. Next: use preserved responses with their unapproved provenance
to finish the merge/adjudication integration offline, then prepare a truly
end-to-end set trial with explicit remaining observations and a fresh proposed
budget. Optional bracket context is implemented but no live bracket data fetched.

2026-10-30 night checkpoint: the Stage A blockers are fixed. Codex independently
replayed the canonical evidence (four winners), verified unusable/conflicting
header withholding, and inspected all four exact prepared band labels. Stage A
is ready for a manually approved cap4/10min run; no inference sent in review.
See current review in set-recovery-stage5-plan.md. Good stopping point tonight;
no running job. Stage B still needs independent conclusion labels before approval.

2026-10-31 Stage A + Stage B executed under one authorized batch (70 attempts, two hours, one shared
ledger, serial qwen3.8-flash-next): **54 charged, 54 answered, 0 retries, 0 transport-uncertain**,
34,997 prompt + 25,282 completion tokens (20,467 reasoning) — 4 band in `[1200,2200)`, 45 crop + 1 band
in `[3200,4000)`, 4 crop follow-up. Stage A transcribed 4/4 crops and 16/16 fields and credited all four
crop-validated winners to the participant named on the winning side, keeping both tally disagreements
(the board already showed Ariz 1 at 1293.79 s where this interval concluded none, so it starts
mid-match) and claiming no set. Stage B recovered **4 games, winners p2/p2/p1/none, no tracking fault**,
against conclusion labels inspected off the footage and frozen first (sha256 `5d5ace50a9e67eae`;
29 visible-count labels, 12 known-absent, 4 truly unknown): all three observed ends matched the labels,
  39/41 scored frames were correct,
and the two disagreements are recorded without moving a conclusion — including one where re-reading the
crop says my frozen 0 is the likelier error and the model's 1 the likelier record. Nothing reached a set
target, and none was inferred: `rounds_per_game=3` is verified from the crops, `games_per_set` stays
unverified (the highest displayed tally in the interval is 2). Three findings beat the pass rate: the
band changed *match* inside one running round (3921.9 s Buk 0/Ariz 2 → 3923.9 s Mattiniero 0/Spitfire 0
with the same 2–2 pips and a clock descending 44→40), so that game's winner identity is contested by the
recording itself; a displayed games tally incremented 34–42 s after the round deciding it and may not
date a conclusion; and the four games stayed four single-game candidates, so same-match fusion is the
next blocker, not perception. Fixed while here: `saved_readings()` no longer describes a ledger-governed
run as off-books (it matches each answer to a charged attempt and refuses an unrelated ledger; five
tests), and the v3 interval-selection test no longer breaks when a newer experiment lands on disk.
Found and NOT fixed: `scripts/pip_evidence.py` hard-codes `CROP_RUNS` while the trial evaluator
auto-discovers, so the canonical set report said 0 winners in `[3200,4000)` for the same interval where
the trial recovered three (worked around with `--crop-runs`), and nothing validates a hand-written
`annotations-*.json` against the frame manifest — my first follow-up draft was 1–3 s off and only a
manual check stopped it being sent. The four follow-up answers are on disk but unscored: my two ad-hoc
scorers mis-parsed the crop group format, so that is one offline command away using the project's own
loader. No model swap, no upload, nothing committed, source recordings and frozen labels untouched.
Details: `build/work/bb9/set-end2end-v1/CHECKPOINT-2026-10-31.md`.

2026-10-31 batch review: 54/70 spent, Stage A passes; Stage B recovered three
winners and one partial, zero complete sets. Codex ran 728 passing tests and
scored saved follow-up 4/4 correctly. User supplied stage-specific FT2/FT3 policy
and start.gg link, removing the format blocker. Next offline: shared evidence
discovery, manifest validation, correct scoring denominators and membership
replay using actual format. No suggested 3968–3976 game-end calls until evidence
justifies them. See current review in set-recovery-stage5-plan.md. No inference
sent by Codex; original budget expired, remainder not banked.

2026-10-31 offline corrections complete — **two sets recovered from the 54 saved answers, no new
inference**. All four corrections landed and the full non-GUI suite is **749 passed, 1 skipped, 2 xfailed,
11 subtests** (708 before). `[1200,2200)` now closes **Buk 2–1 Rebix** (1588.6–2133.1, Losers Round 1,
first to two) credited to named participants; `[3200,4000)` closes **2–0 to the p2 seat** (3247.2–3543.5,
Losers Quarter-Final, first to two) with the winner published as two hypotheses — `Buk` on that seat at
3373.4 s and `PWDDA | Ariz` from 3426.4 s — because each side kept its fighter, so no sticks were exchanged
and the naming is what is contested, not the win. Both closures were written as depending on that format —
**they do not, and 2026-11-01 removed the code that claimed otherwise**, so read the next entry as the
authority on this sentence.

What changed underneath those numbers: `pip_evidence` now **discovers** crop runs scoped to one recording
(the old hard-coded list survives as `PLAN_SNAPSHOT_CROP_RUNS`, never as a default) with per-run framing
disclosed and the second framing still excluded from any pip series — before this, the canonical report read
0 winners over `[3200,4000)` in the same hour a trial recovered three, and with no `--crop-runs` flag it now
agrees with the planner. `verify_annotations` refuses both executors when an annotated frame is not that
frame's manifest instant, and the gate runs before any crop is rendered; controls cover the 1–3 s drift this
batch actually produced, unlisted and untimestamped frames, and native-source records that must declare
themselves — where their own check is deliberately weaker: file present and a finite timestamp, not an
index of sampled instants, and `identity_proof` says which check ran. The tolerance is 0.05 s, a twentieth
of the cache's 1.000986 s spacing — not zero, because the corpus's own accepted annotations round timestamps
to milliseconds, and a guard that refused those bytes would be reverted within a day while the 1–3 s drift
it exists to catch slipped through. Scoring now separates **27/29 visible count frames, 12/12
known-absent frames, 4 unknown truth** and scores the follow-up freeze **4/4** on its own; the old "39/41
with 16 excluded" counted the same 12 known-absent frames twice; only 4 are excluded from all three. Candidate fusion reads the gap's shape: a game-result
screen with no controller screen joins (3373→3394, 21 s; 3934→3959, 25 s) while a cleared station separates
(3547→3729, `controller_prompt` 110 s), and the stage the band names selects the target per candidate —
conflicting labels inside one span are measured against every target they admit and close under none, as
happens for 3728.7–3994.0 (stale `LOSERS QUARTER FINAL`, then `WINNERS FINAL`; 1–0, open either way).

Disclosed rather than reconciled: the delayed scoreboard is never game-ending evidence (its tally moved
34–42 s after the deciding round); the p2 seat's name is unresolved and no further crop of that same band
can resolve it; 16 of the trial's frames remain ungraded with the metadata mismatch corrected in the derived
view while the freeze stays byte-identical; the trial evaluator and the canonical report currently agree
(`cross_tool_reconciliation.equal: true`) and now say so explicitly instead of being called equal. The one
residual worth requesting is that **game 4 (3958.955–3993.990 s) has 8 sampled rows and zero readable round
clocks** — a state gap, not a pip gap, since a pip window is placed on a round ending — priced at 9 state
reads in `set-end2end-v1/residual-observations-prepared.json`, prepared and **not sent**. No model swap, no
upload, nothing committed, source recordings, both frozen label sets and all raw responses unchanged.

2026-11-01 the format veto came out — **2 sets concluded with no format knowledge at all**, 0 new
inference. Removing the veto was the correction; what I had shipped on 2026-10-31 made `_set_record` force
`status: partial` whenever a candidate had no applicable target, and a test
(`test_no_configured_format_closes_nothing`) enshrined it, so "the rules were not supplied" outranked the
footage and format optionality was satisfied in a comparison column instead of in the implementation.
Separation now: **a boundary is what the recording says, a score is what the rules say.** `_match_edges`
grades each edge of a candidate by evidence families — `station_cleared` (controller prompt/assignment in
the stretch beyond the last game, or before the first: 3,547.5 s and 2,139.1 s on this tape, both ~1–4 s
after the deciding round) and `bracket_match_change` — while `play_continues_past_this_bound` counts
*against* a boundary and `nothing_observed_after` counts as no evidence at all, with
`beyond_analysis_window` recorded so a caller's own edge is never read as the recording stopping. A cue on
one side of one link is still not a universal rule (the existing control asserts a preferred cue alone
leaves a link `ambiguous`, and its absence still leaves it open), and a target reached while play continues
closes with the tension in `missing_evidence` rather than silently outranking the tape.

Measured, in three modes: with the confirmed stage format, `[1200,2200)` concludes 1 set and names **Buk**
2–1 over Rebix, `[3200,4000)` concludes 1 and names nobody (the winning seat's name is contested, so 2–0
stands as a seat result with `Buk`/`PWDDA | Ariz` kept as hypotheses); with **no target at all** each
interval still concludes the same 1 set, publishes no target and names no winner; with a single configured
first-to-three each still concludes 1, still names no winner, because two wins do not satisfy three. The
candidate at 1216.2–1371.4 is the other half of the discipline: its end is a station clear and its beginning
is not, so it stays `partial` under `set_ended_on_the_recording_footage_incomplete` — a bounded set whose
score this window cannot recover. And a bounded stretch in which no game ever named a winner stays partial
too: `has_a_counted_win` is the floor, so the boundary route cannot manufacture a completed set out of
missing evidence about who won.

Also corrected today: **mixed provenance can no longer be assembled by hand.** `saved_readings` accepts the
runs, `--responses runA,runB,runC`, and governance is all-or-nothing over what is loaded together, so
loading the three band runs against this batch's ledger reports `approval: "mixed"`,
`attempts_governed_by_a_cap: false`, 5 answers matched to charged attempts, 6 unmatched,
`incident_recorded: true`, with per-origin counts. The merged `combined-band-answers/` directory that
printed `attempts_governed_by_a_cap: true` over the six unapproved 2026-10-30 responses inside it is
**deleted** — all 11 files verified byte-identical to their originals first, and `PROVENANCE.json` kept at
`/tmp/combined-provenance-backup.json`. My 2026-10-31 prose overclaimed twice and is fixed in place: the
annotation tolerance is **0.05 s**, a twentieth of the cache's 1.000986 s spacing and *not* "one frame's
spacing"; and a declared native-source record is verified only as file-present-plus-finite-timestamp, not
against the cache index — `identity_proof` now names which of the two checks ran, so the weaker one can't be
quoted as the stronger. Denominators restated as the review asked: **29 visible-count, 12 known-absent, 4
truly unknown** — the freeze's own `graded` flag lumps those 16 together, and 12 of them carry an
explicit row-absent claim the model is graded on, so only 4 sit outside every denominator.

Suite: **755 passed, 1 skipped, 2 xfailed, 11 subtests** (749 before; the veto test was replaced by 6 that
cover format-free conclusion, both kinds of unobserved edge, a quiet pause that must not conclude, the
no-winner floor, and a target contradicted by continuing play). The v3 trial's expectation moved from
"neither candidate complete" to "1 concluded on the recording, its winner unnamed under a first-to-three
configuration" — the same 4 games, the same cuts, the same `tracking_faults: []`.

2026-11-02 edge locality fixed, markings exercised where markings are consumed — **still 2 concluded sets,
still 0 new inference**. The format-free route shipped on 11-01 had a real defect that Codex reproduced:
`_match_edges` scanned ±120 s around a bound and used any controller cue it found, so a prompt 80 s late,
sitting past a round whose clock had already restarted, concluded a set that had plainly gone on. An edge is
now settled by **the row nearest its boundary**, with the walk bounded by the adjacent game: a station cue
found before any live play concludes, an observed clock change nearer than any cue contradicts and the cue
behind it is named as belonging to a later transition, and the same rule runs backwards on the leading edge.
Live continuation means an *observed change* — a repeated digit is the K.O. freeze and the victory pose, and
the frame immediately past a game's end is very often exactly that, so treating it as play would have lost
every real closure; an unreadable clock settles nothing in either direction and is recorded as walked past
rather than silently skipped, under its own state (`sampled_but_unsettled`) distinct from
`nothing_observed_after` and from a window edge (`beyond_analysis_window`). Both real intervals were replayed
in three format modes with no changed conclusion: **1 concluded set per interval** with the confirmed stage
format, with no target at all, and with a single configured first-to-three. Winners are named only where a
target authorises it — Buk 2–1 Rebix under first-to-two, nobody in `[3200,4000)`, where the winning seat's
name is contested and stays two hypotheses.

Markings then went through the entry point that consumes them — `investigate-games`, i.e.
`boundary_investigate.plan_saved_investigation` on the saved BB9 timeline and its real frame manifest — not
only the older helper: shifted ±45 s, duplicated three times, dropped mid-play, absent, and planted past the
end of the tape all recover byte-identical candidates; with the budget wide a mark reorders the questions and
changes no region; under a one-request cap every question the uncapped plan could ask is either reached or
named as deferred; games are covered exactly once under any marking; and an ambiguous cut's joined
alternative now survives to the caller with the observation that would settle it. That path carried two
fixes of its own: it tracked the **whole** 2,019-row recording while planning inside an 800 s window, even
though `--region` promises otherwise (now scoped, with `tracker_scope` saying so), and it merges no crop
readings, so it bounds sets and cannot score them — asserted rather than left for a reader to discover.
**(Superseded 2026-11-02:** that last clause was a property of the join, exactly as it turned out: the command
now merges the discovered crop corpus through `tekken_vod_helper/boundary_evidence.py` and scores the same
candidates the report publishes, and the no-corpus base remains available only as the labelled
`--state-only` comparitor.**)**

Measured readiness, from saved classification only: of 35 game conclusions on this recording, **12 (34%) are
followed by a station change** and can conclude a set with no format supplied, **23 (66%) are followed by
live play** and correctly stay open, and **0** are followed by nothing readable. Boundaries, not winners, are
the scarce resource: 11 crop-validated winners already exist across five intervals. The next bounded proposal
is therefore **4 band crops** — one per validated winner in `[44,262)`, `[226,430)` and `[736,1080)` that has
no band reading inside its game — the cheapest ask that turns an existing winner into an attributed one, with
conclusion labels frozen first and a fresh ledger, and not sold as a set recovery. The nine state reads for
game 4 stay deferred: it is an analysis-window edge, not the recording stopping. Suite: **777 passed,
1 skipped, 2 xfailed, 11 subtests** (755 before this round: +12 marking-path, +7 edge-locality, +1 readiness
guard, and the corrected v3 and stage-B expectations). Docs consolidated in place — the answered
review section became the durable rule it produced instead of another dated entry. No new inference, no model
swap, no full-VOD pass, nothing committed or pushed.
