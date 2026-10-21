# Match cutting policy

Recovered from the historical notes in `docs/boundary-history/`. This is the
rules-only context for detection. Historical examples, timestamps, player names,
and backtest results belong to calibration/scoring, not the detection prompt.

**Visual State Classification Rules:**
- **stage_select**: Requires visible stage selection tiles/grid and names. Placeholder tiles (red/blank squares), loading screens, or character rosters are NOT stage_select.
- **character_select**: Character rosters, loading placeholders, or character-select layouts (including placeholder tiles/unknown labels) map to character_select. If ambiguous, use character_select or unknown.
- **stage_intro**: A blank black gameplay region with only broadcast overlay/webcams supplies no intrinsic evidence of a stage intro. Classify as unknown unless visible scene proves intro.

- A match clip represents a tournament set, which may contain multiple games.
- Setup-only attempts and visibly aborted play are not completed bracket sets.
  Require observed gameplay before confirming a candidate; sparse sampling may
  miss play, so retain uncertainty. Gameplay alone does not establish a completed
  bracket match. Flag bracket validity for review rather than inventing completion.
- Prefer the final confident stage-select occurrence before the set begins.
  Require visible stage-selection tiles/grid; background resemblance is insufficient.
- Character selection, controller assignment dialogs, VS/loading screens, stage
  or character intros, gameplay, and rematch side menus are distinct states.
  None should be mislabeled as stage select.
- Repeated stage selection or rematches within a set do not automatically start
  another set. Use the sequence of screen states and available scoreboard evidence.
- Prefer the first stable controller/start prompt endpoint after a plausible set.
  An ordinary between-game rematch menu is insufficient evidence that a set ended.
- If a clean stage-select start is absent, retain uncertainty. Historical manual
  edits sometimes used character select or an early safe pre-intro frame; flag
  such fallbacks for review rather than presenting them as confident stage select.
- A Grand Final Reset is a separate set even when both players remain unchanged.
  Require sequence/score evidence; unchanged player names alone cannot merge it away.
- Treat controller disconnects, long downtime, incomplete recordings, and the final
  set conservatively. Unknown boundaries remain review items, not invented times.
- Preserve the source and historical project. Produce a new proposal with evidence,
  confidence, and review reasons. Human confirmation precedes YouTube submission.

The historical image-similarity detector had documented false positives and
withheld starts. Its confidence scores do not establish production readiness.
Historical timestamps can represent rough or later manual cuts; report measured
agreement with them honestly alongside screen-state/policy diagnostics.

For an autonomous backtest, detection receives the source, these rules, and
declared calibration references only. It receives no label-derived windows,
timestamps, names, match count, or stopping hints. Load historical JSON for scoring
only after predictions are persisted. Report calibration and held-out results
separately; a successful model/API call is not a successful accuracy test.
