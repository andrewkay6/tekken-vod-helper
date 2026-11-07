#!/usr/bin/env python3
"""Evaluate a consecutive-game trial through the canonical merge and tracker, and say what is left open.

This is not the historical revalidation: it takes the *trial's own* readings -- crop-arm group files --
through ``validate_game_one.merged_rows`` over the trial's frozen interval and into
``boundary_games.track_games``, the same path production uses. A result here is a result about the
pipeline, not about a hand-assembled summary. Which interval, which manifest and which frames are all
read from ``<trial>/selected-frames.json``: nothing about one particular trial is baked into this file,
because an evaluator that hard-codes its own subject can only ever confirm it. Three rules are enforced
rather than trusted:

* only saved crop readings **inside the interval** are merged, so a crop read at 55 s cannot walk in and
  turn another experiment's game into this trial's coverage;
* old full-frame pip values stay quarantined by that merge -- the crop arm is the only framing this
  project has validated for the dot rows, and re-asking in the quarantined framing would smuggle the
  bad reading back in as corroboration;
* a frame is named by the manifest's id, path and timestamp, never by arithmetic on a second.

Three things are reported separately, because collapsing them is how a bad run looks like a good one:

* **tracking** -- were the rounds, the game boundaries and the game conclusions recovered;
* **perception** -- of the frames the evaluator can read, which counts were right, and which
  disagreements with the frozen labels stay reported as disagreements rather than being decided here;
* **resources** -- attempts charged against the shared ledger, and the tokens provenance recorded.

The frozen labels are opened only by the scoring step, and only once readings exist; nothing here
schedules, prompts or reconstructs from them. A game with insufficient evidence stays unresolved: there
is no target number of games and no winner to be found by trying harder.

    # before sending: what the trial sees with only the readings already on disk
    env PYTHONPATH=. .venv/bin/python scripts/evaluate_consecutive_game_trial.py \\
        --trial build/work/bb9/consecutive-games-v3 --dry-run

    # after sending: tracking, perception, resources, and a crop-framed follow-up plan
    env PYTHONPATH=. .venv/bin/python scripts/evaluate_consecutive_game_trial.py \\
        --trial build/work/bb9/consecutive-games-v3 --plan-follow-up
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import validate_game_one as vg                                        # noqa: E402
from tekken_vod_helper import boundary_games as bg                    # noqa: E402
from tekken_vod_helper import boundary_pips as bp                     # noqa: E402
from tekken_vod_helper import boundary_windows as bw                  # noqa: E402

EVIDENCE_ROOT = ROOT / "build/work/bb9"
DEFAULT_TRIAL = EVIDENCE_ROOT / "consecutive-games-v2"
ROUNDS_PER_GAME, GAMES_PER_SET = 3, 3


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class Trial:
    """One frozen trial, read from its own plan rather than from constants in this file.

    The interval, the frame manifest and the requested frames all come from ``selected-frames.json``,
    because that is the artifact the run was actually prepared from. The previous revision hard-coded
    ``[226, 430)`` here *and* recomputed frame numbers as ``timestamp / 1.000986`` -- arithmetic on a grid
    the manifest already answers exactly, which invents a second, slightly-wrong identity for a frame and
    cannot express a frame that is missing from the cache at all.

    ``start`` and ``end`` override the plan's interval only when the operator passes them, and the
    override is recorded, so a replay that quietly moved the bound is visible in the artifact.
    """

    def __init__(self, directory, start=None, end=None):
        # type: (Any, Optional[float], Optional[float]) -> None
        self.dir = Path(directory)
        plan_path = self.dir / "selected-frames.json"
        if not plan_path.is_file():
            raise SystemExit("no frozen plan to evaluate at {}: prepare the trial first".format(plan_path))
        self.plan = load(plan_path)
        interval = self.plan.get("interval") or {}
        if start is None and end is None:
            if "start_at" not in interval or "end_at" not in interval:
                raise SystemExit("{} records no interval and none was given".format(plan_path))
            start, end = interval["start_at"], interval["end_at"]
            self.moved_bounds = None
        else:
            self.moved_bounds = {"from": [interval.get("start_at"), interval.get("end_at")],
                                 "to": [start, end]}
        self.start, self.end = float(start), float(end)
        self.manifest = (self.plan.get("manifest") or {}).get("path") or bw.DEFAULT_MANIFEST
        self.frames = bw.FrameIndex.load(self.manifest)
        self.requested_paths = {entry["frame"] for entry in self.plan.get("requested") or []}

    def requested_ids(self):
        # type: () -> set
        """Manifest ids of the primary pass, straight from the plan (never recomputed from time)."""
        return {entry["id"] for entry in self.plan.get("requested") or [] if "id" in entry}


def crop_run_dirs():
    """Every directory on disk that holds crop answers, wherever they were produced.

    Reuse has to see all of them: a reading the trial could borrow but does not know about is a reading
    that gets re-asked and re-charged, and one outside the interval is evidence about a different
    experiment. Both cases are handled by loading everything and filtering by instant -- which is also
    why the list is discovered rather than hard-coded, since a hard-coded list silently loses a reading
    the day someone runs one more experiment.

    A run directory is whatever holds a ``crops/groups`` tree, one or two levels under the fixture root
    (``pip-crops-v2`` and ``game1-validate-v1/run`` are both real shapes here).
    """
    found = []
    for groups in list(EVIDENCE_ROOT.glob("*/crops/groups")) + \
            list(EVIDENCE_ROOT.glob("*/*/crops/groups")):
        run = groups.parent.parent
        if run != EVIDENCE_ROOT:
            found.append(str(run.relative_to(ROOT)))
    return [p for p in dict.fromkeys(found) if (ROOT / p).is_dir()]


def trial_run_dirs(trial):
    # type: (Trial) -> List[str]
    found = [str(p) for p in (trial.dir.iterdir() if trial.dir.is_dir() else [])
             if p.is_dir() and (p / "crops" / "groups").is_dir()]
    return sorted(found)


def window_observations(trial, run_dirs, stats=None):
    # type: (Trial, Sequence[str], Optional[dict]) -> Dict[str, Any]
    """Crop-arm readings inside the trial interval, and only those.

    Two things keep this corpus honest, and they are the same mistake in different clothes: a reading
    from outside the interval would count as coverage the trial never asked for (a 55 s crop from the
    accepted game-1 validation joined the trial once), and a reading of the same instant in another
    framing would become a second row at that instant, which the tracker reads as the pips moving. The
    first is filtered here; the second is refused by ``crop_observations``, whose count goes to ``stats``
    so the run reports the exclusion instead of leaving it implied.
    """
    observations = vg.crop_observations([Path(d) for d in run_dirs], stats=stats)
    return {frame: meta for frame, meta in observations.items()
            if isinstance(meta.get("timestamp"), (int, float))
            and trial.start <= meta["timestamp"] < trial.end}


def tracking_rows(trial, run_dirs):
    # type: (Trial, Sequence[str]) -> Any
    stats = {}  # type: Dict[str, Any]
    observations = window_observations(trial, run_dirs, stats)
    rows, joined = vg.merged_rows(observations, (trial.start, trial.end))
    joined["skipped_other_framing"] = stats.get("skipped_other_framing", 0)
    return rows, joined, observations


def sha256_of(path):
    # type: (Path) -> str
    """The freeze's own digest, so a score names the bytes it was computed against."""
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def reconcile_with_report(trial, described, format_policy=None):
    # type: (Trial, Dict[str, Any], Optional[Dict[str, Any]]) -> Dict[str, Any]
    """Where this trial's own tracking and the canonical set report do not agree, say so in seconds.

    The two tools read different corpora on purpose: the trial evaluates the readings it asked for inside
    the interval it was run on, and the report merges every crop answer discovered on disk. That is not a
    bug to average away, and it is not a equality to assert either -- when one tool's window holds a round
    ending the other's does not, the game ends move, and a document that calls both "4 games" hides which
    instant each one actually committed to.

    So the differences are listed per game: this trial's end, the report-scope end, and the gap. An empty
    list is a real result, not a missing comparison, and it is labelled as such.
    """
    runs = [str(ROOT / name) for name in crop_run_dirs()]
    runs = [r for r in runs if Path(r).is_dir()]
    rows, _, _ = tracking_rows(trial, runs)
    other = bg.track_games(bg.build_observations(rows), rounds_per_game=ROUNDS_PER_GAME,
                           games_per_set=GAMES_PER_SET, format_policy=format_policy)
    mine = {round(float(g["start"]), 3): g for g in described["games"]}
    theirs = {round(float(g["start"]), 3): {"end": g.get("end"), "winner": g.get("winner"),
                                            "status": g.get("status")}
              for g in other["games"]}
    differences = []
    for key in sorted(set(mine) | set(theirs)):
        a, b = mine.get(key), theirs.get(key)
        if a is None or b is None:
            differences.append({"game_opening": key, "this_trial": bool(a), "report_scope": bool(b),
                                "difference": "the game exists in one corpus and not the other"})
            continue
        for field in ("end", "winner", "status"):
            if a.get(field) != b.get(field):
                differences.append({"game_opening": key, "field": field,
                                    "this_trial": a.get(field), "report_scope": b.get(field),
                                    "seconds_apart": (round(float(a[field]) - float(b[field]), 6)
                                                      if field == "end" else None)})
    return {"corpora": {"this_trial_reads": len(trial_run_dirs(trial)),
                        "report_scope_reads": len(runs)},
            "games_this_trial": len(mine), "games_report_scope": len(theirs),
            "differences": differences,
            "equal": not differences,
            "note": "a difference here is two questions answered over two corpora, not one of them being "
                    "wrong; neither number may be quoted as the other's"}


def describe_tracking(trial, rows, joined, ledger, observations, format_policy=None, report=None):
    # type: (Trial, Sequence[Dict[str, Any]], Dict[str, Any], Dict[str, Any], Dict[str, Any], Optional[Dict[str, Any]], Optional[Dict[str, Any]]) -> Dict[str, Any]
    result = bg.track_games(bg.build_observations(rows), rounds_per_game=ROUNDS_PER_GAME,
                            games_per_set=GAMES_PER_SET, format_policy=format_policy)
    faults, provisional_ends = classify_tracking_errors(result)
    tracking = {"faults": faults, "provisional_ends": provisional_ends}
    # Every live pip value has to trace to a crop reading at that instant. The merge files the values it
    # displaced under superseded_full_frame_win_dots, but an appended crop row can carry crop values with
    # no source marker of its own, so "is there a crop reading here?" is the question that matters.
    crop_times = [meta["timestamp"] for meta in observations.values()
                  if isinstance(meta.get("timestamp"), (int, float))]
    def live(row):
        # [None, None] is an abstention, not a pip value: only a real count could be stale evidence.
        return any(value is not None for value in (row.get("win_dots") or []))
    untraceable = [round(r["timestamp"], 3) for r in rows if live(r)
                   # a row can be joined to a crop seconds away by the merge's snapping; allow that
                   and not any(abs(r["timestamp"] - stamp) < 1.002 for stamp in crop_times)]
    games = []
    for game in result["games"]:
        rounds = [r for r in result["rounds"] if r["id"] in game["round_ids"]]
        first = rounds[0] if rounds else {}
        last = rounds[-1] if rounds else {}
        games.append({
            "start": game["start"], "end": game["end"], "rounds": game["rounds"],
            "status": game["status"], "basis": game["basis"], "winner": game["winner"],
            "winner_basis": game["winner_basis"], "contested": game["contested"],
            "contested_reasons": game["contested_reasons"],
            "dot_regressions": game["dot_regressions"],
            "counts_toward_set": game["counts_toward_set"],
            # A partial is not a guess about where the game began: it is the tracker saying this
            # round's start was inferred because play was already underway when the interval opened.
            "leading_partial": bool(first.get("start_inferred")),
            "leading_evidence": first.get("start_basis"),
            "trailing_partial": last.get("end_basis") in (None, "recording_or_gap_edge"),
            "trailing_evidence": last.get("end_basis"),
            # Where the game stops, carried as five separate claims rather than folded into `end`:
            # the conclusion event, the first row confirming who took it, the transition screen, the
            # bounds on the conclusion when no end was observed, and what a clip would have to contain.
            # `end` may legitimately precede the confirmation -- the K.O. stops the clock, the pip
            # arrives with the victory pose -- and saying so is the point of keeping them apart.
            "conclusion_event_at": game.get("conclusion_event_at"),
            "conclusion_evidence": game.get("conclusion_evidence"),
            "winner_confirmed_at": game.get("winner_confirmed_at"),
            "winner_confirmation_lag_seconds": game.get("winner_confirmation_lag_seconds"),
            "transition_at": game.get("transition_at"),
            "transition_evidence": game.get("transition_evidence"),
            "end_uncertainty": game.get("end_uncertainty"),
            "end_conflict": game.get("end_conflict"),
            # observed / bounded / conflicting / open, and whether the bound may be quoted as a cut
            # point yet. A bounded end is evidence-backed but still not what the recording showed.
            "end_status": game.get("end_status"),
            "provisional_end": game.get("provisional_end"),
            "boundary_certain": game.get("boundary_certain"),
            "clip_end_at": game.get("clip_end_at"),
            "clip_end_basis": game.get("clip_end_basis"),
            "last_observed_play_at": game.get("last_observed_play_at"),
            "round_windows": [{"end": r.get("end"), "end_basis": r.get("end_basis"),
                               "last_clock_at": r.get("last_clock_at"),
                               "clock_readings": r.get("clock_readings"),
                               "win_dots_seen": r.get("win_dots_seen")} for r in rounds]})
    by_time = sorted({r["timestamp"]: r.get("state") for r in rows}.items())
    boundaries = []
    ordered = sorted((g for g in result["games"] if g["end"]), key=lambda g: g["start"])
    for earlier, later in zip(ordered, ordered[1:]):
        screens = {state for stamp, state in by_time
                   if earlier["end"] < stamp < later["start"]} - {"unknown", "gameplay", "stage_intro"}
        boundaries.append({"between": [earlier["end"], later["start"]],
                           "screens": sorted(screens),
                           "stage_or_character_select_present": bool(
                               screens & {"stage_select", "character_select"}),
                           "set_meaning": "not decided here: a stage or character select can occur "
                                          "within a set, so neither its presence nor its absence makes "
                                          "a new set"})
    return {
        "generated_at": now(), "fixture": "bb9", "trial_dir": str(trial.dir),
        "window": [trial.start, trial.end],
        "identity_source": {"plan": str(trial.dir / "selected-frames.json"),
                            "manifest": trial.manifest,
                            "frame_ids": "the manifest's own; no timestamp is converted into a frame "
                                         "number anywhere in this evaluation"},
        "format": {"rounds_per_game": ROUNDS_PER_GAME, "games_per_set": GAMES_PER_SET,
                   "source": ("stage-specific from the event format, per candidate" if format_policy
                              else "configured; never inferred from how many rounds were seen"),
                   "policy": ({k: format_policy[k] for k in ("default_games_to_win", "overrides", "source")}
                              if format_policy else None)},
        "rows_in_window": len(rows), "merge": joined, "rounds": len(result["rounds"]),
        # The review asked for the quarantine to be visible in the trial's own record, not assumed from
        # the merge helper's behaviour: these are the rows where the timeline's full-frame dot values
        # were set aside in favour of crop readings, and the count of rows still carrying live
        # full-frame dots has to be zero.
        "full_frame_dots_quarantined": sum(1 for r in rows if r.get("superseded_full_frame_win_dots")),
        "pip_values_not_from_crops": untraceable,
        "crop_readings_merged": len(observations),
        "games": games, "game_boundaries": boundaries,
        "readings_set_aside": [
            {"side": q.get("side"), "value": q.get("value"), "at": q.get("at"),
             "confidence": q.get("confidence"), "supported_by": q.get("supported_by"),
             "reason": q.get("reason"), "raw_copy_retained": True}
            for rnd in result["rounds"] for q in (rnd.get("win_dots_rejected") or [])],
        "unresolved": [{k: value for k, value in item.items()
                        if k in ("type", "at", "side", "from", "to", "count", "of",
                                 "example_timestamps", "reason", "would_resolve", "kinds",
                                 # the boundary claims: which end is a bound, how wide, and what
                                 # evidence sits earlier than the clock the bound came from
                                 "end_bound_high", "seconds_unwatched", "because",
                                 "end_status", "seconds_wide", "bounded_by",
                                 "tape_after_conclusion_seconds", "clock_frozen_after_end",
                                 "next_round_start", "round_id", "end_basis",
                                 "evidence_earlier_than_clock", "claim_kept", "winner",
                                 "next_game_start", "anchor", "round_start", "game_start")}
                       for item in result["unresolved"]],
        "accounting": result["accounting"],
        # The set candidates the game layer can now attribute, with the evidence each one rests on. No
        # set is concluded by this trial, but "not concluded" and "not even attributed" are different
        # claims and only the first one used to be recorded.
        "sets": [{"start": s["start"], "end": s["end"], "games": s["games"],
                  "status": s["status"], "membership": s["membership"],
                  "game_wins": s["game_wins"], "game_winners": s["game_winners"],
                  "clip_end_at": s["clip_end_at"], "clip_end_basis": s["clip_end_basis"],
                  "closed_by_target": s["closed_by_target"],
                  "identities": s["identities"], "links": s["links"],
                  "cut_before": s["cut_before"], "cut_after": s["cut_after"],
                  "missing_evidence": s["missing_evidence"]}
                 for s in result["sets"]],
        "set_status": ("no set concluded by this trial: every candidate is attributed and partial, so "
                       "none is counted, completed or predicted"),
        "ledger": ledger,
        "clip_policy": result.get("clip_policy"),
        "tracking_faults": tracking["faults"],
        # Ends the recording bounded but never showed. Not faults, not export-ready either.
        "provisional_ends": tracking["provisional_ends"],
    }


def classify_tracking_errors(result):
    """Failures of the model of the recording, kept apart from failures of the eyes.

    An empty list used to mean only "no rule was broken", and it was read as "the boundaries are right".
    Those are different claims: the executed trial reported four clean games while game 1's end was a
    camera dwell 215.2 s past the row that decided it. The boundary checks below are what was missing,
    and the distinction they draw is the one the review asked for -- a K.O. two seconds before the pip
    update is legitimate and stays unreported, while an end that trails its own conclusion evidence is a
    fault even though the winner it carries is sound.
    """
    faults, provisional = [], []
    for game in result["games"]:
        for side, movement in (game.get("dot_regressions") or {}).items():
            faults.append({"kind": "win_dots_regressed_inside_one_game", "side": side,
                           "from": movement["from"], "to": movement["to"], "at": movement["at"],
                           "meaning": "a pip row fell inside one game: either a reading is wrong or two "
                                      "games were grouped as one, and the frames in hand cannot say which"})
        if game["contested"] and game["winner"] is not None:
            faults.append({"kind": "contested_game_named_a_winner", "start": game["start"]})
        if not game.get("boundary_certain", True):
            conflict = game.get("end_conflict") or {}
            faults.append({
                "kind": "game_end_not_evidenced", "start": game["start"],
                "reported_end": game.get("end"),
                "end_status": game.get("end_status"),
                "conclusion_evidence": game.get("conclusion_evidence"),
                "winner_confirmed_at": game.get("winner_confirmed_at"),
                "transition_at": game.get("transition_at"),
                "unwatched_seconds": (game.get("end_uncertainty") or {}).get("seconds_unwatched"),
                "seconds_late_after_evidence": conflict.get("seconds_late_after_evidence"),
                "winner_still_claimed": game.get("winner"),
                "meaning": "the end is neither observed nor pinned by the recording's own conclusion "
                           "evidence; the winner stands on its own pip readings, so this is a boundary "
                           "gap to close with frames and not a reason to withdraw the result"})
        elif game.get("end_status") == "bounded":
            # Not a fault -- the recording did bound the conclusion -- but not a timestamp either, and
            # a report that calls every non-fault boundary "resolved" is what this whole file exists to
            # stop. Provisional ends are listed so a reviewer can see what may not be cut on yet.
            provisional.append({
                "start": game["start"], "end": game.get("end"),
                "bounded_by": (game.get("end_uncertainty") or {}).get("bounded_by"),
                "seconds_wide": (game.get("end_uncertainty") or {}).get("seconds_wide"),
                "tape_after_conclusion_seconds": (game.get("end_uncertainty") or {}).get(
                    "tape_after_conclusion_seconds"),
                "frozen_clock_readings_after_last_fall": ((game.get("end_uncertainty") or {})
                                                          .get("live_bound") or {}).get(
                    "frozen_readings_after"),
                "winner": game.get("winner")})
    return faults, provisional


def perception_errors(trial, run_dirs, labels):
    """Score the trial's crop readings against the frozen evaluator file, after inference only."""
    readings = []
    for directory in run_dirs:
        results = sorted(Path(directory).glob("result-*.json"))
        for path in results:
            payload = load(path)
            for observation in payload.get("observations") or []:
                entry = dict(observation)
                entry["frame"] = entry.get("frame") or ""
                readings.append(entry)
    scored = bp.score_readings(readings, labels["frames"])
    asked = {Path(entry["frame"]).name for entry in trial.plan["requested"]}
    answered = {Path(r["frame"]).name for r in readings}
    # Two different claims were being made by one word. A frame whose row is *known not to be drawn* is
    # graded -- the model is asked whether a row exists, and "no" is right or wrong. A frame nobody could
    # read at all is not. The freeze's own ``graded`` flag mixes them (16 frames, of which 12 are known
    # absences), so the split here is read off the content and the declared list is kept beside it,
    # labelled as declared rather than silently corrected in the frozen file.
    declared_ungraded = sorted(Path(f["frame"]).name for f in labels["frames"] if not f["graded"])
    unknown_truth = sorted(Path(f["frame"]).name for f in labels["frames"]
                           if f.get("row_drawn") is None
                           # `not (None, None)` is False -- a two-tuple of Nones is a non-empty tuple, and
                           # a truthiness test on one quietly classified every unreadable frame as known.
                           and not any(f["pips"].get(side) is not None for side in ("LEFT", "RIGHT")))
    known_absent = sorted(Path(f["frame"]).name for f in labels["frames"] if f.get("row_drawn") is False)
    return {
        "generated_at": now(), "run_dirs": run_dirs, "readings": len(readings),
        "scored": scored,
        "perception_errors": {
            "frames_wrong": [f for f in scored["per_frame"] if not f["correct"]],
            "error_kinds": scored["error_kinds"],
            "requests_never_answered": sorted(asked - answered),
            "no_answer_count": scored.get("no_answer_count")},
        "not_perception": {
            "excluded_unknown_truth": unknown_truth,
            "declared_ungraded_in_the_freeze": declared_ungraded,
            "scored_for_correct_absence": known_absent,
            "note": "a frame the evaluator cannot read is not a frame the model failed; counting it "
                    "either way would grade the labeler's eyes, not the model's. A frame labelled as "
                    "having no row drawn is a different thing: its absence is a claim the model can get "
                    "right, so it is a denominator and not an exclusion."},
        "accuracy_is_reportable": scored["accuracy_is_reportable"],
        "separation": "perception_errors are about pixels: wrong count, wrong side, invented score, "
                      "unanswered request. tracking_faults are about the recording model: rounds, "
                      "grouping, and a pip row falling inside one game."}


def follow_up_plan(trial, described, attempts_left, already_read=None):
    # type: (Trial, Dict[str, Any], int, Optional[Sequence[str]]) -> Dict[str, Any]
    """Ask again in the validated crop framing, and defer everything else out loud.

    Two kinds of open question arrive here and they must not be conflated. A question about the **pip
    row** can only be followed up with crops -- full-frame pip values are the measurement this project
    already quarantined, so re-asking in that framing would smuggle the bad reading back in as
    corroboration. A question about something else (the clock digits, play before the interval, the set
    layer) is answered here only to be *deferred with its reason*, because dropping it silently is the
    failure the standing review names.

    Frames are named the way the rest of the pipeline names them: the manifest is asked which frame
    covers an instant, and its id, path and timestamp are what get written. Deriving an index from the
    timestamp and formatting it back into a filename was the identity bug this replaced -- it rounds a
    lookup into a guess, drifts where the cache is not exactly 1 s apart, and plans a request for a frame
    that may not exist.
    """
    read = set(already_read or []) | trial.requested_paths
    pip_questions = ("win_dots_never_read", "win_dots_regressed", "game_winner_unobserved",
                     "round_end_not_observed")
    wanted, other, skipped = [], [], []
    seen = {Path(p).name for p in read}
    planned = set()
    for item in described["unresolved"]:
        at = item.get("at")
        if at is None:
            examples = item.get("example_timestamps") or []
            at = examples[0] if examples else None
        if item["type"] not in pip_questions:
            other.append({"type": item["type"], "at": item.get("at"),
                          "why_not_asked_with_crops":
                              "this is not a question about the pip row, and the crop arm is only "
                              "evidence about a pip row",
                          "would_resolve": item.get("would_resolve")})
            continue
        if not isinstance(at, (int, float)):
            other.append({"type": item["type"], "at": None,
                          "why_not_asked_with_crops": "the tracker named no timestamp to look at",
                          "would_resolve": item.get("would_resolve")})
            continue
        for offset in (1.0, 3.0, 5.0):
            frame = trial.frames.at_or_after(at + offset)
            if frame is None:
                other.append({"type": item["type"], "at": at,
                              "why_not_asked_with_crops":
                                  "the manifest has no frame at or after the tracker's timestamp: the "
                                  "cache ends, so there is nothing to ask",
                              "would_resolve": item.get("would_resolve")})
                break
            if not (trial.start <= frame["timestamp"] < trial.end):
                if frame["timestamp"] < trial.start:
                    other.append({"type": item["type"], "at": at,
                                  "why_not_asked_with_crops":
                                      "the frames that would settle it are before the frozen interval's "
                                      "leading bound, so asking them would move the experiment",
                                  "would_resolve": item.get("would_resolve")})
                else:
                    other.append({"type": item["type"], "at": at,
                                  "why_not_asked_with_crops":
                                      "the manifest frame at or after the tracker's timestamp is past "
                                      "the frozen interval's trailing bound",
                                  "would_resolve": item.get("would_resolve")})
                continue
            if Path(frame["path"]).name in planned:
                continue                      # this pass already chose it for another question
            if Path(frame["path"]).name in seen:
                skipped.append(dict(frame, because_of=item["type"]))
                continue
            seen.add(Path(frame["path"]).name)
            planned.add(Path(frame["path"]).name)
            wanted.append(dict(frame, because_of=item["type"]))
    fits = wanted[:max(0, int(attempts_left))]
    if fits:
        reason = "{} pip question(s) have an unanswered frame inside the interval and the allowance " \
                 "covers {} of them".format(len(wanted), len(fits))
    elif wanted:
        reason = "the pip questions named frames, but the shared allowance is spent: they are deferred " \
                 "by name rather than dropped"
    else:
        reason = "no pip-row question is left with an unanswered frame inside the interval; what stays " \
                 "open is of another kind and is listed in deferred_as_other_kind, so no attempt is " \
                 "justified and unspent budget is the honest end state"
    return {"framing": "crops arm only: the validated 4x LEFT/RIGHT geometry. Full-frame pip values "
                       "stay quarantined and are not offered as corroboration.",
            "requests_planned": len(fits), "images_planned": 2 * len(fits),
            "attempt_allowance_considered": int(attempts_left),
            "reason_no_further_attempt_was_justified": reason,
            "identity": "frame id, path and timestamp are the manifest's own, looked up at "
                        "{}; nothing here converts a second into a frame number".format(trial.manifest),
            "frames": [{"frame": frame["path"], "id": frame["id"], "timestamp": frame["timestamp"],
                        "row_drawn": None, "pips": {"LEFT": None, "RIGHT": None},
                        "selection_role": "follow_up_pip_crop", "because_of": frame["because_of"]}
                       for frame in fits],
            "deferred_for_budget": [{k: v for k, v in frame.items() if k in ("id", "path", "timestamp")}
                                    for frame in wanted[max(0, int(attempts_left)):]],
            "deferred_as_other_kind": other,
            "reused_instead_of_reasking": [{"frame": frame["path"], "id": frame["id"],
                                            "timestamp": frame["timestamp"],
                                            "because_of": frame["because_of"]} for frame in skipped],
            "why_these_frames": "offsets the primary pass did not ask, centred on the timestamp the "
                                "tracker itself named as unresolved"}


def ledger_state(path):
    path = Path(path)
    if not path.is_file():
        return {"path": str(path), "exists": False, "cap": None, "attempts_charged": 0,
                "attempts_remaining": None, "deadline_at": None}
    data = load(path)
    data["exists"] = True
    return data


def tokens_from_provenance(run_dirs):
    """What the runs in question actually cost, summed from per-request provenance."""
    totals = {"attempts_recorded": 0, "prompt": 0, "completion": 0, "reasoning": 0, "runs": []}
    for directory in run_dirs:
        one = {"run": directory, "attempts": 0, "prompt": 0, "completion": 0, "reasoning": 0}
        for path in sorted(Path(directory).glob("crops/batches/*.json")):
            usage = (load(path).get("response") or {}).get("usage") or {}
            one["attempts"] += 1
            one["prompt"] += int(usage.get("prompt_tokens") or 0)
            one["completion"] += int(usage.get("completion_tokens") or 0)
            one["reasoning"] += int((usage.get("completion_tokens_details") or {})
                                    .get("reasoning_tokens") or 0)
        totals["runs"].append(one)
        for key in ("attempts_recorded", "prompt", "completion", "reasoning"):
            totals[key] += one[key if key != "attempts_recorded" else "attempts"]
    totals["note"] = "summed from crops/batches/*.json, which is the record of what was actually " \
                     "charged; a run summary written only at the end of a process is not"
    return totals


def tokens_from_ledger(path):
    """The other account: what the shared ledger charged, request by request.

    Two independent records of the same spend, because they fail differently. Provenance files are
    written per process and, before the naming was fixed, a resume could overwrite an earlier attempt's
    file -- so the ledger, which only ever appends, is what the attempt count is trusted from. Where the
    two disagree, the difference is reported instead of quietly reconciled.
    """
    state = ledger_state(path)
    totals = {"attempts_charged": int(state.get("attempts_charged") or 0), "prompt": 0,
              "completion": 0, "reasoning": 0, "with_usage": 0, "by_purpose": {}, "states": {}}
    for entry in state.get("entries") or []:
        usage = entry.get("tokens") or {}
        totals["by_purpose"][entry.get("purpose", "?")] = \
            totals["by_purpose"].get(entry.get("purpose", "?"), 0) + 1
        totals["states"][entry.get("state", "?")] = totals["states"].get(entry.get("state", "?"), 0) + 1
        if not usage:
            continue
        totals["with_usage"] += 1
        totals["prompt"] += int(usage.get("prompt_tokens") or 0)
        totals["completion"] += int(usage.get("completion_tokens") or 0)
        totals["reasoning"] += int((usage.get("completion_tokens_details") or {})
                                   .get("reasoning_tokens") or 0)
    return totals


def label_disputes(trial, scored, described, rows, label_rows):
    """Every graded frame where the model and the frozen label disagree, reported without a verdict.

    A disagreement is a fact about two records, and neither one is this function's client: writing "the
    label is the likelier error" is the same unearned call the trial exists to catch, and it is exactly
    what the stage-3 review asked to have removed. What *can* be stated is evidence -- the frozen basis
    text in its own words, the neighbouring labels with their instants and graded flags, and whether
    taking this frame out of the merge moves a winner or a contest. Who was wrong, if anyone was, is for
    a reviewer reading the crop.
    """
    outcomes = {round(g["start"], 2): (g["winner"], g["contested"]) for g in described["games"]}
    disputes = []
    for frame in scored["scored"]["per_frame"]:
        if frame["correct"]:
            continue
        own = next((l for l in label_rows if Path(l["frame"]).name == Path(frame["frame"]).name), None)
        nearby = [{"at": round(l["timestamp"], 3), "id": l.get("id"),
                   "pips": [l["pips"]["LEFT"], l["pips"]["RIGHT"]], "graded": l["graded"],
                   "seconds_relative_to_dispute": round(l["timestamp"] - frame["timestamp"], 3),
                   "basis": l.get("basis")}
                  for l in label_rows if abs(l["timestamp"] - frame["timestamp"]) <= 6.0 and l is not own]
        without = [r for r in rows if abs(r["timestamp"] - frame["timestamp"]) >= 0.005]
        retry = bg.track_games(bg.build_observations(without), rounds_per_game=ROUNDS_PER_GAME,
                               games_per_set=GAMES_PER_SET)
        outcomes_without = {round(g["start"], 2): (g["winner"], g["contested"])
                            for g in retry["games"]}
        disputes.append({
            "frame": frame["frame"], "at": frame["timestamp"], "window": frame.get("window"),
            "frozen_label": list(frame["expected"] or []),
            "frozen_basis": (own or {}).get("basis"),
            "model_reported": frame["reported"], "errors": frame["errors"],
            "neighbouring_frozen_labels": nearby,
            "outcome_without_this_frame": outcomes_without,
            "changed_any_conclusion": outcomes_without != outcomes,
            "not_decided_here": "which record is wrong, if either is. Two observations of one row "
                                "disagree; nothing in this file ranks them.",
            "decision": "the frozen label stays byte-for-byte as it was. A corrected count needs a new "
                        "version string and a new freeze before the run that uses it, never an edit "
                        "after an answer exists",
            "for_the_reviewer": "the crop itself is the evidence: "
                                "{} (LEFT/RIGHT PNGs beside it)".format(frame["frame"])})
    return disputes


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trial", default=str(DEFAULT_TRIAL),
                        help="Frozen trial directory: its selected-frames.json fixes the interval and "
                             "the manifest, so nothing about this trial is hard-coded here.")
    parser.add_argument("--start", type=float, default=None,
                        help="Override the planned interval start. Recorded as a moved bound.")
    parser.add_argument("--end", type=float, default=None,
                        help="Override the planned interval end. Recorded as a moved bound.")
    parser.add_argument("--run-dir", action="append", default=None,
                        help="Crop run to merge. Default: every run on disk, filtered to the interval.")
    parser.add_argument("--labels", action="append", default=None,
                        help="Frozen label file. Repeatable, or leave it out: the trial's own "
                             "<trial>/labels-eval-frozen.json plus every <trial>/*/labels-eval-frozen.json "
                             "is scored, separately and together. A follow-up run with its own freeze has "
                             "to appear in the denominator, not be folded into the first trial's rate.")
    parser.add_argument("--budget-file", default=None, help="Default: <trial>/attempts.json")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing anything.")
    parser.add_argument("--plan-follow-up", action="store_true",
                        help="Write annotations-followup.json in the validated crop framing.")
    parser.add_argument("--attempts-left", type=int, default=None,
                        help="Cap for the follow-up plan. Default: what the ledger has left.")
    parser.add_argument("--out", default=None, help="Default: the trial directory itself.")
    parser.add_argument("--format-policy", default=None,
                        help="A user-confirmed event format file. The trial's own tracking otherwise uses "
                             "the single configured target, and then it and the canonical set report can "
                             "disagree about which candidates close for a reason that has nothing to do "
                             "with the footage.")
    args = parser.parse_args()

    trial = Trial(args.trial, args.start, args.end)
    # The trial's own freeze, then any freeze belonging to a run under it (a follow-up window is labelled
    # and frozen separately, before its own answers exist, and both freezes deserve their own denominator).
    label_paths = [Path(p) for p in (args.labels or [])] or \
        [trial.dir / "labels-eval-frozen.json"] + sorted(trial.dir.glob("*/labels-eval-frozen.json"))
    label_paths = [p for p in label_paths if p.is_file()] or [trial.dir / "labels-eval-frozen.json"]
    budget_file = args.budget_file or str(trial.dir / "attempts.json")
    out = Path(args.out) if args.out else trial.dir
    from tekken_vod_helper import boundary_format
    format_policy = boundary_format.load_policy(args.format_policy) if args.format_policy else None

    runs = list(args.run_dir) if args.run_dir is not None else \
        [str(ROOT / name) for name in crop_run_dirs()] + trial_run_dirs(trial)
    runs = [str(Path(r)) for r in dict.fromkeys(runs) if Path(r).is_dir()]
    ledger = ledger_state(budget_file)
    attempts_left = args.attempts_left
    if attempts_left is None:
        attempts_left = 0 if ledger.get("cap") is None else max(
            0, int(ledger["cap"]) - int(ledger.get("attempts_charged") or 0))
    rows, joined, observations = tracking_rows(trial, runs)
    described = describe_tracking(trial, rows, joined, ledger, observations, format_policy)
    described["crop_runs_merged"] = runs
    described["cross_tool_reconciliation"] = reconcile_with_report(trial, described, format_policy)
    if trial.moved_bounds:
        described["interval_moved_from_plan"] = trial.moved_bounds

    if args.dry_run:
        # Scoped to this trial's own runs: summing every merged run would report other experiments'
        # spend as though this trial had already cost it, which is the one thing the resource report
        # must never do before a run has started.
        own = tokens_from_provenance(trial_run_dirs(trial))
        print(json.dumps(dict(described, resources={
            "ledger": ledger, "tokens_from_provenance": own,
            "merged_runs_not_charged_here": [r for r in runs if r not in trial_run_dirs(trial)],
            "attempts_left": attempts_left}), indent=2, sort_keys=True, default=str))
        return 0

    out.mkdir(parents=True, exist_ok=True)
    written = []

    def write(name, payload):
        (out / name).write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
                                encoding="utf-8")
        written.append(name)

    write("trial-tracking.json", described)
    if observations:
        # One score per frozen label set, and one over all of them together. A trial that grew a follow-up
        # run with its own labels has to report both, because "39/41" over the first freeze and "4/4" over
        # the second are different questions, and merging them silently would let the easier frames lift a
        # rate the harder ones earned.
        per_source, merged_frames = [], []
        for path in label_paths:
            source = load(path)
            merged_frames.extend(source["frames"])
            single = perception_errors(trial, runs, source)["scored"]
            per_source.append({"labels": str(path), "version": source.get("version"),
                               "frozen": source.get("frozen"), "sha256": sha256_of(path),
                               "frames_in_file": len(source["frames"]),
                               "scored": single["frames"],
                               "scored_visible_count": single["scored_visible_count_frames"],
                               "scored_visible_count_correct": single["scored_visible_count_correct"],
                               "scored_known_absent": single["scored_known_absent_frames"],
                               "scored_known_absent_correct": single["scored_known_absent_correct"],
                               "unknown_truth": single["unknown_truth_frames"],
                               "frames_correct": single["frames_correct"],
                               "error_kinds": single["error_kinds"]})
        combined = load(label_paths[0])
        combined["frames"] = merged_frames
        scored = perception_errors(trial, runs, combined)
        totals = scored["scored"]
        scored["label_sources"] = per_source
        scored["label_metadata_recounts"] = {
            "note": "the label files' own counts are quoted beside the counts recomputed from their "
                    "frames. A freeze that describes its withheld frames imprecisely stays byte-identical "
                    "-- the correction lives here, in the derived view, and never in the frozen bytes",
            "declared": [{"labels": str(path), "graded_frames": load(path).get("graded_frames"),
                          "ungraded_frames": load(path).get("ungraded_frames")}
                         for path in label_paths],
            "by_content": {"visible_count": totals["scored_visible_count_frames"],
                           "known_absent": totals["scored_known_absent_frames"],
                           "unknown_truth": totals["unknown_truth_frames"],
                           "why_they_differ": "a frame labelled row_drawn false with no counts is scored "
                                              "for correctly claiming an absent row, so it is a "
                                              "denominator and not an exclusion; only row_drawn null is "
                                              "unknown truth"}}
        scored["tracking_faults"] = described["tracking_faults"]
        scored["separation"] = ("perception_errors are about pixels: wrong count, wrong side, invented "
                                "score, unanswered request. tracking_faults are about the model of the "
                                "recording: rounds, grouping, and a pip row falling inside one game.")
        scored["label_disputes"] = label_disputes(trial, scored, described, rows, merged_frames)
        write("trial-evaluation.json", scored)
    else:
        write("trial-evaluation.json", {"status": "not scored", "accuracy_is_reportable": False,
                                       "reason": "no crop answers exist inside this interval yet"})
    spent_here = tokens_from_provenance(trial_run_dirs(trial))
    charged = tokens_from_ledger(budget_file)
    write("trial-resources.json", {"ledger": ledger, "attempts_left": attempts_left,
                                  "this_trial_charged_by_ledger": charged,
                                  "this_trial_provenance": spent_here,
                                  "accounts_disagree": {
                                      "attempts_charged": charged["attempts_charged"],
                                      "provenance_records": spent_here["attempts_recorded"],
                                      "reading": "the two records of the same spend differ. The ledger "
                                                 "only appends, so its count is the one to trust for "
                                                 "what was charged; the provenance count is what "
                                                 "survived on disk. Which attempts are missing, and "
                                                 "why, is a question for the run's own logs.",
                                      "known_cause_fixed": "provenance files used to be named by a "
                                                           "per-process call counter, and are now named "
                                                           "by the request and left un-overwritten "
                                                           "(boundary_media._append_record), so a "
                                                           "disagreement from here on is not that bug"}
                                     if charged["attempts_charged"] != spent_here["attempts_recorded"]
                                     else None,
                                  "note_on_scope": "tokens are summed over this trial's own run "
                                                   "directories; the older runs merged as evidence are "
                                                   "not this experiment's spend",
                                  "deadline_is_one_shared_clock": True})
    plan = follow_up_plan(trial, described, attempts_left, list(observations)) \
        if args.plan_follow_up else None
    questions = {"generated_at": now(), "unresolved": described["unresolved"],
                 "forced_outcomes_refused": ["game count", "winner", "set membership"],
                 "tracking_faults": described["tracking_faults"],
                 "budget_unspent": {
                     "attempts_left": attempts_left,
                     "reason": plan["reason_no_further_attempt_was_justified"] if plan else
                               "no follow-up plan was asked for; the ledger still holds these attempts",
                     "what_would_change_this": "a pip-row question with a timestamp inside the interval "
                                               "that no saved reading already answers"}}
    if plan:
        write("annotations-followup.json", {"kind": "pip-row-annotations",
                                           "version": "followup-crops-v2",
                                           "selected_by": "the tracker's own unresolved timestamps in "
                                                          "the trial interval, crop framing only",
                                           "provenance": {"attempts_left": attempts_left,
                                                          "ledger": ledger.get("path"),
                                                          "manifest": trial.manifest},
                                           "frames": plan["frames"]})
        questions["follow_up"] = plan
    write("unresolved-questions.json", questions)
    print(json.dumps({"written": written, "games": len(described["games"]),
                      "winners": {round(g["start"], 2): g["winner"] for g in described["games"]},
                      "tracking_faults": described["tracking_faults"],
                      "unresolved": len(described["unresolved"]),
                      "attempts_charged": ledger.get("attempts_charged"),
                      "attempts_left": attempts_left}, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
