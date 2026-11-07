# Set recovery — current authority

Updated 2026-11-06. Forward work and current evidence only; raw responses,
freezes and detailed executed reports remain under build/work/bb9. This file
supersedes the historical Stage A/B narratives formerly duplicated here.

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
## Current state — 2026-11-06 (latest): review path built; **the boundary gate is not met**

**Historical diagnostic: timestamp-derived clock/edge claims below are withdrawn by the current status above.**
*The bullets below this heading describe the 2026-11-06 model-only review path and are kept as the
ledger of that pass; where they conflict with the section above (question counts, "no app caller",
"UI not built"), the section above is current. The gate verdict is unchanged: **not met**.*


Reconstruction stands at **30 games / 30 winners / 122 rounds** and today's public bill is
**231 requests / 486 images / 714,000 completion tokens** (`coverage-audit-v25`). Those are hypotheses
about a recording, not an independently checked result, and the difference matters right now because the
check was finally run.

**Boundary correctness, measured on the tape.** The scoring protocol was frozen before any score was
computed (`build/work/bb9/boundary-scoring-protocol-2026-11-06.md`), then all 10 predicted set boundaries
were read from cached frames **blinded** — labels only, half the tiles random controls — and finally
measured by reading the round clock off an enlarged HUD strip
(`boundary-review-2026-11-06/predicted-start-clocks.png`). Result, in
`boundary-review-2026-11-06/boundary-reference-2026-11-06.json`:

* ~~**5 of 10 predicted set starts are 2 to 7 seconds inside a live round** — 790.790 (clock 58),
  3723.720 (58), 4464.460 (56), 5495.490 (53), 6486.480 (53).~~ **Withdrawn 2026-11-06 late session:** those
  values were read off tiles whose filenames did not match their labels; the aligned frames read 60 at
  3723.720 and 4464.460, and 53 at 5495.490/6486.480 was never a measured value. See the correction above.
* All 10 predicted set ends fall on non-fighting frames, which is what a padded conclusion looks like.
* **The clock overturned the impression twice**: 3247.244 looked mid-round and reads 60 (an opening), and
  3723.720 looked like an opening and reads 58 (late). Blinded reading is not enough on its own, which is
  why both are recorded.
* The pink score box is recorded and decides nothing: three of these "set starts" show a non-zero score,
  but this recording's score fields have been seen resetting inside one live round, so they cannot settle a
  boundary. They are a flag for a reviewer, not evidence.
* **Unverified and still open:** the 30 game-level conclusions, every interior round edge of the 122, and
  anything shorter than the 1-second cache step. No percentage in these documents certifies boundaries, and
  counts, unit tests, confidence and completion figures explicitly cannot.

**Gate: not met.** The finding recorded here ("five late starts") has been withdrawn with the tiles it came
from; what replaces it is the state-sequence recovery above, which is a proposal for a reviewer and not a
score. The flagging defect it stood on was real and is fixed: openings are now asked about for every clip.

**This reference is partial, and the required inventory is not built.** The checkpoint asks for an
evaluator-only inventory covering **all** gameplay and **all** set transitions, including unmarked matches
and partials. What exists here is 20 set edges plus 20 blinded controls, one frame each: enough to fail the
gate on measured evidence, nowhere near enough to be that inventory. Producing it means watching the
recording, which has not happened. The five late starts are therefore a demonstrated problem, not a scored
pass rate, and no number in these documents may be read as boundary accuracy.

**What was built this pass (offline, no inference, no encoding) — superseding the checkpoint's "neither has
been delivered".** A real review path, not another design:
`tekken_vod_helper/boundary_review.py` imports saved recovery into persisted segments that keep per-game
evidence, per-side identity, alternative groupings, uncertainty and provenance;
`boundary_cli review-project` writes it from saved artifacts
(`build/work/bb9/boundary-review-2026-11-06/bb9-review.tvh.json`: 10 sets, 30 games attached, zero count
mismatches, 17 footage questions); `MatchSegment` now survives a save/load instead of discarding its
evidence; usage is reported per field so a retried malformed reply can no longer vanish from a total; a
remembered character can no longer overwrite an observed one, and a value carried in under another player's
name is not attributed to the new one. The export path no longer borrows the next mark's start for a
recovered row, and held jobs are announced instead of skipped.

**GUI limitation, plainly:** this box has no `tkinter` in any interpreter and the prompt forbids rebuilding
the environment, so the review queue and picker were **not executed**. The rules they use are tested
directly (46 tests across the review, character and usage modules); the presentation layer is verified only
by reading, and the manual checks are written out in
`build/work/bb9/outcome-recovery-post-review-2026-11-06/manual-review-checks.md`.

## Fresh recording: source verified, timings measured (2026-11-06 15:00)

The transfer finished while this pass was running, so the new session exists and its identity is attested
rather than assumed. `metadata/move-status.txt` reads `COMPLETE / finished 2026-11-06 15:00:23-0500 /
verified-or-skipped 21/21`.

* **File** `VODs/2026-11-05 Basement Brawl 10 Cafe Edition/recordings/2026-11-05 19-35-07 [original].mkv`,
  31,451,794,033 bytes equal to `expected_bytes`, with `sha256_source_before`, `sha256_streamed` and
  `sha256_source_after` all `d49e32ddb06549d130c146127d930b7d984162067332e0a99f8966af69cbafd9`, from source
  `C:\Users\KWTekken\Videos\2026-11-05 19-35-07.mkv`, status `verified`.
* **Duration 8774.516 s (146.2 min)**, 1920x1080, 59.94 fps, HEVC, ~28.7 Mb/s -- read from the landed file.
* **Metadata association is by archive location, not by content.** The paired project holds 14 matches and 16
  players, but `video_path` is `''` and `duration` is `0.0`, so nothing inside it names the recording. The
  pairing is the mover's directory layout: an association, not a link, and stated as that.
* **Measured cost structure on this file, decode only (`-f null`, nothing written):** seek is 0.16-0.27 s at
  60 s, 3,600 s and 8,400 s, so random access is not the expense. Decoding to 640x360 runs 286 frames/s near
  the start and ~114 frames/s deeper in, while BB9's cache pass ran ~8 frames/s end to end -- **the cold pass
  is encode/IO bound, not codec bound.** A 1-second cache of BB10 is ~77 s of decode inside roughly 18 min at
  the historical writing rate. Scaling BB9's ~79-minute classification by 146.2/115.5 gives ~100 min, which is
  an **order of magnitude from two ratios, not a measurement of this file**.
* An early probe of mine reported 0.12 s "seek" at every position while decoding **zero frames** -- a timing
  of nothing. It is recorded as an error, and the numbers above replace it.
* Frame extraction at scale and any media rendering were **not** run: this pass stops before encoding. That
  caching is I/O-bound says where time goes; it is not a promised saving, and changing the cache format needs
  the representative accuracy/cost comparison first, because 640x360 JPEG quality is what the model on.
* Fresh-VOD generalization stays required and is now unblocked. The blocker is the BB9 boundary gate above,
  which is a measurement problem, not an availability problem.

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

## Latest Codex post-outcome review — 2026-11-06, five-read authorization completed

Actual prompts and tool results in analysis session `a5ac83ca-73f3-4572-b279-a4b2598722c6` verify Andy
manually authorized the five frames and Qwen executed that packet. The prior readiness/held record is
superseded, not a pending execution instruction. No additional inference is authorized. The newer
`3938b580-b4ff-4a3d-b357-256be3cddf84` session concerns VOD archive transfers; preserve its work and do
not restart, interrupt or modify that transfer as part of this review.

**Independently verified by Codex:** 117 affected tests pass (51.82 s), plus 15 game-reset tests pass
(2.20 s), **132 passed total, zero failed**. Fresh grading, support and corrected consequence audits
agree with the saved outcome artifacts; the live public plan matches the reported bill. Qwen's actual
saved broad test run reports **1099 passed, 1 skipped, 2 xfailed** (three tkinter modules excluded).
The superseded reset regression preserves the historical evidence separately and the newer outcome
case pins the surviving split; tests were not weakened to permit incorrect behavior.

**Execution:** six charged model attempts, five unique requests answered, one malformed-JSON reply
retried, zero request failures and zero transport uncertainty, one reserve unspent. One 900 s deadline
was armed at first send; request time was 40.5 s. The all-attempt ledger accounts for **3,774 prompt +
3,642 completion = 7,416 tokens**, including 2,940 reasoning tokens. Its six `answered` states describe
received model responses, not six valid parsed crop answers. The malformed reply's 1,379 completion
tokens are real spend. Fresh grader `spent.tokens` still totals only the five parsed replies (3,145
prompt / 2,263 completion); retain that as accepted-reply usage and fix the generic report to also show
all-attempt usage/provenance. Do not quote the parsed-only figure as the run's full spend.

The completed `build/work/bb9/outcome-recovery-v1/` authorization priced
**5 requests / 10 images / 15,000 completion tokens** nominally,
with **cap seven** including retries and one **15-minute** deadline. These are historical limits,
not authorization to execute again.

**Grade:** four complete-count frames/eight sides exact; frame-6340 LEFT=3 is also correct outside the
strict frame rate, and RIGHT=0 at confidence 0.70 is **unverifiable**, since its evaluator count is unknown.
That is not a demonstrated misread or disagreement. All ten known presence sides agree. Keep the
unknown count unscored and preserve the runtime reading and reviewed labels separately. Withholding
that RIGHT value changes no reported game/set/clip/queue claim in the current consequence audit.
Runtime annotation, prepared and recorded request/group files remain label-free.

**Current reconstruction: 30 games / 30 winners / 122 rounds, all games complete**, support **24
single-frame and six two-frame winners**, zero failed support drops (`winner-support-v4.json`). Three
new LEFT winners replace the previously unobserved conclusions. Actual regrouping replaces start
5715.709699 with **5660.654702**, alongside concluded **5495.489711–5630.624704**; no net game is added.
Those boundaries remain reconstruction hypotheses, not independently proved match structure.
`game_winner_unobserved` and `game_incomplete` disappear; `win_dots_never_read` is 36. All ten set claims
remain unresolved, even though nine candidates reach a target under optional confirmed format and
three set winners can be named. Unknown identity is separate from detecting a set and reviewing it.

**Current public price: 231 requests / 486 images / 714,000 completion-token ceiling**
(`coverage-audit-v25`), 180 pip / 15 state / 36 header. Reuse is 209, with no stopped/deferred/unfunded
work under the audit's generous allowance; that is a quote for potential work, not permission to send it.
The seven saved marking variants preserve recovery. This report's 72 rows outside every game span are
not 72 missed-play rows: the fresh placement audit finds **62 exported with existing clips, seven next
to an unobserved conclusion, three genuinely unassigned rows (0.0 s)**. All four new confirmation rows
(5217.212, 5632.627, 5635.630, 6345.339) lie inside the actual exported candidate clips. Padding remains
based on conclusion/confirmation evidence rather than marks. No new omission is inferred from those
four accounting rows.

Evidence: `build/work/bb9/outcome-recovery-post-review-2026-11-06/` now contains fresh grade, support,
consequence, public plan, all-attempt spend verification, `unplaced-play.json`, and
`confirmation-clip-coverage.json`. The original model outputs and both evaluator freezes remain intact.

Historical diagnostic selection remains explicitly limited: the public scope was 35 requests, a
heuristic produced 29, and no rule reproduces the fourteen inspected; five were reviewer/rehearsal
selected. **Automatic prioritization remains unvalidated. Coverage and legibility are now recorded
separately**, not inferred from cache existence. The conditional phrase that 5660.655 takes over the start previously reported at 5715.710
is now also the measured regrouping. Pip crops alone cannot
settle participant identity; missing **band coverage** can still matter, but manual bracket selection
does not require us to resolve every name automatically first.

The prior eight-read count audit remains **18 landed, 6 not applicable, 0 failed**, with six support
changes and no winner-side changes. No natural presence-only control with a measured effect was found
among its 57 no-count crop answers; that is a scoped result, not proof presence is always irrelevant.

**Next priority follows Andy's new-VOD use case:** discover an entirely unmarked match, propose evidence-
based set/clip boundaries and character suggestions, and let Andy choose the corresponding bracket match.
The existing app already applies a start.gg set to a selected video segment. Automatic participant naming
or more BB9 header reads are not prerequisites for that manual association workflow. Character suggestions,
character changes and unknowns must remain distinguishable from remembered player-character defaults.

Prepare that fresh-recording validation offline: identify the new VOD from the archive manifest, verify
that the chosen file is fully copied and its source identity/project association is sound, preserve marks
as optional input and evaluator truth separately, and make the public recovery-to-project/review path
concrete using saved evidence first. Do not read a growing partial as a completed source or disturb the
transfer. The known missing match is an evaluation case, never a timestamp hint fed to discovery. If its
identity is needed, request the minimum operator information while continuing independent preparation.
Specify cold-run phases, cached-resume behavior and one bounded inference proposal; send nothing.

Keep public plan/execute/resume, saved-answer reuse, shared budgets, explicit per-instant deferrals and
stopping reasons. The completed BB9 five-read diagnostic was reviewer/rehearsal selected; automatic
prioritization remains unvalidated. Cache availability does not prove legibility; the earlier blanket
readability claim was too broad. Stage/controller cues, format and bracket input remain optional support.
Readiness remains about **80%**: fresh-VOD validation, autonomous prioritization, set/identity uncertainty,
character/review workflow and structural cleanup remain. Flash Next stays. No inference, model swap,
subagents, encoding, transfer changes, commit or push is authorized by this review.

## Latest Codex post-run review — 2026-11-06

Qwen session `a5ac83ca-73f3-4572-b279-a4b2598722c6` contains Andy's actual manual authorization,
the exact execute tool result and saved post-run checks. Codex independently confirms **eight authorized
frames, eight answered attempts, no failed/uncertain attempts, two unused retry reserves**, one 1,200 s
deadline, 53.1 s of request time, 5,048 prompt and 5,198 completion tokens. Raw response identities and
all source/crop digests match the preparation; runtime documents and recorded payloads remain label-free.
This authorization is completed; unused retries do not fund a different frame or follow-on batch.

**Independent checks: 136 affected tests pass (66.79 s), fresh grading agrees exactly with grade-v2,
and fresh support auditing agrees with winner-support-v3.** Readable counts are 6/6 frames and 12/12
sides exact; known presence is 14/15 correct with one unknown side. The frame-2465 RIGHT disagreement
remains recorded. Full-answer ablation removes each no-count crop observation, clears corresponding
row-carried pip evidence and proves one observation removed: tracked games, sets and clips stay the
same for both 2465 and 3545. Count accuracy alone was not the proof of inertness.

Baseline now: **30 games / 27 winners / 122 rounds**, with **21 single-frame and six two-frame
winners**. Three winners gained support; no winner changed. Current public plan independently prices
**238 requests / 508 images / 745,000 completion-token ceiling** (`coverage-audit-v24`), routes 185 pip /
17 state / 36 header. Eight requests were retired and one new interior request opened. One candidate
clip ends 1.001 s earlier from its earlier supported winner confirmation. These are reconstruction and
planning figures, not ground truth or a live authorization. Readiness remains about 80%; fresh-recording
validation, unresolved outcomes, autonomous investigation and structural cleanup remain.

Historical pre-batch price: **245 requests / 522 images / 766,000 completion-token ceiling**
(`coverage-audit-v22`, with byte-identical v23). The current bill is v24 as stated above.
Evaluation continues to use `winner-corroboration-v2/labels-codex-reviewed-2026-11-05.json`, including
`row_drawn_by_side` unknowns; the superseded evaluator freeze is preserved and refused as truth input.

**Consequence-report caveat:** the original Qwen JSON preserves stale aggregate names
`drops_that_failed: 6` and `experiments_that_moved_a_winner: 6`. The former six are not-applicable
count deletions (no count existed); the latter compare winner/support strings and change support only.
Keep the original raw file. The normalized projection reports **18 count drops landed, six not
applicable, zero failed applicable drops, six support changes, zero winner-identity changes**. Make the
producer reproduce these distinctions before reusing it. Do not cite its stale aggregates literally.

Evidence: `build/work/bb9/winner-corroboration-post-review-2026-11-06/` (fresh grade/support, public plan,
full-answer ablation, normalized consequence, verification and three-unwon-game queue inventory).
The original result and evaluator freezes remain unchanged.

**Next task is offline outcome recovery planning, not another blanket corroboration batch.** Inspect
the three games without a winner: 5060.054734–5215.209726 (incomplete, four rounds),
5495.489711–5710.704700 (incomplete, five rounds), 6246.239672–6341.334667 (complete, three rounds but
winner not observed). They account for one and two missing game winners in the affected two set
candidates; ten unresolved set claims do not mean ten unseen matches. Classify missing outcome evidence
as unread, unreadable, outside the current query, or genuinely unavailable on the recording. Preserve
partials and alternatives. Prioritize a small public-plan investigation by expected outcome/ambiguity
reduction, using complete saved evidence and shared budgets. Stage/controller cues and format remain
optional supporting evidence; do not manufacture a target or depend on a particular timestamp.

The newly offered 2463 interior remains explicitly unexecuted; it is earlier gameplay and has no
established outcome benefit over these three games. Do not spend to remove the presence disagreement
or bulk-corroborate 21 winners without a stated recovery benefit. Offline inspection/repair and preparing
an exact small proposal are authorized; **no new inference, subagents, model swap, commit or push**.

## Executed — 2026-11-06: the eight authorized reads are in, graded, and their consequence measured

Andy manually authorized the frozen `winner-corroboration-v2` batch and it ran exactly as prepared:
**8 requests / 8 answered / 0 failed / 0 uncertain** in **53.1 s**, one shared 20-minute deadline armed at
the first send and never re-armed, **8 of 10 attempts spent with both retry reserves unspent**
(`winner-corroboration-v2/attempts.json`). Tokens: **5,048 prompt + 5,198 completion (4,516 reasoning) =
10,246** total. The 5,198 completion tokens are 21.7 % of the 24,000 completion-token ceiling. Pre-send gates (`winner-corroboration-v2/pre-send-gates-2026-11-06.json`)
verified the preparation identity, model, endpoint, allowance, all 24 digests, canonical saved-answer
reuse (no answer existed for any of the eight anywhere in the 21-store corpus), a label-free runtime and
an empty ledger before the first byte left. Nothing outside the eight authorized identities was sent.

* **Counts: 6 of 6 visible-count frames, 12 of 12 sides exact** on the reviewed denominator
  (`winner-corroboration-v2/grade-v2.json`, `--no-sensitivity`; confidence 0.85–0.95). Readings: 232.232
  `[1,2]`, 236.236 `[1,3]`, 2465.463 `[2,2]`, 2466.464 none, 3372.369 `[1,2]`, 3374.371 `[1,3]`,
  3545.542 `[2,3]`, 3547.544 none.
* **Presence, kept separate from counts: 14 of 15 known sides agree, 1 disagrees, 1 is unknown.** The
  disagreement is frame-2465 RIGHT — the reviewer recorded a partly visible row behind the hand (drawn,
  count unknown), the model reported **not drawn** at 0.75. Frame-2465 LEFT is unknown both ways;
  frame-3545 is known absent on both sides and the model agreed. Both frames remain outside every count
  rate, and it is preserved here rather than resolved by one more look. Independently removing each whole
  no-count answer leaves tracked games, sets and clip ends unchanged (Codex post-review artifact).
* **Recovery is unchanged: 30 games / 27 winners / 122 rounds / 28 complete / 10 candidates.** No winner
  moved side or basis, no game disappeared, and every winner still counts toward its set. What moved is
  corroboration: **single-frame support 24 → 21, two-frame 3 → 6** — the games at **53.053 s, 3247.244 s
  and 3394.391 s** each gained a second look at the deciding count — and `game_winner_single_reading`
  fell 24 → 21 (`winner-support-v3.json`; load-bearing 21, **zero failed drops**, against the preserved
  pre-batch `winner-support-v2.json`).
* **Consequence measured by identity, not inferred**
  (`winner-corroboration-v2-consequence-2026-11-06.json`): 24 withholding experiments — crop answer *and*
  the row's own `win_dots` — **18 landed, 6 inert** (the two frames that carry no count have nothing to
  withhold; naming those "failed drops" would blame the instrument for a null answer), **0 failed**. Six
  experiments moved a *support count* and none moved a winner.
* **One published clip end tightened:** the candidate starting 3247.243829 now runs to
  `clip_end_at 3547.541814` instead of 3548.542814, because its winner confirmation moved to
  3545.541814 — frame-3543 is the earliest reading at the target and agrees with the reviewed label. The
  other nine ends are untouched.
* **Today's bill is `coverage-audit-v24`: 238 requests / 508 images / 745,000 completion-token ceiling**,
  routes 185 pip / 17 state / 36 band; interiors 30 → 23, search-budget continuations 14 → 11, **zero
  stopped claims, zero deferrals, zero unfunded routes**, reused 204, both format modes and all seven
  marking arms agreeing, no marking arm changing recovery. All eight authorized identities left the plan
  and **one new look opened**: frame-2463.jpg at **2464.46187** (`freeze_interior_minus_3s`, window 42,
  anchor 2467.46487) — window 42's +1 s look answered "no row drawn", so the search went further back
  inside the same bound. That single request (2 images, 3,000 ceiling) is the only work this batch made
  cheaper-by-substitution; it is **unprepared and unpriced for sending**, and nothing was asked for it.
* Unread frames 4803 → 4800; `win_dots_never_read` stays **40** and `set_unresolved` stays **10**. These
  reads corroborated; they did not close the recording. **Reconstruction counts, not ground truth.**

Three test expectations were repinned to this measured state, each naming what moved it: the live-corpus
bill in `tests/test_corpus_current_state.py` (v22 → v24, 245 → 238), the live support totals in
`tests/test_winner_support_provenance.py` (24/3 → 21/6), and a real defect in
`tests/test_public_recovery_integration.py::report_shape`, which fed the **frozen** corpus to the command
side while letting the report side use **live discovery** — two evidence bases, not two tools; it passed
only because the drift had not yet touched those windows, and the batch's clip-end move exposed it.
Broad suite after the run and the repinning: **1078 passed, 1 skipped, 2 xfailed, 0 failed**. Runtime
stayed label-free after the sends (the eight recorded request payloads and every runtime document walk
clean), the reviewed labels and the superseded freeze are both untouched, and no commit or push was made.

**Next unresolved work, priced:** the one new window-42 interior above; the remaining 21
single-reading winners; 40 never-read `win_dots` claims; 10 unresolved sets (the two candidates with missing game winners contain one and two unwon games); 3 games with no winner observed; 5 impossible falls, 7 dwells, 1 unread clock, 1 unobserved
round end, 2 clock-impossible rounds and 68 play rows outside every game. Nothing further is authorized.

## Offline implementation history — verified before batch execution

The two holds from the review below are **executed offline; nothing was sent, committed or pushed.**

* **Overlapping deferrals now name their unfunded work.** Deferrals carry ownership the way funded
  requests already did (`deferred_by` beside `planned_by`), and a claim whose bounded pool holds a frame
  another window could not fund reports `stopping: "deferred_elsewhere"` with
  `deferred_elsewhere_in: [1]` and `exhausted: false` — never "already answered", never "requested".
  The reviewer's saved reproduction (`reproduce_overlap_deferral.py`, max_requests=1) passes verbatim;
  the shared frame appears in the deferred list exactly once (no duplicate request or deferral spend);
  the funded-overlap case keeps its `planned_elsewhere` report; and positive-budget regressions on the
  public plan pin both kinds (`test_a_deferred_frame_in_an_overlapping_bound_names_the_unfunded_window`,
  `test_full_budget_moves_the_shared_frame_from_unfunded_to_a_named_send`), as does the executor-level
  fake-transport partial-allowance resume
  (`test_a_partial_allowance_defers_the_unfunded_interiors_by_name_and_the_resume_picks_them_up`,
  extended to assert zero deferrals and zero stops once the bound is funded).
* **The reporting change moved nothing else.** The whole-tape public plan regenerated on the repaired
  tree is byte-identical to the reviewer's saved `public-plan.json` (same call inputs, absolute paths):
  245 requests / 522 images / 766,000 ceiling, 192 pip / 17 state / 36 header, reused 204, 30 interiors,
  zero stopped claims, 14 search-budget continuations, recovery 30 / 27 / 122. Support re-audits
  identically (`winner-support-v2.json` totals as in the review's own). So `coverage-audit-v22` stands,
  both format columns included, and the eight-frame preparation was **not** reselected: identities,
  roles, anchors and digests re-verified against the unchanged `run/prepared.json`; no ledger, groups
  or answers exist.
* **Evaluation reads the reviewed labels.** `scripts/grade_reset_probe.py` now keeps unknown presence
  unknown (`row_drawn: null` never passes through `bool()`), prefers the review's `row_drawn_by_side`
  (frame-2465: LEFT unknown, RIGHT drawn), withholds both unknown-count frames from the rate while
  reporting their labelled sides outside it, and **refuses to grade against the superseded freeze** the
  moment a sibling file declares it replaced — no silent fallback. Qwen's original freeze is preserved
  byte-for-byte as the inspection record, with the reviewer's correction carrying the superseded
  entry verbatim in `original_label`. Pinned by
  `test_the_grader_reads_the_reviewed_labels_and_refuses_the_superseded_freeze`.
* **Reverified independently 2026-11-06, by re-measuring rather than restating.**
  `build/work/bb9/post-repair-reverification-2026-11-06.json` records: the reviewer's
  `reproduce_overlap_deferral.py` passing verbatim (exit 0; one funded 5 s look, four unique deferrals,
  the shared 23 s frame reported `stopping: "deferred_elsewhere"`, `exhausted: false`,
  `deferred_elsewhere_in: [1]`, and no "requested by" wording in that reason); the regenerated whole-tape
  plan agreeing with the saved `public-plan.json` on all 48 shared top-level keys (zero moved); a fresh
  audit in `coverage-audit-v23` **byte-identical to `coverage-audit-v22`** — both format modes, all seven
  marking arms, every interval; a support re-audit byte-identical to `winner-support-v2.json`
  (24 single-frame / 3 two-frame / 27 winners); all eight frame digests and all sixteen crop digests
  matching **both** label files; and a scratch re-render of the batch whose 16 crop PNGs are
  byte-identical with the published ones (only the recorded output path, and the path-derived group
  fingerprint, move — so `--execute` must reuse the published `--output-dir`). No module under
  `tekken_vod_helper/` names any label file; the batch still has no ledger, groups or answers, and
  nothing was sent, committed or pushed.

Broad suite after the change, re-run 2026-11-06: **1078 passed, 1 skipped, 2 xfailed, 0 failed** in
239.65 s. The three tkinter-bound modules (`test_app_export_jobs.py`, `test_overlay_integration.py`,
`test_project_recovery.py`) have to be named as exclusions — a bare `pytest tests/` aborts during
collection on `ModuleNotFoundError: tkinter` on this box instead of reaching the count. The three
additions over the previous run are this repair's regressions.

```bash
env PYTHONPATH=.qwen/tmp/pylibs:.venv/lib/python3.14/site-packages:. /usr/bin/python3 -m pytest -q tests/ \
  --ignore=tests/test_app_export_jobs.py --ignore=tests/test_overlay_integration.py \
  --ignore=tests/test_project_recovery.py
```

## Earlier today — the winner confirmation probe executed (2026-11-05)

Historical price after confirmation (v18 through v20): **215 requests / 462 images / 676,000 ceiling**.
The current selector prices 243; the earlier bill is retained here as a measured comparison only.


**One request of two allowed, answered, and the load-bearing winner is no longer load-bearing on an
unverifiable pixel.** `game-reset-confirmation-v1` asked cached frame-4068 (4071.066786 s), the next frame in
the same clock freeze as the flash-obscured 4069.064786 whose `3` concluded the game at 3958.955. Preparation
was verified first (1 frame / 2 images / 3,000 ceiling / `expected: null` / fresh cap 2 / 5-minute deadline,
no ledger, no answers, and the reviewer's frame digest matching the cache frame). Executed: **1 sent, 1
answered, 0 failed, 0 uncertain, 1 attempt unspent, one 300 s deadline stamped at the first send**. The
original flash frame was **not** re-asked.

**Grade: 1 of 1 frame correct, 2 of 2 sides exact, error kinds `{"exact": 2}`** — the model read LEFT 3
(slots 1, 2, 3) and RIGHT 0 at 0.95 against Codex's independently inspected 3-0, so there was no disagreement
to preserve (`game-reset-confirmation-v1/grade-v1.json`). Labels stayed out of runtime: the arm itself
reported `accuracy_is_reportable: false` with its single frame ungraded, and the grader walks
`annotations.json`, `prepared.json`, `plan-crops.json` and the recorded request payload for any non-null
`expected`/`pips` and finds none.

**Measured, by withholding one side at a time through the canonical merge** (`unknown_slot_sensitivity`; the
side-reading count proves each drop landed: 371 → 370 → 369):

| column | games | winners | game 3958.955 |
|---|---|---|---|
| both readings | 30 | 27 | complete, p1 (`win_dots`) |
| withhold 4069.064786 LEFT | 30 | 27 | complete, p1 |
| withhold 4071.066786 LEFT | 30 | 27 | complete, p1 |
| withhold both | **31** | **26** | incomplete, no winner; boundary drawn by `win_row_cleared_across_the_candidate_boundary` at 4049.044787 |

So the sensitivity that made that winner questionable is closed — either frame carries it alone — and what
remains is smaller but worth keeping in the sentence: the two frames are 2.002 s apart inside one round's
freeze, read by one model from the same JPEG family. That is corroboration of the value around that round
end, not an independent observation at the instant 4069.064786. The independent part is the reviewer's eye on
frame-4068, not the second timestamp.

**Recomputed from saved answers (`coverage-audit-v18`, zero sends, both columns).** Games 30, winners 27,
rounds 122, contested 0, all ten recording-wide candidates `complete` — unchanged from v17, and the named set
winners are still Buk, Mattiniero and PWDDA | Ariz with the optional policy and none without it, so neither
membership nor completion moved. **Prices fell by exactly the request this probe answered: 215 requests /
462 images / 676,000 ceiling (pip_crops 163 → 162)**, and the plan's identity diff shows one dropped
`pip_crops` request and nothing added. Clip coverage: **68 play rows outside every game in 26 stretches**
(was 67 in 25) because this probe added a *row* at 4071.066786 that lies between two games; it joins the
`exported_with_the_clip` bucket, and the figure that would mean a missing game — 3 rows of unassigned live
play, 0.0 s (`unplaced-play-v10.json`) — did not move. Marking matrix unchanged at 215 in all seven arms.

**Tests:** broad suite again **1049 passed, 1 skipped, 2 xfailed, 0 failed, 0 errors** (147.8 s).
`tests/test_game_row_reset_grouping.py` is 14 passed; the second real-evidence test was rewritten
as `test_the_flash_winner_survives_either_frame_alone_and_falls_with_both`, because the claim it pinned (one
frame owns that boundary) stopped being true, and `tests/test_corpus_current_state.py` was repointed to v18.
`build/work/bb9/check_doc_claims.py` passes, including new claims for the executed confirmation, its grade and
the withholding columns.

## Earlier today — the reset probe executed (2026-11-05)

Latest Codex review: ledger confirms six answered attempts, no failures or
uncertainty; v17 reports **30 games / 27 winners**. Independently ran **20
reset/current-corpus tests: passed in 2.25 s**. Unknown evaluator count is not
proof the runtime count is wrong; corroborate through runtime evidence.

Inspected adjacent frame-4068 at **4071.066786 s**: exact crops clearly show
**LEFT 3 / RIGHT 0**. Prepared `game-reset-confirmation-v1/README.md`: **one
request / two images / 3,000 completion-token ceiling**, fresh cap two attempts,
five-minute deadline. Reviewed labels are evaluator-only; no ledger or answers
exist. Ready for a manual prompt, followed by offline recovery comparison with
and without the earlier 4069 s flash reading. No inference was sent by Codex.

**The win-row reset probe ran once, as authorised: 6 requests of the 8 allowed, 6 answers, 0 failures, 0
uncertain transports, one 12-minute deadline, 2 attempts left unspent.** Command is the reviewed
`game-reset-probe-v1/README.md` line (explicit compatibility env, `--execute`), with Codex's ffmpeg
`-fps_mode` fixes untouched and no model swap. Ledger:
`build/work/bb9/game-reset-probe-v1/attempts.json` (cap 8, 12.0 min, `attempts_charged: 6`,
`attempts_remaining: 2`, `uncertain_attempts: 0`, deadline = create + 720 s). Tokens: 3,774 prompt /
3,000 completion of which 2,518 reasoning, against the 18,000 ceiling.

**Grade (offline, `grade-v1.json`, via the same `boundary_pips.score_readings` as earlier probes): 5 of 5
graded frames correct, 10 of 10 graded sides exact, error kinds `{"exact": 10}`.** Frame-4066 is withheld
from every denominator because its LEFT slot is unknown truth -- the K.O. flash blooms across the first two
slots -- so the model's answer of `3` at 0.55 there is neither a hit nor a miss, and its labelled RIGHT
side (0) agrees; both are recorded outside the rate. The runtime stayed label-free and that was re-checked
after the fact: `scripts/grade_reset_probe.py` walks `annotations.json`, `prepared.json`, `plan-crops.json`
and every recorded request payload for a non-null `expected`/`pips` and found none, and the executed arm
itself reported `accuracy_is_reportable: false` with 6 ungraded frames.

**Both spans answered 0-0 after the break, exactly as the labels said. Only one of them is a reset.**

- **5060.054734-5545.539708 split on the win row.** The boundary at **5495.489711** carries
  `win_row_cleared_across_the_candidate_boundary`, verdict `reset`, readings p1 `[2, 0]` / p2 `[1, 0]`,
  `confirms` set -- the first time this rule has confirmed a boundary on this recording rather than refusing
  one. Head `5060.055-5215.210` (4 rounds, no observed conclusion), tail `5495.490-5710.705` (5 rounds,
  none either). No winner, as predicted by the rehearsal's shape.
- **3958.954792-4174.169781 never reached a cap-forced cut at all.** The `3` at 4069.064786 -- the
  unverifiable slot -- put p1 at the target *inside* the run, so the winner path owns that edge:
  `3958.955-4069.065` is a complete game won by p1 (`winner_basis: win_dots`), and the row that read 0-0 at
  4094.089785 opens `4094.090-4299.295`, also won p1. Withholding that one reading and changing nothing
  else gives **31 games / 26 winners** with the split at `4049.044787` drawn by the reset rule and no winner
  named. So the p1 winner on the first game of that pair rests on a pixel nobody could read: report it,
  do not bank it. Both columns are in `grade-v1.json` under `unknown_slot_sensitivity`, and both are pinned
  by `tests/test_game_row_reset_grouping.py`.
  *(Measured before the confirmation section above; with either frame carried alone the winner now survives,
  and the pinned test is `test_the_flash_winner_survives_either_frame_alone_and_falls_with_both`.)*

**Recomputed from saved answers (`coverage-audit-v17`, zero sends, both columns).** Canonical games **29 →
30**, winners **26 → 27**, rounds 122 unchanged, contested games 1 → 0 (`game_conclusion_contradicted` left
the queue), `win_dots_never_read` 45 → 40, `game_end_bounded` 11 → 9. Set hypotheses improved where the
boundary moved: the two `partial` candidates are now **`complete` with footage** --
`4464.460-5215.210` (4 games) and `5495.490-6341.335` (5 games) -- and all ten recording-wide candidates are
`complete`; named set winners are unchanged at Buk, Mattiniero and PWDDA | Ariz, and the no-format column
still names none, so the optional policy moved no grouping here. Clip coverage is unchanged: 1,172 of 1,239
play rows inside exactly one game, 67 outside every game in 25 stretches, longest unread frame stretch 3.003 s
(`unplaced-play-v8`, same 67/25 as v7 over one more game). **Executable prices fell: 216 requests / 464
images / 679,000 ceiling, by route pip_crops 163 + state 17 + header 36** (was 167 + 22 + 34 at 223), and
the marking matrix is unchanged at 216 in all seven arms with no `recovery_changed`.

**The rehearsal's totals were not the acceptance test and did not reproduce.** It said 31 games and unwon
3 → 5; measured is **30 games, winners 26 → 27, unwon 3 → 3**. The difference is one reading: the rehearsal
fed no count at 4069.064786, so in that column the run went to its cap and split, leaving two unwon halves.
With the model's answer, the same region keeps two games -- but the old unwon `3958.955-4174.170` closes as a
won game and the next game starts at the cleared row, so the recording gains a winner instead of two
unwon games. Nothing was tuned to make that agree with the rehearsal; the code is the same in both columns and
only the evidence differed.

**Tests after the execution: 1049 passed, 1 skipped, 2 xfailed, 0 failed, 0 errors** (three tkinter GUI
modules excluded, as throughout; 147.7 s). `tests/test_game_row_reset_grouping.py` is 14 passed: the 13 from
the repair, less the one test that pinned the pre-probe corpus state, plus two that regroup the recording
from named crop corpora -- with the probe's answers, and with that single unverifiable slot withheld.
Four current-state guards failed on first contact with the new evidence and were corrected to the measured
figures, not to make them pass quietly: `tests/test_corpus_current_state.py` now names
`coverage-audit-v17` (216 requests / 464 images / 679,000; 27 winners of 30 with the unwon count still 3),
and `tests/test_boundary_games.py::test_the_queue_is_no_longer_keyed_to_footage_status_on_the_real_recording`
now reads ten complete candidates instead of eight. `build/work/bb9/check_doc_claims.py` reports
**ALL CHECKS PASSED**, including the new executed-probe, grading and sensitivity claims.

**Nothing else is authorised.** No follow-up request, no retry of frame-4066, no model swap, no commit. Two
attempts remain under this ledger but the deadline has passed, so they are spent by nothing; a further
observation -- including a second reading of the flash frame from a different source, if one exists -- needs
its own authorisation.

### Earlier today — reset-rule repair and readiness (offline, no inference)

Latest Codex verification: **20 reset/current-corpus/freeze tests passed in
1.61 s**. Fixed the two obsolete ffmpeg `-vsync` invocations to output
`-fps_mode passthrough` / `vfr`; **all 20 sampling/source tests passed in
23.87 s**, resolving the ffmpeg 9 failures reported below.

Verified the reset probe's `--prepare` using the explicit Python 3.15
compatibility command now in `game-reset-probe-v1/README.md`. Standard `.venv`
remains stale, not rebuilt. Price: **6 requests / 12 images / 18,000 ceiling,
cap 8, 12-minute deadline**, no answers or ledger. Ready for a manual execution
prompt. Reviewed labels are evaluator-only; unknown frame-4066 LEFT can receive
a valid runtime count, so rehearsal winner totals are not an acceptance test.
No inference, media edits, commit or push were made in Codex's review.

**The isolated-dip reset regression is fixed offline; no inference was sent, nothing was committed.**

Codex's reset-rule review of 2026-11-04 stood: with five rounds reading p1 `[0,1,0,1,2]`, p2 all zero and the
middle 0–0 at confidence 0.1, `_dot_regressions` set the dip aside while `_row_reset_within` split the run on
that same reading and the record called the boundary confirmed. One reading cannot be a misread and a game
boundary at the same instant, so the reset paths now ask the fall rule first:
`boundary_games._set_aside_readings` runs `_dot_regressions` over a copy of the run, and `_win_row_cut`
judges only the readings it would stand behind. The dip no longer opens a game; the run stays one game whose
conclusion was never seen, and the group carries
`win_row_reset_declined_by_temporal_adjudication` with `confirms: null`. Nothing was rewritten: the reading
stays in `win_dots_evidence`, and `win_dots_rejected` keeps the neighbours that outweighed it.

The other half of the review — preserve a contested/alternative boundary rather than declaring one — is now a
verdict instead of a silence. Where the row reads empty on both sides and the side that held the pip is read
back **at that height on its very next reading**, a scoreboard that never went dark and a new game that
immediately won a round are the same two frames: no split is drawn, the game is contested
(`win_row_reset_unresolved_at_…`), and the split is recorded as the alternative
(`win_row_reset_ambiguous`). A cap-forced cut with that shape used to produce no note at all, because
`_cap_boundary_reset` declined and `_cap_boundary_unread` judged both ends read. No timestamp and no
confidence cutoff was introduced: the test is whether the row's own next reading gives the clear back — and
the adjudication that decides it now spans the cut, because a bracket read from one side of it is no bracket.
Where that bracket does resolve the dark reading as a misread, the note says `declined` instead and names the
reading it yielded to: the same frames with the opposite verdict, and a different consequence. Where the row
stays dark — the shape `game-reset-probe-v1` expects — the split still happens and still says so.

Two consequences of the ambiguous verdict are deliberate. It reaches the investigation queue through the
existing `game_conclusion_contradicted` item, whose `reasons` now carry the boundary instant. It does **not**
enter `alternative_groupings`: that vocabulary means "join these two games into one", and this alternative is
"cut this game into two", so the split is kept on the note (`alternative.after_round_index`,
`alternative.at`) rather than published as the inverse of what the field means.

A third defect came out of rehearsing that probe rather than out of the negative. The head of a split was
bounded by the round after the whole counted run instead of by the round its own boundary opened, so a
`ROUND 2` belonging to the game that had just legitimately opened contested the reset that produced it
(`round_numbering_continues_at_2` on 5060.055–5215.21 in the rehearsal). `head_next` is the tail's first
round now, and it reaches the game record's `conclusion_signals` as well as its outcome, which is also what
lets a contradicting announcement at the boundary itself be seen.

Tests: `tests/test_game_row_reset_grouping.py` is **13 passed in 0.60 s** — the eight 2026-11-04 tests
unchanged, plus the reviewed negative end to end through `track_games`, the unresolved-dip contest, the
cap-boundary contest, the still-confirmed sustained clear, and the split-head neighbour. Each new negative
was replayed against the pre-fix rule — `_set_aside_readings` re-pointed at an empty set, every verdict
relabelled `reset`, the harness at `.qwen/tmp/prove_regression.py` — and fails there; the end-to-end one
splits into `[(20000.0, 20060.0), (20080.0, 20180.0)]`. The sustained-clear positive passes both ways, which is
why it is kept. Broad suite: **8 failed, 1030 passed, 1 skipped, 2 xfailed, 10 errors, 11 subtests passed** against a
baseline of **8 failed, 1025 passed, 1 skipped, 2 xfailed, 10 errors** taken before the change — the same 18
pre-existing failures, all of them the environment below, and five more passing tests.

**Probe readiness re-verified after the fix; it was executed later the same day (see the checkpoint above).**
`scripts/rehearse_reset_probe.py` (new, zero sends) feeds the independently reviewed labels through
`boundary_evidence.merge_crop_readings` and `boundary_games.track_games` — the same merge the audit uses,
with only the transport replaced. Both spans split (29 → 31 games, winners unchanged at 26), the boundaries
come back verdict `reset` with `confirms` set, and frame-4066's count-unknown LEFT entered no series (zero
readings at 4069.064786). The rehearsal also corrected the probe's own prediction: unwon games move
**3 → 5**, not 3 → 4, because neither head reaches three rounds. Prepared state is unchanged — 6 frames, 12
rendered crops, allowance 8, model `qwen3.8-flash-next`, no `groups/`, no `batches/`, no ledger — and the six
`frame_sha256` values in `labels-codex-reviewed-2026-11-04.json` match the cached frames byte for byte.
Evidence: `build/work/bb9/game-reset-probe-v1/label-rehearsal-v1.json`.

**Recovery and prices rebuilt from saved evidence.** `coverage-audit-v14`, `-v15` and `-v16` were run after the
first fix, the second, and the final tidy of the same rule, each with the recorded four-band-run command; all
three are byte-for-byte identical to `coverage-audit-v13` (canonical JSON sha256 prefix `c733a25760053f55`):
**29 games / 26 winners / 3 unknown / 122 rounds**, bill **223 requests / 500 images / 728,000 ceiling**,
`inference_sent: false`, and the marking matrix unchanged at 223 requests per fault. At
that point the reset rule was still inert by design — those spans held no model-read pip rows — which is what
the identity proved; the executed probe at the top of this file is what changed it, and
`coverage-audit-v17` is the current measurement. `build/work/bb9/check_doc_claims.py` reports
**ALL CHECKS PASSED** against the rebuilt artifacts.

**This machine's test environment, and none of it is the boundary code.** The ostree update moved the system
to Python 3.15, orphaning `.venv` (its packages sit in `lib/python3.14/site-packages` and the interpreter is
gone, so `.venv/bin/pytest` cannot start and `.venv/bin/python -m pytest` reports no pytest). Every command
here therefore ran as
`env PYTHONPATH=.qwen/tmp/pylibs:.venv/lib/python3.14/site-packages /usr/bin/python3 -m pytest …`, where
`.qwen/tmp/pylibs` holds a `--target` Pillow install so the three PIL-importing modules can be collected.
Rebuilding that venv is a separate decision and was not made here. **ffmpeg 9.0.2 removed `-vsync`**, which
broke 18 source/sampling tests; Codex then fixed the two obsolete invocations to `-fps_mode passthrough` /
`vfr`, so the executed-probe run below has no ffmpeg failures (the 1030-pass line earlier in this file still
lists them, because it was measured before that fix).

### Reset-rule review requirement this checkpoint answers

Latest Codex reset-rule review: **15 reset/current-corpus/freeze tests passed in
1.52 s**, but an additional negative fails. Using the existing `round_at`
fixture, five rounds read p1 `[0,1,0,1,2]`, p2 all zero, with the middle 0–0
at confidence 0.1. `_dot_regressions` resolves the isolated dip, while the new
`_row_reset_within` splits into two games and calls the boundary confirmed.
Reset logic must not bypass temporal adjudication or silently prefer a new game
over an isolated misread. Preserve a contested/alternative boundary when evidence
cannot distinguish them; do not patch with a timestamp or arbitrary confidence
cutoff. Keep clear-reset and accepted-game positives.

Independently inspected all six proposed reset-probe frames: readable counts
match Qwen's labels; both post-break rows are drawn and empty. Frame-4066 LEFT
remains count-unknown due to the flash. Evaluator-only reviewed labels are in
`game-reset-probe-v1/labels-codex-reviewed-2026-11-04.json`. The probe remains
held until the isolated-reset negative is addressed. Presentation cues remain
supporting evidence rather than being universally excluded or mandatory.
No inference was launched by this review.

Current recording-wide measurement before the probe: `coverage-audit-v16` (identical to v13/v14/v15) reported
**29 detected games, 26 known winners, three unknown**. The current measurement is
`coverage-audit-v17`: **30 games, 27 winners, three unknown, 122 rounds**.
Current bill before the probe: **223 requests / 500 images / 728,000 ceiling**; after the probe's answers
joined the saved evidence, **216 requests / 464 images / 679,000 ceiling**. Optional-policy named set winners
were and remain Buk, Mattiniero and PWDDA | Ariz; no-format names none. This is detector output,
not proof that every remaining segment is one correctly bounded game.

### Regression fixtures and coverage

Qwen froze the historical corpus and reports **1034 non-GUI tests passed**.
Codex independently ran **70 claim/dispatch/current-corpus/named-run tests**.
The initial freeze checked old hashes but still loaded new answers appended to
an old run. Codex corrected `tests/_frozen_corpus.py` to reject unexpected group
files and added `test_frozen_corpus_manifest.py`. **44 freeze/current-corpus/
dispatch tests passed after this change**. Preserve these local fixes.
Current runtime discovery remains separate from the test-only freeze.

`unplaced-play-v7` is current: **67 rows / 25 stretches**. Twenty stretches
(57 rows) lie inside export clips, two (7 rows) lie beside uncertain conclusions,
and three singleton rows remain explicitly unassigned:

| placement | stretches | rows | seconds |
|---|---|---|---|
| inside export clips | 20 | 57 | 46.0 |
| beside uncertain conclusions | 2 | 7 | 7.0 |
| genuinely unassigned | 3 | **3** | 0.0 |

Historical correction: v6 has **61 rows in 23 stretches**; the earlier
"41 items, 446 s" claim was wrong. The current claim ledger reports
0 points unmet in both columns (22 and 16 claims). The executed v2 batch
matched **14 of 14 sides exact**, a targeted check rather than a benchmark.
Those are coverage
findings; they do not independently prove that no gameplay was omitted.

### Next work is temporal repair, not an approved probe

**Temporal repair done 2026-11-04, offline; no inference sent.** A game boundary forced by the round
count is now asked of the win row and nothing else. `boundary_games._row_reset_within` splits a run only
where a row that showed a round won reads **empty on both sides** at a later round's opening, and -- after
the checkpoint above -- only where the temporal rules leave that clear standing;
`_cap_boundary_reset` confirms the cut itself the same way; `_cap_boundary_unread` files a count-only
boundary as unverified and names the two frames nobody read. The pre-existing guard that *refuses* a cut
when a read row persisted still governs, and the module's tests cover both directions -- 8 then, 13 with
the adjudication coupling -- including the negative case
that caught me attaching the disclaimer to fourteen decided games on the first attempt.

Two corrections to my own reasoning are recorded in those tests rather than quietly fixed. Five rounds is
this recording's **normal** game shape -- eight decided games have exactly five -- so duration neither
accepts a span nor rejects one; my "two merged games" reading of 3958.955-4174.170 was as unsupported as
the duration argument it replaced. And a fade plus a stage intro appears in 12 of 112 round transitions,
including inside accepted games, which is why presentation vocabulary is excluded from the rule entirely.

On the evidence available when the rule shipped the rule was **inert by design** -- those spans held no
model-read pip rows at all -- so canonical counts stood at 29 games / 26 winners / 122 rounds, the two
suspect runs carried `cap_boundary_has_no_win_row_read`, and the deciding observation was prepared as
`build/work/bb9/game-reset-probe-v1/` (6 requests, 12 images, 18,000 ceiling, cap 8, 12 minutes, labels
evaluator-only and independently reviewed). That probe has since been executed; the two runs are now
answered, one of them by a confirmed reset, and the counts above this section are current. The four-frame
`round-end-pips-v1` it supersedes was left unexecuted: its frames were aimed at game ends, and the boundary
is between rounds (see "Resolved since" below).


### Resolved since — the 2026-11-04 review's open items (historical, not pending work)

Every instruction in the two paragraphs below has since been carried out, and none of them is pending:

- *Reconcile the new-game presentation and pip reset before treating 3958–4174 as one game* -- done by
  evidence, not argument: the row reads 1-0 at 4019.014789, and 0-0 on both sides at 4094.089785 with 0-1 at
  4144.139782. With the deciding pip at 4069.064786 the span is a game p1 took at the target and a second
  game opening at 4094.090; with that one reading withheld it splits on the cleared row at 4049.045. Either
  way it is **not** one game (checkpoint above).
- *The controller/menu span 5060-5545 needs the same analysis* -- done: it split on the cleared row at
  **5495.489711**, verdict `reset`, confirmed, and the two halves stay winner-less because nothing on screen
  concluded them.
- *Rebuild canonical games, coverage and executable prices from saved evidence* -- done at each step; the
  current figures are `coverage-audit-v22` with `unplaced-play-v10.json` (the continuation repair moved no
  recovery count, so the play-row account did not move either); see the current state above.
- *The four-frame `round-end-pips-v1` batch is not approved* -- it is now **superseded rather than
  unapproved**: it aimed at game ends, and the boundary between those games is between rounds. Its
  annotation file remains on disk unexecuted, and the frames it would have asked are already answered by the
  probe that replaced it.
- *Prepare a new targeted proposal only for observations still missing* -- done, twice superseded:
  `build/work/bb9/winner-corroboration-v2/` is the live proposal -- eight interior corroboration requests
  derived from the corrected whole-tape public plan, prepared and label-frozen offline, held for a manual
  authorization. The v1 seven-frame freeze stays preserved byte-for-byte (three of its inspected crops
  carry into v2 with digests re-verified identical); the 21-frame proposal remains superseded raw evidence.

The proposed four-frame `round-end-pips-v1` batch has no inspected frozen labels
and was **not approved**. Its first-span diagnosis is not established by a normal
five-round duration. Codex inspected the exact crops: 4019 s shows LEFT 1 / RIGHT
0, while 4144 s shows LEFT 0 / RIGHT 1. Saved rows place black fades at 4074/4079,
a REINA-vs-AZUCENA splash at 4084 and a stage intro at 4089. Full cached frames
at 4069 and 4144 show different stages. Reconciling the intervening new-game
presentation and pip reset is what the two probes above did, and 3958–4174 is not
one game. Accepted
five-round games refute a duration-only rejection; they do not prove this span.

The 5060–5545 span is 2.2× the longest accepted game and includes a long controller/menu/selection break; it
needed the same general boundary analysis. Do not require controller or result
prompts, or split on stage intros alone. A clock returning to 60 is a round reset,
not proof of a new game. Test combined new-game/reset evidence against ordinary
round transitions and controller interruptions with continuing pips/clocks.
Then rebuild canonical games, coverage and executable prices from saved evidence.
Those tests are `tests/test_game_row_reset_grouping.py` and the rebuild was repeated from saved evidence at
each audit (`coverage-audit-v13` through `-v20`).

No inference was launched by Codex's review, and no commit or push was made.
Broad inference and Batch A remain held; the four-frame probe was superseded by the executed reset probe.

## Public-path integration (done 2026-11-02)

`investigate-games` reads the recording-scoped evidence base instead of the
classified timeline alone, and the shared implementation moved to
`tekken_vod_helper/boundary_evidence.py`:

* **One join.** The crop corpus is discovered per recording, the framing guard
  (a second picture of one instant never joins one pip series) and the window
  guard (an out-of-window answer is dropped, not appended) travel with it, and
  `scripts/pip_evidence.py`, `scripts/validate_game_one.py`,
  `scripts/report_saved_header.py` and the command all call that one function.
  A second implementation of the join is how a label once moved across a HUD
  transition and how the planner and the report disagreed about winners.
* **Zero-request equivalence.** With `--no-format`, the command's
  `recovery.candidates` equal the report's `candidates` field for field on both
  intervals: `[1200, 2200)` → 4 games with validated winners, candidate
  1588.59–2133.13 complete on `station_cleared` with deciding wins Buk 2 /
  Rebix 1, and 1216.21–1371.37 partial; `[3200, 4000)` → 3 winners, candidate
  3247.24–3543.54 complete (attribution withheld on an identity conflict),
  3728.72–3993.99 partial. Evidence:
  `build/work/bb9/public-recovery-integration-v1/zero-request-equivalence.json`,
  with the two command plans and the two reports beside it.
* **The format stays optional.** `--format-policy` applies the user-confirmed
  rules per candidate; `--no-format` withholds every target, which is the
  recording-only column. Boundary, score, identity, winner, footage and the clip
  cut are published as six separate claims, and a target never vetoes a closure
  the footage supports on its own terms.
* **The corpus is named, never assumed.** `evidence.crop_corpus` is
  `discovered`, `named_by_caller`, `operator_state_only` or `no_corpus_named`;
  the last two carry a warning that the base cannot credit a win. Library
  callers default to *no* corpus, because discovery inside a unit test would
  merge BB9's win dots into a synthetic timeline whose frames share a filename.
* **Spend is governed before it is possible.** A live `investigate-games`
  requires `--budget-file`; the shared attempt ledger carries one cap and one
  wall-time deadline across both executors and this command and every resume,
  charges before the socket, refuses to start when the groups still owed exceed
  what remains, reconciles a hanging attempt only against an answer file, and
  records `groups_deferred_by_ledger` when it stops. `--dry-run` stays free.
  Every refusal path is tested with the real client class and `_post` replaced:
  the guard fires, zero requests leave the process, and no answer file appears.
* **Typed dispatch, actually dispatched.** A question is routed to the executor
  that can answer it: the win-dot row goes to `PipReadClient(arm=crops)` at the
  decision instants (the anchor, +2 s, +4 s — the spacing measured from the K.O.
  flash and the victory close-up), clock/banner/cue/transition questions go to
  `RoundFactsClient` on its frame grid, and identity gaps go to
  `HeaderReadClient` at two instants per disputed span. A win-dots-only region no
  longer produces a whole-frame batch at all, so the fallback is structurally
  gone rather than discouraged.
* **Evidence grades are enforced where values are written and where they merge.**
  Only a cache-arm crop reading may occupy a row's `win_dots`. A whole-frame or
  band pip answer is written to `win_dots_diagnostic`, which
  `boundary_games.parse_round_facts` never reads, so it cannot create a winner
  even on a frame nobody ever cropped, and cannot overwrite a crop value even
  when it arrives later — the disagreement is kept in `pip_reading_conflicts`
  with both sides, and the row's provenance accumulates instead of being
  replaced. `evidence.pip_readings_demoted` counts the refusals, because a rule
  that fires silently is indistinguishable from one that never ran.
* **One ledger across routes, and route-qualified attempt names.** Cropping one
  frame for its pip row and band-cropping the same frame for its nameplate are
  two attempts; keyed on the bare frame token they collided, and a resume could
  close the band's hanging attempt with the crop's answer file. Attempts are now
  tagged `pip_crops:…` / `state:…` / `header:…`, purposes are recorded per route,
  and a test asserts every charged attempt name is unique.
* **Routes are funded explicitly, and priced either way.** `--routes` (or an
  injected transport, which now narrows the run rather than leaving the other
  routes live) restricts what may be sent. An unfunded route reports
  `requests_planned: 0` with its real bill in `requests_unfunded` and every
  request in its deferral list, because "nothing to ask" and "nobody funded
  this" are different facts and one zero cannot carry both.
* **A multi-pass loop with a progress stop.** Pass one's answers are merged back
  through the canonical loaders and the plan is recomputed, so a game that gains
  a winner can raise an identity question that pass one never had and route it to
  the band. Stops are `supported_outcome` (nothing left, or nothing a send can
  close), `no_new_evidence` (a pass answered requests and closed no question),
  the ledger's own reason, or `passes_exhausted`. Every deferral names its route
  and its frame or instant.
* **Markings re-run against the loaded base.** The same fault matrix (none,
  faithful, ±45 s, duplicated ×3, one missing, mid-set, before the tape, past its
  end, reversed) reaches one *scored* recovery — one complete candidate, the
  original partial, the crop-validated wins — and marks still move only the order
  of the questions. Revalidation artifact:
  `public-recovery-integration-v1/marking-matrix.json` (all ten markings
  reproduced the clean recovery on both intervals, zero sends).

Tests: `tests/test_typed_recovery_dispatch.py` (34) holds the grades, the
dispatch, the framing proof read back out of request bytes, the loop, cross-route
budgets and cancellation/resume; `tests/test_public_recovery_integration.py` (30)
holds the equivalence, corpus naming, provenance, format optionality and the
ledger guard; `tests/test_marking_resilience_current_path.py` (18) holds both
marking halves. One test from the previous module was **deleted rather than
updated**: it fed known crop counts through a mocked `RoundFactsClient` and read
the resulting winners as proof the loop worked, which validates the wrong
framing — the whole frame is the picture measured as unable to read a pip row.
Its replacement drives a real `PipReadClient` with `_post` mocked and asserts that
two labelled PNG crops left the process.

What is still not established: the loop's convergence bound is set by
`--passes`, so a window whose questions keep regenerating new ones runs out of
passes rather than proving it is finished, and it says `passes_exhausted` when
that happens. Dispatch and grading are proved with mocked transports only; no
crop, state or band request has ever been sent by the dispatch path itself, so
"the routes work end to end" is currently a statement about framing, persistence,
budgets and merge semantics — not about model accuracy on the new interval.


## The bounded unseen-interval trial — executed 2026-11-02

The tested intervals could no longer show generalisation: every question in them was
already answered on disk, so the trial ran on unseen footage at
`build/work/bb9/unseen-set-structure-v1/`. It spent 61 of its 66-attempt cap inside
the single 45-minute deadline, sent zero requests outside that ledger, and changed no
label. The proposal below is kept as the record of what was asked and why; the
outcome is in `trial-evaluation.json`.

**The previous interval was withdrawn, and the withdrawal needed correcting too.**
[4800, 5600) was chosen partly because approximate operator metadata labelled that
stretch as one match, so a set boundary there could not be confused with a match
change — a premise never checked. Checking it was right; the conclusion first drawn
from it was not. That correction said the window "contains at least three match
changes" and therefore measured the wrong thing. That is not a disqualifier: menu
screens are what a bracket recording looks like, and this tape has them
everywhere — [1200, 2200) and [3200, 4000), whose recoveries are accepted, hold 105
and 104 match-change rows against 58 in [4800, 5600). "Contains menus" cannot be
the reason to reject a window when it is equally true of the windows that worked.

What distinguishes them, measured the same way for every candidate window:

| window | rows | gameplay | games concluded | change screens inside a tracked game |
| --- | --- | --- | --- | --- |
| [1200, 2200) accepted | 333 | 55% | 3 of 4 | 1 |
| [3200, 4000) accepted | 287 | 50% | 2 of 4 | 0 |
| **[2400, 3200) proposed** | 284 | 50% | **4 of 4** | **0** |
| [4800, 5600) withdrawn | 159 | 60% | 2 of 4 | 1 |
| [4000, 4800) | 160 | 74% | 2 of 4 | 0 |

[4800, 5600) is withdrawn because its classification is ~1.8× sparser than the
accepted intervals (159 rows per 800 s against 287–333), only half its games reach a
conclusion, and one of those games contains a change screen and part of the
5220–5445 s controller-assignment stretch, where the players are not in a bout at
all. [2400, 3200) is proposed because it is unseen, is as dense as the accepted
intervals, has every tracked game reach a conclusion, and has no change screen
falling inside one. `trial-proposal-4800-5600.json` keeps these numbers, because
the file now records two reasoning errors worth not repeating: trusting an
approximate label as structure, and then overcorrecting into a criterion the
accepted intervals would also have failed.

* Interval **[2400, 3200)** — 800 s holding zero crop readings and zero band
  readings, 284 state rows (50% gameplay), 14 rounds and 4 tracked games, **all
  four concluded**, grouped into **2 candidates of two games each**, both
  `partial`, with **0 winners** because no pip row in the window has ever been
  read. The shape is measured and reported, not predicted; treating "four games /
  two candidates" as a fixture is dropped with the old interval.
* The question was **"is each two-game candidate one set, or are these four
  one-game matches?"** -- and the inspected labels answered it before the run:
  neither. The clock says when rounds end and the pip row says who took them;
  neither says whether the menu between two games closed a set or closed a match,
  which is why the tracker's grouping is a hypothesis and why the trial is graded
  over **every** grouping of the four games (eight of them), not two named
  favourites. `unseen-set-structure-v1/set-structure-evaluation.json` is that
  grading, computed offline from the frozen labels.
* Exact typed requests, each measured by the tool that would spend it: **42 crop
  requests / 84 images** (14 decision windows × 3 frames, two labelled crops per
  frame, 3,000-token ceiling each), **4 state batches / 24 images** (8,000 each),
  **8 band readings / 8 images** (1,500 each) — **54 requests, 116 images,
  170,000-token ceiling**, plus a **12-request second-pass reserve** because pass
  one's answers create the next questions. Prepared without sending by
  `run_pip_crop_probe.py --prepare` (`unseen-set-structure-v1/run/plan-crops.json`)
  and `run_header_trial.py --prepare` (`.../header-run/plan-header.json`).
* Fresh aggregate budget: `build/work/bb9/unseen-set-structure-v1/attempts.json`,
  cap **66**, one **45-minute** deadline, shared by both executors and this
  command. It is not created by this proposal. The executed 54-of-70 batch and the
  expired remainder of the 2026-10-31 ledger are history, not headroom.
* **Independent labels: inspected and frozen 2026-11-02, before any inference.**
  All 14 window sheets and the band sheet were read by eye at 8x the pixels the
  model receives; `labels-eval-frozen.json` (sha256 `843865ec8be6d196`) now carries
  the literal values with per-crop and per-band digests, `inspected: true` and
  `frozen_at: 2026-11-02`. The denominators are **22 graded visible-count frames
  (44 sides), 13 known-absent frames, 7 frames whose row is drawn but not
  closeable** -- occluded by a victory close-up, or covered by a K.O. flash -- and
  those 7 are unknown truth, excluded from every denominator rather than guessed.
  The band labels are the eight nameplate pairs and the eight displayed games
  columns: `PWDDA | Ariz` and `Mattiniero` at all eight instants, with the column
  reading **0-0, 0-0, 0-1, 0-1, 0-2, 0-2, 0-2, 0-2**. No label was derived from a
  tracker prediction; the runtime annotation file still carries null for every
  frame, and the four frames where a band and a crop cover the same instant agree
  on the pip counts (1-1, 0, 2, 3-1), which is the cross-check that the crop
  geometry is being read the right way round. The selection caveat stands: the 8
  band instants come from the tracker's own game boundaries, so they are not
  independent of the pipeline under test.
* **What the labels already say about the structure.** The pip rows name three of
  the four games (RIGHT, LEFT, LEFT) and leave the first unreadable. The games
  column climbs twice and resets nowhere, and the pairing never changes, so four
  one-game matches would need three resets that are not there, and a set boundary
  at the menu near 2473 s is contradicted by the column climbing through it. The
  cut the pipeline actually drew -- between 2603.6 s and 2771.8 s, on a controller
  prompt at 2625.6 s -- is its own **proposal**, and the evaluation keeps it there:
  it is printed beside the column, the two disagree at that instant, and neither
  one eliminates a grouping, so all eight stay open and `unresolved` is the answer.
  Reading that edge back as supported temporal evidence was the circularity the
  review of 2026-11-02 caught -- the trial would have graded the very hypothesis it
  exists to test. The alternative that keeps the pipeline's grouping needs the
  column to have stayed stale for 168 s, against the 34-42 s lag measured on this
  same tape.
* Sequence, done: read the sheets and fill the labels (2026-11-02) → feed the filled
  labels through the real merge and tracker with the transport replaced (arithmetic,
  not evidence: 22 visible-count frames, 13 known-absent, 7 withheld, four games with
  the first winner unnamed) → re-plan and confirm the bill (54 requests / 116 images;
  the default `--max-requests` is 24, which silently truncates a dry-run, so the
  allowance is passed explicitly) → **execute** → grade transcription and structure
  apart, with boundary, score, identity, winner, footage and clip cut kept separate.
* **Result, 2026-11-02 (`run-attempt/`, `trial-evaluation.json`).** 61 attempts
  charged of the 66 cap, 60 groups answered, **0 uncertain, 0 in flight, 0 failed
  request**, 16.1 minutes of the single 45-minute deadline used; 46 crop + 7 state +
  8 band sends. Pass 2 spent 6 of its 12-request reserve and stopped itself on
  `no_new_evidence`: 11 questions stayed open and the frames it still owed did not
  settle them. One group was re-sent after a reply the client would not accept, so 61
  charges bought 60 answers and the ledger says so (61 entries, 60 distinct names).
  Tokens: 44,118 prompt / 39,044 completion of which 32,347 reasoning.
  *Transcription*: **22 of 22** graded visible-count frames correct, **44 of 44**
  sides exact; **11 of 13** known-absent frames correct. The two failures are one
  class: `pip_invented_on_undrawn_row`, at 3052.048840 s (claimed LEFT 1) and
  3054.050839 s (claimed RIGHT 2), both frames where the camera had panned off the
  HUD and the label says no row is drawn. The 7 drawn-but-not-closeable frames stayed
  outside every denominator, and the model answered numbers on all 7 -- which is why
  they are withheld rather than forgiven. Three frames asked in pass 2 (2513.513,
  2515.515, 2517.517 s) have no labels at all, so they grade as nothing.
  *Identity*: all **8 of 8** nameplates and **8 of 8** displayed games columns read
  exactly as inspected, including the 0-0 → 0-1 and 0-1 → 0-2 climbs and the two
  character changes.
* **The finding worth more than the score.** Those two invented pip counts are not a
  cosmetic miss: the invented LEFT 1, two seconds after the real LEFT 3, is what the
  tracker read as the left row *falling*, so game 4 came back
  `contested:win_dots_regressed:p1` with its winner withheld -- a winner the inspected
  labels name (LEFT 3, RIGHT 1 at 3050.046840 s). The pipeline reported 2 of the 3
  winners the labels settle. The scoring layer refuses credit for a count on an
  undrawn row, but the row still enters the same pip series as the real readings,
  where a fall rule can see it: **a presence error is a structural error**, and the
  fix belongs in the merge or the regression rule, not in the prompt. Nothing was
  patched mid-trial and no answer was re-asked; this is the next narrow proposal.
* **Structure, as graded.** All eight groupings of the four games survive: nothing
  independent decided any of them, and the report publishes the set rather than
  choosing from it (`unresolved: true`, `one_answer_published: false`). The pipeline
  drew its cut between 2603.6 s and 2771.8 s on `station_cleared` and that edge stays
  a **proposal**; the column climbs through it, and the clash is recorded with
  `decides: false`. What the run itself concluded is narrower than a winner: the
  candidate 2771.77-3050.05 s is **complete on the footage** and its end is settled by
  a station clear, so it is reported as a set ended on the recording with no format
  supplied and **no winner named**, because no target was configured; candidate
  2400.40-2603.60 s stays **partial at its leading edge** (`round_start_not_observed`:
  its first game was already running when the stretch began) and both facts are
  reported apart from each other. The credited win inside candidate 2 is 1-0 to Ariz,
  where the inspected labels would give Ariz both of its games -- the missing one is
  the withheld winner above, not a disagreement about the boundary.
  The reading that survives the labels best is still one continuing set across the
  window, undecided inside it, and the reading that keeps the pipeline's two-candidate
  split survives only if the column was stale from 2603.6 s onward. Both stay in the
  report; neither is published as the answer.
  *Superseded on 2026-11-03 by the frame-by-frame section below*: the staleness question
  turned out not to be the hinge, the "1-0 to Ariz" figure predates the corroboration guard
  (candidate 2 is 2-0 in the corrected report), and the station's own content plus the
  in-match `WIN` counter now favour the split.
* On the withheld frames, one corroboration worth recording: on the 7 frames labelled
  drawn-but-not-closeable the model returned a count for 11 of their 14 sides, claimed
  no row on 2 and unreadable on 1. Where my note said "1 or 2" (2552.55 s, RIGHT) it
  answered 2, and where I could read the outer slot as dark but not the pole-covered
  one (3004.00 s) it answered LEFT 2 with RIGHT 0, which is what I saw on the side that
  was legible. That is agreement on the parts that could be checked, not a licence to
  fill the withheld frames in: they stay outside every denominator.
* What this does not settle, and the saved state answers say more here than the last
  handoff admitted. The displayed column, at every instant the trial's own evidence
  carries it:

  | span | column | reading |
  |---|---|---|
  | 2402.40-2467.46 | 0-0 throughout | game 1; the column climbs to 0-1 at 2476.47 |
  | 2493.49-2603.60 | 0-1 throughout | game 2; it climbs to 0-2 at 2612.61 |
  | 2771.77-3050.05 | 0-2 throughout | games 3 and 4, both of which the pip series give to LEFT |
  | 3050.05-3236.23 | 0-2 -> **0-0** | the station after game 4; the display resets here, and the next set plays from 0-0 |

  The column is therefore not dead, and reading it finely changes which hypothesis it
  supported. It updates normally until 2612.61, is then **frozen at 0-2 from 2612.61 to
  3234.23** -- across all of games 3 and 4, a station clear, and the next set's character
  select -- resets at 3236.23, and resumes tracking immediately (0-1 at 3381.38, 0-2 at
  3553.55). Three consequences, in order of how much they cost the old story:
  * The climb the structure report flagged as clashing with the cut at 2603.6 is the
    **credit for game 2**, 9.0 s after the K.O. that closed it. A credit inside a
    between-games edge is what a credit looks like whether the set continued or not, so it
    was never evidence against the cut. `overlay_tally.can_decide: false` was right; the
    word "clash" made it sound like a contradiction.
  * From 2612.61 to 3234.23 the display carries **no information at all**. The one-continuing-
    set reading has no display support left in that stretch: it cannot point at a 0-2 that
    also sat unchanged across a boundary where the display demonstrably knew how to move.
  * What the pip series say about games 3 and 4 (LEFT takes both) is therefore unopposed by
    the column -- and unconfirmed by it. Under the already-supplied first-to-two target two
    games to one side is a concluded set, which is why the remaining gap is a confirmation
    and not a contradiction.
  The "one result screen inside the station" that this section previously named as the
  smallest remaining observation is **withdrawn**: the same menu family appears after game 1
  (2473.47-2479.48: 'Thanks'/'Next', then Rematch / Select Character / Select Stage / Quick
  Select / Main Menu), so in this broadcast a post-match menu marks a *game* end and reading
  one would prove nothing about a set. What the station does contain, and what the
  game-1-to-game-2 transition does not, is in the next section.

The four extra nameplates were not funded: they widen attribution on games that were
already tested. The nine state requests at the old analysis-window edge remain
deferred and remain named -- pass 2 spent 6 of its 12-request reserve and stopped
itself on `no_new_evidence`. No full-VOD pass, model swap, upload, commit or push, and
the run re-asked nothing already answered (`groups_reused: 0`, `not_attempted: 0`).
Retain Flash Next + cached crops; the native renderer stays available without a
general superiority claim.

**The presence/temporal fix, done 2026-11-02 from saved answers only.** A pip count
invented on a frame where no row is drawn was refused credit by the scorer and still
consumed by the merge as a dot observation, so `3 → 1` across two seconds of cutscene
became a dot regression and withheld a winner the labels name. Filtering `drawn:false`
could never have caught it: both invented readings said `drawn:true` (LEFT 1 at
0.55, RIGHT 2 at 0.60). Looking up a "graded row" was refused too -- that is evaluator
truth at runtime. The rule that works is temporal, it is directional, and -- after the
next review -- it is corroborated:

> A win-dot count is set aside as a row leaving the HUD only when all of these hold, from runtime
> evidence alone: it is the **last** reading of its side's series; it lands within
> `RESULT_SEQUENCE_SECONDS` (5.0) **after the observed end of the round it belongs to**; the count it
> lowers was **already settled at or before that end**; **the same answer reports the counterpart row on
> that frame as not drawn** (`counterpart_row_drawn is False`), so the frame is not a complete scoreboard;
> and the fall is **not claimed more confidently than the count it contradicts** -- an ordering between
> two readings of one row, never a calibrated probability, and refused outright when either confidence is
> missing because then no ordering exists. It is reported on the game as
> `dots_after_round_end`; raw series are never rewritten, and a fall refused on any ground stays a live
> contradiction, recorded as `dots_after_round_end_declined` with the reason.

Direction and bound are both load-bearing, and two existing tests taught me that the
hard way. My first version excused any post-end fall; `test_dots_that_fall_inside_a_game
_contest_the_winner_and_are_queued` rejected it, because a fall 40 s after a round closed
is not a victory pose -- it is the wrong-boundary signature the fall rule exists to keep.
My second version popped the reading and recompressed the series; the same test's
`win_dots_seen == [0, 3, 0, 1]` assertion rejected *that*, which is what "preserve raw
observations" means in practice. And only a trailing reading is excused: `3, 1, 2` is a
series disagreeing with itself and still contests. A rise after the end stays evidence,
because that is how a deciding pip arrives -- game 2 of this very trial was won by a row
read 2.002 s past its own end.

Measured A/B over the canonical join across six regions -- `[2400,3200)`, both accepted
intervals and the three historical game windows -- comparing the shipped guard with the
corroboration disabled: **one reading is set aside** (3052.04884 s, counterpart row
`drawn: false`, fall 0.55 against a standing 0.92), **none is declined**, and every
window's winners are identical either way. The rule therefore buys its generality at no
cost on this recording, and the case it now refuses is the reviewer's probe: a 3 read at
0.30 on the K.O. frame and a 2 two seconds later at 0.99 reports
`{'p1': {'from': 3, 'to': 2, 'at': 22.0}}` with `declined_because` naming the ordering,
where the version before this round excused it and silently preferred the shakier number.

**Game 4 remains supported.** Its winner is still `p1` on the same evidence, but now for a
reason the recording actually supplies rather than by assumption from the K.O.: the frame
that reports LEFT 1 is the same frame that reports RIGHT's row as not drawn.

Ten cases hold the block (`test_boundary_games.py`, "the pip row read after its own round
closed"). Five are the exception and its refusals: the corroborated fall; the same fall on a
frame that still draws both rows; the same fall where nobody read the counterpart row
(silence is not a presence claim); the review's probe, a 3 at 0.30 answered by a 2 at 0.99;
and a pair the recording cannot order because a confidence is missing. Four are the edges
that were already load-bearing and must stay: the 40 s fall still contesting, `3, 1, 2` still
contesting, a post-end rise staying evidence, an inferred round end bounding nothing. The
tenth checks the wiring, because the corroboration is worth nothing if the merge drops the
counterpart's presence claim before a fall is judged. The pre-existing mixed-load regression,
`test_dots_that_fall_inside_a_game_contest_the_winner_and_are_queued`, lives outside the
block and still refuses the unbounded version of this rule on its own.
Genuine resets and one-sided series keep their
coverage: every fixture carries one side's series only, and
`test_a_fully_absent_row_still_contributes_no_counts` keeps the absent-crop case honest in the
other direction. Nothing here reads a label file, no timestamp is whitelisted, and nothing
deletes a reading. A refused exception is now visible as `dots_after_round_end_declined` with
the ground that refused it.

Measured on the saved answers after the tightening: the guard set aside **one** reading
across the six regions run (3052.04884 s, `counterpart_row_drawn: false`, fall 0.55
against a standing 0.92), **declined none**, and every window's winners are identical to
the pre-tightening guard -- the exception costs nothing that the round end was already
paying for, and it is the probe that it now refuses. Game 4 remains supported: winner
`p1`, same evidence as before, with the corroboration named in the record rather than
assumed from the K.O.

**Two reporting defects the run exposed, both fixed the same day.**

*Band provenance, now per origin.* The provenance in `run-attempt/investigation.json`
says "these responses were preserved from a run that has already happened; they are
re-read, never re-sent, and no ledger is claimed for them", reports
`ledger_recorded: false`, and counts 13 answers as having no record of their spend.
Eight of those 13 were sent by that command minutes earlier and are recorded in its own
`attempts.json` as `investigate-games:header` -- 8 entries, all answered, all with token
usage, all inside the 66 cap and the 45-minute deadline. The artifact described an
authorised, counted spend as an uncounted one: the mirror image of the 2026-10-30
incident, and wrong in the direction that makes a governed run look ungoverned. Two
causes, both fixed: `boundary_investigate._combined_header` appended the run's own band
store with **no** ledger argument, and even had it passed one, `_ledger_claim` matched
only a bare `frame-2401` while `investigate-games` charges the route-scoped
`header:frame-2401`. Provenance is now computed per origin (`saved_readings(...,
own_run=)`), so borrowed answers keep their own history and the run's answers link to the
run's ledger. Recomputed over the identical saved corpus:
`answers_with_no_record_of_their_spend` **13 → 0**, the 8 fresh answers `governed`, the 4
+ 1 from the two attribution runs `governed` against their own ledger, and the 6
unapproved answers still `incident_recorded` -- an incident record is a record, and
counting those six as unrecorded would soften what happened on 2026-10-30 into an
omission. Aggregate `approval` stays `mixed`, because mixed is the truth: 13 charged, 6
incident-recorded, none silent. Fixing the call site also exposed a latent crash -- a run
that sent band answers but borrowed none hit `response_directories("")` and died; an
empty corpus is now empty, not an error.

*Recovery output, now separating three claims.* `trial-evaluation.json` could call a
candidate `complete` and `concluded_by: ["station clear"]` while the structure report in
the same directory kept every grouping of the same games open. Those are three different
claims and only two of them were earned: a station clear is an **observed end** and
supports a **proposed cut**; a **confirmed completion** needs a tally reaching a target,
and none was supplied and none reset. Candidates now publish
`end_transition_observed`, `cut_proposed_by_pipeline`, `set_completion.{state,
target_supplied, target_reached, why}` and `grouping_unresolved`, and the
`format_comparison` key `closed_by_the_recording_alone` is renamed
`cuts_proposed_by_the_recording_alone` beside a new
`sets_completed_by_a_confirmed_target` -- the key was part of the overclaim. `status`
keeps its footage meaning, so nothing is discarded: the candidate at
2771.768854-3050.046840 s is still complete on the tape with a station clear at its end,
and now also says plainly that its completion is `proposed_cut_only`.

## The disputed boundary, read frame by frame (2603.6-2771.8), 2026-11-03

Eighty-five rows of the saved state timeline already cover this stretch at 1 s cadence -- no request was
sent to write this section, and no evaluator label is used in it. The sequence, with the model's own words
abbreviated:

| span | what the frames say |
|---|---|
| 2603.60-2606.60 | K.O. banner over the arena at timer 18, KO freeze, victory pose, in-match 'YOU WIN'; explicitly "no menu or controller overlay" |
| 2607.61-2612.61 | Reina stage-intro cinematic with subtitles; the broadcast box reads 0-1, then "updated to 0-2" at 2612.61 |
| 2613.61-2621.62 | post-match menu: 'Thanks'/'Next', then Rematch / Select Character / Select Stage / Quick Select / Main Menu, scoreboard 0-2 |
| 2622.62-2624.62 | another stage-intro cinematic, still 0-2 |
| 2625.62-2717.72 | **92 s** of central "Player 1 / Press [button] on the controller" with a 'Controller disconnected.' toast; scoreboard 0-2; at 2632.63 the line-up behind it is read as "P1/P2 tags, timer 60, **WINS 2**" |
| 2715.71, 2719.72 | OS "Who's using this controller? / Assign Controller", Guest2 selected |
| 2718.72-2726.72 | pause menus over idle fighters, both REINA nameplates, then a black transition |
| 2727.73-2730.73 | **Tekken 8's own Main Menu** -- Story/Offline/Online/Customization/Replay, "Arcade Battle > VS > Player vs Player" highlighted |
| 2731.73-2757.76 | **character select**: roster grid, Hwoarang and Kazuya browsed, then NINA for P1 and JACK-8 for P2 with a costume preset open |
| 2758.76-2763.76 | black loading, only the broadcast box and webcam |
| 2762.76 | stage select, 'REBEL HANGAR', '?' cursor |
| 2764.76-2767.77 | VS splash "NINA vs JACK-8", score 0-2 |
| 2768.77-2770.77 | stage intro on the hangar |
| 2771.77 | "Fighters in starting stances with 'ROUND 1' callout ... broadcast set score remains 0-2" |

Compare the *undisputed* game-1-to-game-2 transition, same recording, 2473.47-2493.49: 'Thanks'/'Next' and
the Rematch/Select Character/Select Stage/Main Menu list, three seconds of black, **stage select with
RANDOM**, a 'REINA VS REINA' splash, stage intro, ROUND 1 -- twenty seconds, no controller reassignment, no
main menu, and both players keep the same character. Three of those four are therefore not discriminators:
the post-match menu family marks a *game* end here, which is why the result-screen idea above is withdrawn.

What does separate the two stretches, in the saved words:

1. a return to Tekken's **main menu** and a fresh Arcade Battle > VS > Player vs Player entry
   (2727.73-2730.73), which the between-games flow never does;
2. a **full character re-selection for both players** (Reina/Reina -> Nina/Jack-8), where the game-1
   transition only re-rolled a stage;
3. **92 s of controller disconnect and reassignment** including two OS "Who's using this controller?"
   screens -- a station change, not a round timer;
4. the in-match **`WIN n` counter**: `WIN 1` during game 2 (2497.50, 2522.52, 2602.60), `WINS 2` across
   the station (2632.63, 2682.68, 2707.71), and **`WIN 1` again during game 4** (2890.89 intro, 3043.04
   live). A tally that reached 2 and later reads 1 is not counting the same match.

Its limits, stated plainly: the prose never says *which* player a given `WIN` tag belongs to, and Tekken's
VS line-up also shows a per-character win total, so `WINS 2` at 2632.63 may not be a game count. The
counter is therefore **supporting** evidence, and it is the pipeline's existing behaviour that keeps it
there -- `station_cleared` is already barred from closing anything on its own.

What each grouping rests on now:

| grouping | support | against it |
|---|---|---|
| games 1-2 / 3-4 (the pipeline's two candidates) | the three station differences above, `WIN` 2 then 1 again, the box crediting games 1 and 2 (2476.47, 2612.61) and then dying, and the supplied first-to-two target for a "LOSERS ROUND 1" stage | nothing in the saved evidence contradicts it; it is simply not *published* as confirmed, because this run carried no target and the counter's owner is unrecorded |
| one continuing set (all four games) | only the box's 0-2 held across games 3-4 | now excluded where a target is applied: game 1 was named **p2** by the decider probe below, so p2 stood at two games by 2603.60 and a first-to-two set cannot continue past it. It survives only in the no-target column, which measures the footage with no rules rather than the rules' absence from the recording |
| any cut at 2880.88 (inside games 3-4) | nothing | that transition is the short kind: 11 s, and although it does open the same 'Rematch / Select Character / Select Stage' panel (2886.88-2888.89), it has no main-menu exit, no character re-selection and no controller reassignment -- so the discriminators are those three absences, not the presence of a menu |

The eight groupings in the structure report stay eight, and the equality among them is an artefact of
`--no-format`, not a finding about this footage.

Two things the state route gives for free, which the pip series alone did not claim:

* **Both games 3 and 4 went to Nina**, i.e. the left side: 2883.88 "Victory pose: Nina standing over
  downed Jack-8" and 3050.05 "Round-end KO moment: Nina victory stance, Jack-8 downed". So candidate 2's
  "two games to one side" has a second, independent runtime signal, and under the supplied first-to-two
  target it is a concluded match. It is still published as `proposed_cut_only`, because this run carried no
  target and the counter's owner is unrecorded -- correctly, not by omission.
* The frames around game 4's end are described as **victory close-ups** (3051.05 "Nina victory-pose
  close-up", 3052.05 "'YOU WIN' round-result text over Nina", 3054.05 "Post-KO victory-pose camera on
  Nina") -- a framing in which no pip row is on screen at all. That is the same fact the corroboration
  guard now needs, arriving from a *different* route than the crop's own counterpart claim, and it covers
  the second invented reading (RIGHT 2 at 3054.05) that the directional rule deliberately leaves in the
  series. Recording it here instead of wiring it in: the guard is closed for this round, and a second
  corroborating source is a change to make with its own negative cases.

## The decider frame — executed 2026-11-03, one request

Game 1 was the hinge, and for a reason unrelated to the boundary: it is truncated by the window. The crop
arm's own first answer, frame-2406 at 2407.40 s, reads LEFT 1 / RIGHT 1 with both rows drawn at 0.88, so
two of its five rounds were fought before 2400 s. The trial stepped 2 s and missed the decider: frame-2466
(2467.46487) answered LEFT 2 / RIGHT 2 -- the score *before* the decider lit -- and frames 2468 and 2470
answered no row on either side. The round clock reads 42 at 2467.465 and again at 2468.466, so 2468.46587
is the last frame of that freeze, and game 4 of this same recording is the precedent that a deciding pip
arrives about 2.002 s after its K.O.

Codex inspected both candidate frames before any send and cut the probe to one: frame-2469 is a YOU WIN
close-up with no pip HUD, so asking it would have spent an attempt on a frame that cannot answer. That was
the right call and it covered a real gap in my plan -- the "stop after a decisive frame" rule I wrote was
never implemented in `persist_probe`, so limiting the input is what actually prevented the second read.

```bash
PYTHONPATH=. .venv/bin/python scripts/run_pip_crop_probe.py \
  --annotations build/work/bb9/game1-decider-probe-v1/single-frame-annotations.json \
  --frame-manifest build/work/bb9/detect-1440p-v1/frames/sample-manifest.json \
  --output-dir build/work/bb9/game1-decider-probe-v1/run-single-frame \
  --arms crops --max-requests 2 \
  --budget-file build/work/bb9/game1-decider-probe-v1/attempts-single-frame.json \
  --budget-cap 2 --budget-minutes 10 --purpose game1-decider-single-frame --execute
```

**Charged: 1 of 2 attempts** (3.96 s, 1,060 tokens, 0 uncertain, 0 in flight). Answer for frame-2467 at
2468.46587 s: LEFT drawn `true` lit **2**, RIGHT drawn `true` lit **3**, both at 0.95.
`grading-2026-11-03.json` scores it against `labels-codex-inspected-2026-11-03.json`: **2 of 2 sides
exact**, frame SHA-256 matching the label record, those labels evaluator-only and never a runtime input.
One caution the counts hide: the *slot indices* do not share a convention (LEFT `[1,2]`, RIGHT `[1,2,3]` on
a three-slot row -- at most one is 1-based), so positions are not trustworthy even where the count is.
Nothing downstream reads them as evidence.

Canonical recovery re-run over the saved answers (`recompute3-{noformat,format}/plan.json`, both
`inference_sent: false`, both including the trial's own eight bands in `--header-responses` -- leaving them
out was my first attempt and it understated the policy column, because "LOSERS ROUND 1" is in those
answers). Four claims, kept apart:

1. **Winners.** All four games now name one, `basis: win_dots`: game 1 **p2**, game 2 p2, game 3 p1,
   game 4 p1. Game 1 is the new one, and it agrees with the broadcast box crediting Mattiniero at 2476.47
   and with the in-match `WIN 1` seen during game 2.
2. **Proposed grouping.** `[2402.40-2603.60] [2771.77-3050.05]` -- unchanged in shape, but its status
   changes with the column. Without a target the structure report keeps all **8** edge subsets surviving,
   `membership_supported: 2`, `ambiguous_links_kept_as_alternatives: 2`. Under the supplied policy the cut
   after game 2 is *forced*: p2 reached two wins there, so a first-to-two set ended, and the same holds for
   p1 at game 4. `grouping_unresolved` becomes `false` and `membership` stays `supported`, not assumed.
3. **Partial coverage.** Unchanged by the probe and not implied by the two points above: game 1 opened
   inside a set already in progress (`round_start_not_observed` at 2402.399874, `footage_complete: false`
   on candidate 1), 2 of its 5 rounds precede the sampled window, the 168 s station was seen by state and
   never crop-read, and the 7 drawn-but-not-closeable frames stay withheld from every denominator.
4. **Completion.** No-format column: both candidates `proposed_cut_only`,
   `sets_completed_by_a_confirmed_target: []`, `with_format {complete: 1}` vs `without_any_target
   {complete: 1}` and no winner named from footage alone. Format column: both candidates
   `set_completion.state: confirmed`, `concluded_by: [target_reached, station_cleared]`,
   `complete_under: [2]`, set winners **Mattiniero** and **PWDDA | Ariz**, `with_format {complete: 2,
   winners_named: 2}`. The target arrives as `stage named by the band inside this candidate's span`
   ("losers round 1" -> 2), so the closure is footage + an optional rule, never the rule alone, and both
   winner names are `winner_unit: broadcast_identity_hypothesis`.

**The disclosure defect that recompute exposed, fixed the same day.** In the no-format column candidate 1
listed `the start of this set: ...` in `missing_evidence`; in the format column that line vanished and
`missing_evidence` came back `[]`, because the claim was keyed to `basis ==
"set_ended_on_the_recording_footage_incomplete"` and reaching a target changes the basis. A target is a
fact about the rules and the leading edge is a fact about the evidence, so the disclosure is now keyed to
the edge (`_set_record` asks only `edges["start_observed"]`) and a confirmed completion carries it. The
edge also says *which kind* of absence it is (`_match_edges` publishes `limit`), and the wording differs
because the remedies differ:

| `edges.start.limit` | what it means | what closes it |
|---|---|---|
| `analysis_window` | the caller's window opened after the set did; the tape exists upstream | widen the window -- costs no inference |
| `recording_head` | the recording itself begins inside the set | nothing: no window reaches it |
| `footage_never_sampled` | frames exist there and none was ever asked | a finer grid, which is the next section |

Three tests hold it (`test_set_membership.py`): a reached target keeps the line and names the window; the
same candidate shape read against no source evidence, a source that really starts at the edge, and a cache
that reaches back past the window produce three different disclosures; and a set whose first game opened on
tape still claims completion with nothing appended, so the fix is not a blanket downgrade. The trailing
edge initially looked safe -- it had its own line -- but that line read "the recording (or the caller's
window) stops there", offering the reader a choice the evidence had already made, and it was silenced by a
reached target exactly like the head. Both edges are now keyed to the edge and to its kind; see the
widening section below.

Controller events, character changes and the WIN-counter reset stay **supporting** evidence: they
corroborate a station change, and none of them closes or vetoes a set. Neither the pip winner nor the
frozen broadcast column proves the grouping on its own -- the target does that, optionally, and the target
has never been verified against the bracket.

Nothing further is required to settle membership inside `[2400, 3200)`. What remains askable is the
leading edge (one state read before 2402.4 to say where the first set began) and the 7 withheld frames;
neither is a prerequisite, and the trial's own 61-attempt artifacts are untouched. The discovered crop
corpus is now 205 readings -- one more than the executed run merged -- so a recompute of this window
legitimately differs from `run-attempt/`, and that difference is this probe.

## Autonomous discovery of the decider frame (2026-11-03)

The probe above was aimed by hand. The gate now is that the investigator finds that frame itself, from the
original 61-attempt corpus, with the probe's own answer and every evaluator label withheld from planning --
so the recovered result cannot be the one that steered the search.

The rule added to `pip_requests` (no new route, no new prompt, no new module):

> Refine a decision window only when all four hold: **every** coarse frame of that window already has a
> crop answer on disk; **none** of those answers shows a side reaching the round target; **the tracker
> still has a pip question at that instant** (a `PIP_QUEUE_KINDS` anchor inside the probe span); and the
> window's own answers say the row stopped being readable at some instant after the end. Then offer the
> cached frames strictly between the coarse offsets, up to one per gap, bounded by that last readable
> instant and by the run's shared allowance.

Two rejections shaped it. "No reading reached the target" alone was tested first and it asked for **11**
extra frames across `[2400, 3200)`, because most rounds close 2-1 or 1-0 -- the target test cannot tell a
settled round from an unanswered game, and only the tracker's own open question can. And refinement is an
*append*: the first cut widened the frame cap instead, which leaked the `next_round_opening` candidate into
11 plans and broke `test_loading_the_corpus_shrinks_the_work_without_touching_the_conclusion`, whose
guarantee is that a loaded corpus leaves the crop route silent on `[1200, 2200)`. With both rejections in
place that interval plans **zero** crop requests again (7 state, as before) and the trial's own window plans
exactly **one**.

What it selects, planned from the corpus as the trial left it (204 crop readings, probe excluded):

```
2468.466  freeze_interior_plus_1s  frame-2467.jpg   anchor=2467.465  window=2
```

That is the frame the hand-built probe asked -- found from the clock freeze, the abstaining neighbours and
the queue, with no timestamp written down anywhere. Replaying the saved answer offline closes the loop:

| corpus | crop readings | game winners | asks frame-2467 | open pip questions |
|---|---|---|---|---|
| trial as executed | 204 | `[None, p2, p1, p1]` | yes (1) | `game_winner_unobserved` |
| + that one answer | 205 | `[p2, p2, p1, p1]` | no (0) | -- question gone |

Genuine contradictions are untouched by this: `dwell_after_observed_end`, `round_clock_impossible_fall`,
`round_clock_unread` and `round_start_not_observed` all stay in the queue after the refinement runs, and
four tests pin the negatives (a window that already shows the target asks nothing; an unasked coarse frame
outranks any interior; no queue anchor means no refinement at all).

**Remaining gaps to autonomous recovery, measured rather than assumed.**

* **Backward reach: implemented 2026-11-03, see the widening section below.** The measurement that
  motivated it stands on its own: with `[2300, 3200)` the same planner spontaneously puts six questions
  before 2402.40 (round ends at 2332.33 and 2362.36) while still finding the +1 s interior at 2468.47,
  because every question is generated from observations inside the window. The gap was never the reach --
  it was that nothing widened the query on the candidate's behalf.
* **Forward: same fix, same cause.** The tail disclosure fires when nothing was observed after the last
  game, and the widening now reads that disclosure; footage beyond the last sample is still never queued
  without it, which is why the station after game 4 has state rows and no crop probe.
* **Unmarked set boundaries remain undetectable by design.** `_match_edges` accepts two families as
  boundary evidence -- `station_cleared` and `bracket_match_change` -- and everything else inspected here
  (main-menu navigation, character re-selection, controller reassignment, the `WIN` counter) is supporting
  only. So a set that ends with no station cue and no band-named stage can only be closed by an applied
  target, and `[2400, 3200)` is exactly that case: without the policy both candidates stay
  `proposed_cut_only`, and the footage gains no confirmation from any of the transition signals however
  strongly they hint. A question that would ask a frame "does this screen state a set result" does not
  exist in the current prompt set, and inventing one is a bigger step than this round's refinement was.
* **A refinement that answers nothing is still a cost.** The gate fires per window, so a recording whose
  queue is full of `win_dots_never_read` anchors could spend its whole allowance on interiors before
  reaching the coarse asks it never made. Ordering protects that today (`exhausted` requires the coarse
  triple first); a per-run cap on refinement requests, separate from the allowance, does not exist and is
  the next thing to add if this route stays.

## Bounded automatic widening of the query (2026-11-03)

Two changes, in the order the review insisted on: prove where the tape is before reaching for it.

**Source-boundary provenance.** `_match_edges` used to infer `recording_head`/`recording_tail` from the
first and last analysed observation, which proves nothing -- Codex reproduced it with rows starting at
110 s and a candidate at 100 s, and the pipeline asserted the *recording* began at 100 s. The claim now
comes from `boundary_games.source_bounds(manifest=, cache=, duration=, recording_start=, recording_end=)`
and `footage_limit()`, and the three extents are kept apart deliberately:

* **cache** -- which frames exist on disk. `boundary_media.sample_frames` writes `start`/`end` as the span
  it *asked the encoder for* (clamped to the duration), so a sampling header establishes cache extent only;
* **source** -- how long the media is and where its timeline starts, from explicit metadata alone:
  `recording_start` / `recording_end`, a manifest's `source_duration` (now written by
  `boundary_media.sample_frames`), or a caller-supplied `--duration` from probing the file;
* **query** -- the analysis window, which belongs to the caller and is a third thing again.

A cache sampled from 100-200 s of a 1000 s recording therefore yields `not_in_cache` at 100 s, never
`recording_head`: unexamined footage, recoverable by sampling the source, is not the end of the tape. With
no cache and no source metadata it yields `source_bounds_unknown` and says nothing at all.

| limit | what is actually known | remedy |
|---|---|---|
| `recording_head` / `recording_tail` | the edge sits at the proven start/end of the source | nothing on tape can settle it |
| `analysis_window` | footage exists past the query's own edge and the query declined to read it | widen the window: no inference |
| `footage_never_sampled` | inside the span, in the cache, never read | the finer grid above |
| `not_in_cache` | the recording covers the instant, the cache does not | sample the source |
| `source_bounds_unknown` | no manifest, no cache, no duration | say nothing; ask for a bound |

On the trial window with real bounds the head reads `analysis_window` ("footage exists beyond the query's
own edge at 2400.000 s; this run declined to read it"); with the same rows and no bounds it reads
`source_bounds_unknown`, and the disclosure text changes with it. The trailing edge had the same defect and
the same conflated sentence -- "the recording (or the caller's window) stops there" -- and is now edge-keyed
and kind-specific too, because a reached target says a set ended and says nothing about whether play
continued past the last frame read.

`test_a_recording_that_begins_midset_is_not_reported_as_a_window_limit` was **deleted**: it asserted a
truncation claim from a fixture that supplied no source evidence, so it was enforcing the bug. In its
place, `test_only_a_proven_source_bound_may_claim_the_recording_was_truncated` reads one candidate shape
three ways (no bounds -> unknown, a manifest whose source starts at the edge -> truncation, a cache
reaching back past the window -> the query's own limit),
`test_a_partial_cache_never_becomes_a_truncated_recording` is the review's repro at the helper level,
`test_a_sparse_cache_edge_is_not_read_as_source_truncation` keeps `footage_never_sampled` apart from
`not_in_cache` apart from `recording_tail`, and `test_the_source_bounds_helper_reads_the_manifest_it_is_given`
pins the legacy-header / `source_duration` / nonzero-origin readings separately.

**The widening itself** (`expansion_needs`, `expansion_window`, `expansion_report`, CLI `--expand-window`
/ `--expansion-progress`, default 180 s per side, 0 disables). It triggers on a candidate's own
`analysis_window` edge and on nothing else -- no controller screen, result screen, marking, bracket or
known format is consulted, and it refuses to reach past the cache or the tape. Saved rows are reused
first; only frames with no saved answer are priced, diffed by request **identity** so a frame in both
plans is never billed twice; the original and expanded windows are both kept, and the progress cursor is
written to `expansion.json` so a resume continues from where the last widening got to. Stopping reasons:
`expanded`, `expansion_limit_reached`, `cache_edge`, `recording_head`, `recording_tail`,
`source_bounds_unknown`, `nothing_further_to_reach`, `budget_exhausted`, `expansion_disabled`,
`no_region_to_widen`.

**It now runs on the live path, not just the dry run.** The first version passed `expand` only to
`plan_saved_investigation`, so a run started at 2400-3200 priced a wider window and then executed the
narrow one, and the documented workaround was an operator retyping `--region 2220 3200` -- which is not
automatic recovery. `investigate_saved_games` now widens through `apply_expansion` after preparing and
before any client exists, plans and dispatches against `working_region`, and keeps that region for every
later pass and for the final after-state: two `_refresh` calls still passed the *requested* region, so a
widened run shrank back to the narrow window on its second pass and in its own report. Both windows are
published (`region_requested`, `region_executed`, the `expansion` block in `plan.json`), and the cursor is
written to `expansion.json` only after the run's guards pass -- a run that refuses must not leave a promise
about a window it never worked, which is exactly what `test_a_live_run_refuses_before_any_route_client_is_built`
now re-proves. A resume continues from that cursor instead of re-widening, because the executed window is
part of the input identity a resume is checked against; it reports `already_expanded`.

Measured on the real recording with every route's socket replaced, the unseen interval moves by the
default reach on both sides: `[4800, 5600] -> [4680, 5720]`, 159 -> 207 observations, 6 new reads.

Covered by public-CLI tests with all routes mocked at one seam -- `_client` for the fake transport, and
`_post` on the three real classes in the dispatch suite, so the ledger wrapper is genuinely exercised and
the cap is not left untested: `test_the_public_cli_executes_the_widened_window_from_the_narrow_request`,
`test_the_public_cli_refuses_to_widen_without_sourcing_the_tape`,
`test_a_resumed_cli_run_reuses_the_saved_expansion_instead_of_walking_again`,
`test_the_public_cli_stops_at_its_cap_with_the_widening_recorded`, plus
`test_a_live_run_widens_its_own_narrow_request_by_default` and its counterpart
`test_a_pinned_run_works_exactly_the_window_it_was_handed`. The dispatch suite's other live tests pin
`expand=0.0`: their claims are about which route is charged for what, and a wider window legitimately
changes request counts without touching any of those guarantees.

Two of my own measures were wrong on the way. Budget was first judged against `requests_unfunded`, which is
only populated for *disabled* routes, so it could never fire; and counting every deferred item as
over-allowance made `--routes pip_crops` report `budget_exhausted` purely because the unfunded state route
had deferred its whole workload by the operator's choice. Only deferrals from funded routes count now.

Measured offline, on real footage, `inference_sent: false` throughout:

| case | window | expanded | triggered | reason | observations | new reads |
|---|---|---|---|---|---|---|
| mid-game query | 2400-3200 | 2220-3200 | yes | expanded | 302 -> 380 | 6 (12 images) |
| both edges cut mid-play | 2400-3000 | 2220-3180 | yes | expanded | 244 -> 376 | 6 |
| head only, pure reuse | 2950-3060 | 2770-3060 | yes | expanded | 54 -> 112 | **0** |
| edges both observed | 2700-2890 | unchanged | no | - | - | 0 |
| recording tail, allowance 60 | 6300-6928.42 | 6120-6928.42 | yes | expanded, **budget_exhausted** | 126 -> 162 | 12 |
| marks true / shifted +37 s / empty | 2400-3200 | 2220-3200 | yes, identically | expanded | 302 -> 380 | 6 |

**What widening recovered from saved evidence, at no cost.** On the mid-game query it took candidate 1 from
`2402.400-2603.601, partial, start_limit=analysis_window` to `2271.269-2603.601, complete, footage_complete
= true`: game 1 is not three rounds but five, and the two rounds before the window -- round ends at 2332.330
and 2362.360 -- were always on the tape. `set_unresolved` left the queue. What it did *not* do is invent a
resolution: it converted an unaskable question ("did the set begin there?") into two askable ones
(`win_dots_never_read` at those round ends) and priced them at 6 attempts. Uncertainty moved, it did not
vanish -- and `round_start_not_observed`, `dwell_after_observed_end`, `round_clock_unread` and the two
`round_clock_impossible_fall` contradictions all survive the widening unchanged.

**Bounded widening run — executed 2026-11-03.** The six labels were inspected and frozen **before** any
request (`labels-widening-2026-11-03.json`: three readable rows, two frames where the row is simply not on
screen, one anchor whose left reading is confirmed on the later frame rather than trusted alone), then the
dry run was re-verified in a separate output directory, then the command below ran. It is the proposal
verbatim plus `--header-ledger build/work/bb9/unseen-set-structure-v1/attempts.json`, so the eight borrowed
band answers are attributed to the ledger that actually paid for them instead of reading as unrecorded
spend. It keeps the operator's original narrow region -- `--region 2400 3200` -- because the point of this
round is that the run widens itself rather than being handed a wider window.

```bash
cd /var/home/andy/Code/Personal/tekken-vod-helper && env PYTHONPATH=. .venv/bin/python \
  -m tekken_vod_helper.boundary_cli investigate-games \
  --timeline build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json \
  --frame-manifest build/work/bb9/detect-1440p-v1/frames/sample-manifest.json \
  --duration 6928.421136 \
  --region 2400 3200 \
  --step 2 --no-format --routes pip_crops --expand-window 180 \
  --crop-runs build/work/bb9/consecutive-games-v2/run,build/work/bb9/consecutive-games-v3/run,build/work/bb9/game1-validate-v1/run,build/work/bb9/game2-validate-v1/run,build/work/bb9/game2-validate-v1/run2,build/work/bb9/pip-crops-v2,build/work/bb9/set-end2end-v1/follow-up,build/work/bb9/set-end2end-v1/run,build/work/bb9/source-crop-smoke-v1/run,build/work/bb9/windows-interval-v2/run,build/work/bb9/unseen-set-structure-v1/run-attempt/pip,build/work/bb9/game1-decider-probe-v1/run-single-frame \
  --header-responses build/work/bb9/unseen-set-structure-v1/run-attempt/header \
  --max-requests 10 \
  --budget-file build/work/bb9/game1-decider-probe-v1/attempts-widening.json \
  --budget-cap 10 --budget-minutes 20 \
  --purpose game1-earlier-rounds \
  --output-dir build/work/bb9/game1-decider-probe-v1/widening-run
```

**What the run returned.** `supported_outcome`, **7 attempts charged of the shared 10**, 0 uncertain, 0 in
flight, 8,030 tokens (4,515 prompt + 3,515 completion, summed over every batch record including the one
that failed). Six reads were planned and seven were charged because frame-2361's first reply failed pip
validation (`Extra data` after the JSON object) and the retry drew on the same cap; that first reply is
preserved beside the good one rather than overwritten. Graded against the frozen labels: **12 of 12 sides
on drawn, 12 of 12 on count, both abstentions honoured, no mismatch** -- including the two victory-close-up
frames where the model answered `drawn: false` rather than inventing a nil row, and the anchor at
2362.360 where both rows genuinely read 1-1. Slot positions again disagree between the two rows on the
same frame (RIGHT calls its outer pip position 3), so positions stay out of every inference.

The four claims, kept apart:

| claim | result |
|---|---|
| **recovered opening + round sequence** | **supported.** Game 1 is `2271.269-2467.465` with five rounds read from the dots: 0->1 left at 2332.330 (pip visible at +2 s), 0->1 right at 2362.360, 1->2 left by 2407.405, 1->2 right at 2442.440, 2->3 right at 2467.465. The set's leading edge is settled by footage now (VS screen -> stage intro -> round HUD at 2271.269) and its `analysis_window` limit is gone. The opening itself cost 0 attempts: 78 saved rows were reused, and 302 observations became 380. |
| **game winners** | **4 of 4 from `win_dots`**: p2 3-2, p2, p1, p1. Game 1 is the new one, and it is now a full five-round reading rather than a window-truncated three. The broadcast column credited Mattiniero at 2476.474 for game 1 and the pips independently give the same side; the box remains corroborating, never the evidence. |
| **set membership** | unchanged and still *supported*, not assumed: two candidates, 2 ambiguous links kept as alternatives, `membership_assumed 0`. Without a target `grouping_unresolved` stays true. |
| **completion with the optional format** | both candidates `confirmed` under the supplied first-to-two policy for the band-named stage "losers round 1" -- `concluded_by: [target_reached, station_cleared]`, winners **Mattiniero** then **PWDDA \| Ariz**, `targets_published: 2`, `winners_named: 2`, and `winner_unit: broadcast_identity_hypothesis`. Without the format the same evidence yields `proposed_cut_only` for both. |

`win_dots_never_read` and `set_unresolved` left the queue and no new question took their place; what
survives is `round_clock_unread` 1, `round_clock_impossible_fall` 2, `dwell_after_observed_end` 1 and one
`round_start_not_observed` -- and that last one is now an **extraction gap rather than a footage gap**: the
state route's own prose at 2271.269 reads "Active round HUD with full health bars and 60-second timer" and
at 2272.270 a central `READY` banner with timer 60, but `round_call` was never populated on those rows, so
the tracker cannot use what the recording plainly shows. Fixing that is a parsing change to the state
route's vocabulary, not another request, and it is outside this round's scope.

The crop route in this window is now exhausted -- 0 planned, 48 reused -- and the only outstanding asks are
**4 state-route requests** for the clock questions. They are recorded as a next bounded ask, not
approved and not priced as part of this run. A resume of this directory is expected to report
`already_expanded` and hold the window at 2220-3200 rather than widening again -- that is the behaviour the
mocked public-CLI test pins, and it was deliberately not exercised against the live endpoint, because a
resume is an inference-capable invocation and this round's authorisation covered the six reads only.

*Superseded on the count, 2026-11-03:* the four were **4 state requests before the round-start rule and 2
after it**, on identical inputs -- see "Round starts read from the game's own opening signal". The other
statement above stands: nothing was approved, nothing was sent, and a resume was not exercised live.

*Superseded by the run above:* the earlier statement that `round_start_not_observed` and the two
`win_dots_never_read` "survive the widening unchanged" was written from the dry run. After the real reads,
only `round_start_not_observed` survives, and for the different reason just given.

Pre-flight validation, re-run in `widening-dryrun-check/` before anything was charged (a dry run writes no
ledger and no answer): `[2400.0, 3200.0] -> [2220.0, 3200.0]`,
`stopping_reasons: ["expanded"]`, `within_allowance: true`, **6 new reads / 12 images**, all `pip_crops` --
the coarse triple at each of the two newly-recovered round ends:

```
2332.330  round_end_evidence_frame   frame-2331.jpg      2362.360  round_end_evidence_frame   frame-2361.jpg
2334.332  after_end_plus_2s          frame-2333.jpg      2364.362  after_end_plus_2s          frame-2363.jpg
2336.334  after_end_plus_4s          frame-2335.jpg      2366.364  after_end_plus_4s          frame-2365.jpg
```

Terms it ran under: six of a ten-attempt shared allowance, Flash Next, one wall-time deadline stamped at
the first send, transport uncertainty halting without retry, the allowance never refilled by re-running,
and a fresh output dir over a spent ledger refused before any client is built. Seven were charged, because
one reply failed validation and its retry drew on the same cap. What it settled: game 1's `p2` win **was**
3-2, from five round readings, so the first set's 2-0 to Mattiniero now rests on footage rather than on the
broadcast column that froze at 2612.61. What it cannot: it does not touch membership, which only an applied
target settles, and it cannot reach past the end of the tape -- at the recording's own tail
`footage_limit(6927.92, "after")` returns `recording_tail` against the real manifest, so an edge there is
refused rather than chased; the `expansion-tail-budget` query did not demonstrate that refusal, because its
candidates both stop on a station clear and never ask to go forward.

## Round starts read from the game's own opening signal (2026-11-03)

The widening round left exactly one question that was a parsing gap rather than a footage gap: game 1 of
the unseen interval opened at 2271.268881 s and was still reported `round_start_not_observed`, with two
state requests priced against it. The saved rows say what happened, in the recording's own words --
2270.268 `stage_intro: "health bars empty and no round timer yet"`, 2271.269 `gameplay: "Active round HUD
with full health bars and 60-second timer"` (a clock the prose states *before* the number, which the clock
pattern reads only after the word, so no digit was extracted), 2272.270 `stage_intro: "Central 'READY'
banner ... timer 60"`, 2274.272 `timer 59`. A banner that names no round was the missing evidence, and
`round_call` could not carry it because there is no number to carry.

**The rule, and the four things it refuses to do.** `parse_round_facts` now reads the game's own opening
banner into a field of its own, `round_open_cue`, deliberately apart from `round_call`. Two positive
signatures turn "play was already underway" into "the round began here", and both are read from rows that
were already classified -- nothing new is asked:

* a **READY or FIGHT banner** landing on the opening row, on the row straight before it, or inside the
  round **at or before the instant its clock first fell**. The bound is what keeps the rule honest: the
  same banner read after the digits had started falling belongs to a round already counting down, so it is
  recorded as a sighting and refused as an opener. Only the *first* fall counts, so later falls cannot
  widen the window backwards -- that was a bug in the first version of this code and is now a test
  (`test_the_first_fall_is_the_first_and_not_the_latest_one`).
* a **full round clock arriving straight off a pre-round screen** (`stage_select`, `character_select`,
  `vs_screen`, `stage_intro`) **and then falling** inside that round. The second half is the whole claim:
  one frame reading 60 is an overlay being drawn, and a clock that only repeats its value is not proof of
  play -- the same rule the round *end* already applies to its frozen tail. A banner row the coarse label
  kept out of gameplay is promoted only as corroboration, never as a boundary of its own.

It never invents a round number: `number_observed` stays `None` under both signatures, so a first round
remains an assumption the report can see, and `round_open_cue` is not `round_callout`. It never moves a
boundary: 2271.268881 stays the round's start, because that is the first frame of the round's own HUD, and
the banner frame is recorded beside it (`round_start_observed`) with the reading it carried -- which for
that frame is nothing at all. It refuses a *denied* banner: prose that looked for the start signal and did
not find it ("no 'READY' or 'FIGHT' banner") is now a recorded negation and vetoes both signatures for that
round, rather than being talked past by a nearby 60. And it keeps a true truncation truncated: a query that
opens mid-round, with the digits falling and no opener in front of it, still reports
`round_start_not_observed` and still prices the frames that would settle it.

Matching is **case-sensitive on the banner token**, and the measurement is the reason: over the 2,000
classified BB9 rows the capitals appear on 43 rows and every one of them sits within 6 s of a clock-driven
round opening, while the same words read case-insensitively match 190 rows ("Active fight with timer 46",
"fighters in ready stance") that describe a round already being fought. The banner is drawn in capitals
and the classifier quotes it; ordinary prose about fighting is not evidence about a boundary.

**What it changed, on identical inputs** (`build/work/bb9/roundstart-claims-v1/claims-ab.json`; "before"
is the shipped code with the settling pass switched off, which is exactly the previous behaviour). Every
candidate's recorded claims -- boundary, status, membership, games, side and identity tallies, set winner,
conclusion, footage completeness, clip end, completion state, grouping, missing evidence -- are identical
in both arms of every interval:

| interval | candidates | state requests | round-start questions | claims |
|---|---|---|---|---|
| [1200, 2200) | 2 -> 2 | **2 -> 0** | 1 -> 0 | identical |
| [2220, 3200) | 2 -> 2 | **4 -> 2** | 1 -> 0 | identical |
| [3200, 4000) | 2 -> 2 | 7 -> 7 | 0 -> 0 | identical |
| [2400, 3200) (pinned mid-round) | 2 -> 2 | 3 -> 3 | **1 -> 1** | identical |
| whole recording | 10 -> 10 | 23 -> 21 | 1 -> 0 | identical |

The pinned narrow row is the negative control on real footage: the same recording, queried from 2400 s
where the round had been running for two minutes, still says its start was not observed. Recording-wide,
rounds whose start is inferred went 1 -> 0 out of 121; the one that was inferred (53.052997, the head of
the tape) is settled by the second signature, and 117 openings were always established by a clock restart
and 3 by a round callout.

**Repriced outstanding work for the unseen interval.** Four state requests become two. The removed ones are
the two groups at `[2259.269, 2274.269]` -- six frames at 2260.258-2270.268 plus 2272.270, asking for the
round callout the saved prose already gives. Both arms are saved at
`build/work/bb9/game1-decider-probe-v1/roundstart-v1/full-corp-{before,after}/plan.json`, priced on one
input set: the widened window, the 13-run crop corpus including the six reads the executed run wrote, the
19 saved band answers, `--step 2 --no-format`, and no ledger. What remains is only the clock arithmetic no
saved row settles, and it is unchanged by this work:

| | before | after |
|---|---|---|
| state requests | 4 | **2** |
| frames asked | 15 | **8** |
| image ceiling (6-frame batch) | 24 | 12 |
| completion-token ceiling | 32,000 | 16,000 |
| pip requests / reused | 0 / 48 | 0 / 48 |
| band requests | 0 | 0 |
| queued: impossible fall / unread clock / dwell | 2 / 1 / 1 | 2 / 1 / 1 |

The two survivors are the disputed falls: 4 frames at `[2516.52, 2528.52]` (2518.516, 2520.518, 2523.521,
2525.523) and 4 at `[3007.01, 3019.01]` (3008.005, 3011.008, 3014.011, 3016.013). **This is also the
executable price, not just the asked one:** priced again with the standing 180 s per side available, this
window does not widen at all (`expansion_triggered: false` -- both candidates end on a station clear and a
result screen), so a live run would send these same 2 requests against the same window. The interval table in
the audit section carries the per-window check for the other intervals, several of which do widen and cost
substantially more than their headline said. Cost basis, measured from
the attempts ledgers this recording has actually paid (7 charged state attempts): **16.0 s mean, 31.0 s
worst, 1,242 prompt and 1,605 completion tokens drawn on average**. Two requests are therefore ~32 s of
endpoint time against the 8,000-token-per-request ceiling the plan carries, and the previous four were
~64 s. Nothing was approved and nothing was sent: the recompute is the dry-run path, `inference_sent:
false`, no ledger and no answer file written.

*Superseded as a priority, 2026-11-03:* the same review asked why these two were the headline. They are the
cheapest outstanding state work and they recover no game and no winner, so the next run is the crop batch in
"The 2026-11-03 prepared run, since spent" and these two sit behind it. Their price stands -- 2 requests is
also the executable price -- it is just not a reason to open the endpoint.

**Tests.** Twelve added to `tests/test_boundary_games.py`: five positive (a READY banner in front of the
digits, a FIGHT banner on the pre-play screen, a full clock off a stage screen, a structured
`round_open_cue` answer, the absorber recognising a banner-only opener fragment) and seven negative (a bare
frame reading 60, a 60 that never falls, a banner drawn after the digits were running, the first fall not
being the latest, a denied banner, prose about fighting, and no invented round number). The positives all
fail with the settling pass switched off; each negative fails under the specific over-broad rule it refuses
-- case-insensitive matching, negation ignored, no first-fall bound, no fall requirement -- so none of them
is a test that could only ever pass. `tests/test_public_recovery_integration.py` moved one fixture: its
resume test needed an interval that still owes two state batches, and [1200, 2200) no longer owes any for
the reason this section exists, so it now uses `STAGE_C = (2220.0, 3200.0)` with its band reads priced and
unfunded as the crop reads were. Non-GUI suite at that point: **934 passed, 1 skipped, 2 xfailed** (954 after the clock repair, below).

## Recording-wide coverage audit from saved evidence (2026-11-03)

`scripts/audit_recording_coverage.py` answers one question over the whole tape and sends nothing: what do
the saved rows, the saved crop readings and the saved band readings already establish, what is still
missing, and what would each remaining piece cost. Every arm is `plan_saved_investigation` -- the same dry
run the public command writes before approval -- over the one canonical evidence base, reported twice: the
recording-only column and the same tape with the user-confirmed format applied. Artifact:
`build/work/bb9/coverage-audit-v1/coverage-audit.json` as first written (superseded by v2, below), with the
marking file each arm read written beside it. Inputs: the 2,019-row classified timeline, the 1 s frame cache (6,922 frames), 13 discovered crop runs
(211 readings, 4 dropped for a second framing, 0 demoted), 19 saved band answers across four preserved
runs (6 + 4 + 1 + 8 groups) against the shared ledger that paid for them, the operator's six marks, and
the 2026-10-31 format file.

**Footage coverage and confidence are two numbers, and they are far apart.** Coverage first: the cache
holds a frame every 1 s from 0.0 to 6,927.92 s of a 6,928.421136 s tape (`source: build/work/bb9/oct22-1440p.mp4`,
`proven: true` from `source_duration`). The joined base carries 2,114 rows -- the 2,019 classified ones
plus 95 that exist because a crop reading had no state row to join to (the count after game-final-pips-v1; it
read six frames that no state row covers). **4,812 cached frames were never read by any route**, and they are the gap between a cache and an observation, not a hole in the cache: no
unread stretch exceeds 3 s, because the state pass swept the whole recording at 3-5 s spacing. That spacing
is what varies -- 233 read frames per 600 s block through 3,600 s, 120 per block from 4,200 s, and 66 in
the last partial block. The tape has all been looked at; the second half has been looked at half as
often.

Confidence, from the same base with no target supplied:

| claim | recording-only | with the user-confirmed format |
|---|---|---|
| rounds | 122, every start observed | 122 |
| games | 29, 27 complete, **16 with a winner** | 29, 27, 16 |
| candidates | 10: **7 complete on the boundary, 3 partial** | 10, 7 complete |
| set winners named | **0** -- nothing closes, because no target is claimed | **3** (Buk, Mattiniero, "PWDDA \| Ariz"), 7 still unnamed |
| play rows | 1,231: 1,172 inside one game, **59 inside none**, 0 inside two | same |
| edges with a footage limit | **2**, both `footage_never_sampled` | same |
| outstanding requests | **241** (189 crop, 18 state, 34 band) | same |
| outstanding images | 520 (378 / 108 / 34) | same |
| completion-token ceiling | 762,000 (567,000 / 144,000 / 51,000) | same |

The two columns are reported apart on purpose: applying the format changed no boundary, no game count and
no winner tally, and named three set winners out of ten candidates -- and it did that by *crediting a
tally*, which is the claim the recording-only column refuses to make at all. The 57 unplaced play rows are
21 short stretches, 2-10 s each; every one is classified, with its evidence, in "Every unplaced play row,
accounted for" below -- 53 of them are the footage a round produces after it ends, and the stretch that was a
round still being fought is placed inside that round. Since the crop experiment, **four of the 61 are `unresolved`
in two stretches** and they are the experiment's own +2 s / +4 s confirmation frames, which land outside game
spans five seconds short of their conclusions. They are not 61 rows of missing match play, and they are not
nothing either.

**Markings, at recording scale.** Seven arms -- unmarked, the operator's six marks, all shifted 45 s early,
45 s late, each mark duplicated three times, one match's mark missing, and marks dropped mid-set -- reach
one result: same ten candidates, same spans, same statuses, and the same 241 requests. What moves is the
order the questions are asked in: unmarked prices the 3,593 s clock contradiction first, the true marks put
3,953 s first. A mark is a statement about where a human was looking, and this is the tape saying that it
cannot move a boundary, a tally or a bill.

**The next bounded investigations, priced at what a run would actually execute.** Each interval below was
priced twice: once on the window as asked (a dry run sends nothing and does not apply its own widening),
and once on the window a live run would *execute* after automatic widening, with expansion then switched off
because the widening has already happened. **The approval number is the executable one.** The requested-window
figures are kept only to show what the first version of this table got wrong: it published the headline
`requests_planned` of a dry run and called it the price, while the same artifact recorded a larger expanded
bill beside it. All arms: `--step 2 --no-format`, widening 180 s per side, the 13-run crop corpus, four
preserved band runs, over the **14-run** crop corpus. Artifact:
`build/work/bb9/coverage-audit-v4/coverage-audit.json` (v3 is the same audit before the crop experiment, v2
before the clock repair; v1 before both).

| interval | as asked | executable window | **executable requests** | crop / state / band | images (batch ceiling) | summed ceiling | of those, reads widening makes new |
|---|---|---|---|---|---|---|---|
| [4464, 5546] | 66 | [4284, 5726] | **90** | 72 / 6 / 12 | 192 | 282,000 | 18 |
| [5545, 6342] | 64 | [5365, 6522] | **78** | 57 / 12 / 9 | 180 | 261,000 | 6 |
| [6486, 6867] | 39 | [6306, 6927.92] | **52** | 36 / 8 / 8 | 128 | 184,000 | 6 |
| [3543, 3729] | 13 | [3363, 3909] | **13** | 3 / 8 / 2 (30 crop reads reused) | 56 | 76,000 | 3 |
| [5976, 6246] | 30 | none: pinned, all routes | **30** | 18 / 6 / 6 | 69 | 111,000 | -- |
| **[5976, 6160] -- Batch A, held** | 12 | none: `--expand-window 0` | **12** (crop route) / **20** (all three) | 12 / 4 / 4 | 24 / 45 | 36,000 / 74,000 | 0 -- pinned on purpose, and left on it becomes [5796, 6340] at **42** (33 / 3 / 6, 90 images, 132,000) |
| ~~[6150, 6246] crop arm~~ | 6 | none | **executed 2026-11-04: 6 requests, 12 images, 3,798 prompt / 3,691 completion tokens drawn** | 6 / 0 / 0 | 12 | 18,000 ceiling | -- |
| [2220, 3200] | 2 | **not widened -- nothing asked it to** | **2** | 0 / 2 / 0 (48 crop reads reused) | 12 | 16,000 | 0 |

The ceilings are the sum of the three routes' own ceilings (3,000 per crop read, 8,000 per state read, 1,500
per band read); the plan's `completion_tokens_worst_case` field covers the state route only, which is another
number a reader can mistake for the bill. The executable window [6486, 6867] stops at 6,927.92 s because
that is where the cache ends (`cache_edge`), and [2220, 3200] did not widen at all: its candidates end on
station clears and a result screen, so the standing 180 s per side has nothing to reach for -- that is the
one case where the asked and executed prices were already the same, and it is now verified rather than
assumed.

**Dry run against live plan, checked rather than asserted.** `scripts/verify_live_plan_agreement.py` drives
the real `investigate_saved_games` over the proposal interval with every route's transport replaced at
`_post` -- the seam the repository's own ledger tests use -- and compares what the run planned to send with
what the dry run priced. On [6156, 6246]: the run executed the window **[5976.0, 6246.0]**, exactly the
dry run's executable window; its first-pass plan held **30 groups**, exactly the executable price, with
**identical request identities** on all three routes (`priced_but_not_planned` and `planned_but_not_priced`
both empty); it wrote no answers, because the stub's reply is built to fail validation, and it charged only
a throwaway ledger under `/tmp`. Getting there required fixing the check, not the product: the first run
compared a dry arm priced against the default endpoint with a live arm pointed at the stub, and because a
request identity embeds the endpoint -- so that one run's answer can never be reloaded as another's -- six
identical state groups looked like six different requests. Reported in
`build/work/bb9/plan-agreement-v1/all-routes-6156-6246.json`.

**The 241-request recording-wide bill is a total over the unwidened whole-tape arm, not a proposal.** and it is
dominated by the crop route for a reason worth stating plainly: 189 of its 241 requests exist because 55 rounds
have never had their pip row read, and a game with no pip reading cannot name a winner at all. Two of those 58
arrived with the repair -- reading a clock the parser had been blind to splits a round, and a new round is a new
pip row to read, which is the honest cost of the fix. Six of those windows have now been bought and read, which is
what took the bill from 252 to 246 and then to 241. Which of those reads are worth buying first
is what the next section is about, and the two disputed clock falls are not the answer.

**Priority, and the one thing that makes a crop read worthless.** A pip read is only worth buying at the
instant a round's result is on screen, so every one of the 122 crop windows the recording-wide bill prices
was checked against the round it belongs to and the digits that follow it
(`build/work/bb9/crop-anchors-v4/recording.json`, `scripts/audit_crop_anchor_quality.py`). **64 of the 122
anchors sit on an observed conclusion** -- a K.O., a TIME UP, a round callout, or the result screen itself. **58
sit on a bound that is not a conclusion**, 26 of them more than 6 s -- up to 72 s -- from the next one. Before
the repair, 2 anchors were provably early in the strongest sense (digits still falling after them inside the same
round) and in both cases the later digits existed only in prose the clock pattern could not read: 4,019.02 (20 s)
and 6,201.20 (15 s). That was a parsing gap, not a footage gap, and closing it (Step 0, landed) removed one of
the two: **exactly 1 anchor is now provably early, at 4,019.02, and its continued digits are read rather than
merely stated.** The 6,201.20 anchor no longer exists because its round's bound moved to 6,216.21.

Order of spend, on that evidence:

The order, with each step's price named in the section below:

1. **Step 0 -- done:** read a clock the prose states ahead of its word, then re-price. Landed with tests; the
   35-row effect and what it refused to change are measured in `clock-repair-v1/before-after.json`.
2. **Batch A -- [5976, 6160] crop route, pinned: 15 requests, 30 images, 45,000-token ceiling. HELD**, because
   two of its five anchors were the ones whose interpretation was withdrawn: a crop read at a bound that may be a
   round boundary rather than a game boundary answers a different question than the one priced.
3. **Executed 2026-11-04 -- [6150, 6246] crop route, pinned: 6 requests, 12 images, 3,520 tokens drawn.** The two
   anchors 6,156.15 and 6,216.21, three frames each. It contradicted the split at 6,161.155, found the row reaching
   2-3 at 6,220.214, and moved winners 15 -> 16. See "The targeted crop experiment, executed".
4. **Withdrawn as a discriminator -- the 4-request band arm.** Identity questions do not grade continuity; its
   measured price and the 2,952 s band-coverage gap stay on record.
5. **Batch B -- [5545, 6342] -> [5365, 6522]: 78 requests** (densest winner-less block, 4 games after the
   timeline repair, was 6) and
   **[4464, 5546] -> [4284, 5726]: 90 requests** (4 games), after re-checking their anchors.
6. **New and cheap: the boundary defect at 6,216.210.** The game's own record already carries the later conclusion
   instant; settling that bound is a parsing/evidence question about the K.O. the state route never wrote, not a
   purchase.
4. **[6486, 6867] -> [6306, 6927.92]: 52 requests** for the tape's last 3 games, and **[3543, 3729] ->
   [3363, 3909]: 13 requests** for the minute of play inside no candidate and its clock contradiction.
6. **Explicitly not next: the two `round_clock_impossible_fall` reads on [2220, 3200]** (2 requests, 8
   frames, 16,000-token ceiling, ~32 s at the measured 16.0 s per state request). They are the cheapest
   outstanding state work on the recording and they answer nothing about a winner or a missing game: the
   interval they sit in already has both candidates complete with footage claims settled and all four of its
   game winners named. They were the headline of the previous round's reprice because they were what was
   left after the parsing fix; they are not a reason to open the endpoint.

What the audit cannot show, in its own words: that any of the 15 winner-less games was won by anybody; that
the 7 complete candidates were *sets* rather than stretches the players stopped after (that is a target, and
a target is not evidence); that the 57 unplaced play rows were all explained by somebody who checked the
frames -- they are classified from the rows' own evidence, and only the 21 frames named in
"Every unplaced play row, accounted for" were read; and that a `footage_never_sampled` edge is a finished
recording rather than an unread one.

## Every unplaced play row, accounted for (2026-11-03)

The audit reported 63 gameplay-labelled rows inside no game, in 25 stretches, and stopped there. That is the
kind of number that turns into either invented work or excused work, so `scripts/account_unplaced_play.py`
now classifies every stretch from the evidence around it -- the adjacent games, the round whose end sits
nearest, the row's own clock, cue, end marker and prose -- and writes the reason next to the verdict. The table
is the **post-crop-experiment** account (`build/work/bb9/unplaced-play-v4/account.json`: 59 rows, 22 stretches, one stretch unresolved). The earlier runs of
the same script are kept because the differences between them are the findings: `v3` 61 rows / 23 stretches / 2
unresolved, `v2` 57 / 21 / 0, `v1` 63 / 25 / 0 (pre-clock-repair).
**All 23 stretches are classified and none is quietly absorbed into "animation". Genuinely unassigned live gameplay
is still zero. Two stretches -- 4 rows, and they are the crop experiment's own +2 s / +4 s confirmation frames --
are classified `unresolved`, which is the honest label for drawn pip evidence that falls between bounds that end
before their own conclusions.**

| kind | stretches | rows | what decides it |
|---|---|---|---|
| post-conclusion footage | 17 | 53 | 1.001-5.005 s after a game the tracker ended, with an end marker or a result transition on its own rows, or digits that hold at one value -- a freeze is not a round being played. The prose says what the pixels say: "Round-end K.O. animation", "YOU WIN banner over win pose", "victory-pose close-up" |
| opening footage of the game that follows | 3 | 3 | 1.001-5.005 s *in front* of a game start, long after the previous game ended, on a row showing an opening screen (`'ROUND 1'` banner, `'READY'`, a full clock with no digits fallen yet) |
| false gameplay label | 1 | 1 | 2,722.72: "Live match scene with round timer (60) and idle fighters; **Player 2 in-match pause menu** (Move List / Controller Setup / Select Character / Main Menu) is open" -- an unnegated menu on a row labelled gameplay, and 119 s after the previous game ended |
| **genuinely unassigned live play** | **0** | **0** | the only case the first pass found -- 6,206.20-6,216.21, digits running down across the stretch with nothing about either boundary explaining it -- was a clock the parser could not read, not gameplay nobody placed. Step 0 put those 3 rows inside their round; 3 further opening rows moved with it |
| unresolved | 1 | 2 | 6,158.152-6,160.154 and 6,218.212-6,220.214: the +2 s / +4 s frames the 2026-11-04 crop experiment bought. Each sits in an identified round, outside a game span that ended 2 s and 5 s before the evidence its own record cites. The account script has no rule to call that animation, and it is not missing footage either -- so it says unresolved, and the bound defect is what to fix |

Two of those categories are the same fact seen from two sides, and the audit has to say so rather than bank
both: the 3 opening rows sit immediately in front of games whose first frame the tracker picked at the next
row up. The round-start rule closes the *disclosure* for those games (their start is observed), and
deliberately does not move the *boundary*, because "the recording's first frame of this round's own HUD is
2,271.269, and the banner at 2,272.270 corroborates it" is a claim about the opening instant, not licence to
reach backwards. So those 3 rows are unplaced by a choice that is documented, not by a hole in the footage.

**The one real case, read frame by frame -- and what reading it cost nothing.** The stretch at 6,206.20-6,216.21
is not an omitted game and not animation: it is the **continuation of the round the tracker had bounded at
6,201.195**, and since Step 0 landed the tracker says so itself rather than being told here. Cached frame names are
*not* timestamps on this recording -- `frame-6196.jpg` is 6,201.195 s, five seconds adrift by the tape's end,
and the manifest is the only authority -- so each row below names the timestamp first and the file the crop
was rendered from second.

| timestamp | frame file | what the row says | the clock | the pip row, read off the exact crop pixels (drawn, and counted only where this file commits to a count) |
|---|---|---|---|---|
| 6,196.190 | frame-6191 | "Gameplay: Azucena in foreground with '3 Hit Combo / 21 Damage' in-game HUD, timer 36" | 36, read | row drawn; count not committed |
| **6,201.195** | frame-6196 | "Gameplay: Nina (left) and Azucena (right) at close range, timer 34, **both health bars low**" | 34, read -- **this is the bound the crop anchor is placed on** | row drawn; count not committed |
| 6,203.197 | frame-6198 | (no state row; the +2 s frame the plan asks for) | -- | row drawn; count not committed |
| 6,205.199 | frame-6200 | (no state row; the +4 s frame) | -- | row drawn, left health bar nearly gone; count not committed |
| 6,206.200 | frame-6201 | "Volcanic stage active fight; health bars + **29s timer** + combo/Punish HUD" | 29 stated by the prose, **unreadable by the pattern** | -- |
| 6,216.210 | frame-6211 | "Active round with **19s timer**, 2 HIT COMBO / 24 DAMAGE overlay" | 19 stated, unreadable | row drawn; count not committed |
| 6,218.212 | frame-6213 | (no row sampled here; the frame itself) | 18 on screen | **2-2, still.** The bright blob sits over the right group's *already-lit* inner circles. I read this as a third pip arriving and that was wrong: a bloom on a lit circle is a hit spark, and the row neither reached three nor cleared |
| 6,221.215 | frame-6216 | post_match_menu, `result_transition=True`: "Large 'YOU WIN' end-of-match banner over win pose (Nina standing, Azucena downed); **timer gone**, round resolved" | gone | no row drawn |

So the round the tracker closed at 6,201.195 was fought for another ~16 s and concluded near 6,218-6,221.
Three consequences, kept apart, plus a fourth that the frames forced out of the structure:

* **the priced crop windows around this anchor are pointed at the wrong instant.** The plan asks the bound at
  6,201.195 s (frame-6196) and its +2 s and +4 s neighbours at 6,203.197 s and 6,205.199 s. Read off the
  crops, all three draw a row at the same state and none of them shows the increment settling -- the
  third right pip, which is the one that would decide the game, is not lit at any of them. Those 3 requests (6 images) would be spent to record a score that is about to change.
* **the cause was a parser gap and it is now closed** (see Step 0, "the clock is read in both wordings"). Reading the
  digits where the prose puts them first moves this round's bound from 6,201.195 to **6,216.210**, and the crop anchor
  moves with it, so the priced window reaches 6,216.210 / 6,218.212 / 6,220.214 instead of stopping at 6,205.199. The
  read was never worth buying where it stood: a missing clock reading, not missing footage, was pointing it at a
  mid-round state.
* **both game-winner attributions written earlier in this session are withdrawn.** They were built the same way: count
  lit circles, then treat a round opening as a game closing. The counting is what failed. At 6,096.09 the rows read 1-0
  and at 6,156.15 they read 2-2, which would need three rounds decided in 60 seconds; that is not a run of fast rounds, it
  is me reading bloomed circles at 640x360. The bright blob at 6,218.212 sits over the right group's inner, already-lit
  circles and covers the position that would decide it, so that frame is *undecidable*, not evidence. `rounds_per_game`
  is an inherited default rather than an observation of this set, and neither winner survives the loss of the count.
* **what the frames do refute is the narrow claim that a game closed at 6,156.15 and another opened at 6,161.155.** The
  rows neither reach three nor clear anywhere across 6,156.150, 6,161.155 and 6,218.212, so nothing in that sequence
  shows a game closing however the timer behaved. That is not the same as settling the structure, and **three
  explanations stay open, unresolved**: the split is false and 6,016.01-6,221.215 is one long game; the split is real and
  the row clears later than any cached frame reaches; or the grouping is off by a boundary *earlier* in the stretch,
  since the 5-round game at 6,016.01-6,156.15 is not a game either. The saved rows that a re-read turned up -- the same
  lava stage on both sides of 6,161, a broadcast box reading 0-0 through the whole stretch, and no stage change until
  the black transition at 6,226.220 -- are laid out in "The disputed sequence, re-evaluated from the saved rows", which
  is where the arguments for each live. Separating them takes either the band's identity/tally reading across the two
  sides or a pip read at the round's own opening. The pip read was bought on 2026-11-04 and answered it; the band
  arm was withdrawn as a discriminator, because identity questions do not grade continuity.
  Meanwhile the game count in this report is a detector count, and 30 is not a verified number of games.

## The 2026-11-03 prepared run, since spent (unknown-winners-batch-v1)

**Status 2026-11-05: this batch was executed on 2026-11-04** -- 6 requests of a shared cap of 8, 6 answers,
6 batch records, 12 crops (`check_doc_claims.py` re-verifies the spend from the saved responses). What
follows is kept as the history of how it was chosen, not as pending work. The one batch that is prepared and
**not** executed today is `build/work/bb9/winner-corroboration-v2/`, named in the current state at the top;
the v1 seven-frame freeze it replaced is preserved untouched evidence.

The review of this session's audit said: prioritise missing gameplay and winner-less games, and do not buy
the two clock-fall warnings because they are cheap. Doing that properly turned up a reason to hold two of the
cheapest-looking reads, and the proposal below is ordered around it.

### Step 0 -- landed: the clock is read in both wordings

`_CLOCK_BACKWARDS_RE` now reads a seconds count written ahead of its word, and it changed what the audit could
claim. Measured effect, before/after on identical inputs (`scripts/measure_clock_repair.py`,
`build/work/bb9/clock-repair-v1/before-after.json`): **35 further gameplay rows carry a clock** recording-wide,
**rounds 121 -> 123**, the round bounded at 6,201.195 moves to **6,216.210**, and the two candidate starts that
were pinned to a row one frame late move back to their own opening frames (2,272.270 -> **2,271.269**, 3,728.725
-> **3,723.720**). **The complete inventory of what moved, recording-wide** (same artifact): 6 of the 30 games changed a bound and
**none changed a winner** -- 2,272.270 -> **2,271.269** and 3,728.725 -> **3,723.720** (starts that sat one row
late), the game 3,958.955-4,199.195 now ending at **4,174.170** with its successor starting **4,179.175** instead
of 4,209.205 (25 s and 30 s, in the 4,019 neighbourhood where play provably continued), the disputed
6,161.155-6,201.195 now ending at **6,216.210** (+15 s), and the game from 6,606.600 now starting at
**6,601.595**. Two candidate spans' starts follow their games earlier; **all 10 candidates keep their status**
(7 complete, 3 partial). The honest cost of the fix is two rounds whose end is not observed
(`round_end_not_observed` 57 -> 59) and two rounds with no pip reading yet (`win_dots_never_read` 56 -> 58).
What did **not** move is the part that matters for the accepted record: **30 games and 15 winners, identical
winner-for-winner in both arms**, and the accepted [1200,2200), [2220,3200) and pinned [2400,3200) claims
byte-identical.

The reading is deliberately narrow and refuses four things the recording actually contains. It needs a unit, so
`set score 1-1` and `24 DAMAGE` stay numbers and not clocks. It will not outrank the old wording: on a row
stating both, `timer 41` wins and the reading is labelled `after_word`. It will not outrank a structured answer:
a vision field that states a clock, or states that it could not read one, wins over prose either way, and the
provenance field says which (`stated_field` / `stated_unreadable`), because "the frame was dark" and "the parser
had no pattern" must never be merged into the same blank. And a denial anywhere in the noun's own clause refuses
it in both directions -- `no 30s timer` and `the 30s timer is not visible` are both an unread clock, which the
tracker's existing 32-character backwards look could not reach; measured, that clause check costs no real reading
(0 of the 34).

Tests: 8 positive wordings, 9 refusals, the two precedence rules, the full-time ceiling, and an end-to-end case
on the real 6,201.195 rows asserting the round now runs to 6,216.210 and still *discloses* its unobserved end
rather than inventing one.

### Verification after the clock repair (2026-11-03)

Non-GUI suite: **954 passed, 1 skipped, 2 xfailed, 11 subtests passed in 130.09 s**. Three files
(`test_app_export_jobs.py`, `test_overlay_integration.py`, `test_project_recovery.py`) are not collectable on
this box for want of `tkinter` -- the recorded environment quirk, not a regression -- and are the only
exclusion. The parser's own file is **102 passed**.

Ten tests failed on the first run after the change. Every one was a frozen count of what the planner prices for
a saved interval, and each was checked against the rows that moved it before the expectation was touched:

* **[4800, 5600] crop plan 36 -> 39** requests (72 -> 78 images, 49 -> 52 total, 26 -> 29 deferred, 160,000 ->
  169,000 ceiling, resume 24 -> 27). Cause, from the rows themselves: the clock reads 35, 30, **25** at
  4,944.940-4,954.950, then **60, 58, 53, 48, 43** to 4,979.975, then 60 again at 4,984.980. That is three
  rounds' falling digits, not two, so a third round-end window is genuinely unread -- the extra 3 requests are
  that window, not a duplicate of an answered one.
* **[4200, 5000] missing-crop plan 42 -> 45** and its totals 46 -> 49: the same split, same interval.
* **The exhausted-ledger test's arithmetic 49/48 -> 52/51**, which is that plan plus the retry the test
  engineers. Its "cap one short of the owed count" sibling is untouched and still refuses to start.
* **The marking-resilience cross-check was not silenced and no artifact was rewritten.** It compares the live
  path with `build/work/bb9/set-end2end-v1/recovery-stage-b-no-format.json`, which is a record of what the
  *old* parser recovered. The test now reproduces that parser -- one guarded pattern switched off, exactly the
  switch `measure_clock_repair.py` uses -- and demands the two readers still agree to the frame; then it
  asserts separately that the only difference under the current parser is the candidate start 3,728.725 ->
  **3,723.720**, the move recorded above. A frozen artifact stays frozen; the drift is what the test states.

**And again after the spend.** With the six new answers in the discovered corpus (14 crop runs), the non-GUI
suite was re-run: **954 passed, 1 skipped, 2 xfailed, 11 subtests in 132.59 s** -- no frozen count moved, because
the test intervals are elsewhere in the recording. `check_doc_claims.py` was repointed to the post-experiment
artifacts (coverage-audit-v4, unplaced-play-v3, crop-anchors-v3, next-run-v3, the experiment record) and its
its checks pass (the version shipped with this repair runs **46**, including the superseded artifacts the doc keeps as contrasts and a live A/B of the two timeline rules against the grouping code).

Before that re-run, and during the round it describes, **six requests were sent** -- the authorized crop
experiment. So the sentence below is about the repair round only, and it is kept in place because the audit it
describes is still the record of that round.

**Nothing was sent [in the repair round].** The only endpoint-facing commands this round ran with `--dry-run`, which reports
`inference_sent: false` and writes no ledger; the verified band-arm dry run wrote to `/tmp` and the corpus was
re-checked afterwards -- `boundary_evidence.crop_run_dirs` still discovers **13** crop runs with none of this
session's output among them. `build/work/bb9/check_doc_claims.py` checks the figures in this file against their
artifacts, and passes (46 checks as this repair shipped, against 55 before it -- two superseded artifacts were
retired and the live grouping checks were added; it is 77 as this round shipped, against `coverage-audit-v8` and the executed game-final-pips-v1 record).

### Batch A -- **HELD**: the interval whose anchors were priced before the claims were withdrawn

[5976, 6160] holds 2 of the recording's 15 winner-less games as the tracker counts them today (5,775.77-5,995.99
and 6,016.01-6,156.15), 15 crop frames, every one of them inspected and labelled. It is **held, not approved**:
two of its five anchors are at bounds whose interpretation this document has just withdrawn, and a pip read taken
at a bound that might be a round boundary rather than a game boundary answers a different question than the one
priced. Step 0 has now landed, so the pricing below is the repaired pricing -- but the ordering question is which
*route* asks the structural question first, and that is the next section. **The interval is pinned on purpose** --
with widening left at its default the same command runs out to [5796, 6340] and costs more than three times as
much, which is the exact mistake the previous review caught:

| flags | window executed | requests | crop / state / band | images sent | token ceiling |
|---|---|---|---|---|---|
| `--region 5976 6160 --expand-window 0 --routes pip_crops` | [5976, 6160] | **15** | 15 / 0 / 0 | 30 | 45,000 |
| `--region 5976 6160 --expand-window 0` (all routes) | [5976, 6160] | **23** | 15 / 4 / 4 | 51 | 83,000 |
| same interval, widening left on (default 180 s/side) | **[5796, 6340]** | **53** | 39 / 6 / 8 | 122 | 177,000 |

```bash
env PYTHONPATH=. .venv/bin/python -m tekken_vod_helper.boundary_cli investigate-games \
  --timeline build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json \
  --frame-manifest build/work/bb9/detect-1440p-v1/frames/sample-manifest.json \
  --duration 6928.421136 \
  --region 5976 6160 --step 2 --no-format --expand-window 0 \
  --crop-runs build/work/bb9/consecutive-games-v2/run,build/work/bb9/consecutive-games-v3/run,build/work/bb9/game1-validate-v1/run,build/work/bb9/game2-validate-v1/run,build/work/bb9/game2-validate-v1/run2,build/work/bb9/pip-crops-v2,build/work/bb9/set-end2end-v1/follow-up,build/work/bb9/set-end2end-v1/run,build/work/bb9/source-crop-smoke-v1/run,build/work/bb9/windows-interval-v2/run,build/work/bb9/unseen-set-structure-v1/run-attempt/pip,build/work/bb9/game1-decider-probe-v1/run-single-frame,build/work/bb9/game1-decider-probe-v1/widening-run/pip \
  --budget-file build/work/bb9/next-run-v1/attempts-crop.json --budget-cap 18 --budget-minutes 15 \
  --max-requests 18 --output-dir build/work/bb9/next-run-v1/run-crop --dry-run
```

The command as written is the dry run and sends nothing; dropping `--dry-run` is the approval. Funding the
other two routes as well (`--routes pip_crops,state,header`, plus `--header-responses`/`--header-ledger`)
raises the same pinned window to **23 requests, 51 images, 83,000-token ceiling** -- 15 crop, 4 state, 4 band
-- of which the 4 state requests are the ones whose frames include the digit series quoted above. The command
above is the crop-only tranche because that is what names a winner.

| item | value | where it comes from |
|---|---|---|
| anchors bought | 5,995.99 · 6,066.06 · 6,096.09 · 6,126.12 · 6,156.15 | the plan's own `pip_requests`, not a hand list (`next-run-v1/prepared-5976-6160.json`) |
| frames and crops | 15 frames, 30 images, every one rendered and digested | `next-run-v1/prepared/` -- `crop_files_sha256_of_all: ffdc88d7b918c549`, sheets in `prepared/label-sheet/` |
| **retry reserve** | **cap 18 for 15 requests** (+3) | 1 malformed reply in 53 charged crop attempts on this recording (the executed widening run's group-6, retried once). Two passes re-plan, and a second pass that asks nothing costs nothing |
| **deadline** | **15 wall minutes, one ledger, shared with resumes** | 15 × 6.0 s measured mean = 90 s of endpoint time; 15 × 21.4 s at the slowest attempt ever recorded here = 5.4 min, so the deadline covers a slow run and a resume |
| token ceiling | 45,000 completion (15 × 3,000), prompt 640 measured mean per request | the plan's per-route ceiling; measured draw is 508 completion per crop request |
| evaluator-only labels | `next-run-v1/labels-evaluator-only.json` v3: 18 crop frames + 13 band/clock frames read, expected counts per side, 3 entries deliberately left uncommitted | read off the exact 4x crops the probe would send, then corroborated against K.O. lettering, victory close-ups and the row clearing at the next `ROUND 1` |
| what it settles | **nothing yet, and that is the finding.** Two winner attributions written here earlier are withdrawn: they read a pip count and then took a round opening for a game opening. At full resolution the rows read 2-2 at 6,156.150, 2-2 at 6,161.155 and 2-2 at 6,218.212, so no row reached three and none cleared -- nothing in that sequence closes a game. What the frames do settle is drawn-versus-not-drawn and that the digits ran 36 down to 18 after the bound at 6,201.195, which the parser now reads | labels v5, `withdrawn` and `what_the_frames_do_settle` |
| what it does not settle | who won the round that ends near 6,221, whether 6,016.01-6,221.215 is one game or two, nor which set any of this belongs to | after Step 0 the round's anchor sits at 6,216.21 rather than 6,201.20, so the priced frames reach the end -- but the deciding circle at 6,218.21 is covered by a bloom in the cached frame, and whether a row cleared at 6,161 is a question the [6150, 6246] arm answers for 18,000 tokens. Membership needs the band route and a target |

Every number in the sections above is asserted against the artifact it cites by
`build/work/bb9/check_doc_claims.py` -- all passing as this was written, 46 checks in its repaired version --
covering the interval
prices and their executable windows, the recording-wide bill, the repair's measured effect and its winner
invariance, both unplaced-play accounts, the anchor tallies, the held batch and both smallest arms, the label
file's withdrawn state, and the dry-run/live agreement. It reads nothing but saved files and sends nothing; if a
figure and its artifact ever disagree, that script says so by name.

### The disputed sequence, re-evaluated from the saved rows (2026-11-03)

The review asked for this stretch to be re-read with round banners, pip rows, transitions and conclusion
evidence rather than with a clock reset. Every row below is a saved classification row; the clock column is what
the repaired parser reads from that same row, and nothing here is inferred.

| timestamp | clock read | end / cue on the row | what the row's own prose says |
|---|---|---|---|
| 6,151.145 | 41 | -- | "Top broadcast scoreboard Spitfire2929 vs derpingtoast with **set score 0-0**; gameplay, timer 41, both health bars low, active exchange" |
| 6,156.150 | 37 | **`ko`** | "**round-ending 'K.O.' overlay** during live match at timer 37; in-game HUD, **not post-match menu**" |
| 6,161.155 | 60 | -- | "**new round start** with timer 60 and full health bars, characters in stance; no controller prompt overlay" -- and **set score still 0-0** |
| 6,166.160-6,201.195 | 55, 50, 45, 40, --, 36, 36, 34 | -- | continuous combat **on the same lava/space stage**, "round pips visible", set score 0-0 throughout; 6,186.180 is a close-up camera with no timer on screen (a genuinely unread clock, not a parsing gap) |
| 6,206.200 / 6,211.205 / 6,216.210 | **29 / -- / 19** | -- | the three rows the old parser lost: "health bars + **29s timer** + combo/Punish HUD", "continued in-game combat (air kick, spark effects)", "Active round with **19s timer**, 2 HIT COMBO / 24 DAMAGE" |
| 6,221.215 | -- | result transition | "Large **'YOU WIN' end-of-match banner** over win pose (Nina standing, Azucena downed); timer gone, round resolved" |
| 6,226.220 | -- | -- | "**Black transition screen between match and next stage**; only persistent broadcast top bar 0-0; no game content identifiable" |
| 6,231.225-6,241.235 | -- | -- | "dark panning shot of a **new (jungle/temple) stage** fading in", "**Azucena character intro pose**", "no HUD/health bars yet, **pre-round presentation**" |
| 6,246.240 | 60 | -- | "Top broadcast set scoreboard shows Spitfire2929 (AZUCENA) **0** vs derpingtoast (NINA) **0**; fighters in neutral stance, round timer 60, full health bars" |

What the tracker builds out of exactly those rows now: round `6,131.125 -> 6,156.150` (ends on the K.O.), round
`6,161.155 -> 6,216.210` (`round_end_not_observed`, **10 clock readings where the old parser held 8**), round
`6,246.240 -> 6,266.260`; and games `6,016.010 -> 6,156.150` (**5 rounds**, incomplete) and
`6,161.155 -> 6,216.210` (**1 round, marked complete**). No ROUND banner is recorded anywhere in the stretch:
both of those round starts rest on `round_clock_reset`, the basis this document has now twice refused to let
stand in for a game boundary.

**Three explanations stay on the table, and the saved rows do not pick one.**

1. **The split at 6,161.155 is false and 6,016.01-6,221 is one game.** For it: the row's own prose calls 6,161.155
   a "new round start", not a new game; the stage does not change until the black transition at 6,226.220; the
   broadcast box reads 0-0 on both sides of the split and again at 6,246.240; and every cached pip read in the
   sequence (6,156.15, 6,161.155, 6,218.21) shows a row that neither reached three nor cleared.
2. **The split is real and the instrument is the pip row, which is known to be unreliable here.** For it: the
   pilot result for this recording is that win-dot rows do not read dependably at 640x360, so "2-2 persisting"
   is weak evidence against a clear; and a "game" of 5 rounds is not a game either, so something in this stretch
   is mis-grouped -- just possibly the other way round.
3. **Both are wrong because the grouping is off by one boundary elsewhere**: 6,016.01-6,156.15 carrying 5 rounds
   with a 1-0 read at 6,096.09 suggests a *missing* game boundary inside the first group, which would move the
   disagreement to 6,096 rather than 6,161.

What none of them gets to use: the 6,221.215 "YOU WIN" banner as a game result. It says a side won *something*,
the classifier wrote both "end-of-match" and "round resolved" in one sentence, and the broadcast box reads 0-0
forty seconds later. A banner that contradicts itself in a single row is a reason to read the band, not a reason
to name a winner.

### The targeted crop experiment, executed (2026-11-04)

**The four-header arm is withdrawn as a boundary discriminator.** Its own four questions are
`identity_mapping`, stated purpose "which participant holds the win": they neither ask nor grade continuity, and
the band route's pip answers are diagnostic-only. Same players and a stale 0-0 can persist across either
competing grouping, so its low price was never an argument that it settled anything. Two facts from that
preparation are kept, because they are measurements: the arm priced at **4 requests / 4 images / 6,000 tokens**
(`build/work/bb9/next-run-v2/smallest-band-arm.json`, which records its four `identity_mapping` request identities,
its stopping criteria and the fact that it was never run),
and **the band corpus stops at 3,976.47 s of a 6,928.42 s tape -- 2,952 s of this recording has never had one
band read.** The second is a gap worth funding later, for identity, not for boundaries.

What was executed instead is the crop arm around the two contested anchors.

**Gates before spending anything.** The command was run as a dry run first and reported `requests_planned: 6`,
`window: [6150.0, 6246.0]`, `inference_sent: false`, 12 images, 3,000 tokens per request. The evaluator labels
were then written *from the exact 4x LEFT/RIGHT PNGs the client sends*, one read per frame with no value inherited
from a neighbour, and **two counts left unknown on purpose** (frame-6151 RIGHT and frame-6213 RIGHT, both under
the K.O. flash). Frozen in
`build/work/bb9/next-run-v2/crop-probe/labels-frozen-evaluator-only.json` (version `crop-6150-6246-frozen-1`),
`labels_are_runtime_input: false`, `accuracy_denominator: null`. Nothing in the approved command reads that file.
A rehearsal then fed the frozen counts through the real pipeline with `PipReadClient._post` stubbed, to prove the
answers would join and to prove the run writes no answer it did not ask for; its output went to `/tmp` and the
discovered corpus stayed at 13 runs.

**Exact spend.** 6 requests sent of a shared cap of 8 (2 attempts unused), 12 images, **3,520 completion tokens of
an 18,000 ceiling** (per request 487 / 425 / 497 / 525 / 1020 / 566), **32.5 s of endpoint time** against the
10-minute deadline (per attempt 4.7 / 3.5 / 3.8 / 4.3 / 11.0 / 5.1 s), 0 retries, 0 transport failures, 0
validation failures, 0 deferrals, `stopped_reason: supported_outcome`. Every response saved under
`build/work/bb9/next-run-v2/crop-experiment-run/pip` (answer, raw reply, batch record, the crops it labelled);
ledger `next-run-v2/attempts-crop-experiment.json`; the whole record, spend and verdict included, in
`next-run-v2/crop-experiment-result.json`. Model `qwen3.8-flash-next` at `http://127.0.0.1:8000`, the
same endpoint and identity as the dry run, so nothing in the comparison is a transport artefact.

**What the pip row says.** The merged series, read through the canonical join:

| timestamp | role | LEFT | RIGHT | confidence |
|---|---|---|---|---|
| 6,156.150 | round-end anchor | 2 | *2, flash over slots 1-2* | 0.82 / 0.70 |
| 6,158.152 | +2 s | 2 | 2 | 0.90 / 0.90 |
| 6,160.154 | +4 s | 2 | 2 | 0.95 / 0.95 |
| 6,216.210 | round-end anchor | 2 | 2 | 0.95 / 0.95 |
| 6,218.212 | +2 s, K.O. | 2 | *3, flash blob* | 0.85 / 0.55 |
| 6,220.214 | +4 s | 2 | **3** | 0.90 / 0.90 |

**The split at 6,161.155 is contradicted.** A game opening clears the win row. This row is drawn and reads 2-2
two seconds before the reset, two seconds after it, and again at the next round's end anchor -- nothing cleared,
nothing reached three. The only thing that ever suggested a new game there was the clock returning to 60, and the
review's rule held: that is a round reset.

**And the conclusion this stretch was missing is now visible: the row goes to 2-3 at the end of the second
window**, three lit circles on the right at 6,220.214 with the left row still at two, the increment landing after
the flash exactly as the freeze-window rule predicts. That is 3-2, a game, and the tracker now concludes it:
`winner p2, winner_basis win_dots, concluded_level game, win_dots_reached_target true`. Recording-wide, **games
with a winner went 15 -> 16**, `game_winner_unobserved` 15 -> 14 and `win_dots_never_read` 58 -> 56, with rounds
123, games 30 and all ten candidates (7 complete, 3 partial) unchanged.

**Where the answer agrees with the frozen labels, stated without a denominator.** Every side that could be read
matched the independent read: ten gradeable sides, all agreeing, plus the two frames frozen as obscured and
deliberately not scored. No fraction is quoted from that: two windows picked to decide one question are not a
benchmark. What is worth keeping is that the model's own confidence dropped on exactly the two flash-contaminated
frames (0.55 and 0.70 against 0.90-0.95 elsewhere) -- the abstain path behaving, not a score. One instability is
recorded and unused: LEFT answers slot positions [1,2] at five frames and [2,3] at 6,218.212 for the same
outer-dark row shape, so anything consuming positions rather than counts would see a fake event there.

**The defect this bought into view.** *(Repaired on 2026-11-04 -- see "The canonical timeline repaired offline"
below: the spurious round inside 6,016.01-6,216.21 is reconciled, the round-count cap can no longer split a game the
win row says continued, and the four end claims below are now separate fields. What follows is kept as the record of
what the six answers actually showed.)* The tracker concludes the game but bounds it at
6,216.210 -- the last row whose clock was read -- while the same game record carries
`last_observed_play_at 6,220.214` and `transition_at 6,221.215`. The end is five seconds short of the evidence
that produced the winner, and the reason is upstream: the state route never wrote a K.O. for frame-6213. Its prose
at 6,216.210 is "Active round with 19s timer", and the first conclusion word anywhere near it is "YOU WIN" at
6,221.215. A boundary change here needs its own tests and its own review, so none was made. Two consequences are
disclosed rather than absorbed: the four confirmation frames at 6,158.152/6,160.154 and 6,218.212/6,220.214 land
*outside* every game span, so unplaced play rows rise 57 -> 61 in 23 stretches with **two stretches classified
`unresolved`** (the account script has no rule for a crop-only row between two bounds), and the 5 s between the
conclusion and the bound is the clearest `round_end_not_observed` cost yet measured. Genuinely unassigned live
play is still **0** -- these rows are inside identifiable rounds; it is the game spans that are short.

**The grouping question, and how it closed.** *(Answered by the repair section below: the extra unit was the
6,071.065-6,076.070 fragment, a `ROUND 2` banner read twice across a falling clock. The game is 5 rounds, 3-2.)*
Six round-units sit between 6,016.01 and 6,216.21 for a game
that ended 3-2, so one of them is not a round: the 20 s fragment 6,071.065-6,076.070, immediately followed by the
round that opens on a `round_callout` at 6,081.075, is where to look, and it is answerable from saved rows. And
1-0 at 6,096.09 is no longer implausible -- it is the opening round of a five-round 3-2 game, which is the reading
that made the sequence coherent in the first place.

### The canonical timeline repaired offline (2026-11-04, saved answers only, nothing sent)

The experiment contradicted the split in a separate report. It is now gone from the output the recovery publishes,
and two rules in `boundary_games` keep it gone.

**Root cause, in the round layer.** The unit at 6,071.065-6,076.070 was never a round. The saved rows read
**60 at 6,071.065, 58 at 6,076.070, 53 at 6,081.075, 48, 43, 38** to 6,096.090 -- one countdown -- with the same
`ROUND 2` announcement drawn on the first three. The round layer treated any callout at least
`ROUND_CALL_MIN_SECONDS` (10 s) into an open round as a new opener, so an announcement that stayed on screen cut one
round in two. `_banner_still_in_open_round` now refuses exactly that, and the round keeps the absorbed announcement
on its own record as `banners_seen_inside` (at, banner, the clock it read, the open round's clock, whether the
numbers matched) so the merge is auditable instead of inferred from a changed count: the banner row must have read the clock, the
reading must be trusted and not a rise, the open round must already have a reading, and that reading must sit
**below a fresh clock** -- genuinely counting down. What it does not do is swallow evidence:

* a banner on a row that never read the digits still opens a round (an absence is not continuity);
* a clock stalled at 60 and re-announced 45 s later still opens one (there is no fall to continue);
* a clock taken back up still opens one, whatever the banner says;
* a banner naming a **different** round than the one open still opens one, and the disagreement is kept on the round
  as `round_numbering_conflicts` rather than resolved by quietly preferring the digits.

Recording-wide this moved **one** round boundary (123 -> **122 rounds**), which is the expected size of the fix: the
classifier sampled one announcement twice and nothing else in the tape does it.

**The guard, in the game layer.** `_group_into_games` closed a game once `2 * rounds_per_game - 1` rounds had run
without a conclusion. That cap is where the fabricated boundary came from, and removing it or merging everything it
disagreed with would trade one error for another. It is now evidence-gated: when the win row is **read, non-zero and
identical** on both sides of the candidate cut, the group stays open and the refused cut is recorded on whatever game
eventually closes it, as
`round_count_beyond_target: [{type: win_row_persisted_across_the_candidate_boundary, at, readings, rounds_seen}]`,
with the run reaching the queue as `game_incomplete`. A cleared row, a missing reading, or a side that reached the
target keeps the old close, because those are a genuine reset and a genuinely unresolved run.

**Measured four ways**, so each fix's contribution is visible rather than assumed (whole recording, saved answers):

Winners below are the totals at that measurement; every arm reports 18 now that game-final-pips-v1 has run,
because the two new winners come from saved answers rather than from either rule.

| configuration | rounds | games | winners | game starting 6,161.155 | the 6,016.01 game |
|---|---|---|---|---|---|
| neither fix (yesterday's published state) | 123 | 30 | 16 | **present** | 5 rounds, no winner |
| win-row gate only | 123 | 29 | 16 | gone | 6 rounds, p2, 1 refused cut recorded |
| banner continuity only | 122 | 29 | 16 | gone | **5 rounds, p2**, nothing refused |
| **both (shipped)** | **122** | **29** | **16** | **gone** | **5 rounds, p2** |

Read the winners column carefully: **16 before and 16 after**, because the game invented at 6,161.155 was the one
carrying the p2 winner. What moved is which span that winner belongs to and how many games sit in the set around it
-- the candidate covering it is published as **5,555.5 -> 6,341.3 with 4 games** (`game_wins {p1: 0, p2: 1}`) where
it said 5. **The split is gone from canonical output, not merely contradicted in prose:** no game in the recording
starts at 6,161.155, and `investigate-games --dry-run` over [5900, 6400] publishes three games, the middle one
6,016.010 -> 6,216.210 with 5 rounds and a winner.

**Where a game stops is now four fields, not one.** `end` stays the conclusion *estimate*. `last_live_at` is the last
instant the digits fell, and is clamped so it can never sit above an observed conclusion -- three games had rounds
whose digits were still drawn 1 s to 119 s past a watched K.O. (up to 2,722.72 against a conclusion at 2,603.60),
and every one of those rows is the victory camera, not play still being played. `winner_confirmed_at` is the read
naming the side, with `winner_confirmation_readings` carrying the confidence it arrived at (the 6,218.212 reading is
value 3 at **0.55**, now visible to a reviewer instead of being silently trusted).
`footage_after_conclusion_seconds` reports play-labelled rows past the estimate rather than widening it. And
`clip_end_at` = the conclusion bound plus configured padding, capped at the next opening: for this game
6,218.212 + 2 s = **6,220.212**, which stops 26 s short of the 6,246.240 opening instead of running through the menu
dwell. The exact K.O. instant stays **unknown**, because the runtime rows never read one -- the prose at 6,216.210 is
"Active round with 19s timer" and the first conclusion word near it is "YOU WIN" at 6,221.215. `end_uncertainty`
keeps the band [6,216.210, 6,218.212] separate from the 30.03 s of tape after it, which is the distinction the
review asked to preserve.

**Rebuilt recording-wide from saved answers** (`coverage-audit-v5`, `unplaced-play-v4`, `crop-anchors-v4`,
`next-run-v4/held-batches-repriced.json`):

| figure | before the repair | after |
|---|---|---|
| rounds / games / complete / winners | 123 / 30 / 27 / 16 | **122 / 29 / 27 / 16** |
| candidates | 10 (7 complete, 3 partial) | 10 (7 complete, 3 partial); three spans' game counts corrected |
| winner-less games | 14 of 30 | **13 of 29**, 10 of them inside the three partial candidates |
| play rows: inside one game / outside every game | 1,170 / 61 | **1,172 / 59** |
| unplaced play: stretches / unresolved | 23 stretches, 2 unresolved (4 rows) | **22 stretches, 1 unresolved (2 rows)** |
| rounds never pip-read | 56 | **55** |
| whole-tape bill (crop / state / band), images, ceiling | 246 (189/21/36), 540, 789,000 | **241 (189/18/34), 520, 762,000** |
| marking arms | 7 arms, identical recovery, 246 | 7 arms, identical recovery, **241** |
| interval executions | 90 / 83 / 52 / 13 / 2 / 47 | **90 / 78 / 52 / 13 / 2 / 42** |
| anchors | 122 windows, 64 conclusions, 58 bounds, 26 late, 1 provably early | unchanged, and 6,216.210 is still one of the bounds |

**Superseded by this section and removed from the record above:** that six round-units sat inside 6,016.01-6,216.21
so one was spurious and the grouping was "still open"; that two stretches of unplaced play were unresolved; that the
game at 6,161.155 was a complete one-round game; and every figure in this file carrying 30 games, 123 rounds, 246
requests, 61 unplaced rows or a 47-request Batch A widening. **What is not superseded:** the 6,216.210 anchor is
still a bound and not a watched conclusion; 4,019.015 is still the one provably-early anchor; a winner is still a
screen side until a band read names a person; and the `unresolved` pair at 6,218.212-6,220.214 is still unplaced,
because the bound it falls past is exactly what the paragraph above refuses to fake.

**Held work, repriced against the repaired timeline.** Batch A pinned `--region 5976 6160 --expand-window 0
--routes pip_crops` is **12 requests / 24 images / 36,000 tokens** on anchors 5,995.99 / 6,066.06 / 6,096.09 /
6,126.12; all three routes pinned there is 20 requests / 74,000; left unpinned it executes [5796, 6340] at **42
requests / 90 images / 132,000**. The [6150, 6246] arm this document bought now plans **0 requests**. Batch A stays
held -- but for a narrower reason than before: its four remaining anchors are all inside the 5,775.770-5,995.990 and
6,016.010-6,216.210 games, both of which are now correctly grouped, so the question left is winner recovery for
those two games, not whether a split is real.

**Verification.** `build/work/bb9/check_doc_claims.py` grew a live section for this: it disables each rule in
turn inside the grouping code and demands the A/B table above, so "the split is gone" is re-measured on every run
rather than quoted from a JSON file written by the change it is checking. `tests/test_boundary_games.py` **115
passed**, 13 of them new: the banner-continuity case with its
four negatives, the round-count gate with its three negatives (no-evidence cap still closes, cleared row still
closes, target reached still concludes), and the four-claims/clip policy including the clamp past a watched K.O..
The non-GUI suite is **967 passed, 1 skipped, 2 xfailed** with no frozen count moving elsewhere in the repository --
no other test interval contains the fragment. Nothing was sent for this section: one `--dry-run` into `/tmp` to read
the published candidate list, and the evidence corpus still discovers exactly 14 crop runs with none of this round's
output among them.

### Both reproduced defects from the 2026-11-04 review are fixed (offline, saved answers only, nothing sent)

Measured record: `build/work/bb9/timeline-repair-v2/clip-and-conflict.json`. It carries the before-state of each
defect reproduced against the shipped code path, the after-state, and the BB9 invariants.

**Defect 1 -- a set's clip stopped before the read that named its winner.** `_set_record` anchored on
`conclusion_event_at or end` and padded from there, so it ignored the game layer's confirmation-aware bound. On
the review's fixture (last live 1028, pip confirmation 1030, configured padding 2) the game clip was 1032 and the
set clip 1030. Now the set **inherits the game's `clip_end_at`** and only falls back to conclusion(+confirmation)
+ padding when a game record carries none, so the padding is applied exactly once. The same fixture: set clip
**1032.0**, and `clip_end_anchor` publishes `conclusion_event_at 1028 / winner_confirmed_at 1030 /
game_clip_end_at 1032 / set_end_at 1028` beside it, with `clip_uncertainty` forwarded so the band is still
visible. Negatives pinned too: no double padding (not 1034), a next opening at 1031 caps the cut at 1031 rather
than padding to 1032, and a 400 s dwell leaves the clip at 1032.

On real footage, **four of the ten published candidate clips ended short of their own confirmation** --
candidates starting 790.790 (+1.001 s), 1588.587 (+2.002), 2271.269 (+2.002) and 3247.244 (+3.003); the other six
had no confirmation past their conclusion, or none at all, and did not move. Two consequences reported rather than
hidden: the export join forwarded `clip_end_at` and `clip_end_basis` -- **and silently dropped the anchor and the
band**, which is why this section first claimed they were published. They were not; the claim was wrong and is
retracted here. `coverage-audit-v5` also carried no clip ends at all, so the rebuild that round came back
byte-identical for a reason that had nothing to do with the fix. Both holes are closed in "Output propagation
finished" below.

**Defect 2 -- the continuity guard overrode an explicit new-game callout.** Adding `round_call=1` to the t=200 row
of the six-round fixture produced exactly what the review reported: one game of six rounds, `winner=p1`,
`contested=false`. A persisted 2-2 and a ROUND 1 are two positive readings pointing opposite ways, and the guard
was resolving that by silence. Now `_cap_boundary_contradicted` reports the callout
(`new_game_callout: {at, round_number_announced, conflicts_with_pip_continuity}`), and when it says **ROUND 1** the
announced boundary stands while the disagreement is carried by both games it separates:

| | before | after |
|---|---|---|
| games | 1 (six rounds) | 2 (five rounds + one) |
| winner | p1 | None on both |
| contested | **false** | true, reasons `pip_continuity_contradicts_round_1_callout` / `inherited_game_boundary_conflict` |
| counts toward set | true | false on both |
| alternative grouping | -- | `start_is_disputed` on the second game: persisted `{p1:[2,2], p2:[2,2]}`, announced round 1, `one game containing the rounds on both sides` |
| queue | nothing about it | `game_boundary_conflict` at 200.0 naming the win row at the candidate round's opening and the ROUND callout as what would settle it |

Withholding is not forgetting: `winner_if_boundary_untouched` keeps the p1 reading visible. The controls are the
other half of the fix -- a **ROUND 2** at t=200, and no callout at all, both still merge the six rounds with
`winner=p1`, `contested=false`, exactly as the BB9 repair requires. And on BB9 itself nothing moved: 122 rounds,
29 games, 16 winners, no game starting at 6,161.155, the 6,016.010 game still 5 rounds / complete / p2 /
uncontested with clip 6,220.211673, and **zero boundary conflicts anywhere in the recording**.

**Two existing tests caught me, and one was right about a real bug.** `test_clip_end_is_conclusion_evidence_plus_padding_only`
failed because my first version had folded the game's `end` into the `max()`, which would have pushed every
no-confirmation clip out to `end + padding` -- a behaviour change nobody asked for. The precedence is now the
original (`conclusion_event_at`, falling back to `end` only when no conclusion was named) with the confirmation
added, and the test passes unchanged. `test_the_original_partial_names_what_it_is_missing_and_the_padding_follows_the_conclusion`
failed because my new basis string dropped the word "padding" that the test guards on; the string now names the
anchor, the configured padding, that it was applied once, and that no marking extends it.

**Verification.** `tests/test_boundary_games.py` **124 passed** -- nine new: four set-clip cases (inherit, no
double padding, next-opening cap, long dwell), the ROUND 1 conflict through `track_games`, the disputed-start
record, and the ROUND 2 / no-callout controls. Non-GUI suite **976 passed, 1 skipped, 2 xfailed in 172.33 s**.
Nothing was sent: the only commands touching the endpoint were the reads already on disk, and the crop experiment's
own ledger still stands at 6 of 8 with its responses untouched. Batch A and broad inference stay held.

### Output propagation finished (2026-11-04, saved answers only, nothing sent)

Three connections, no new rules: the repaired behaviour already existed on the game record and never reached the
places a reviewer or an export reads.

**1. The candidate now reports its own disputed game boundary.** A fixture with a win row persisting 2-2 across a
boundary the game announces as ROUND 1 published `grouping_unresolved: null`, `alternative_groupings: []` and no
trace of the conflict; the disagreement lived only in the two game records. Now `_set_record` calls
`_candidate_boundary_conflicts(games)` and the candidate carries:

```json
"boundary_conflicts": [{"at": 200.0, "readings_persisted": {"p1": [2, 2], "p2": [2, 2]},
                        "round_number_announced": 1, "between": [0.0, 200.0],
                        "winners_withheld": true, "would_resolve": "the win row at the candidate round's own
                        opening, and the ROUND callout on that round's first rows"}],
"grouping_unresolved": true
```

plus one entry in the **existing** `alternative_groupings` list -- the merged reading, in the same schema the
budget and the grouping diff already consume (`at`, `joined_with`, `would_merge: [0.0, 200.0]`, `reading`,
`reason`, `why_not_primary`, `kind: "game_boundary_conflict"`) -- and a line in `missing_evidence` spelling the
conflict out, because "partial" without the reason is indistinguishable from "nothing found". No new channel, so
nothing that reads these records has to learn about it.

**2. Uncertainty is no longer a synonym for "target not reached".** `boundary_evidence.recovery()` derived
`grouping_unresolved` from `set_completion.state != "confirmed"` alone. It is now
`bool(boundary_conflicts) or <that>`, and the test pins the distinction: the same fixture with no ROUND 1 reaches
no target either, so both candidates report unresolved -- but only the callout variant carries a conflict. Rolling
it up from the games means a candidate can also be unresolved while its tally is complete.

**3. The export projection forwards the clip claims.** `clip_end_anchor` (`conclusion_event_at 1028.0 /
winner_confirmed_at 1030.0 / game_clip_end_at 1032.0 / set_end_at 1028.0`) and `clip_uncertainty` (the band, its
cause, and the tape behind the conclusion) were supplied by `_set_record` and dropped by the projection. They are
published now, with `clip_end_at` and `clip_end_basis` unchanged to the microsecond -- the propagation moved no
cut, which is asserted rather than intended.

**4. The coverage audit publishes clips, and compares them.** `candidate_summaries()` was extracted from
`coverage()` so this is testable without a recording, and each candidate now carries `clip_end_at`,
`clip_end_basis`, `clip_end_anchor`, `clip_uncertainty` and `boundary_conflicts`. `claims()` -- the tuple the seven
marking arms are diffed against -- gained the uncertainty width and the conflict count, so a marking fault that
moved a clip now fails the same comparison that catches a fault moving a boundary. All seven arms still report
`recovery_changed: false` at 241 requests.

**Nothing about inference prices moved.** `coverage-audit-v6` versus `coverage-audit-v5`: rounds 122, games 29,
winners 16, ten candidates, 59 unplaced play rows, whole-tape bill **241** requests / 520 images / **762,000**
ceiling, interval executions **90 / 78 / 52 / 13 / 2 / 42**, and the marking matrix unchanged. The propagation
touches what is published, not what is missing -- which is the expected result, and the reason it is worth stating
instead of assuming.

**Preserved, measured after the change:** BB9 candidate clips are numerically identical to the previous round
(572.570, 1374.371, 2137.133, 2607.603, 3052.047, 3548.543, 4301.295, 5547.540, 6343.335, 6868.860), zero
boundary conflicts anywhere in the recording, no game starting at 6,161.155, the 6,016.010 game still 5 rounds /
complete / p2 / uncontested, and contested winners still withheld on both games in the fixture that has one.

**Regressions, at the layer the review found them.** Four new tests in
`tests/test_public_recovery_integration.py`: the candidate conflict + alternative + `missing_evidence` line through
`be.adjudicate` → `be.recovery`; the target-vs-conflict distinction; clip anchor/band forwarding with the cut
pinned at 1032.0; and the audit's `candidate_summaries` + `claims` carrying clips and conflicts. They go through the
public projection and the audit output rather than `track_games`, because every one of these defects was invisible
at the module level and obvious in the published record.

**Verification of this round:** `tests/test_public_recovery_integration.py` **35 passed** (four new), and
`build/work/bb9/check_doc_claims.py` ran **62 checks**, all passing -- then against `coverage-audit-v6`, with
checks that every published candidate carries its clip end, anchor and band, that the ten BB9 clip numbers are
byte-for-byte what the previous round computed, and that no candidate gained or lost a conflict. Non-GUI
suite: **980 passed, 1 skipped, 2 xfailed in 183.33 s** (up from 976 by exactly these four tests). The audit was
rebuilt as `coverage-audit-v6`; `coverage-audit-v5` is kept as the pre-propagation contrast, and both report the
same 241-request bill.



Only after Step 0 (done) and a decision on Batch A (held), and only against the executable window: [5545,
6342] runs as [5365, 6522] at **78 requests** (57 crop / 12 state / 9 band, 180 images, 261,000 ceiling), and
[4464, 5546] runs as [4284, 5726] at **90 requests** (72 / 6 / 12, 192 images, 282,000). Between them they hold 12 of the 15
winner-less games. Before either is approved, re-run `scripts/audit_crop_anchor_quality.py` on the interval:
in [5545, 6342] only 6 of 18 anchors sit on an observed conclusion, and 7 have their next conclusion 10-60 s
away, so a third of that bill reads a pip row at an instant that is not a result.

### Next bounded batch prepared (2026-11-04): two observed K.O. game ends, 6 requests

**Status: approved and executed the same day** -- see "game-final-pips-v1 executed" below for the actual spend,
grading and recomputation. Everything in this section is kept as the approval record, including its
before-execution figures (241 requests, 16 winners), which are labelled as such rather than updated.

`coverage-audit-v7` is the current report: 122 rounds, 29 games, 16 winners, ten candidates, 13 winner-less games,
55 rounds never pip-read, whole-tape **241** requests / 520 images / **762,000** ceiling, interval executions
90 / 78 / 52 / 13 / 2 / 42, and all seven marking arms invariant at 241 with clip cuts identical to v6. The raw
candidate `grouping_unresolved` flags now agree with the public projection (all ten no-format candidates read
unresolved, because none reaches a target), which is why the claim checks below were refreshed rather than kept
green against a stale artifact.

**The anchor screen came before the ask.** Every one of the 13 winner-less games was joined to the anchor audit and
to the saved rows that follow its bound, and the planner's own window list for the best candidate was inspected
rather than accepted:

| winner-less game | anchor | why it is or is not worth buying |
|---|---|---|
| 6,486.480 -> 6,561.555 | `round_end_ko` | **bought.** Observed conclusion at the anchor; three rounds inside the game; row never read |
| 6,601.595 -> 6,701.695 | `round_end_ko` | **bought.** Same shape; the row is drawn on both sides at that instant |
| 6,726.720 -> 6,866.860 | `recording_or_gap_edge` | **rejected at the time, and the rejection was wrong.** I recorded the row as 2-1 at all three frames and called the game structurally unresolvable; the deciding pip actually lands at +2 s (LEFT 2 -> 3 -> 3). The window was bought on 2026-11-04 and it names the winner -- see "The third-game probe, executed" |
| 3,958.955 -> 4,174.170 | `round_end_not_observed` | rejected: the next saved conclusion is 90 s later (4,264.260), and this is the one anchor measured as provably early -- 20 s of falling digits past the bound |
| 5,060.055 -> 5,545.540 | `round_end_not_observed` | rejected: 40 s to the next conclusion, same shape |
| the other seven | mixed | the planner would buy their mid-game round ends too: 6,506.5 / 6,511.505 / 6,531.525 / 6,631.625 / 6,681.675 / 6,746.74 / 6,781.775 / 6,821.815 -- a round win cannot name a game winner |

Asking the planner for the candidate itself (`--region 6486 6868`) costs **30 requests / 60 images / 90,000**
across ten windows, of which two can settle anything. Pinning the two observed K.O. ends separately costs a fifth
of that.

**The batch, exactly.** One shared ledger and deadline across two steps, expansion off on purpose:

```bash
env PYTHONPATH=. .venv/bin/python -m tekken_vod_helper.boundary_cli investigate-games \
  --timeline build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json \
  --frame-manifest build/work/bb9/detect-1440p-v1/frames/sample-manifest.json \
  --duration 6928.421136 \
  --region 6556 6570 --step 2 --no-format --expand-window 0 --routes pip_crops \
  --endpoint http://127.0.0.1:8000 --model qwen3.8-flash-next --purpose game-final-pips-6561 \
  --budget-file build/work/bb9/game-final-pips-v1/attempts.json --budget-cap 8 --budget-minutes 8 \
  --max-requests 8 --output-dir build/work/bb9/game-final-pips-v1/run-6561 --dry-run
# then the same with --region 6698 6710, --purpose game-final-pips-6701 and --output-dir .../run-6701,
# sharing the SAME --budget-file so the cap and the 8 minutes are one budget across both steps
```

**Cost, verified through `boundary_cli --dry-run` before approval** (`inference_sent: false`): 3 requests / 6 images
/ 9,000 ceiling per step, anchors `[6561.555]` and `[6701.695]` -- **6 requests, 12 images, 18,000-token ceiling**
for the batch, crop route only, no state or band requests. Executed windows are the asked windows because
`--expand-window 0`; left at the 180 s default this ask widens to roughly [6376, 6890] and re-plans the whole
candidate. Frames are `frame-6556/6558/6560.jpg` and `frame-6696/6698/6700.jpg` at 6,561.554655 / 6,563.556655 /
6,565.558655 and 6,701.694648 / 6,703.696648 / 6,705.698647 -- the file names are not the timestamps, which is why
every frame is pinned by timestamp and source hash in the labels. Retry reserve **cap 8 for 6 requests** (2
retries; 1 malformed reply in 53 charged crop attempts on this endpoint). Deadline **8 wall minutes**, shared with
resumes, against a measured 5.4 s mean per crop attempt (32 s expected, 66 s at the slowest attempt seen).

**Stop immediately** if `--dry-run` reports anything other than 3 requests / 6 images per step; if an anchor other
than 6,561.555 / 6,701.695 appears (the timeline moved and the labels no longer describe the frames); on any
transport uncertainty, including one reply that cannot be parsed twice; at the cap or the deadline; or if two
frames in one window disagree about the same row in a way the labels do not predict -- in that case the answer is
a framing question, not another window.

**Labels are already frozen and independently read** (`game-final-pips-v1/labels-frozen-evaluator-only.json`,
version `game-final-pips-v1-frozen-1`, `labels_are_runtime_input: false`, `accuracy_denominator: null`), taken from
the exact 4x LEFT/RIGHT PNGs the client sends, one frame per read, nothing inherited from a neighbour:

| frame | LEFT | RIGHT |
|---|---|---|
| 6,561.554655 (K.O.) | **drawn, 3 lit** -- three bright cores, verified at full magnification | **not drawn** -- the crop sits on the victory close-up |
| 6,563.556655 / 6,565.558655 | not drawn | not drawn |
| 6,701.694648 (K.O.) | **drawn, 3 lit** -- a hit spark crosses the row but every core is lit | **drawn, 0** -- three hollow dark rings |
| 6,703.696648 / 6,705.698647 | not drawn (throne close-up) | not drawn |
| 6,866.860 / 6,868.862 / 6,870.864 | drawn, 2 | drawn, 1 |

Nothing in the proposed batch is unreadable: the close-up rows have no row present at all, which is a readable
answer and the abstain path, and the one flash-contaminated frame from the previous experiment (6,218.212) stays
frozen as unknown and is not re-proposed.

**Expected effect, recorded before any answer existed** (kept verbatim; the actual outcome follows the
execution section, and one number in it was wrong): winners 16 -> 18 of 29, `game_winner_unobserved` 13 -> 11,
and candidate 6,486.5 -> 6,866.9 from `game_wins {p1: 0, p2: 0}` to `{p1: 2, p2: 0}` -- **still partial, still no
set winner**, because first-to-three needs the third game and that one is the structural question above. Narrowing,
not concluding, is what this batch buys.

**What stays held:** Batch A [5976, 6160] (12 requests / 36,000), the recording-wide crop bill, and the state and
band routes. The open structural question worth the next *offline* pass is game 3's four round-units against three
row wins -- saved rows, not requests.

### game-final-pips-v1 executed (2026-11-04): two winners named, twelve sides graded, one new disclosure problem found

Executed exactly as reviewed and approved: both pinned windows, crop route only, one shared eight-attempt ledger
and one eight-minute deadline, no model swap, no other inference.

**Gates first, in separate fresh dry-run directories.** Each step was verified before anything left the process:
3 requests planned, 6 images, anchor exactly `6561.555` / `6701.695`, `window == asked window` with
`expansion.triggered: false`, `inference_sent: false`, the exact three-frame list per window (anchor,
`after_end_plus_2s`, `after_end_plus_4s`), a 9,000-token ceiling per step, and **only `pip_crops` carrying work**
(`state` and `header` are listed at 0 requests). Two gates caught their own bugs rather than the run: the first
version asserted the dispatch list *contains only* `pip_crops` (it lists all three, two unfunded) and the second
reused a stale dry-run directory, which the CLI refuses -- both stopped the script before any send, and the
ledger did not exist at either stop, which is the evidence that nothing had been spent.

**Actual spend:** **6 requests of the 8-attempt cap (2 unused), 12 images, 3,043 completion tokens of an
18,000 ceiling** (per attempt 299 / 472 / 637 / 252 / 1073 / 310), **31.2 s of endpoint time** against the
8-minute deadline (per attempt 3.0 / 4.8 / 6.4 / 2.5 / 11.4 / 3.1 s), 0 retries, 0 transport failures, 0
validation failures, 0 deferrals, `stopped_reason: supported_outcome` on both steps, 12 observations written.
Every response saved under `game-final-pips-v1/run-6561/pip` and `run-6701/pip`; ledger
`game-final-pips-v1/attempts.json`. The frozen labels were never passed to the runtime -- no label flag exists on
that command, and the run's own `investigation-inputs.json` shows the same prompt and model as the dry run.

**Grading, without a denominator:** all **twelve sides agree** with the independently frozen read.

| frame | model | confidence | frozen |
|---|---|---|---|
| 6,561.554655 (K.O.) | LEFT 3 lit `[1,2,3]`, RIGHT not drawn | 0.95 / 0.90 | match |
| 6,563.556655 / 6,565.558655 | both rows not drawn | 0.90 / 0.90, then **0.30 / 0.30** | match |
| 6,701.694648 (K.O.) | LEFT 3 lit `[1,2,3]`, RIGHT **drawn 0** | 0.95 / 0.95 | match |
| 6,703.696648 / 6,705.698647 | both rows not drawn | **0.50 / 0.50**, then 0.90 / 0.90 | match |

The confidences are worth reading rather than the tally: the model said "no row" at full confidence on the clean
close-ups and wavered (0.3, 0.5) on the two transition-blurred frames -- the abstain path again, and the same
behaviour the 6,218.212 flash produced. No per-side count is credited from this table, and no fraction is quoted.

**Winner gains, reported separately from everything else it touched:**

* **Winners: 16 -> 18 of 29 games.** `game_winner_unobserved` 13 -> **11**, `win_dots_never_read` 55 -> **53**.
  Both new winners are the **left side**, credited from the row reaching three at each K.O. anchor
  (`basis result_transition_then_menu`, `confirmed_at` = the anchor itself, `clip_end_at` anchor + 2 s).
* **Identity: unchanged, and still absent.** Both winners are `p1`, a screen side. The name-side mapping across
  6.5-6.9 ks has never been read at band resolution (the band corpus still ends at 3,976.47 s), so no participant
  is named by these six requests.
* **Set membership: unchanged.** Candidate 6,486.480 -> 6,866.860 keeps its three games and its span; no boundary
  moved, no candidate was added or merged, and `alternative_groupings` for it is the pre-existing one about the
  6,341.335 link.
* **Completion: not achieved.** `set_winner` is still `null` and `deciding_wins {p1: 2, p2: 0}` under a first-to-3
  target -- two named games and an unknown third cannot conclude it. What this batch bought is a narrowed
  candidate, not a set.
* **Structural uncertainty: the claim here was wrong, and the probe that followed disproved it.** I wrote that
  the third game (6,726.720 -> 6,866.860) was structurally unanswerable -- four round-units against a row reading
  2-1. That rested on my own misread labels: the row is 2-1 at the anchor and **3-1 at +2 s and +4 s**, the same
  post-K.O. increment I had used to credit the two games above. The probe named that winner too, so the
  "four rounds, three wins" puzzle was my error and not a property of the tape. Kept in place rather than edited
  away, because the mistake is the useful part: I applied the increment rule to the frames I expected to be
  decisive and not to the one I had already decided was hopeless.

**The unexpected result, preserved rather than smoothed over: naming those two winners silenced a queue entry.**
That candidate's `status` moved from `partial` to **`complete`**, because `status` describes whether its *games*
concluded -- and `track_games` emits `set_unresolved` only for candidates whose status is not `complete`
(`boundary_games.py:798`: `if record["status"] == "complete": continue`). So recording-wide `set_unresolved`
dropped **3 -> 2** while the set gained no winner, and a candidate with `set_winner: null` and
`games_without_winner: 1` is now invisible to exactly the queue that decides what to investigate next. This is the
same failure mode already documented for disclosures keyed to a status instead of to the fact. The candidate does
still say it out loud -- `grouping_unresolved: true` and `missing_evidence: "1 game(s) in this candidate name no
winner"` -- so nothing is lost in the record; what is lost is the count. Recommended, **not done**: key
`set_unresolved` on `set_winner is None or games_without_winner > 0` rather than on `status`, which is a
queue-semantics change needing its own tests and a review, not a side effect of a spend.

**Recomputed from saved answers** (16 crop runs, 223 readings): `coverage-audit-v8` -- rounds 122, games 29, **winners 18**, ten candidates unchanged, 59 play rows outside every game, whole-tape **235** requests / 508 images / **744,000** ceiling (183 crop + 18 state + 34 band), interval executions 90 / 78 / **46** / 13 / 2 / 42, seven marking arms invariant at 235, and clip cuts identical to v7. The six requests show up exactly where they should: the crop route fell 189 -> 183 and [6486, 6867] fell 52 -> 46, with every other interval untouched.
Unplaced play is **unchanged at 59 rows in 22 stretches with one unresolved stretch** (`unplaced-play-v5`) --
the six new readings all fall inside rounds that were already placed, so this spend created no new hole -- and
the anchor audit is unchanged at 122 windows / 64 observed conclusions / 58 bounds / 26 late / one provably-early
anchor (`crop-anchors-v5`), which is the point: anchor quality does not improve by counting, so the batch had to
be selected on `round_end_basis` and saved conclusion evidence instead. Held-batch repricing in `next-run-v5`
shows both executed windows now planning **0 requests**. All seven marking
arms remain invariant on the same bill, and the batch's own anchors now price at zero.

**Verification of the batch round.** The non-GUI suite was **982 passed, 1 skipped, 2 xfailed in 131.08 s** before the probe; the probe round's own run is recorded in the next section. The run
before the pin update showed exactly one failure, and it was the corpus pin doing its job: it asserted 16 winners
and the spend had produced 18, so it was corrected to 18 with the two credited games named (`6,486.480 -> p1`,
`6,601.595 -> p1`) rather than relaxed. `check_doc_claims.py` runs **77 checks**, all passing, including the
spend, the twelve-side grading, the v6 -> v7 -> v8 price and clip identity, and the silenced-queue defect.

**What is held and what is next.** Batch A [5976, 6160] (12 requests / 36,000) stays held, as does broad
inference; the two remaining proposed-but-rejected anchors from this round are unchanged (4,174.170 and 5,545.540,
both 40-90 s from any conclusion, and 6,866.860 pending the structural question). Nine of the eleven remaining
winner-less games are in candidates whose last rounds end `round_end_not_observed`: the cheap next step is offline,
not a purchase -- settle whether a game's conclusion row can move the bound, which is the same defect that put the
6,220.214 pip outside its own game.

### The third-game probe, executed (2026-11-04): my labels were wrong, and what that changed

`coverage-audit-v9` (post-probe, pre-queue-fix) and `coverage-audit-v10` (after) are the current reports.
Records: `game-final-pips-v1/third-game-result.json`, `attempts-third-game.json`, `run-6867/`.

**Preflight first, and separately.** A fresh dry-run directory of its own (`/tmp/gf3-dry-…`), its own throwaway
ledger path, the Codex preflight directory left untouched: 3 requests, 6 images, anchor exactly 6866.86, window
`[6864, 6875]` with `expansion.triggered: false`, the exact three frames, only `pip_crops` funded, 9,000 ceiling,
`inference_sent: false`. Every gate had to be green before the live command ran, and both preflight artefacts were
checked to not exist as ledger files afterwards.

**Spend:** **3 requests of the 4-attempt cap (1 unused), 6 images, 1,395 completion tokens of 9,000
(461 / 456 / 478), 12.2 s of the 5-minute deadline (3.8 / 3.9 / 4.5 s)**, 0 retries, 0 transport or validation
failures, 0 deferrals, one live directory, separate ledger.

**The answer, and my error.** The model returned LEFT 2 / RIGHT 1 at 6,866.859639, then **LEFT 3** / RIGHT 1 at
6,868.861639 and 6,870.863639 — matching the evaluator's independent inspection on all six sides. My own frozen
labels said LEFT 2 at all three, and my proposal had *rejected this window* on the strength of them, calling the
game "structurally unresolvable — three wins for four rounds". The reviewer was right: the deciding pip arrives at
+2 s, which is the same post-K.O. increment rule I used to read the 6,561.555 and 6,701.695 windows in the previous
batch. I did not apply it to the frame I had just decided was hopeless, and that turned a misread into a
recommendation not to look. The wrong readings are **annotated as withdrawn in the labels file rather than
deleted** — the misread is the finding about my method. Two of the three counts I committed were wrong: the
`2 → 3` transition is exactly what the window was for.

**What it changed, kept separate as asked:**

| question | after the probe | what it is not |
|---|---|---|
| **winner** | `6,726.720 → p1`; recording-wide **19 of 29** games name a winner (was 18), `game_winner_unobserved` 10, `win_dots_never_read` 52 | not a person, and not three sets' worth of progress |
| **membership** | unchanged: rounds 122, games 29, ten candidates, same spans; the game already sat inside candidate 6,486.480 | no boundary moved |
| **identity** | unchanged and now blocking: that candidate is `game_wins {p1: 3, p2: 0}` with `set_winner: null`, because the right-hand name changes inside the span so the wins stay screen sides | not a completion question |
| **completion** | no-format claims no target at all; **with the supplied optional policy three candidates name a set winner (Buk, Mattiniero, PWDDA \| Ariz) and this one still cannot** | not solvable by more pip crops |
| **structural uncertainty** | the "four rounds against three wins" puzzle dissolves — there were four rounds and three row-wins *plus* the increment I had missed | the two other rejected anchors (4,174.170 and 5,545.540) are still 90 s and 40 s from any conclusion |
| **unplaced play** | 59 → **61 rows / 23 stretches / 2 unresolved**: the two post-K.O. frames sit outside the game span, the bound-versus-confirmation gap, disclosed rather than absorbed | not new footage and not a regression in coverage |

**Queue fix (offline, `coverage-audit-v9` → `v10`).** The review was right that this needed explicit claims and not
`set_winner is None`: `track_games` emitted `set_unresolved` behind `if record["status"] == "complete": continue`,
where `status` only says every game concluded. `_set_unresolved_claims(record)` now returns separate claims —
`game_results`, `set_continuity`, `identity`, `format`, `completion` — each with the evidence that resolves it, the
route, and `costs_inference`. Recording-wide the queue went **2 → 10 entries** (8 of them `status: complete`, all
previously silent) while **the bill stayed at 231 requests in both the no-format and optional-format columns, and
unchanged across all seven marking arms** — disclosure grew, work did not. Seven of the ten entries owe something
purchasable; three owe only a format, which is marked `route: user_policy, costs_inference: false` so nothing can
re-ask the model for it. An entry never lists the same gap in both `needs_inference` and `needs_from_user`.

**Recomputed from saved answers after the probe** (`coverage-audit-v9` before the queue fix,
`coverage-audit-v10` after; 17 crop runs, 223 readings, `unplaced-play-v6`, `crop-anchors-v6`): rounds 122, games
29, **winners 19**, ten candidates, **61** play rows outside every game, whole tape **231 requests / 496 images /
727,000 ceiling** (180 crop + 17 state + 34 band), interval executions 90 / 78 / **42** / 13 / 2 / 42, seven marking
arms invariant at 231. Against v8 the crop route fell 183 -> 180 and state 18 -> 17, i.e. exactly the six requests
this round bought, and the anchor table did not move at all (122 windows, 64 conclusions, 58 bounds, 26 late, one
provably early) - the probe consumed two windows rather than changing any. Unplaced play rose 59 -> 61 rows in 23
stretches with two unresolved: the +2 s and +4 s frames whose row decided the game sit outside the game span,
because the span still ends at the last falling clock. That is the bound-versus-confirmation gap showing up in the
coverage numbers, and it is reported instead of being smoothed into the clip.

**Regressions added** (nine new in `tests/test_boundary_games.py`, file now **133 passed**, and the corpus pin lifted from 18 to 19 winners): the pre-probe shape
written directly — a `status: complete` record with an unwon game yields `game_results` and not silence; a closed
candidate with a supplied target, mapped players and every game won yields **no** queue entry at all; the format
claim never costs inference; identity and format stay distinct on real candidates (790.790 owes only a format;
6,486.480 owes identity too, and under the supplied policy the first names a set winner and the second cannot);
the corpus pin now asserts 19 winners and the three credited games; and one test compares v9 with v10 to prove the
fix bought nothing.

**Verification of this round.** `tests/test_boundary_games.py` **133 passed**; the non-GUI suite is
**991 passed, 1 skipped, 2 xfailed in 129.48 s** — the batch round's 982 plus exactly the nine new queue
regressions, with no test relaxed or removed to get there. `check_doc_claims.py` runs **89 checks**, all passing, against
`coverage-audit-v10`, and they include the probe's spend and grading, the two withdrawn label entries, the
preflight's separation from the live ledger, the winner ladder 16 → 18 → 19, the claim ladder 3 → 2 → 2 → 10 at a
bill that stayed at 231, and the fact that the one clip end that moved (6,486.480 → 6,870.861639) moved to its own
confirmation rather than into the dwell behind it.

**Held:** Batch A (12 requests / 36,000), broad inference, the state and band routes. Nothing committed or pushed.

**Still held: Batch A and broad inference.** Batch A's own price moved with the answers it bought: pinned
`--region 5976 6160 --expand-window 0 --routes pip_crops` is now **12 requests / 24 images / 36,000 tokens**
(anchors 5,995.99 / 6,066.06 / 6,096.09 / 6,126.12) because 6,156.15 is read, and the interval left on it
executes [5796, 6340] at **47 requests** where it was 53. It stays held: three of its original five anchors sit
inside the game this experiment showed to be continuous, so its question has to be re-asked against the corrected
grouping before 36,000 tokens are spent on it. Re-pricing everything against the new corpus is in
`build/work/bb9/next-run-v3/post-experiment-batches.json`, and it carries one number worth keeping in isolation:
**the window this experiment bought now plans 0 requests** -- [6150, 6246] is answered, so the same command run
again would spend nothing.

### Explicitly demoted

The two `round_clock_impossible_fall` state requests on [2220, 3200] (2 requests, 8 frames, 12 images,
16,000-token ceiling, ~32 s at the measured 16.0 s per state request) are the cheapest outstanding state work
on the recording. They are not the next run: that interval already names all four of its game winners, both
of its candidates are complete, and settling a disputed fall would clear a warning without recovering a game.
`dwell_after_observed_end` (7) and `round_clock_unread` (1) recording-wide are report-only and route to
nothing. Cheap is not a reason.

## Validated evidence and limits

- Historical game windows: 53.05–235.23 RIGHT; 785.78–994.99 RIGHT;
  1018.02–1171.17 LEFT. Frozen evidence persists in game1/game2 validation and
  windows-interval-v2. The temporal baseline's evidence_ids change is disclosed.
- [1200,2200): four games, winners RIGHT/LEFT/RIGHT/LEFT. Buk/Rebix candidate
  1588.59–2133.13 has evidence-based boundaries without a target; user-confirmed
  FT2 supports Buk 2–1. Leading candidate remains partial. The displayed tally
  disagrees at one sampled instant; never force it to match recovered wins.
- [3200,4000): RIGHT/RIGHT/LEFT/unknown. Candidate 3247.24–3543.54 concludes
  without a target; FT2 supports a 2–0 stable-seat result, with Buk/Ariz naming
  hypotheses retained. Later candidate remains partial at the analysis bound.
- Stage B scores: 27/29 visible count frames; 12/12 known-absent frames; FOUR
  genuinely unknown. Follow-up freeze 4/4 correct. Frozen bytes stay unchanged;
  the freeze's graded flag lumps known absence with unknown, so disclose derived
  denominators rather than cite sixteen excluded unknowns.
- Public marking tests: marks shifted ±45s, duplicated, absent, mid-play,
  before/after tape and reversed; marks affect question priority and not
  boundaries, and under the loaded base the invariance is of a scored recovery
  (complete candidate, credited wins, clip cut) rather than of an empty queue.
  Tight budgets name deferred work in both bases.
- measure_set_concludability.py yields 12 cue classifications / 23 live-play /
  zero unreadable at 35 game conclusions. These are algorithm output categories,
  NOT 12 independently validated distinct sets or a recovery accuracy measure.
- The four native-crop smoke calls agreed with cached results at identical
  instants. Three uncertain probes establish no general conclusion about model
  superiority, downsampling loss, or future recordings.

## Format, identity and evidence rules

Format/bracket data is optional. BB9: first to TWO ordinarily, first to THREE
for Winners Final, Losers Semi-Final, Losers Final and Grand Final; explicit
reset handling. Source is Andy, not a fetched bracket result. Policy file:
`build/work/bb9/set-end2end-v1/format-user-confirmed-2026-10-31.json`.
Event link:
https://www.start.gg/tournament/basement-brawl-9-caf-edition-pre-pre-clash-clash/details
The web tool could not read it; actual bracket scores remain unverified.

Marks and operator metadata starts are approximate anchors. A name, score, long
pause, character/stage selection or controller cue is not universally sufficient
or mandatory. Stable/stale nameplates remain approximate. Side changes can be
identity-mapping questions or an overlay correction, not proof players physically
exchanged controllers. Do not splice unrelated matches, invent missing gameplay,
or infer an unknown set target from a convenient game count. Preserve supported
alternatives and automatically investigate uncertainty within the approved budget.

## Replay and provenance

Use scripts/report_saved_header.py with comma-separated ORIGINAL header runs:
- build/work/bb9/header-trial-v1/unapproved-execute-2026-10-30
- build/work/bb9/set-end2end-v1/attribution-run
- build/work/bb9/set-end2end-v1/band-run

For region 3200 4000 or 1200 2200, use --no-format, or --format-policy pointing
to the user-confirmed file. --budget-file reads
build/work/bb9/set-end2end-v1/attempts-shared-2026-10-31.json.
Write new review outputs to a fresh directory. All these reports are offline.

`investigate-games` is now the second reader of that same base and takes the
same three band runs through `--header-responses`, the ledger they were checked
against through `--header-ledger`, and the same `--no-format` / `--format-policy`
choice.

The **recording-wide audit takes four band runs, not three.** It reads the same
evidence base the report was built from, and that base includes the Stage-A band
trial's own answers in
`build/work/bb9/unseen-set-structure-v1/run-attempt/header` (8 answers, four of
them inside games the other three runs leave unread). Replaying the audit with
three covers four fewer games, which prices 8 more band requests and reports 246
requests instead of the 238 that `coverage-audit-v11` measured. Listing the runs
keeps each answer's origin attached to it, so this is the command that reproduces
the published bill -- `--interval` takes two floats, and there is no `--region`
flag on this script:

```bash
env PYTHONPATH=. .venv/bin/python scripts/audit_recording_coverage.py \
  --timeline build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json \
  --frame-manifest build/work/bb9/detect-1440p-v1/frames/sample-manifest.json \
  --duration 6928.421136 \
  --header-responses build/work/bb9/header-trial-v1/unapproved-execute-2026-10-30,build/work/bb9/set-end2end-v1/attribution-run,build/work/bb9/set-end2end-v1/band-run,build/work/bb9/unseen-set-structure-v1/run-attempt/header \
  --header-ledger build/work/bb9/set-end2end-v1/attempts-shared-2026-10-31.json \
  --format-policy build/work/bb9/set-end2end-v1/format-user-confirmed-2026-10-31.json \
  --marks build/work/bb9/oct22.tvh.json --out build/work/bb9/coverage-audit-v11 \
  --step 2 --max-requests 2000 \
  --interval 1200 2200 --interval 2200 2800 --interval 3200 4000 \
  --interval 4464.46 5545.54 --interval 5555.55 6486.48 \
  --interval 6486.48 6928.421136 --interval 0 6928.421136
```

The format policy must be the user-confirmed file (kind
`user-confirmed-event-format`); `recovery-stage-a-policy.json` and
`recovery-stage-b-policy.json` are runtime shapes and are refused, which is the
loader refusing to invent a target rather than a bug. A fresh `--out` directory
every time: the audit writes, it does not patch. `report_saved_header.py` also accepts `--timeline`, which its own usage
example had named for two days without implementing: omitting it reproduces the
pilot facts every saved report was built from.

The dispatch replays through the same three commands, and none of these sends
anything:

```bash
# Price every route and recover the sets from saved evidence (zero sends).
env PYTHONPATH=. .venv/bin/python -m tekken_vod_helper.boundary_cli investigate-games \
  --timeline build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json \
  --frame-manifest build/work/bb9/detect-1440p-v1/frames/sample-manifest.json \
  --duration 6928.421136 --region 3200 4000 --step 2 --no-format --max-requests 500 \
  --header-responses build/work/bb9/header-trial-v1/unapproved-execute-2026-10-30,build/work/bb9/set-end2end-v1/attribution-run,build/work/bb9/set-end2end-v1/band-run \
  --header-ledger build/work/bb9/set-end2end-v1/attempts-shared-2026-10-31.json \
  --output-dir build/work/bb9/public-recovery-integration-v1/cli-3200-4000-noformat --dry-run

# Fund one route at a time and see the others priced but refused (checks --routes).
env PYTHONPATH=. .venv/bin/python -m tekken_vod_helper.boundary_cli investigate-games \
  --region 2400 3200 --routes state --no-format --dry-run ... # dispatch.routes[].requests_unfunded

# Prepare the trial's two arms without spending (renders crops and bands locally).
env PYTHONPATH=. .venv/bin/python scripts/run_pip_crop_probe.py \
  --annotations build/work/bb9/unseen-set-structure-v1/annotations-window.json \
  --output-dir build/work/bb9/unseen-set-structure-v1/run --arms crops --max-requests 42 --prepare
env PYTHONPATH=. .venv/bin/python scripts/run_header_trial.py \
  --trial build/work/bb9/unseen-set-structure-v1/trial.json \
  --output-dir build/work/bb9/unseen-set-structure-v1/header-run --prepare

# Grade the structure offline from the inspected labels: every grouping of the four
# games, the sequence verdict beside the display column, clashes named instead of
# resolved, partials kept, no target. Zero sends; this is what the run will be scored
# with, run before it and not after.
env PYTHONPATH=. .venv/bin/python scripts/evaluate_set_structure.py \
  --labels build/work/bb9/unseen-set-structure-v1/labels-eval-frozen.json \
  --games 2402.399874:2467.464870,2493.490869:2603.600863,2771.768854:2880.877849,2891.888848:3050.046840 \
  --recovery build/work/bb9/public-recovery-integration-v1/cli-2400-3200-noformat.json \
  --out build/work/bb9/unseen-set-structure-v1/set-structure-evaluation.json
```

This round's artifacts:
`public-recovery-integration-v1/{zero-request-equivalence.json, marking-matrix.json,
cli-{1200-2200,3200-4000,2400-3200}-noformat.json, report-{1200-2200,3200-4000}-noformat.json,
capped-2400-3200.json, trial-proposal-4800-5600.json (withdrawal notice)}` and
`unseen-set-structure-v1/{TRIAL-PROPOSAL.json, labels-eval-frozen.json, labels-rehearsal.json,
set-structure-evaluation.json, trial.json, trial-evaluation.json, attempts.json, run-attempt/,
label-sheet/, band-sheet/, run/plan-crops.json, header-run/plan-header.json,
corrected-2026-11-02/corrected-reports.json (digest d8caee5a94de9ca1, recomputed from the saved
answers and extended through 2026-11-03 with the membership section and the decider probe; the
baseline above it is not rewritten)}` and
`game1-decider-probe-v1/{single-frame-annotations.json, labels-codex-inspected-2026-11-03.json,
grading-2026-11-03.json, attempts-single-frame.json, run-single-frame/, autonomy-dryrun/plan.json,
recompute3-{noformat,format}/plan.json, marks-{true,shifted,empty}.json, proposal-widening/plan.json,
expansion-{mid-game,both-edges,forward-reuse-only,no-gap,tail-budget,marks-*}/plan.json,
widening-annotations.json, widening-labels/, labels-widening-2026-11-03.json,
attempts-widening.json, widening-dryrun-check/plan.json, widening-run/,
grading-widening-2026-11-03.json, widening-result-2026-11-03.json (digest 0b93f86a1c1db797),
post-widening-{noformat,format}/plan.json}` --
every `expansion-*`, `proposal-widening/` and `widening-dryrun-check/` artifact is a dry run with
`inference_sent: false`, each carrying its own `expansion.json` cursor; `widening-run/` is the one
executed run, and its 7 charged attempts live in `attempts-widening.json` alone
-- `run/` and `annotations.json` are the earlier two-frame proposal, superseded by the single-frame
input and kept only as the record of what was prepared before the inspection cut it.
Preserve these source artifacts. Consolidate superseded documents in place; do
not keep obsolete histories inside the repo.

The executed batch charged 54/70, all answered; its expired remainder is not a
new allowance. Six earlier header calls were sent without approval/ledger; their
original RECORD.json and responses are preserved. Never re-send them to create an
apparently governed history or relabel their origin. A mixed load still says
`mixed`, but the verdict is now per origin rather than one blanket label, and the
baseline label was wrong about the present: it printed "no ledger named" and called
13 of the 19 band answers unrecorded, when 4 attribution-run and 1 band-run answers
were charged to the shared ledger and this run's 8 were charged to its own. Per
origin those 13 read `governed` against the ledger that paid for them, the 6 from
`header-trial-v1/unapproved-execute-2026-10-30` keep their `incident_recorded` claim
and nothing about them is relabelled, so `answers_with_no_record_of_their_spend`
falls 13 → 0 by attribution, not by optimism.
Pip/header frame digests, exact manifest times, source framing and per-answer
provenance remain part of the reusable evidence identity.

Cache annotation tolerance is 0.05 seconds, not a full frame. Native declarations
currently prove file presence + finite timestamp, not cache-manifest identity.

Detailed artifacts from earlier rounds: set-end2end-v1/CHECKPOINT-2026-10-31.md
(superseded caveats are marked), recovery-stage-a*.json, recovery-stage-b*.json,
trial-tracking.json, trial-evaluation.json, attribution-evaluation.json,
labels-eval-frozen.json and follow-up/labels-eval-frozen.json, plus
public-recovery-integration-v1/price-4800-5600.json — pricing for the withdrawn
interval, kept because it is the arithmetic the withdrawal notice refers to.

## Session state — 2026-11-04, third stop: coverage is per point, the audit is rebuilt, one batch is prepared

This section supersedes the two dated "session state" notes that stood here. Both described work that is
now finished and both carried figures that no longer hold (one quoted a planned 239, the other said the
v11 audit was absent and the next batch unprepared). Non-GUI suite at this stop: **1021 passed, 1 skipped,
2 xfailed**, and `build/work/bb9/coverage-audit-v11/coverage-audit.json` was on disk. At that stop nothing
had been sent and nothing was committed or pushed; the batch this section anticipates ran later the same
day, and its record is the section above. Everything below keeps its date.

### The band route's instants are now its frames' own

The defect review named was real, though not where it was located: no code selects a frame by JPEG file
name. `route_questions()` snapped to the nearest cached frame with a radius it accepted and never
enforced, then kept the **asked-for** number as the answer's instant. Measured in the corpus before the
fix, **4 of the 19 preserved band answers were filed 0.5005 s from the frame they were rendered from** --
`header-1293` drew `frame-1292.jpg` at 1293.291932 s and was filed at 1293.792432 s; the same at
1890.888901, 2060.057892 and 3975.971791. The join keys on that number to decide which game a reading
covers, and therefore which participant a win is credited to, so four games were covered by pixels that
never drew them. A band answer is now credited to the instant its own frame was drawn, with
`requested_at`, `offset_seconds` and `time_pinned` beside it; the radius is enforced and a frame further
than it is refused rather than snapped onto; a stored answer whose frame disagrees is re-filed and counted
(`instant_pin`, which reports 19 pinned, 0 unpinned, 4 corrected on this corpus); and the shipping path
refuses to send a band whose frame does not draw its credited instant. All 89 live checks in
`build/work/bb9/check_doc_claims.py` pass after the change, so no published winner or attribution moved.
The instance named in review -- `frame-2273.jpg` at 2271.266327 s -- is referenced by no band artefact in
this tree (that file's manifest time is 2274.27188, and 2271.266327 appears nowhere under `build/`), so
the repair was written against the reproducible instance.

### Claim coverage is counted per point, not per claim

The ledger that connects the set layer's explicit claims to the planner had the failure the review
reproduced: it asked whether **any** of a claim's points matched a request. A pip claim needing 10 s and
30 s, with 10 s scheduled and 30 s in the route's own deferral list, reported `planned` with a reason
claiming every instant, and `claims_resolved` stayed true. Overlap is not coverage.

Each required point is now classified on its own against actual scheduled frame identities, saved
evidence and the routes' deferral lists -- `scheduled`, `saved`, `deferred`, or `unaccounted`, the last
meaning this accounting has a hole rather than that the recording has no answer. A claim is `planned` only
when every point is provided for; a mix is **`partial`**, and `unmet` names what this run will not send.
A state window counts only when the plan schedules frames **inside** it, not when a region merely
overlaps it -- a region whose frames were all skipped as already answered carries nothing. A point covered
by a saved answer is not a shortfall, but `closes` stays false, because reuse of evidence that already
failed to settle a claim is not progress. Two booleans replaced one that answered two different questions:
`claims_resolved` (was every claim accounted for) and `claims_fully_carried`.

That reclassification moved a claim's required points, which is where a second defect surfaced: the
identity claim named the **candidate's two edges**, but a nameplate is read during the game whose win needs
a person -- exactly how `missing_header_observations` prices it. It now names the games whose wins need
attribution, as stretches. The recording's ledger at full allowance is
**23 claims against 31 required points: 8 planned, 5 reused, 10 user-answer, 0 points unmet**. Narrow the
allowance and the same ledger reports the shortfall instead of the success: at 120 it is 3 planned, 6
reused and 4 deferred with 11 points named unmet, and at 1 it is 7 deferred, 6 reused and 18 unmet. Those
are the four-band-run figures the audit prices; on the three-run list the split moves to 9 planned and 3
reused, which is the same claim accounting over a corpus with four fewer games covered, not a different
rule. A test walks four allowances and asserts the
arithmetic closes every time. Format claims stay inference-free and are asked **once for the recording**
rather than once per candidate.

### The measured bill, and why the recorded one moved

`coverage-audit-v11` (correct inputs, below): **238 requests, 538 images, 783,000 completion-token
ceiling**, equal in the no-format and optional-format columns, across all seven marking arms. v10 recorded
231 / 496 / 727,000 for the same evidence base (2,116 rows, 226 crop readings). Two changes account for the
difference, and both were measured rather than asserted: the band corpus for the audit is **four** runs and
the doc's replay command listed three -- adding `unseen-set-structure-v1/run-attempt/header` (8 answers)
covers four more games and takes the band route from 42 requests to 34, i.e. 246 to 238; and routing the
set layer's own continuity claim to the whole-frame route costs 7 state batches (17 to 24), ranked last so
an allowance spent on winners spends nothing there. Crop work is unchanged at 180 sends and 186 reused
windows, which is the duplicate-request control: a claim that named footage another question already
reaches is attributed to that request and buys nothing.

### The bounded batch that was prepared, and then executed

`build/work/bb9/unknown-winners-batch-v1/` holds the ten winner-less games screened on evidence already on
disk (`anchor-screen.json`: an observed round end, a repeating clock, a present HUD), three that pass, the
six exact crops rendered at the route's own scale, my per-frame reads in
`labels-evaluator-only.json`, an `annotations.json` that deliberately carries **no** expected counts, and
`README.md` with the runnable preflight and execute commands, the price (6 requests, 12 images, 18,000
token ceiling), an 8-attempt cap that reserves 2 for retries, a 12-minute deadline, and five stopping
criteria. Two of the six frames have one side that cannot be read -- a blown highlight and an occluded
group -- and those values are recorded as unknown with the reason, so an abstention grades as correct.
*Executed 2026-11-04: 6 requests, 6 answers, 2 attempts unspent. Broad inference and Batch A
remain held.*

