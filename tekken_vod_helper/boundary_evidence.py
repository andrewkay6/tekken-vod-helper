"""One recording-scoped evidence base, and the one way its set-level result is summarised.

Two things live here because both were once duplicated and both duplications cost real money.

**The join.** A pip reading is only evidence about the frame it was cropped from, and a frame is only
joined to the timeline by the timestamp the cache recorded for it. Getting that join wrong is not a
small error: appending a saved reading from outside the requested window once put game 1's frames
inside game 2's replay and invented a game, and answering for a neighbouring instant moves a label
across a HUD transition. So the framing guard (a second picture of one instant never joins one pip
series) and the window guard (a reading outside the window is dropped, not appended) travel with the
only merge function anyone is allowed to call.

**The disagreement this closes.** On 2026-10-30 the set-trial planner merged the saved crop answers and
reported four validated winners in ``[1200, 2200)`` while the integration report read the state timeline
alone for the same interval and reported zero. Each was faithful to the rows it fed the tracker; only one
had the crop corpus. The figure that changed was ``games_with_a_winner`` -- which a reader takes as a
property of the footage when it is a property of the join.

This module used to be ``scripts/pip_evidence.py`` plus the merge inside ``scripts/validate_game_one.py``.
It moved here when the public recovery command (``investigate-games``) needed the same evidence base: a
library cannot import a script, and a *second* implementation of the join in the package is exactly the
failure this file exists to prevent. The scripts now delegate, so there is one merge, one discovery rule
and one summary.

Nothing here sends a request, and nothing here writes a file: it reads answers that already exist and
adjudicates them.
"""
import json
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import boundary_games
from . import boundary_pips

ROOT = Path(__file__).resolve().parents[1]

#: Where one recording's saved answers live. Discovery is scoped to a recording on purpose: "every crop
#: answer on disk" would eventually join two recordings' pip series, and the framing guard exists because
#: two pictures of one instant already corrupted one series once.
DEFAULT_FIXTURE = "bb9"

#: The clock/state facts the canonical merge joins crop readings onto. Named once so a caller cannot walk
#: a different timeline and then compare its own numbers with someone else's; the public command passes its
#: own ``--timeline``, which is the same file the read-only reports use.
DEFAULT_FACTS = ROOT / "build/work/bb9/games-pilot-game1/run1/timeline-with-facts.json"

#: Directories full of images and empty of answers, pruned from the walk so a default report is not paying
#: for a filesystem tour it does not need.
_PRUNE = {"frames", "bands", "crops", "source-crops", "label-sheet", "__pycache__"}

#: The list every plan written up to 2026-10-30 priced itself against. Kept as a record, never a default:
#: stored plans name their own inputs, and a report that reads this list today misses the trials that ran
#: after it was written -- exactly how the canonical report came to print `games_with_a_winner: 0` for
#: `[3200, 4000)` in the same hour a trial recovered three winners there from answers already on disk.
PLAN_SNAPSHOT_CROP_RUNS = ["build/work/bb9/pip-crops-v2", "build/work/bb9/game1-validate-v1/run",
                           "build/work/bb9/game2-validate-v1/run", "build/work/bb9/game2-validate-v1/run2",
                           "build/work/bb9/windows-interval-v2/run",
                           "build/work/bb9/consecutive-games-v2/run",
                           "build/work/bb9/consecutive-games-v3/run"]

#: Imported by nothing new. A name kept so an old script or notebook cannot fail confusingly; the default
#: evidence base is discovered, and reading this constant instead is how a report goes stale silently.
CROP_RUNS = PLAN_SNAPSHOT_CROP_RUNS

#: State alone is a legitimate comparison -- it shows how much of a conclusion the crop readings carry --
#: but it is never the base a winner count is quoted from, because on this recording it is always zero.
STATE_ONLY = "state_only"

#: The two sides of a pip row. A reading that answers one side and not the other is a half-reading and is
#: kept as one, never promoted to a scoreline.
SIDES = ("LEFT", "RIGHT")

#: Statuses of a band mapping question: settled means the nameplate says who sat where, open means it is
#: still a costable observation.
OPEN_STATUSES = ("uncovered", "contested")
SETTLED_STATUSES = ("swap_confirmed", "pairing_change_confirmed", "steady_confirmed")


def _finite(value):
    # type: (Any) -> bool
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


# --------------------------------------------------------------------- which saved answers exist


def evidence_root(fixture=None, path=None):
    # type: (Optional[str], Optional[Any]) -> Path
    """The directory that holds one recording's experiments."""
    if path is not None:
        return Path(str(path))
    return ROOT / "build/work" / (fixture or DEFAULT_FIXTURE)


def _as_seen(path):
    # type: (Path) -> str
    """Repository-relative when it lives in the repository, absolute when it does not.

    A survey pointed at another checkout or a temporary recording root is a legitimate thing to run, and
    making names relative to whatever the caller happens to have in mind is how two machines report
    different corpora for one directory tree.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def crop_run_dirs(fixture=None, root=None):
    # type: (Optional[str], Optional[Any]) -> List[str]
    """Every directory under one recording's root that holds crop answers, wherever they were produced.

    Discovered rather than listed. A hard-coded list is a slow-motion lie: it stays correct the day a trial
    runs and quietly under-reports the next morning, and the figure it under-reports is a winner count.
    Reuse has to see all of them, because a reading an experiment could borrow but does not know about is
    a reading that gets re-asked and re-charged.

    A run directory is whatever holds a ``crops/groups`` tree, at any depth. The order is sorted by path, so
    two invocations of one command read the corpus the same way.
    """
    base = evidence_root(fixture, root)
    if not base.is_dir():
        return []
    found = []
    stack = [base]
    while stack:
        current = stack.pop()
        try:
            children = sorted(p for p in current.iterdir() if p.is_dir())
        except OSError:
            continue
        if (current / "crops" / "groups").is_dir():
            found.append(current)
        stack.extend(p for p in children if p.name not in _PRUNE)
    rel = [_as_seen(p) for p in found]
    return sorted(dict.fromkeys(rel))


def crop_runs(value=None, fixture=None, root=None):
    # type: (Any, Optional[str], Optional[Any]) -> List[Path]
    """Resolve a comma-separated list or a sequence of crop runs, or discover them, into real directories.

    An explicit list still wins, because reproducing an old plan means naming the inputs it saw.
    ``state_only`` still means "no crop readings at all", which is a comparitor and never a winner count.

    A sequence is read as a sequence. Stringifying one first -- which is what this function used to do --
    turns ``["build/work/bb9/a", "build/work/bb9/b"]`` into two names wrapped in brackets and quotes, no
    name resolves, and the caller silently gets the **no-corpus** evidence base: fewer rows, a lower
    ``games_with_a_winner``, and a bill full of reads the corpus already holds. A named corpus that
    resolves to nothing is therefore an error, not an empty answer.
    """
    if isinstance(value, str) and value.strip().lower() == STATE_ONLY:
        return []
    if value is None or (isinstance(value, str) and not value.strip()):
        return crop_run_dirs(fixture=fixture, root=root)
    names = ([item.strip() for item in value.split(",")] if isinstance(value, str)
             else [str(item).strip() for item in value])
    names = [name for name in names if name]
    paths = []
    for name in names:
        candidate = Path(name)
        if not candidate.is_dir():
            candidate = ROOT / name          # a discovered or typed relative path means the repository
        if candidate.is_dir():
            paths.append(candidate)
    if names and not paths:
        raise ValueError(
            "a named crop corpus resolved to no directories at all: %s. That is indistinguishable from "
            "\"this recording has no crop answers\", which changes both the winner count and the bill -- "
            "so it is refused rather than reported as a measurement. Checked relative to %s and the "
            "repository root." % (", ".join(names[:4]), Path.cwd()))
    return paths


def framings(runs=None, fixture=None, root=None):
    # type: (Optional[Sequence[str]], Optional[str], Optional[Any]) -> Dict[str, Dict[str, int]]
    """Per-run group counts by framing, using the predicate the merge itself applies.

    Disclosure, not filtering: :func:`crop_observations` is what keeps a second framing out of one pip
    series. This only says in advance that a listed run holds answers the merge will drop, so a reader who
    spots a master-framing run knows it was seen and deliberately not joined.
    """
    names = [str(p) for p in crop_runs(runs, fixture=fixture, root=root)] if runs is None \
        else list(runs)
    out = {}
    for name in names:
        path = Path(name) if Path(name).is_absolute() else ROOT / name
        counts = {"cache": 0, "other_framing": 0, "errored_or_other_arm": 0}
        groups = sorted((path / "crops" / "groups").glob("*-*.json")) if path.is_dir() else []
        for group_path in groups:
            group = json.loads(group_path.read_text(encoding="utf-8"))
            if group.get("arm") != "crops" or group.get("errors"):
                counts["errored_or_other_arm"] += 1
            elif boundary_pips.is_cache_framing(group):
                counts["cache"] += 1
            else:
                counts["other_framing"] += 1
        out[str(name)] = counts
    return out


# --------------------------------------------------------------------- the canonical join


# --------------------------------------------------------------------- what a pip reading is worth

#: The pip fields, as a set. A route either answers all of them about one frame or none of them, and
#: grading them one at a time is how half a reading ends up deciding a game.
PIP_FIELDS = ("win_dots", "win_dots_drawn", "win_dots_confidence", "pip_row_drawn")

#: The only source whose win-dot counts are measurements. The cache-arm crop has frozen geometry
#: (``boundary_pips.LEFT_BOX``/``RIGHT_BOX`` measured off a coordinate grid), a control arm and a
#: measured accuracy figure; nothing else in this project does.
VALIDATED_PIP_SOURCE = "crop_readings"

#: Where a demoted reading goes instead. ``parse_round_facts`` never reads this key, so a diagnostic can
#: reach a report but cannot reach a winner.
DIAGNOSTIC_PIP_SUFFIX = "_diagnostic"

#: Why each non-crop source is a diagnostic, stated once. A generic "untrusted" flag is what the
#: broadcast-header route already carries, and it reads as an apology rather than a reason.
PIP_GRADES = {
    "crop_readings": ("validated", "frozen geometry, control arm, measured accuracy"),
    "pip_crops": ("validated", "the crops arm's own answer about its crop"),
    "vision_group": ("diagnostic", "a whole 640x360 frame: this is the reading that lost the "
                                   "measured comparison against the crop arm"),
    "round_facts": ("diagnostic", "asked of the whole frame, so it competes with the busy HUD"),
    "header_band": ("diagnostic", "the scoreboard band has no frozen pip geometry and no control"),
    "classified_evidence_text": ("diagnostic", "mined from prose, never from the row of circles"),
}


def pip_grade(source):
    # type: (Any) -> str
    """``validated`` or ``diagnostic`` for a reading whose source is one of the known routes.

    Unknown sources are diagnostic: a new producer cannot promote itself to evidence by not being
    listed, which is the same asymmetry the framing guard uses.

    A composite source -- the ``"classified_evidence_text+crop_readings"`` a merged row carries -- is
    graded by its strongest part, because a row that received a crop reading *has* a crop reading, and
    the prose that came alongside it does not demote it.
    """
    parts = [part for part in str(source or "").split("+") if part]
    grades = [PIP_GRADES.get(part, ("diagnostic", "unknown pip source")) for part in parts]
    return "validated" if any(grade == "validated" for grade, _ in grades) else "diagnostic"


def pip_reason(source):
    # type: (Any) -> str
    """Why this source is or is not a measurement, for the artifact that has to explain a rejection."""
    parts = [part for part in str(source or "").split("+") if part]
    known = [PIP_GRADES[part] for part in parts if part in PIP_GRADES]
    if not known:
        return PIP_GRADES["vision_group"][1]
    return "; ".join("%s: %s" % (part, reason) for part, reason in zip(
        parts, [grade[1] for grade in known]))


def demote_diagnostic_pips(row, reason=None):
    # type: (Dict[str, Any], Optional[str]) -> Dict[str, Any]
    """Move a non-crop pip reading out of the evidence fields, keeping the raw values and the clash.

    Nothing is thrown away: the numbers stay on the row under ``win_dots_diagnostic`` and a disagreement
    with the crop reading that outranks it is recorded in ``pip_reading_conflicts`` with both sides. That
    is what makes the diagnostic useful -- it is evidence about the model's eyes, not about the fight.
    """
    moved = []
    for field in PIP_FIELDS:
        if field not in row:
            continue
        slot = row.get(field)
        if slot in (None, [], [None, None], {}):
            row.pop(field)
            continue
        row[field + DIAGNOSTIC_PIP_SUFFIX] = slot
        row.pop(field)
        moved.append(field)
    if moved:
        row["pip_evidence_class"] = "unvalidated_pip_diagnostic"
        row["pip_diagnostic_reason"] = reason or PIP_GRADES.get(
            str(row.get("fact_source") or ""), PIP_GRADES["vision_group"])[1]
    return row


def grade_pip_row(row):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """Stamp this row's pip reading with its evidence class, demoting it if it is a diagnostic.

    This is the rule every tool applies before the tracker sees a row, in one place: a pip value that
    did not come from the cache-arm crop does not get to be a ``win_dots`` at all. It survives as
    ``win_dots_diagnostic`` -- which nothing in :mod:`tekken_vod_helper.boundary_games` reads -- so a
    whole-frame or band guess can be reported, audited and compared, and cannot name a winner even on a
    frame nobody ever cropped.
    """
    if not any(field in row for field in PIP_FIELDS):
        return row
    source = str(row.get("fact_source") or row.get("source") or "")
    if row.get("pip_evidence_class") == "validated_pip_evidence":
        return row
    if pip_grade(source) == "validated":
        row["pip_evidence_class"] = "validated_pip_evidence"
        return row
    return demote_diagnostic_pips(row, "%s reading is a diagnostic, not a measurement: %s"
                                  % (source or "unnamed", pip_reason(source)))


def record_pip_conflict(row, rejected, incoming_source, kept_source=None):
    # type: (Dict[str, Any], Any, str, Optional[str]) -> Dict[str, Any]
    """Note that a diagnostic pip reading disagreed with the measurement that outranks it."""
    clashes = row.setdefault("pip_reading_conflicts", [])
    clashes.append({"rejected": rejected, "rejected_source": incoming_source,
                    "kept_source": kept_source or VALIDATED_PIP_SOURCE,
                    "because": "a crop reading with frozen geometry decides the pip row; a whole-frame "
                               "or band reading of the same instant is kept as a diagnostic, never as "
                               "the score"})
    return row


def crop_observations(run_dirs, stats=None):
    # type: (Sequence[Any], Optional[dict]) -> Dict[str, Dict[str, Any]]
    """Readings from the crops arm only, keyed by frame path. Nothing is re-sent to get these.

    Source-framing answers are skipped: same instant, different picture, and a pip series must not be
    built from two framings. ``stats`` is filled in so a caller can report the exclusion instead of
    letting it read as an absence.
    """
    found = {}
    for run in run_dirs or []:
        run = Path(str(run))
        store = run / "crops" / "groups"
        for path in sorted(store.glob("*-*.json")) if store.is_dir() else []:
            group = json.loads(path.read_text(encoding="utf-8"))
            if group.get("arm") != "crops" or group.get("errors"):
                continue
            if not boundary_pips.is_cache_framing(group):
                if stats is not None:
                    stats["skipped_other_framing"] = stats.get("skipped_other_framing", 0) + 1
                continue
            by_label = {o["label"]: o for o in group.get("observations") or []}
            if not all(label in by_label for label in SIDES):
                continue
            drawn = {by_label[l].get("drawn") for l in SIDES}
            found[group["frame"]] = {
                "timestamp": group.get("timestamp"),
                # An absent row stays None/None. Coercing it to 0/0 here would hand the tracker a
                # scoreline for pixels that are not on screen -- the exact first-pilot failure.
                "win_dots": [None, None] if drawn == {False} else
                             [by_label[l].get("lit") for l in SIDES],
                # Per-side presence travels with the count: "row unseen on this side" is what makes a
                # zero here non-evidence, and collapsing both sides into one flag loses that.
                "win_dots_drawn": [by_label[l].get("drawn") for l in SIDES],
                "win_dots_confidence": [by_label[l].get("confidence") for l in SIDES],
                "pip_row_drawn": True if drawn == {True} else False if drawn == {False} else None,
                "source": "crop_readings",
                "from_group": _as_seen(path)}
    return found


def frame_identity(path):
    # type: (Any) -> str
    """One spelling of one frame file, so a path and its absolute form are the same picture.

    Group files record the path they were *given*, and callers spell it differently: the cache manifest
    says ``build/work/bb9/detect-1440p-v1/frames/frame-4298.jpg`` and a hand-written annotation file says
    ``/var/home/andy/.../frame-4298.jpg``. Those are one frame, and when they index as two, a reading that
    exists stops counting as one -- the crop route re-prices a frame it already answered, which is exactly
    the spend a corpus is supposed to prevent. Measured 2026-11-04 on six executed crop answers: all six
    were in the room and none of them was treated as answered.

    Not :func:`frame_of`, deliberately: the basename is the right key for joining a reading onto a clock
    row inside one recording, and the wrong one here, because two frame caches of the same broadcast both
    contain ``frame-0573.jpg``. Merging those into one pip series is the second-framing error this module
    already refuses elsewhere, so identity keeps the directory and resolves the spelling.
    """
    text = str(path or "")
    if not text:
        return ""
    try:
        return os.path.realpath(text)
    except OSError:  # an unreadable or vanished path still needs one stable key
        return os.path.abspath(text)


def frame_of(row):
    # type: (Dict[str, Any]) -> str
    """The frame a row describes.

    Timelines are mixed: classifier rows key the image as ``path``, vision-group rows key it as ``frame``.
    Joining on one name alone silently missed three of the eight verified readings and appended them as a
    *second* row at the same timestamp -- which the tracker would then read as the win dots moving, the
    exact regression artifact this project keeps having to hunt down.
    """
    return Path(str(row.get("path") or row.get("frame") or "")).name


def merge_crop_readings(rows, observations, window):
    # type: (Sequence[Dict[str, Any]], Dict[str, Dict[str, Any]], Tuple[float, float]) -> tuple
    """Join saved crop readings onto clock/state rows, inside one window, without touching either file.

    This is *the* merge. A tool that needs pip evidence calls this (through :func:`evidence`) rather than
    walking group files itself, because the two guards below are the difference between a reading and a
    hallucinated game.
    """
    out = [dict(r) for r in rows
           if _finite(r.get("timestamp")) and window[0] <= float(r["timestamp"]) <= window[1]]
    for r in out:
        # Vision-group rows already store a full frame path under "frame"; give every row one name.
        if not r.get("path") and r.get("frame"):
            r["path"] = str(r["frame"])
        # The full-frame pilot's dots failed the measured comparison. Keep them
        # as rejected evidence, never as active measurements alongside crop reads.
        if "win_dots" in r:
            r["superseded_full_frame_win_dots"] = r.pop("win_dots")
        r.pop("pip_row_drawn", None)
    by_frame = {frame_of(r): r for r in out if frame_of(r)}
    # A row with no frame at all still carries a clock reading, so it stays; it simply cannot receive
    # a pip reading, and dropping it would quietly shorten the timeline the tracker walks.
    unjoinable = len(out) - len(by_frame)
    added = skipped = 0
    lo, hi = window
    for path, reading in (observations or {}).items():
        name = Path(path).name
        row = by_frame.get(name)
        if row is None:
            # Outside the window means outside the question. Appending it makes the tracker walk rows
            # the caller never asked for, and because observation ids are positions in the sorted list
            # it renames every id after the insertion as well. A caller that wants a wider window passes
            # a wider one.
            stamp = reading.get("timestamp")
            if isinstance(stamp, (int, float)) and not (lo <= float(stamp) <= hi):
                skipped += 1
                continue
            row = {"id": "crop-" + name, "timestamp": reading["timestamp"], "path": str(path),
                   "state": "gameplay" if reading["pip_row_drawn"] else "unknown",
                   "evidence": "", "confidence": 0.99}
            out.append(row)
            added += 1
        row["win_dots"] = reading["win_dots"]
        # Absent presence is not a claim of contradiction: hand-built controls and any producer that
        # predates this field keep behaving exactly as before.
        if "win_dots_drawn" in reading:
            row["win_dots_drawn"] = reading["win_dots_drawn"]
        if "win_dots_confidence" in reading:
            row["win_dots_confidence"] = reading["win_dots_confidence"]
        row["pip_row_drawn"] = reading["pip_row_drawn"]
        row["fact_source"] = (str(row.get("fact_source") or "") + "+crop_readings").lstrip("+")
    out.sort(key=lambda r: r["timestamp"])
    return out, {"added": added, "unjoinable_rows": unjoinable,
                 # Reported so a caller can see a reading refused rather than wonder where it went:
                 # an out-of-window crop is usually a sign the corpus and the window disagree.
                 "skipped_outside_window": skipped}


def read_facts(path=None, window=None):
    # type: (Optional[Any], Optional[Tuple[float, float]]) -> List[Dict[str, Any]]
    """The classified clock/state rows, in window when one is given."""
    rows = json.loads(Path(str(path or DEFAULT_FACTS)).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        rows = rows.get("observations") or []
    if window is None:
        return [dict(r) for r in rows if _finite(r.get("timestamp"))]
    return [dict(r) for r in rows
            if _finite(r.get("timestamp")) and window[0] <= float(r["timestamp"]) <= window[1]]


def grade_rows(rows):
    # type: (Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]
    """Grade every row's pip reading, and count how many were demoted.

    Counted rather than silent, because the number is the finding: a base with demoted readings is a base
    whose winners were about to be invented, and a report that quietly dropped them would read as footage
    that decided nothing.
    """
    graded, demoted = [], 0
    for row in rows:
        entry = grade_pip_row(dict(row))
        if entry.get("pip_evidence_class") == "unvalidated_pip_diagnostic":
            demoted += 1
        graded.append(entry)
    return graded, demoted


def evidence(window=None, runs=None, state_only=False, fixture=None, root=None, facts=None):
    # type: (Any, Any, bool, Optional[str], Optional[Any], Optional[Any]) -> Dict[str, Any]
    """Tracker-ready observations for a window, with the evidence base recorded beside them.

    ``state_only`` skips the pip merge on purpose. The returned dict says which base was used, so a report
    that quotes ``games_with_a_winner`` cannot silently be quoting the state-only figure: that number is
    zero on this recording whatever the footage shows, and it is the mistake this function replaces.

    ``row_records`` is the merged rows themselves (``observations`` is what the tracker takes, and is
    derived from them). The public recovery command needs the rows because a live run merges its own new
    readings back onto them; asking it to rebuild the join would be asking for the second implementation.
    """
    bound = tuple(window) if window else (0.0, float("inf"))
    facts_path = Path(str(facts or DEFAULT_FACTS))
    if state_only:
        rows_in, demoted = grade_rows(read_facts(facts_path, bound))
        return {"observations": boundary_games.build_observations(rows_in), "rows": len(rows_in),
                "row_records": rows_in, "evidence_base": STATE_ONLY, "merge": {}, "crop_runs": [],
                "crop_readings": 0, "facts": _as_seen(facts_path),
                "crop_runs_source": "state_only_comparitor", "pip_readings_demoted": demoted,
                "window": list(window) if window else None}
    paths = crop_runs(runs, fixture=fixture, root=root)
    stats = {}  # type: Dict[str, int]
    readings = crop_observations(paths, stats)
    merged, merge_stats = merge_crop_readings(read_facts(facts_path, bound), readings, bound)
    rows, demoted = grade_rows(merged)
    return {"observations": boundary_games.build_observations(rows), "rows": len(rows),
            "row_records": rows, "evidence_base": "canonical_pip_merge", "merge": dict(merge_stats),
            "crop_runs": [str(p) for p in paths], "crop_readings": len(readings),
            # In memory only, and deliberately not part of any published block: the readings themselves,
            # so a caller deciding *what still needs asking* never walks the corpus a second time and
            # cannot disagree with the merge about what is already answered.
            "crop_readings_by_frame": readings,
            # Named in the artifact, because "which runs?" is the first question a disagreeing pair of
            # numbers has to answer, and a default that discovers must not look like a deliberate choice.
            "crop_runs_source": "discovered" if not runs else "named_by_caller",
            "framings": framings([str(p) for p in paths]),
            "framing_excluded": int(stats.get("skipped_other_framing") or 0),
            "pip_readings_demoted": demoted,
            "facts": _as_seen(facts_path), "window": list(window) if window else None}


def load(window=None, rows=None, runs=None, state_only=False, fixture=None, root=None):
    # type: (Any, Optional[Sequence[Dict[str, Any]]], Any, bool, Optional[str], Optional[Any]) -> Dict[str, Any]
    """Same evidence base as :func:`evidence`, joined onto rows the caller already holds.

    The public command has already read (and region-filtered) its timeline, and a second copy of the
    windowing rule is how two tools end up disagreeing about the same recording. Passing ``rows`` reuses
    that read; passing nothing falls through to :func:`evidence`.
    """
    if rows is None:
        return evidence(window, runs=runs, state_only=state_only, fixture=fixture, root=root)
    bound = tuple(window) if window else (0.0, float("inf"))
    if state_only:
        joined, demoted = grade_rows([r for r in rows if _finite(r.get("timestamp"))
                                      and bound[0] <= float(r["timestamp"]) <= bound[1]])
        return {"observations": boundary_games.build_observations(joined), "rows": len(joined),
                "row_records": joined, "evidence_base": STATE_ONLY, "merge": {}, "crop_runs": [],
                "crop_readings": 0, "crop_runs_source": "state_only_comparitor",
                "crop_readings_by_frame": {}, "pip_readings_demoted": demoted,
                "facts": None, "window": list(window) if window else None}
    paths = crop_runs(runs, fixture=fixture, root=root)
    stats = {}  # type: Dict[str, int]
    readings = crop_observations(paths, stats)
    merged, merge_stats = merge_crop_readings(rows, readings, bound)
    joined, demoted = grade_rows(merged)
    return {"observations": boundary_games.build_observations(joined), "rows": len(joined),
            "row_records": joined, "evidence_base": "canonical_pip_merge",
            "merge": dict(merge_stats), "crop_runs": [str(p) for p in paths],
            "crop_readings": len(readings), "crop_readings_by_frame": readings,
            "crop_runs_source": "discovered" if not runs else "named_by_caller",
            "framings": framings([str(p) for p in paths]),
            "framing_excluded": int(stats.get("skipped_other_framing") or 0),
            "pip_readings_demoted": demoted,
            "facts": None, "window": list(window) if window else None}


# --------------------------------------------------------------------- adjudication over that base


def adjudicate(observations, rounds_per_game=3, games_per_set=3, header=None, readings=None,
               policy=None, region=None, source=None):
    # type: (Sequence[Dict[str, Any]], int, Any, Optional[Dict[str, Any]], Optional[Sequence[Dict[str, Any]]], Optional[Dict[str, Any]], Optional[Sequence[float]], Optional[Dict[str, Any]]) -> Dict[str, Any]
    """Run the three comparisons over one evidence base, so two tools cannot price different runs.

    ``plain`` gets the band's *stage text* but not its pairings, so the comparison against ``assisted`` is
    about who the band says is playing rather than which round it says this is -- a format policy whose
    stage labels vanish when the band does would make the two runs different formats, and "the band changed
    the grouping" would then be true for a reason nobody meant to test.

    ``unformatted`` is the same recording with no target anywhere: the distance between that column and
    ``assisted`` is what being told the rules bought, stated as a number instead of a claim.
    """
    kwargs = {"rounds_per_game": rounds_per_game, "analysis_window": tuple(region) if region else None,
              # Passed down so a candidate's leading/trailing limit is answered from the tape and the cache
              # rather than from whichever rows this query happened to receive. None is a real answer --
              # it means the caller cannot tell a window edge from a truncated recording -- and the tracker
              # then reports `source_bounds_unknown` instead of inventing one.
              "source": source}
    plain = boundary_games.track_games(observations, games_per_set=games_per_set,
                                       format_policy=policy, stage_readings=readings, **kwargs)
    assisted = boundary_games.track_games(observations, games_per_set=games_per_set, header=header,
                                          format_policy=policy, **kwargs)
    unformatted = boundary_games.track_games(observations, games_per_set=None,
                                             stage_readings=readings if policy else None, **kwargs)
    return {"plain": plain, "assisted": assisted, "unformatted": unformatted}


def grouping(sets):
    # type: (Sequence[Dict[str, Any]]) -> list
    """Only the grouping facts, so a before/after diff cannot hide an attribution change inside them."""
    return [(s["start"], s["end"], s["status"], s["membership"], s["games"], s["game_wins"],
             s["game_winners"], [link["verdict"] for link in s["links"]],
             [(a["at"], a["joined_with"]) for a in s["alternative_groupings"]])
            for s in sets]


def band_questions(sets):
    # type: (Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]
    """One record per boundary question, not per candidate that mentions it.

    A cut is visible from both sides -- each of the two candidates it separates carries it as an
    alternative -- so counting slots would report two settled questions where one was answered, and the
    arithmetic of a budget built on that count would buy the same observation twice.
    """
    seen, out = set(), []
    for record in sets:
        for entry in record["alternative_groupings"]:
            band = entry.get("band_mapping")
            if not band:
                continue
            key = (round(float(entry["at"]), 6), band.get("status"))
            if key in seen:
                continue
            seen.add(key)
            out.append({"at": entry["at"], "of_games": entry["would_merge"],
                        "reported_by_candidates": sum(
                            1 for other in sets for alt in other["alternative_groupings"]
                            if (alt.get("band_mapping") or {}).get("status") == band.get("status")
                            and alt["at"] == entry["at"]), **band})
    return out


def games_without_a_reading(games, readings):
    # type: (Sequence[Dict[str, Any]], Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]
    """Games the band never saw, in span order. One game, one entry, whatever the boundary questions say."""
    out = []
    for record in games:
        covered = any(r.get("at") is not None
                      and float(record["start"]) <= float(r["at"]) <= float(record["end"])
                      for r in readings or ())
        if covered:
            continue
        out.append({"span": [record["start"], record["end"]], "status": record["status"],
                    "winner_side": record.get("winner"),
                    "because": "no preserved band reading falls inside this game, so a win here cannot be "
                               "credited to a named participant"})
    return out


def readings_between_games(games, readings):
    # type: (Sequence[Dict[str, Any]], Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]
    """Band answers that fall outside every game: they bear on where matches change, not on who won."""
    return [{"at": r["at"],
             "sides": [r["pairing"]["left"].get("player"), r["pairing"]["right"].get("player")],
             "use": "bears on where the games and matches change, not on who won them"}
            for r in readings or ()
            if not any(float(g["start"]) <= float(r["at"]) <= float(g["end"]) for g in games)]


def missing_header_observations(assisted, readings):
    # type: (Dict[str, Any], Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]
    """The identity gaps, built from what the band could not reach rather than from a plan.

    Each uncovered mapping question names the games whose spans hold no reading, and a game the band never
    saw is a gap whether or not a boundary question happens to name it -- otherwise the price depends on
    how the cuts fell.
    """
    games = assisted["games"]
    questions = [q for q in band_questions(assisted["sets"]) if q["status"] in OPEN_STATUSES]
    missing, seen = [], set()
    for entry in questions:
        for record in games:
            if record["start"] not in (entry.get("of_games") or ()):
                continue
            key = round(float(record["start"]), 6)
            if key in seen:
                continue                      # one question, listed once
            if any(r.get("at") is not None and float(record["start"]) <= float(r["at"])
                   <= float(record["end"]) for r in readings or ()):
                continue
            seen.add(key)
            missing.append({"for_game": [record["start"], record["end"]],
                            "because": "no preserved band reading falls inside this game, so its screen "
                                       "sides name nobody",
                            "question_at": entry["at"], "status": entry["status"], "route": "header"})
    for record in games:
        key = round(float(record["start"]), 6)
        if key in seen:
            continue
        if any(r.get("at") is not None and float(record["start"]) <= float(r["at"])
               <= float(record["end"]) for r in readings or ()):
            continue
        seen.add(key)
        missing.append({"for_game": [record["start"], record["end"]],
                        "because": "no preserved band reading falls inside this game at all",
                        "question_at": None, "status": "unwatched_game", "route": "header"})
    return missing


def format_comparison(assisted, unformatted, policy=None):
    # type: (Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]]) -> Dict[str, Any]
    """What the event format bought, as a number, with the target named in the artifact."""
    return {
        "policy": (policy or {}).get("source"),
        "targets_selected_per_candidate": [
            {"start": s["start"], "targets": (s.get("applicable_formats") or {}).get("targets"),
             "basis": (s.get("applicable_formats") or {}).get("basis")} for s in assisted["sets"]],
        # What the format column is actually for. It is not the boundary: the station changes on tape
        # conclude those sets with no target supplied at all, and the same closures appear in both
        # columns. What the format buys is a score read as a score -- a published target, and a winner
        # named instead of a seat.
        "note": "an unknown target never vetoes a conclusion the recording supports on its own terms. "
                "The target-free column is what the footage says with no rules supplied; where the two "
                "columns agree on a closure, the closure is the recording's, not the format's.",
        "with_format": _column(assisted),
        "without_any_target": _column(unformatted),
        # Renamed from ``closed_by_the_recording_alone`` on 2026-11-02. That key claimed a set was closed
        # at a moment when what the recording showed was an end, and what the pipeline did was propose a
        # cut there. The structure evaluation over the same footage keeps every grouping of the games
        # open, and "closed" beside "every grouping open" is a report that contradicts itself -- so the
        # key was part of the overclaim. Beside it now sits the only route to a completed set: a target,
        # reached.
        "cuts_proposed_by_the_recording_alone": [
            {"start": s["start"], "end": s["end"], "basis": s["basis"],
             "deciding_wins": s["deciding_wins"], "concluded_by": s.get("concluded_by"),
             "end_transition_observed": s.get("end_transition_observed"),
             "set_completion": s.get("set_completion")}
            for s in assisted["sets"] if s["status"] == "complete"
            and "target_reached" not in (s.get("concluded_by") or [])],
        "sets_completed_by_a_confirmed_target": [
            {"start": s["start"], "end": s["end"], "deciding_wins": s["deciding_wins"],
             "set_winner": s.get("set_winner"), "membership": s.get("membership")}
            for s in assisted["sets"]
            if (s.get("set_completion") or {}).get("state") == "confirmed"],
        "closed_under_the_format": [
            {"start": s["start"], "end": s["end"], "deciding_wins": s["deciding_wins"],
             "complete_under": s["complete_under"], "membership": s["membership"],
             "concluded_by": s.get("concluded_by"), "set_winner": s["set_winner"]}
            for s in assisted["sets"] if s["status"] == "complete"],
        "left_open_under_every_applicable_target": [
            {"start": s["start"], "targets": (s.get("applicable_formats") or {}).get("targets"),
             "deciding_wins": s["deciding_wins"], "why": s["basis"],
             "end_state": ((s.get("edges") or {}).get("end") or {}).get("state")}
            for s in assisted["sets"] if s["status"] != "complete"],
    }


def _column(tracked):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    return {"sets": len(tracked["sets"]),
            "complete": sum(1 for s in tracked["sets"] if s["status"] == "complete"),
            "winners_named": sum(1 for s in tracked["sets"] if s["set_winner"]),
            "targets_published": sum(1 for s in tracked["sets"] if s["games_per_set_expected"])}


def candidate_records(assisted, plain=None):
    # type: (Dict[str, Any], Optional[Dict[str, Any]]) -> List[Dict[str, Any]]
    """One record per candidate, with boundary, score, footage and identity kept as separate claims."""
    out = []
    for index, s in enumerate(assisted["sets"]):
        out.append({"start": s["start"], "end": s["end"], "games": s["games"],
                    "status": s["status"], "membership": s["membership"],
                    "game_wins": s["game_wins"],
                    "deciding_wins": s["deciding_wins"],
                    "deciding_wins_unit": s["deciding_wins_unit"],
                    "set_winner": s["set_winner"], "winner_seat": s.get("winner_seat"),
                    "winner_unit": s.get("winner_unit"),
                    # The target this candidate was measured against, or nothing. Published because
                    # "complete" without it would not say whether the rules or the recording closed it.
                    "games_per_set_expected": s.get("games_per_set_expected"),
                    # Boundary and score are separate claims: this is how a reader tells "the recording
                    # shows the set ending here" from "a number in the format policy was satisfied".
                    "concluded_by": s.get("concluded_by"),
                    "boundary_confirmed": s.get("boundary_confirmed"),
                    # The three claims a station clear earns, published apart so `status: complete` cannot
                    # be read as "a set is confirmed to have ended here": what the tape showed (an end),
                    # what the pipeline did with that (a cut), and what would take it to a completion (a
                    # tally reaching a target). On the executed unseen trial all eight groupings of the
                    # four games stay open, and a recovery block that said otherwise contradicted the
                    # structure report sitting in the same directory.
                    "end_transition_observed": s.get("end_transition_observed"),
                    "cut_proposed_by_pipeline": s.get("cut_proposed_by_pipeline"),
                    "set_completion": s.get("set_completion"),
                    # Two different questions, and the second is not a consequence of the first: a
                    # candidate can reach its target and still contain a game boundary the recording reads
                    # two ways, so uncertainty may never be derived from completion alone.
                    "grouping_unresolved": bool(s.get("boundary_conflicts")) or (
                        (s.get("set_completion") or {}).get("state") != "confirmed"),
                    "boundary_conflicts": s.get("boundary_conflicts") or [],
                    "alternative_groupings": s.get("alternative_groupings") or [],
                    "footage_complete": s.get("footage_complete"),
                    "edges": s.get("edges"),
                    # The cut, as opposed to the boundary: where the clip ends is the last conclusion
                    # plus configured padding, and a marking fault that moved a boundary would move this
                    # with it. Published so the claim can be checked instead of re-derived.
                    "clip_end_at": s.get("clip_end_at"), "clip_end_basis": s.get("clip_end_basis"),
                    # The number alone cannot be checked: which claim set it (conclusion or confirmation),
                    # what band is still open around it, and how much tape sat behind it are what make a
                    # clip end auditable instead of authoritative. `_set_record` supplied these and they
                    # were being dropped here.
                    "clip_end_anchor": s.get("clip_end_anchor"),
                    "clip_uncertainty": s.get("clip_uncertainty"),
                    # Why this status, and what would change it. A reviewer who disagrees with a closure
                    # has to be able to read the argument, not re-derive it from the module.
                    "basis": s["basis"], "missing_evidence": s["missing_evidence"],
                    "attribution_without_band": (plain["sets"][index]["attribution"]["unit"]
                                                 if plain else None),
                    "attribution_with_band": s["attribution"]["unit"],
                    "game_wins_by_identity": s["attribution"].get("game_wins_by_identity"),
                    "attribution_withheld": s.get("attribution_withheld"),
                    "participant_hypotheses": s.get("participant_hypotheses"),
                    "applicable_formats": s.get("applicable_formats"),
                    "complete_under": s.get("complete_under"),
                    "open_under": s.get("open_under"),
                    "format_dependent": s.get("format_dependent"),
                    "links": [{"at": l.get("at"), "verdict": l.get("verdict"),
                               "identity": l.get("identity"),
                               "seat_continuity": l.get("seat_continuity"),
                               "transition": l.get("transition"),
                               "positively_supported": l.get("positively_supported"),
                               "reasons": l.get("reasons")} for l in s["links"]],
                    "cut_mapping_resolution": s["cut_mapping_resolution"]})
    return out


def recovery(evidence_base, tracked, header=None, policy=None, games_with_a_winner=None):
    # type: (Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[int]) -> Dict[str, Any]
    """The set-level result of one evidence base, in the shape every tool reports it.

    Both the read-only report and the public command call this, so a candidate listed by one cannot disagree
    with the other about what it was: the evidence block, the band's mapping questions, the identity gaps,
    the format comparison and the candidates all come from the same functions.
    """
    assisted, plain, unformatted = tracked["assisted"], tracked["plain"], tracked["unformatted"]
    readings = (header or {}).get("readings")
    asked = band_questions(assisted["sets"]) if header else []
    settled = [q for q in asked if q["status"] in SETTLED_STATUSES]
    open_questions = [q for q in asked if q["status"] in OPEN_STATUSES]
    winners = [g for g in assisted["games"] if g.get("counts_toward_set") and g.get("winner")]
    between = readings_between_games(assisted["games"], readings or ())
    return {
        "kind": "set-recovery",
        "inference_sent": False,
        "attempts_charged_now": 0,
        "region": (list(evidence_base.get("window")) if evidence_base.get("window") else None),
        "rows_used": evidence_base.get("rows"),
        "format": assisted["format"],
        "pip_evidence": {
            "base": evidence_base["evidence_base"], "facts": evidence_base.get("facts"),
            "corpus": evidence_base.get("crop_runs_source"),
            "crop_runs": evidence_base["crop_runs"],
            "crop_runs_source": evidence_base.get("crop_runs_source"),
            "framings": evidence_base.get("framings"),
            "crop_readings_merged": evidence_base["crop_readings"],
            "merge": evidence_base["merge"],
            "framing_excluded": evidence_base.get("framing_excluded"),
            "note": "winners come from crop readings; the state timeline alone concludes "
                    "games with no winner, so a winner count quoted from it is not evidence",
            "attribution_requires_crop_readings": True},
        "accounting_without_band": plain["accounting"],
        "accounting_with_band": assisted["accounting"],
        "grouping_changed_by_band": (None if header is None
                                     else grouping(plain["sets"]) != grouping(assisted["sets"])),
        "format_comparison": format_comparison(assisted, unformatted, policy),
        "candidates": candidate_records(assisted, plain),
        "band_mapping_questions": {
            "asked": len(asked), "settled": len(settled), "open": len(open_questions),
            "not_a_mapping_question": sum(1 for q in asked if q["status"] == "no_mapping_question"),
            "settled_at": [q["at"] for q in settled],
            "open_at": [q["at"] for q in open_questions]} if header else None,
        "band_mapping_question_records": asked,
        "band_readings_inside_a_game": (len(readings or ()) - len(between)) if header else None,
        "band_readings_between_games": between,
        "games_with_a_winner": (len(winners) if games_with_a_winner is None
                                else games_with_a_winner),
        "nothing_is_attributable_when_no_game_has_a_winner": not winners,
        "games_needing_conclusion_evidence": [
            {"span": [g["start"], g["end"]], "status": g["status"], "winner": g.get("winner")}
            for g in assisted["games"] if not (g.get("counts_toward_set") and g.get("winner"))],
        "games_without_a_band_reading": games_without_a_reading(assisted["games"], readings or ())
                                       if header else [],
        "missing_observations": missing_header_observations(assisted, readings or ()) if header else [],
        "claims_kept_separate": {
            "boundary": "where the recording shows a set starting and ending",
            "score": "the wins counted per candidate, on screen sides or on people",
            "winner": "who holds the set, which needs a target as well as identified wins",
            "footage": "whether the candidate is complete film or cut by an edge",
            "note": "a candidate may be complete on one of these and open on another; reporting one as "
                    "though it settled all four is the overreach this block exists to prevent"},
    }
