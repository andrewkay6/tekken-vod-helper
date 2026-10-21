# Boundary Backtest Review - sep19 match 1

Source VOD: https://www.youtube.com/watch?v=W_rDl7ZWvuQ  
Project JSON: `backtests/projects/sep19.tvh.json`  
Match: 1, Ag vs CrispyBacon, Winners Round 1

## Run Notes

- Streamed from YouTube with `yt-dlp` + FFmpeg; no full VOD was downloaded.
- Used the short reference copies in `backtests/reference/`.
- Candidate frames are in `candidate_frames/`.
- This first run used a wider-than-needed end search before I patched subset-mode optimization, so it extracted 191 frames.

## Candidate Decisions

| Boundary | Candidate | Score | Decision | Why |
|---|---:|---:|---|---|
| Start | `00:05:09.669` | `0.8006` | Reject | This is active gameplay, not stage select. The current full-frame grayscale score is too weak for start detection. |
| End | `00:18:18.669` | `0.9445` | Keep as class-positive, review timing | This is a real "Player 2 Press ... on the controller" screen, matching the desired end-screen class. It is for the next match setup and is 136.292s after the old manual end, so you should confirm whether this is the desired chop endpoint. |

## Takeaway

The end detector is already finding the correct visual class from your reference image. The start detector needs a different strategy: the single whole-frame stage-select reference can confuse gameplay frames for stage select because both share the same overlay, brightness, and camera layout.

Recommended next changes:

- Use separate reference folders for start/end instead of one image each.
- Score stage select using a lower-screen stage-card/grid crop instead of the full frame.
- Save top N candidates per boundary, not only the winning frame, so review can teach the detector what was close-but-wrong.
- Keep the "press start" end detector, but add context rules to label whether the frame belongs to current post-match or next-match setup.
