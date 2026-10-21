# Historical boundary benchmarks

Recovered from the untracked-files tree of the stash named
`wip local match boundary detection before timestamp editor fix`. The stash was
preserved, and its GUI changes were not applied over the current application.

`projects/` holds historical JSON labels, stripped of machine paths, credentials,
and unrelated settings. `_provenance.sha256` identifies the original stashed bytes.
These cuts may be rough or later manual cuts; score disagreements rather than
silently substituting model output as ground truth. The generated October project
is deliberately excluded from benchmark labels.

`mock_projects/` holds intentionally shifted rough starts for the legacy guided
analyzer. They are not autonomous test inputs or independent ground truth.

`reference/` contains declared calibration positives and negatives. They come from
BB7, so BB7 results are calibration results. BB5/BB4 use the same policy/references
and are held-out VODs. `vod_manifest.json` resolves project paths relative to this
directory. Two positive-image tests expose known legacy heuristic errors as strict
expected failures; Qwen's final decision must not be gated by that heuristic.

Original review notes are in `docs/boundary-history/`; previous generated reports
and candidate images were recovered under ignored `build/work/boundary-history/`.
The rules-only runtime context is `docs/cutting-policy.md`, which excludes case
timestamps and labels. The original notes remain available for implementation
context and for reviewing disagreements.

Use the JSON CLI `backtest-boundaries` or the legacy backtest script's `--agentic`
mode described in `docs/boundary-workflow.md`. Both call the same detector used by
`detect`. The older download/batch and training scripts are preserved historical
tools; they are not the default streamed agentic path.

Reports and sampled media should go under ignored `build/work/boundary-evaluation/`.
Never commit signed stream URLs, browser cookies, or raw authenticated headers.
# Measured runs

[2026-10-19 results](../docs/boundary-backtest-results.md) record actual blind
Qwen runs and their accuracy limitations. Evaluator-only caveats are preserved
in `annotations.json`; no false-start label has been removed.

