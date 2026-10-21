# Agentic boundary backtest: 2026-10-19

The headless workflow works, but these results do **not** support unattended
cutting. Qwen's image-state diagnostic passed five calibration examples; actual
autonomous boundary detection missed sets and produced inaccurate cuts.

Detection received public YouTube media and rules, without historical timestamps,
names, match counts, or label-derived windows. Historical JSON was read for
scoring after predictions were persisted. Sampling streamed low-resolution video
to sparse JPEGs; no full VOD was saved and no browser cookies were extracted.

Model: local `qwen3.8-27b`. Coarse interval: 5 seconds; refinement: 1 second.
The frozen classification policy is
[cutting-policy-baseline-v1.md](boundary-history/cutting-policy-baseline-v1.md),
hash `e28b3ee31ecb4ea6`. Across the two runs, 59 saved model calls used 325,645
local Qwen tokens. This count excludes implementation work and calibration checks;
it is not a measurement of coordinating-model token savings.

| Evaluation | Range | Eligible sets | TP / FP / FN | Recall | Cut errors for matched sets |
| --- | --- | ---: | --- | ---: | --- |
| BB7 calibration | First 20 minutes | 1 | 0 / 0 / 1 | 0% | No finalized match; fallback retained in review |
| BB5 held out | First 30 minutes | 2 | 1 / 0 / 1 | 50% | Start 44.577s early; end 98.482s early |

Matching uses monotonic one-to-one assignment and IoU >= 0.2. BB5's matched
interval had IoU 0.7463; neither cut was within the 5-second tolerance. BB7
precision is undefined because it finalized no predictions. A successful model
response is not an accuracy pass. Three eligible sets cannot establish broader
performance.

BB7 refinement was stopped after repeated remote-media seek failures/stalls; its
reported result uses all 240 coarse observations only. BB5 classified 360 coarse
frames and saved a combined timeline of 450 observations. Its older refinement
code did not record individual failed windows, so full refinement success is not
claimed. The final code reports failures, stops after three consecutive failed
windows, and exposes `refinement_status` separately from workflow completion.

The BB5 process completed detection, then its older scorer failed on an omitted
historical final endpoint. The historical project reports duration zero. Scoring
was repaired and replayed without inference: that unresolved final set starts
outside the evaluated prefix and is explicitly excluded, without inventing an
endpoint. An unresolved endpoint inside the evaluated range remains an error.

The final gameplay-required grouping guard was replayed against the same saved
Qwen observations with **zero additional model calls**. Counts remained 0/0/1
for BB7 and 1/0/1 for BB5. This is a replay of existing evidence, not another
independent held-out trial. Setup-only attempts now produce review items, and
finalized candidates explicitly retain unverified bracket validity.

BB7's user-reported mid-event false start remains unlocated. Its two historical
gap candidates are 1:01:00–1:08:04 and 1:18:37–1:23:52. Separate, label-guided
extraction returned HTTP 403 and then stalled on retry. These candidates are not
proof and caused no label removal or scoring exclusion. See
[annotations.json](../backtests/annotations.json).

Machine-readable results, label digests, and exclusions are in
[2026-10-19-agentic.json](../backtests/results/2026-10-19-agentic.json).
Detailed local artifacts are under ignored `build/work/boundary-evaluation/`;
the summary records their relative paths. Original predictions and baseline
reports are preserved alongside the guarded replays.

Use `review.json` to inspect fallback candidates and uncertain boundaries, sample
targeted frames, then apply a corrected proposal with `apply-matches`. Export and
prepare the YouTube proposal after review. Detection does not establish that a
set was a valid completed bracket match, and no YouTube upload was performed.

Validation after the fixes: 183 tests passed, two documented legacy heuristic
image tests xfailed. The independent real-FFmpeg smoke test passed CPU 1440p
encoding, bounded sampling, path repair, GUI-compatible export/proposal metadata,
corrupt-clip rejection, overwrite protection, and JSON error behavior.

## BB4 prefix recovery and focused development (2026-10-19)

The frozen first-1800s run saved 360 coarse / 596 combined observations in 54
vision batches with no parse failures. The process exited after the temporal
300-second timeout window without predictions; original stderr was unavailable,
so the specific failure is inferred, not verified. Adjudication alone was retried
with the same saved observations, policy and model at timeout 1800; no vision
inference was repeated. Predictions persisted before historical labels were read.
Artifacts: `build/work/boundary-evaluation/bb4-heldout-temporal-v5/`.

The prefix contains **zero eligible complete historical sets**. The first labeled
set starts2622.135 and ends2831.589, beyond this prefix. The scorer reports
0TP/1FP/0FN, but recall and start/end MAE are undefined; zero cuts are within 5s.
These results cannot establish bracket-set accuracy. Original prediction:
469.469-1416.415, retained with bracket-validity review. Two other spans remain
uncertain (missing start or endpoint). All three proposed spans have review;
reference review coverage is undefined because no references are eligible.
One original protocol failure plus one successful adjudication recovery must be
reported separately from boundary accuracy. Failed-call token usage is unknown.

The proposed interval repeatedly reports broadcast0-0 despite several games.
A new evidence guard demotes repeated-zero candidates to uncertainty when no
valid positive set score exists within their interval; it does not assert casual
play or prove that nonzero scores establish a completed set. Missing scores alone
do not activate this guard. A development replay of the saved response used zero
new model calls: zero finalized predictions, three review spans. This is a
conservative development result with zero eligible references, not an accuracy
success. Original predictions remain unchanged under their original run folder.

A separate label-guided development window 2400-3000s is running under
`bb4-bracket-development-v6`, with cached1s frames and cropped thinking-enabled
recognition. It contains one historical complete set and must not be described
as held-out. Its outcome is pending.

Additional implementation fixes classify exact-fit and final partial cached
refinement batches, retain received malformed adjudication replies, and increase
the workflow adjudication timeout to1800s. These changes do not alter the frozen
prefix observations. Publishing remains gated on explicit user confirmation.

### Completed bracket development window

Window 2400-3000s completed: one eligible reference, **1TP/0FP/0FN**.
Prediction 2624.224222-2831.431422 versus reference 2622.135-2831.589:
signed start error+2.089222s, signed end error-0.157578s; MAEs 2.089222s and
0.157578s. Both endpoints are within 5s (1/1); IoU 0.989273. Broadcast score
progression 0-0 ->1-0 ->2-0 supports the candidate, which retains bracket-validity
review. One other span remains uncertain. Candidate review coverage 1/1.

600 dense frames were cached, 120 coarse and 200 combined observations classified.
One vision response used string score "0"; its saved response was recovered by
lossless canonical decimal normalization, with no repeated successful inference.
There were 18 vision calls including that recovered protocol failure; known
vision usage 251826 tokens and successful temporal adjudication 29946 tokens.
The scorer reads truth only after predictions persist. Selection was label-guided:
this is one successful development case, not general or held-out reliability.

Protocol repairs now retain requested frame IDs on parse failure, normalize only
canonical one/two-digit ASCII decimal score strings, and continue to reject
booleans, negatives, fractional or ambiguous scores. Strict external match JSON
is unchanged. Latest full suite 258 passed/2xfailed; subsequent focused refinement
reporting checks 11 passed. A real 1080p HLS source window is being prepared via the
existing 1440p NVENC API for local YouTube review; no publishing is authorized.


### Concrete local proposal

`build/work/bb4-youtube-proposal-v2/exports-exact/review.md` and `proposal.json` are ready for user confirmation. The real clip is2560x1440 H26459.94fps with AAC stereo, generated through the existing1440p NVENC API. Exact re-encoding removes the initial stream-copy export's3.5s padding; both exports are preserved. Final expected duration207.2072s versus measured207.223s (+0.0158s). First/last frames visually verify stage selection and the controller prompt with a2-0 scoreboard. Round metadata remains blank for confirmation (overlay reads Winners Round1). Private default, uploaded:false. No publishing or authentication changes.
