# BB7 Boundary Analyzer Production-Confidence Conclusion

## Backtest Scope

- VOD: Basement Brawl #7 Full VOD
- Local file used: 360p YouTube format 18, about 513 MiB
- Project: `backtests/projects/sep19.tvh.json`
- Matches tested: 9
- Best full run: `backtests/reports/bb7-local-full-v3`

## Result

This is not production-ready yet.

The current local image-similarity analyzer is promising for end detection, but not reliable enough for start detection.

## End Detection

End detection was good on the first 7 matches:

- Match 1: `+0.151s`
- Match 2: `+0.737s`
- Match 3: `-1.000s`
- Match 4: `+2.000s`
- Match 5: `+3.689s`
- Match 6: `-1.799s`
- Match 7: `+1.000s`

Failures:

- Match 8: `-129.957s`
- Match 9: `-1491.279s`

The late-match failures are related to end screens/unplugs/long downtime and need a better end-state classifier plus special handling for the final match.

## Start Detection

Start detection is still unreliable.

The analyzer correctly found a stage-select-like frame for match 1, but selected controller menus, VS screens, or stage intro screens for several other matches. Adding more positive stage-select references improved scores, but did not prevent false positives.

Observed problem classes:

- Controller assignment modal
- Character select / controller setup screens
- VS screens
- Character intro / stage intro screens
- Gameplay frames when the rough timestamp is already inside the match

## Practical Conclusion

The current reference-image approach is good enough to build a review workflow, but not good enough to auto-chop starts unattended.

To reach production confidence, the next implementation should add a second-stage local classifier:

- `stage_select`
- `character_select`
- `controller_prompt`
- `vs_screen`
- `stage_intro`
- `gameplay`
- `post_match_menu`
- `controller_disconnected`

This can still be local and token-free. A lightweight OpenCV/scikit-image classifier or ONNX image classifier trained from harvested frames is more appropriate than OCR alone. Character metadata can help reject VS/gameplay frames, but it should be a validation signal rather than the primary detector.

## Recommended Next Step

Use the existing backtest harness and harvested frames to create a labeled dataset, then train/evaluate a small local classifier. Until then, the UI should present analyzer candidates for review instead of auto-applying them.

## Follow-Up Implemented

- Added frame-state identifiers for `stage_select`, `character_select`, `controller_prompt`, `vs_screen`, `stage_intro`, `gameplay`, `post_match_menu`, and `unknown`.
- Added report fields for detected start/end identifiers and feature scores.
- Added `--timeline-scan` mode to classify a whole VOD without project JSON and merge samples into state segments.
- Added `--timeline-save-frames` to export samples into `dataset/predicted/<state>` folders for manual correction and future ML training.

PyTorch is a good fit for training the frame-state classifier, but it should not be a runtime dependency yet. The current path should be:

1. Run `--timeline-scan --timeline-save-frames`.
2. Correct the predicted label folders by hand.
3. Train a small PyTorch image classifier from those folders.
4. Export to ONNX.
5. Run ONNX locally from the app/backtest tool.
