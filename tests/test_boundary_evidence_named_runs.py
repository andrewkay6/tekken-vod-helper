"""Naming a crop corpus: it resolves, or it is refused. Nothing in between.

``crop_runs()`` is how a caller says *which* evidence a measurement was taken over -- the CLI's
``--crop-runs``, a reproduction of an old plan, a frozen regression fixture. Two failures made it unsafe
for that job, and both were silent:

* a **sequence** was stringified before splitting, so ``["build/work/bb9/a", ".../b"]`` became two names
  wearing brackets and quotes, none of which resolved. The caller got the no-corpus evidence base: fewer
  rows, a lower winner count, and a bill full of reads the corpus already held;
* a name that did not resolve was **dropped**, so a whole mistyped list looked identical to "this
  recording has no answers".

Found while freezing the historical corpus for the regression tests: the freeze loaded 18 runs, joined 0
readings, and the tape looked like it had lost three winners.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tekken_vod_helper import boundary_evidence as be        # noqa: E402

REAL = "build/work/bb9/pip-crops-v2"


def test_a_sequence_of_named_runs_resolves_like_a_comma_list():
    """The same directories, whichever way a caller spells them."""
    assert be.crop_runs([REAL]) == be.crop_runs(REAL)
    both = be.crop_runs([REAL, str(ROOT / REAL)])
    assert len(both) == 2, "a list item that repeats a run must resolve once per name, not vanish"
    # The old bug: str(list) split on commas produces bracketed fragments that match no directory.
    assert be.crop_runs([REAL])[0].is_dir()


def test_a_named_corpus_that_resolves_to_nothing_is_refused(tmp_path):
    """A mistyped allowlist is not evidence of an empty recording.

    Returning ``[]`` here hands the caller the state-only join by accident, and on this recording the
    state-only winner count is a property of the missing join, not of the footage -- which is the exact
    class of number this module exists to keep out of reports.
    """
    with pytest.raises(ValueError, match="resolved to no directories"):
        be.crop_runs(["build/work/bb9/no-such-run-anywhere"])
    # Partially resolvable lists keep working: one bad name among good ones is the caller's business,
    # an empty result is not.
    assert len(be.crop_runs([REAL, "build/work/bb9/no-such-run-anywhere"])) == 1


def test_state_only_and_none_stay_distinguishable():
    """Three different questions, three different answers, none of them a silent zero.

    Also pins the one inconsistency callers must survive: discovering returns the repository-relative
    strings :func:`boundary_evidence.crop_run_dirs` publishes, while a named list resolves to directories
    as the caller spelled them. Both name real runs; neither may come back empty by accident, which is
    what the check below is for.
    """
    assert be.crop_runs("state_only") == []
    discovered = be.crop_runs(None)
    assert discovered and all(Path(str(entry)).is_dir() or (ROOT / str(entry)).is_dir()
                              for entry in discovered)
    assert be.crop_runs("") == discovered
    named = be.crop_runs([REAL])
    assert len(named) == 1 and Path(str(named[0])).is_dir()
