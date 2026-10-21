# Boundary Analyzer Calibration Summary

## Source material

- Project JSON: `backtests/projects/sep19.tvh.json`
- Full VOD used for backtest: `Basement Brawl #7 Full VOD` (`W_rDl7ZWvuQ`)
- User references:
  - Start: `backtests/reference/start/bb7-stage-select-user.png`
  - End: `backtests/reference/end/bb7-press-start-user.png`

## Selected references

- Accepted start: `backtests/reference/start/bb7-match1-stage-select-random.png`
  - Reason: true stage-select grid immediately before match 1 starts, around `00:04:25`.
- Accepted end: `backtests/reference/end/bb7-press-start-match1-next-setup.png`
  - Reason: matches the next setup/press-start family, useful for finding late cuts.

## Rejected examples kept for review

- `backtests/reference/rejected/start/bb7-match1-dark-transition-not-stage-select.png`
  - Reason: stage intro transition after stage select; visually similar to the selected arena, but not the chop start.
- `backtests/reference/rejected/start/bb7-match1-vs-screen-not-stage-select.png`
  - Reason: VS/loading screen after stage select.
- `backtests/reference/rejected/start/bb7-match1-gameplay-not-stage-select.png`
  - Reason: active gameplay.
- `backtests/reference/rejected/start/bb7-match1-gameplay-ready-not-stage-select.png`
  - Reason: active gameplay intro/round-ready area.
- `backtests/reference/rejected/end/bb7-match1-gameplay-not-press-start.png`
  - Reason: in-match frame, useful as a known bad comparison.

## Backtest notes

- Initial run found the correct broad classes but chose the start too late (`00:05:09.669`, gameplay) and end too late (`00:18:18.669`, next setup press-start).
- End detection was changed to use the first stable matching end screen after the plausible match duration. On match 1 it now selects `00:16:02.528`, which is `+0.151s` from the saved manual end (`00:16:02.377`).
- Start detection now requires visible stage-select grid structure and chooses the highest-confidence frame in the final matching run. Cached-frame replay selects `00:04:24.669`, matching the `00:04:25` stage-select reference.
- A live v7 YouTube rerun was blocked by YouTube's anti-bot sign-in gate before frame extraction. I did not use browser cookies.

## Mock rough-timestamp projects

- `backtests/mock_projects/sep19-too-early-starts.tvh.json`
- `backtests/mock_projects/sep19-too-late-starts.tvh.json`
- `backtests/mock_projects/sep19-mixed-rough-starts.tvh.json`

These variants preserve the original match list and manual ends while shifting rough starts earlier, later, or mixed to exercise recovery cases.
