"""The live corpus, measured with the audit's own inputs.

Everything else in this corner of the suite reads the frozen historical evidence by name
(``tests/_frozen_corpus.py``), which is what makes those numbers survive the next executed batch. The cost
of freezing is that nothing then reports what the recording looks like *today*, and a document quoting a
figure nobody re-measured is the failure this file exists to prevent: it reads the same inputs
``coverage-audit-v24`` recorded -- all four band runs, discovered crops, the whole tape -- and compares.
It was repointed from v22 by the executed winner-corroboration-v2 batch on 2026-11-06, which is the
failure mode this file is built for: v22 stays the pre-batch record, v24 is today's bill.

If this fails after a batch ran, the fix is a new audit and the documents that quote the old one. It is not
a new constant here: the assertion texts name the artifact that went stale, because a test edited to match
a changed world hides the change it just witnessed.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from tekken_vod_helper import boundary_evidence as be        # noqa: E402
from tekken_vod_helper import boundary_games as bg           # noqa: E402
from tekken_vod_helper import boundary_header as bh          # noqa: E402
from tekken_vod_helper import boundary_investigate as bi     # noqa: E402
import _frozen_corpus as frozen                              # noqa: E402

R = ROOT / "build/work/bb9"
TIMELINE = R / "games-pilot-game1/run1/timeline-with-facts.json"
MANIFEST = R / "detect-1440p-v1/frames/sample-manifest.json"
LEDGER = R / "set-end2end-v1/attempts-shared-2026-10-31.json"
AUDIT = R / "coverage-audit-v25/coverage-audit.json"
DURATION = 6928.421136
#: The audit's four band runs. Three of them are what most tests use; the fourth covers four games whose
#: identity would otherwise be asked about again, and it is worth 8 band requests on its own.
BAND_RUNS = [str(R / "header-trial-v1/unapproved-execute-2026-10-30"),
             str(R / "set-end2end-v1/attribution-run"),
             str(R / "set-end2end-v1/band-run"),
             str(R / "unseen-set-structure-v1/run-attempt/header")]

requires_audit = pytest.mark.skipif(
    not (AUDIT.is_file() and TIMELINE.is_file() and MANIFEST.is_file()),
    reason="the published audit and the saved BB9 evidence are not present")


def audit_document():
    return json.loads(AUDIT.read_text(encoding="utf-8"))


def header():
    return bh.saved_readings(bh.response_directories(",".join(BAND_RUNS)), LEDGER)


def tape(**kw):
    """The whole recording, priced the way the audit prices it: discovered corpus, four band runs."""
    kw.setdefault("max_requests", 2000)
    return bi.plan_saved_investigation(str(TIMELINE), str(MANIFEST), DURATION,
                                       marks=str(R / "oct22.tvh.json"), header=header(),
                                       crop_runs=bi.DISCOVER_CROPS, step=2.0, expand=0.0,
                                       games_per_set=None, **kw)


@requires_audit
def test_the_discovered_corpus_is_the_one_the_audit_counted():
    base = audit_document()["evidence_base"]
    discovered = be.crop_run_dirs()
    assert set(discovered) == set(base["crop_runs"]), (
        "%s lists %d crop runs and discovery finds %d (only in discovery: %s). The published prices are "
        "not the current tree's: re-audit, then update the documents that quote it."
        % (AUDIT.name, len(base["crop_runs"]), len(discovered),
           sorted(set(discovered) - set(base["crop_runs"]))[:3]))
    joined = be.evidence(None, facts=str(TIMELINE))
    assert joined["crop_readings"] == base["crop_readings"], (
        "the corpus joins %d readings against the %d %s measured"
        % (joined["crop_readings"], base["crop_readings"], AUDIT.name))
    assert joined["rows"] == base["rows"]


@requires_audit
def test_the_current_recording_state_matches_the_published_audit():
    tracked = bg.track_games(be.evidence(None, facts=str(TIMELINE))["observations"],
                             games_per_set=None, header=header(),
                             source=bg.source_bounds(manifest=str(MANIFEST), duration=DURATION))
    coverage = audit_document()["no_format"]["coverage"]
    winners = sum(1 for game in tracked["games"] if game.get("winner"))
    assert (len(tracked["rounds"]), len(tracked["games"]), winners) == (
        coverage["rounds"], coverage["games"], coverage["games_with_a_winner"])
    assert winners == 30 and coverage["games"] - winners == 0, (
        "the unwon-game count the reports publish (%s of %s) no longer holds" % (
            coverage["games_with_a_winner"], coverage["games"]))
    # History of this line, so a future reader can see which batch moved it: 26 of 29 (game-reset-probe-v1
    # added a game and a winner, leaving three unwon) -> 27 of 30 after winner-corroboration-v2, which
    # corroborated without naming anyone -> **30 of 30** on 2026-11-06, when outcome-recovery-v1 named the
    # three games that had never had a winner. The unwon count was the one figure that had resisted every
    # earlier batch precisely because no earlier batch asked for those rows.


@requires_audit
def test_the_current_bill_is_the_one_the_plans_and_reports_quote():
    # 245 -> 238 on 2026-11-06: the authorized winner-corroboration-v2 batch answered eight of the priced
    # interiors (all eight identities left the plan) and one new interior look opened in the window whose
    # frame came back with no row drawn -- net minus seven, with recovery unchanged at 30 / 27 / 122. The
    # two withheld frames contribute presence, not counts, so no winner moved: see
    # build/work/bb9/winner-corroboration-v2-consequence-2026-11-06.json.
    priced = audit_document()["no_format"]["priced"]
    plan = tape()
    assert plan["requests_planned"] == priced["requests_planned"] == 231, (
        "coverage-audit-v24 priced %s requests; the current tree prices %s"
        % (priced["requests_planned"], plan["requests_planned"]))
    assert plan["dispatch"]["images_total"] == priced["images_total"] == 486
    assert (plan["dispatch"]["completion_tokens_ceiling"]
            == priced["completion_tokens_ceiling_sum_all_routes"] == 714000)
    assert ({entry["route"]: entry["requests_planned"] for entry in plan["dispatch"]["routes"]}
            == {route: value["planned"] for route, value in priced["by_route"].items()})


@requires_audit
def test_no_claim_point_is_left_unaccounted_for_on_the_live_corpus():
    """The coverage claim the docs make, measured on today's evidence rather than the frozen set."""
    claims = tape()["claims"]
    assert claims["claims_resolved"] is True, claims["counts"]
    assert claims["counts"].get("unaccounted", 0) == 0, claims["counts"]
    assert claims["points_unmet"] == 0, [
        {"claim": entry["claim"], "candidate": entry["candidate"], "unmet": entry["unmet"]}
        for entry in claims["entries"] if entry["unmet"]]
    assert claims["counts"].get("partial", 0) == 0, "a full allowance should leave nothing half-carried"


@requires_audit
def test_the_freeze_stays_a_strict_subset_of_the_live_corpus():
    """Drift is named, not absorbed: the frozen evidence is inside today's, and the gap is reported."""
    live, pinned = set(be.crop_run_dirs()), set(frozen.crop_runs())
    assert pinned <= live, "a frozen run left the disk: %s" % sorted(pinned - live)[:3]
    assert live - pinned, ("the freeze and the live corpus are identical, so this check stopped saying "
                           "anything about drift -- extend the freeze deliberately, in its own change")


def test_the_frozen_evidence_bytes_are_still_the_ones_the_tests_were_written_against():
    """No batch may quietly rewrite a historical answer that a regression test quotes."""
    assert frozen.verify()
    totals = frozen.totals()
    assert totals == {"runs": 18, "group_files": 236}, totals
