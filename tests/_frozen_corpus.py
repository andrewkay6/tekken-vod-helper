"""The frozen historical evidence the corpus-dependent regression tests are written against.

    from _frozen_corpus import crop_runs

    plan(..., crop_runs=crop_runs())

`boundary_investigate.DISCOVER_CROPS` means "everything this recording has produced", which is the right
answer for the public command and the wrong one for a test that pins how many frames a window owes. Two
executed batches added crop answers to the tree, and tests that never changed started failing because the
floor under them moved -- including four that assert the 4800-5600 window contains **no** crop evidence,
which batch v2 invalidated by answering three frames inside it. Pinning a moving target by moving the
window a second time would leave those four tests asserting a property of whichever window happened to be
clean that week.

So the tests name their evidence: the 18 crop runs `coverage-audit-v12` measured, one entry per group file
with its sha256, in `build/work/bb9/corpus-freeze-v1.json`. A frozen file whose bytes changed raises; a
frozen file that vanished raises. What the live corpus actually looks like today is measured by
`tests/test_corpus_drift.py` instead of being quietly borrowed by tests that meant something else.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "build/work/bb9/corpus-freeze-v1.json"

_document = None


def document():
    global _document
    if _document is None:
        _document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return _document


def verify():
    """Check every frozen file is present and unchanged. Raises on any drift in the frozen set."""
    missing, changed, unexpected = [], [], []
    for entry in document()["crop_runs"]:
        directory = ROOT / entry["run"]
        expected = {record["file"] for record in entry["files"]}
        actual = {path.relative_to(directory).as_posix()
                  for path in (directory / "crops" / "groups").glob("*-*.json")}
        unexpected.extend(str(directory / name) for name in sorted(actual - expected))
        for record in entry["files"]:
            path = directory / record["file"]
            if not path.is_file():
                missing.append(str(path))
            elif hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
                changed.append(str(path))
    if missing or changed or unexpected:
        raise AssertionError(
            "the frozen historical evidence is no longer the evidence these tests were written against: "
            "%d missing %s%d changed %s; %d unexpected %s. Re-freeze deliberately "
            "(and re-derive every number that moves "
            "with it) rather than editing the assertions."
            % (len(missing), missing[:3], len(changed), changed[:3],
               len(unexpected), unexpected[:3]))
    return True


def crop_runs():
    """The frozen run list, verified. Order is the manifest's, which is sorted by path."""
    verify()
    return [entry["run"] for entry in document()["crop_runs"]]


def totals():
    return dict(document()["totals"])


def discovered_not_frozen():
    """Runs discovery sees that the freeze does not name: new evidence, not a defect."""
    from tekken_vod_helper import boundary_evidence as be
    frozen = {entry["run"] for entry in document()["crop_runs"]}
    return sorted(run for run in be.crop_run_dirs() if run not in frozen)
