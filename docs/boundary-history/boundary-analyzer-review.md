# Boundary Analyzer Review

Generated: 2026-10-03

## Summary

The current analyzer is tuned for high-precision chopping:

- Start is emitted only when the scanner finds a confident stage-select frame.
- If the rough timestamp is already past stage select, the analyzer withholds the start instead of choosing gameplay, character select, rematch menus, or intros.
- End is emitted at the first stable controller/start prompt style endpoint in the search window.

This is intentionally conservative. A missing start is safer than a wrong start because the export flow can keep the rough timestamp or ask for review.

## Latest Verification Runs

| Report | Coverage | Start result | End result | Notes |
| --- | ---: | ---: | ---: | --- |
| `strict16-bb7-m1` | BB7 match 1 sentinel | 1/1 | 1/1 | Recovers short stage-select at `00:04:26.669`; earlier character-select false positive is suppressed. |
| `strict12-bb7-full` + `strict16-bb7-m1` | BB7 all 9 matches plus fixed match 1 | 8/9 expected starts | 9/9 | Match 8 start is withheld because no safe stage-select candidate was found near the rough start. |
| `strict9-bb5-full` + `strict16-bb5-m10` | BB5 all 11 matches plus fixed match 10 | 1 safe start in full, match 10 withheld after fix | 11/11 | BB5 rough starts often land after stage select; false starts from menus/intros/gameplay are now suppressed. |
| `strict8-bb4-first6` | BB4 matches 1-6 | 2/6 | 5/6 | Detected starts are real Random stage-select screens. |
| `strict8-bb4-m14` | BB4 match 14 | 1/1 | 1/1 | Start is a real Random stage-select screen. |
| `strict9-bb4-m18` | BB4 match 18 sentinel | 0/1 | 1/1 | Character intro false start is now withheld. |

## Candidate Decisions

Accepted start examples:

- `strict16-bb7-m1/candidate_frames/match-001-start.png`: real stage select, `SECLUDED TRAINING GROUND`, score `0.808`.
- `strict8-bb4-first6/candidate_frames/match-002-start.png`: real Random stage select, score `0.841`.
- `strict8-bb4-first6/candidate_frames/match-004-start.png`: real Random stage select, score `0.890`.
- `strict8-bb4-m14/candidate_frames/match-014-start.png`: real Random stage select, score `0.892`.
- `strict9-bb5-full/candidate_frames/match-001-start.png`: real Random stage select, score `0.886`.

Rejected start classes:

- Character select roster screens, including diverse portrait grids and red BB5 character grids.
- Rematch / select-stage side menus on either side of the screen.
- Bright white transition flashes with menu remnants.
- Character intro closeups and centered character body frames.
- Active gameplay with health bars or gameplay HUD.
- Low-confidence background-only frames that look stage-like but lack stage-select tile mosaic evidence.

## End Detection

BB7 end timings are close to the saved manual boundaries: most are within roughly 0-4 seconds, with match 8 at about -10 seconds. BB5/BB4 often show large negative deltas against historical JSON, but the frames are the "press start / post-match prompt" class the user described. This means the old JSON likely stores later chop points for those VODs, not that the class is wrong.

## Current Risks

- Start detection is conservative. Some valid starts will be withheld if the stage-select screen is extremely brief or absent near the rough timestamp.
- The detector is heuristic/OCR-adjacent, not ML. It should be resilient to small CSS/overlay changes, but a major stage-select UI redesign or DLC tile layout may need a new reference/tuning pass.
- Full BB4 as a single process was too long/unreliable, so BB4 was verified in chunks and sentinels.
- Pytest collection is blocked by a Windows ACL error on `C:\Users\ak\Documents`; `py_compile` passes.

## Recommendation

This is ready to wire into production behind a reviewable flag:

- Apply detected ends automatically when score >= `0.78`.
- Apply detected starts automatically only when score >= `0.70` and state is `stage_select`.
- When start is `None`, keep the rough timestamp and mark the match for optional review.
- Keep saving candidate frames during early production runs so new false positives can be labeled quickly.
