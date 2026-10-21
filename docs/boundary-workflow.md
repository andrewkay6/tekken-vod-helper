# Boundary Workflow

## Current experimental profile

After the established upscale step, use the local 1440p recording directly:

```powershell
python -m tekken_vod_helper.cli detect "C:/recordings/input1440.mp4" `
  --output-dir "build/work/my-boundary-run" `
  --cache-fine --recognition-crops --temporal
```

This streams/decodes once into a one-second JPEG cache, classifies coarse frames,
refines from cached images in bounded batches, then asks Qwen to reason across
games and candidate sets. It retains original source timestamps. Cache size,
classified frames and total model calls have separate caps; temporal reasoning
reserves one call from the total request budget. For YouTube inputs, the resolver
prefers the tested low-resolution H.264 HLS path and uses no browser cookies.

`predictions.json` contains normal match fields accepted by `apply-matches`;
observation IDs and reasoning remain in the adjudication/review artifacts.
`project.tvh.json` can also be opened directly. Inspect `review.json` and
`run.json`'s `refinement_status` before exporting clips and preparing the YouTube
proposal. Workflow completion does not establish cut accuracy or bracket validity.

Vision thinking stays enabled. The optional `--no-vision-thinking` trial produced
malformed JSON; it is not part of this profile. Bounded implementation packets can
separately use `scripts/qwen_work_packet.py --no-thinking`.

See [recognition and OCR experiments](boundary-recognition-experiments.md) for
measured development diagnostics and the separate fresh-footage evaluation.

## Overview

The boundary detection pipeline classifies video frames into screen states, optionally refines transitions, and proposes match boundaries.

## Pipeline Stages

1. **Frame Sampling** – Extract timestamped frames at a fixed interval.
2. **Classification** – Batch-classify frames using a local vision model.
3. **Refinement** – Re-sample around state transitions at finer intervals.
4. **Boundary Proposal** – Determine match boundaries:
   - **Rule-based (default):** `propose_from_timeline` applies heuristic state-transition logic.
   - **Temporal adjudication (optional):** `TemporalAdjudicator` sends the full classified timeline to a model for cross-game reasoning.

## Temporal Adjudication (`--temporal`)

Enabled via `detect(..., temporal=True)` or CLI `--temporal` flag.

- Runs **after** classification and refinement.
- Replaces rule-based grouping entirely.
- Records `proposal_algorithm: "temporal-qwen-v1"` in `run.json`.
- Saves adjudication provenance (prompt fingerprint, usage, raw response) to `adjudication.json` in the output directory.

### Rules

- The model infers tournament **sets** (not individual games).
- A candidate must start at a confident `stage_select`, include observed `gameplay`, and end at `controller_prompt`.
- Rematch menus and controller assignments do not imply set end.
- Score resets with same players (grand final) are separate sets.
- Aborted attempts (no gameplay) go to review, not export.
- All proposals include `bracket_validity_unverified` review items.

### Constraints

- Input is bounded at 500,000 characters (explicit error if exceeded).
- Model response is validated: unknown IDs, overlap, ordering, and disposition are all checked.
- Errors raise `TemporalAdjudicationError` and are **never** silently swallowed.
- No automatic repair of malformed model output.

## Files

| File | Purpose |
|------|--------|
| `tekken_vod_helper/boundary_reasoning.py` | TemporalAdjudicator implementation |
| `tekken_vod_helper/boundary_workflow.py` | detect(), backtest(), rule-based proposal |
| `tekken_vod_helper/cli.py` | CLI entry point with `--temporal` flag |
| `docs/cutting-policy.md` | Rules-only policy context (not edited by this module) |
