"""The four offline corrections of 2026-10-31, each pinned by the failure it was written against.

Read as a set, these tests are about a reporting layer staying honest while the corpus grows:

* **evidence discovery** -- the canonical crop-run list was seven directories frozen on 2026-10-30, and
  the morning after 45 crop answers landed the set report printed `games_with_a_winner: 0` for the interval
  those answers decided. A default that has to be edited to stay correct is edited wrong;
* **frame identity** -- a follow-up annotation drafted by extrapolating a neighbouring window's timestamps
  was 1-3 s off the frames it named, and nothing between there and a paid request noticed;
* **denominators** -- "16 frames excluded" and "27 of 29 visible count frames correct" described the same
  freeze, because 12 frames with a known-absent row are graded for a claim, not withheld from it;
* **candidate fusion and format** -- one global first-to-three read a completed 2-0 quarter-final as two
  games of an unfinished set, and four games of two matches sat as four single-game candidates because a
  naming disagreement was being treated as a boundary.

Fixtures are the real recording wherever the claim is about the recording, and synthetic where the point is
a control the footage does not contain -- and the synthetic ones say which side of a swap they pretend.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from tekken_vod_helper import boundary_format as bf              # noqa: E402
from tekken_vod_helper import boundary_games as bg               # noqa: E402
from tekken_vod_helper import boundary_header as bh              # noqa: E402
from tekken_vod_helper import boundary_pips as bp                # noqa: E402
from tekken_vod_helper import boundary_windows as bw             # noqa: E402

import pip_evidence                                               # noqa: E402

TRIAL = ROOT / "build/work/bb9/set-end2end-v1"
MANIFEST = ROOT / "build/work/bb9/detect-1440p-v1/frames/sample-manifest.json"
FORMAT_POLICY = TRIAL / "format-user-confirmed-2026-10-31.json"
STAGE_B = (3200.0, 4000.0)
#: The band answers named by the runs that produced them: six unapproved from 2026-10-30, four governed
#: from Stage A, one from Stage B. Never copied into one directory -- see tests/test_saved_header_reporting.
BAND_RUNS = [str(ROOT / "build/work/bb9/header-trial-v1/unapproved-execute-2026-10-30"),
             str(TRIAL / "attribution-run"), str(TRIAL / "band-run")]

requires_evidence = pytest.mark.skipif(
    not (TRIAL / "run" / "crops" / "groups").is_dir() and FORMAT_POLICY.is_file(),
    reason="the executed Stage B corpus is not present")


# ---------------------------------------------------------------- 1. discovery


def test_a_crop_run_on_disk_is_discovered_without_anyone_editing_a_list(tmp_path, monkeypatch):
    """The defect: a hard-coded list, correct the day it was written and silently short of it forever.

    Discovery is asserted against a temporary recording root, because the claim is about the *rule* --
    "a directory holding crops/groups is a crop run" -- and asserting it against this checkout's ten real
    directories would pass for as long as nobody added an eleventh, which is precisely how the list broke.
    """
    for name in ("experiment-a", "nested/experiment-b"):
        groups = tmp_path / name / "crops" / "groups"
        groups.mkdir(parents=True)
        (groups / "crops-aaaa.json").write_text(json.dumps({"arm": "crops", "observations": []}))
    # A frames folder is not an answer directory, and must not be walked into just because it is full.
    (tmp_path / "detect-1440p-v1" / "frames").mkdir(parents=True)

    found = [str(Path(path).relative_to(tmp_path)) for path in pip_evidence.crop_run_dirs(root=tmp_path)]
    assert found == ["experiment-a", "nested/experiment-b"], found


def test_the_discovered_corpus_is_a_superset_of_the_snapshot_plans_priced_against():
    """Old plans name their inputs; that list may not become the default anybody else reads."""
    discovered = set(pip_evidence.crop_run_dirs())
    assert set(pip_evidence.PLAN_SNAPSHOT_CROP_RUNS) <= discovered
    assert pip_evidence.CROP_RUNS is pip_evidence.PLAN_SNAPSHOT_CROP_RUNS


@requires_evidence
def test_the_stage_b_answers_are_in_the_default_evidence_base_without_a_flag():
    """The report's default path has to see what the trial spent 45 attempts to produce."""
    evidence = pip_evidence.evidence(STAGE_B)
    assert evidence["crop_runs_source"] == "discovered"
    assert any(path.endswith("set-end2end-v1/run") for path in evidence["crop_runs"])
    assert any(path.endswith("follow-up") for path in evidence["crop_runs"])
    assert evidence["crop_readings"] > 45


@requires_evidence
def test_a_second_framing_is_seen_and_still_not_joined():
    """Framing-aware means the master-framed answers are reported, not silently merged or silently absent."""
    framings = pip_evidence.framings()
    smoke = {key: value for key, value in framings.items() if "source-crop-smoke" in key}
    assert smoke, "the master-framing experiment vanished from discovery instead of being excluded"
    counts = list(smoke.values())[0]
    assert counts["cache"] == 0 and counts["other_framing"] > 0
    # And the merge itself still refuses to join them to one pip series.
    evidence = pip_evidence.evidence(STAGE_B)
    joined = [run for run in evidence["crop_runs"] if "source-crop-smoke" in run]
    assert joined, "the master-framing run vanished from the corpus instead of being excluded from a series"
    # Measured: four of this interval's answers are master-framed, and every one of them is seen and kept
    # out of the cache pip series -- a second picture of one instant would read as the dots moving. The
    # count the merge reports has to be the count the framings table counts, or one of them is lying.
    counted = sum(value["other_framing"] for value in evidence["framings"].values())
    assert counted == evidence["framing_excluded"] >= 1


# ---------------------------------------------------------------- 2. frame identity

def good_annotations(count=6, offset=3240):
    frames = json.loads(MANIFEST.read_text(encoding="utf-8"))["frames"][offset:offset + count]
    return [{"frame": entry["path"], "timestamp": entry["timestamp"], "id": entry["id"]}
            for entry in frames]


def test_the_cache_index_verifies_the_pair_a_request_is_built_from():
    checked = bw.verify_annotations(good_annotations(), str(MANIFEST))
    assert checked["problems"] == []
    assert checked["verified_against_manifest"] == 6 and checked["inference_sent"] is False


@pytest.mark.parametrize("drift", [1.001, 2.002, 3.0, -2.002])
def test_a_timestamp_borrowed_from_the_neighbouring_frame_is_refused(drift):
    """The exact caught failure: real frame file, timestamp belonging to a different instant.

    1.001 and 2.002 s are the cache's own spacing, which is what makes the mistake survive every downstream
    check -- the crop renders, the model answers, and only the *join* is wrong, filing a pip row seconds
    from where it will be believed.
    """
    entry = dict(good_annotations(1)[0])
    entry["timestamp"] = round(entry["timestamp"] + drift, 6)
    problems = bw.verify_annotations([entry], str(MANIFEST))["problems"]
    assert len(problems) == 1 and "drift" in problems[0]


def test_a_millisecond_rounded_timestamp_is_the_same_frame_and_stays_accepted():
    """The tolerance is 0.05 s -- wide enough for this corpus's own rounding, narrow by a factor of twenty.

    The accepted annotations write 55.05 for a frame sampled at 55.054997 s because the merge keys rows at
    three decimals, and the cache's spacing is 1.000986 s, so 0.05 s is a twentieth of the distance to any
    other frame. A guard that refused those bytes would be reverted the first time it blocked real work, and
    the 1-3 s drift it exists to catch would go through with it.
    """
    entries = [dict(entry, timestamp=round(entry["timestamp"], 3)) for entry in good_annotations(8)]
    checked = bw.verify_annotations(entries, str(MANIFEST))
    assert checked["problems"] == [] and checked["verified_against_manifest"] == 8


def test_an_unlisted_frame_and_an_untimestamped_frame_are_both_refused():
    folder = Path(good_annotations(1)[0]["frame"]).parent
    problems = bw.verify_annotations(
        [{"frame": str(folder / "frame-99999.jpg"), "timestamp": 1.0},
         {"frame": good_annotations(1)[0]["frame"]}], str(MANIFEST))["problems"]
    assert len(problems) == 2
    assert "not listed" in problems[0] and "carries no timestamp" in problems[1]


def test_a_declared_frame_id_that_disagrees_with_the_path_is_refused():
    entry = dict(good_annotations(1)[0], id=1234)
    problems = bw.verify_annotations([entry], str(MANIFEST))["problems"]
    assert len(problems) == 1 and "declares frame id 1234" in problems[0]


def test_a_native_source_record_is_supported_without_being_looked_up_in_the_cache():
    """Master-framed crops are not cache frames; the rule is that their own file and time have to exist.

    The declaration is read off the annotation document the source builder writes -- ``version:
    "source-crops-v1"`` -- because a filename pattern would accept any unfindable path, and a mistyped
    cache path is exactly what needs refusing.
    """
    from glob import glob
    real = sorted(glob("build/work/bb9/source-crop-smoke-v1/frames/*.png"))
    assert real, "no native-source frames on disk to run the control against"
    checked = bw.verify_annotations([{"frame": real[0], "timestamp": 1236.0}], str(MANIFEST),
                                    framing="source-crops-v1")
    assert checked["problems"] == [] and checked["verified_native_source"] == 1
    assert checked["verified_against_manifest"] == 0, "a master decode is not a cache frame"
    # The same file, declared as cache framing, is refused: silence about identity is not a pass.
    undeclared = bw.verify_annotations([{"frame": real[0], "timestamp": 1236.0}], str(MANIFEST))
    assert len(undeclared["problems"]) == 1 and "not listed" in undeclared["problems"][0]
    missing = bw.verify_annotations([{"frame": "build/work/bb9/nope.png", "timestamp": 1.0}],
                                    str(MANIFEST), framing="native resolution")
    assert len(missing["problems"]) == 1 and "not on disk" in missing["problems"][0]


def test_the_probe_refuses_a_drifting_annotation_before_rendering_anything(tmp_path):
    """Refused means nothing on disk and nothing sent: a warning beside a rendered crop is too late."""
    entry = dict(good_annotations(1)[0])
    entry["timestamp"] = round(entry["timestamp"] + 2.002, 6)
    annotations = tmp_path / "annotations.json"
    annotations.write_text(json.dumps({"kind": "pip-crop-annotations", "version": "drift-control-1",
                                       "frames": [dict(entry, pips={"LEFT": None, "RIGHT": None},
                                                       row_drawn=None)]}))
    ran = subprocess.run([sys.executable, str(ROOT / "scripts/run_pip_crop_probe.py"),
                          "--annotations", str(annotations), "--output-dir", str(tmp_path / "run"),
                          "--arms", "crops", "--prepare", "--frame-manifest", str(MANIFEST)],
                         capture_output=True, text=True, cwd=str(ROOT),
                         env={"PYTHONPATH": ".", "PATH": "/usr/bin:/bin"})
    assert ran.returncode != 0
    assert "do not match the frame manifest" in ran.stdout + ran.stderr
    assert not (tmp_path / "run" / "crops").exists(), "crops were rendered for a refused annotation"
    assert not (tmp_path / "run" / "prepared.json").exists()


# ---------------------------------------------------------------- 3. format policy


@pytest.mark.parametrize("label, stage, target", [
    ("LOSERS QUARTER-FINAL", "losers quarter-final", 2),
    ("e-Pre-Clash-Cla... LOSERS QUARTER FINAL", "losers quarter-final", 2),
    (")ON) (Pre-Pre-Clash-Cla... WINNERS FINAL", "winners final", 3),
    ("e-Pre-Clash-Cla... WINNERS QUARTER-FINAL", "winners quarter-final", 2),
    ("ON) (Pre-Pre-Clash-Cla... LOSERS ROUND 1", "losers round 1", 2),
    ("Basement Brawl #9 (CAFE EDITION) (Pre-Pre-Clash-Clash) GRAND FINAL", "grand final", 3),
    ("losers semi-final", "losers semi-final", 3),
    ("LOSERS QUARTERFINAL", "losers quarter-final", 2),
    ("#9 (CAFE EDITION) (Pre-Pre-Clash-Cla...)", None, None),      # event title, no round
    (None, None, None),
])
def test_the_stage_the_band_names_selects_the_target(label, stage, target):
    """Read off real label strings from this recording, separators included.

    The event's own title contains words that look like a round, so the check that matters is that
    "(Pre-Pre-Clash-Cla...)" names nothing: an invented target would close or open sets by a substring.
    """
    found = bf.normalize_stage(label)
    assert found == stage
    policy = {"default_games_to_win": 2, "overrides": {s: 3 for s in bf.TOP_FOUR_STAGES}}
    assert bf.target_for_stage(found, policy) == target


def test_a_policy_file_that_cannot_be_read_is_a_refusal_not_a_default():
    good = bf.load_policy(str(FORMAT_POLICY))
    assert good["default_games_to_win"] == 2 and good["overrides"]["winners final"] == 3
    assert bf.load_policy(None) is None
    bad = {"kind": "something-else"}
    tmp = Path("/tmp/bad-policy.json")
    tmp.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="not a user-confirmed"):
        bf.load_policy(tmp)
    tmp.write_text(json.dumps({"kind": "user-confirmed-event-format", "default_games_to_win": 0}))
    with pytest.raises(ValueError, match="no usable default"):
        bf.load_policy(tmp)


def test_a_candidate_measures_against_every_target_its_own_span_admits():
    stages = [(3786.783, "LOSERS QUARTER FINAL"), (3934.931, "WINNERS FINAL")]
    formats = bf.applicable_formats(stages, {"default_games_to_win": 2,
                                             "overrides": {s: 3 for s in bf.TOP_FOUR_STAGES}})
    assert formats["targets"] == [2, 3] and formats["ambiguous"] is True
    assert formats["format_known"] is True
    # A span with no readable stage is not silently first-to-two: it is every target, agreed or none.
    silent = bf.applicable_formats([(1.0, "(Pre-Pre-Clash-Cla...)")],
                                   {"default_games_to_win": 2, "overrides": {s: 3
                                                                             for s in bf.TOP_FOUR_STAGES}})
    assert silent["targets"] == [2, 3] and silent["readings_without_a_stage"]
    # With no policy there is no target to measure against: that is the format-removed comparison, and it
    # has to be an empty list rather than a convenient default.
    assert bf.applicable_formats([])["targets"] == []
    assert bf.applicable_formats([])["format_known"] is False


def test_an_unknown_format_never_closes_a_set_and_says_which_input_is_missing():
    rows = [{"timestamp": 10.0, "screen_state": "gameplay", "overlay": {}}]
    sets = bg._group_into_sets([{"start": 0.0, "end": 100.0, "status": "complete", "winner": "p1",
                                 "counts_toward_set": True, "conclusion_event_at": 99.0},
                                {"start": 200.0, "end": 300.0, "status": "complete", "winner": "p1",
                                 "counts_toward_set": True, "conclusion_event_at": 299.0}],
                               None, rows)
    assert [s["status"] for s in sets] == ["partial"] * len(sets)
    assert all(s["applicable_formats"]["targets"] == [] for s in sets)
    assert any("games_per_set was never configured" in m for m in sets[0]["missing_evidence"])
    assert all(s["games_per_set_expected"] is None for s in sets)


# ---------------------------------------------------------------- 4. fusion and the target


def two_games(pair_first, pair_second, fighters_first, fighters_second, gap_states,
              first=(0.0, 100.0), second=(140.0, 240.0)):
    """Two concluded games, the screens between them, and the nameplates and fighters on each side."""
    games = [{"start": first[0], "end": first[1], "status": "complete", "winner": "p2",
              "counts_toward_set": True, "conclusion_event_at": first[1] - 1},
             {"start": second[0], "end": second[1], "status": "complete", "winner": "p2",
              "counts_toward_set": True, "conclusion_event_at": second[1] - 1}]
    rows = []
    for span, pair, fighters in ((first, pair_first, fighters_first),
                                 (second, pair_second, fighters_second)):
        for at in (span[0] + 5, span[0] + 50, span[1] - 5):
            rows.append({"timestamp": at, "screen_state": "gameplay",
                         "overlay": {"player1": pair[0], "player2": pair[1], "score": [0, 0]},
                         "characters": list(fighters)})
    for at, state in gap_states:
        rows.append({"timestamp": at, "screen_state": state, "overlay": {}})
    return games, sorted(rows, key=lambda r: r["timestamp"])


POLICY = {"default_games_to_win": 2, "overrides": {stage: 3 for stage in bf.TOP_FOUR_STAGES}}
WITHIN_MATCH = [(110.0, "post_match_menu"), (115.0, "stage_select"), (120.0, "vs_screen"),
                (130.0, "stage_intro")]
STATION_CLEARED = [(110.0, "post_match_menu"), (115.0, "controller_prompt"),
                   (120.0, "controller_assignment"), (125.0, "vs_screen")]


def grouping(games, rows, formats=POLICY, header=None):
    return bg._group_into_sets(games, None, rows, header=header, format_policy=formats)


def band(at, left, right, games=(None, None), label=None):
    """A saved band reading in the shape ``saved_readings`` produces, quarantine included."""
    return {"at": at, "frame": "frame-%d.jpg" % at, "frame_id": None, "frame_timestamp": float(at),
            "image_sha256": "sha-%s" % at,
            "pairing": {"left": {"player": left, "games": games[0], "confidence": 0.9},
                        "right": {"player": right, "games": games[1], "confidence": 0.9},
                        "tally": None, "label": label, "mapping_usable": True,
                        "usable": True, "model": "qwen3.8-flash-next"}}


# One reading per game, each naming the same two players the overlay rows show on the same sides: the
# crossing is between the games, exactly as it is at 3,373->3,419 s on the recording.
HEADER = {"kind": bh.SAVED_KIND, "arm": bh.ARM, "version": bh.VERSION, "source_directory": "test",
          "readings": [band(20, "Ariz", "Buk", (0, 0), "LOSERS QUARTER-FINAL"),
                       band(160, "Buk", "Ariz", (0, 1), "LOSERS QUARTER-FINAL")],
          "discarded": [], "inference_sent": False, "attempts_charged_now": 0,
          "provenance": {"ledger": "test fixture"}, "reused_without_resending": 2}


def test_names_crossing_while_the_fighters_hold_join_the_games_and_close_the_set():
    """The Stage B quarter-final, as a control: the same seat won both games and the broadcast renamed it.

    This is the case the review asked not to lose. A 2-0 in a first-to-two set is a completed set even
    though no single name is attached to both wins, and cutting it into two candidates on the strength of a
    naming disagreement throws away the only recovered set in the interval.
    """
    games, rows = two_games(("Ariz", "Buk"), ("Buk", "Ariz"), ("NINA", "REINA"), ("NINA", "REINA"),
                            WITHIN_MATCH)
    sets = grouping(games, rows, header=HEADER)
    assert len(sets) == 1, [s["game_spans"] for s in sets]
    record = sets[0]
    assert record["status"] == "complete" and record["membership"] == "supported"
    assert record["deciding_wins"] == {"p1": 0, "p2": 2} and record["deciding_wins_unit"] == "sides"
    assert record["winner_seat"] == "p2" and record["set_winner"] is None
    assert record["complete_under"] == [2] and record["open_under"] == []
    assert record["format_dependent"] is False
    # The naming is the open part, and it stays open on the page rather than being resolved by preference.
    hypotheses = record["participant_hypotheses"]["p2"]
    assert [entry["name"] for entry in hypotheses] == ["Buk", "Ariz"]
    assert record["attribution_withheld"] and "fighters say it did not" in record["attribution_withheld"]
    link = record["links"][0]
    assert link["verdict"] == "joined" and link["seat_continuity"] == "held"
    assert link["transition"]["within_match"] is True and link["transition"]["station_cleared"] is False


def test_the_same_wins_with_the_fighters_exchanged_do_not_close_anything():
    """The mirror control, and the reason the case above is not a rubber stamp.

    If the two players really did change seats, two wins on one screen side are one win each: a 1-1, not a
    set. Same names, same gap, same scores -- only the fighters move -- and the closure has to disappear
    with a reason naming the observation that would settle it.
    """
    games, rows = two_games(("Ariz", "Buk"), ("Buk", "Ariz"), ("NINA", "REINA"), ("REINA", "NINA"),
                            WITHIN_MATCH)
    record = grouping(games, rows, header=HEADER)[0]
    assert record["status"] == "partial"
    assert record["deciding_wins"] == {"p1": 0, "p2": 2}, "the sides still won twice; only the reading is"
    assert record["basis"] == "seat_wins_cannot_be_read_as_one_person"
    assert any("kept the same player" in m for m in record["missing_evidence"])
    assert record["complete_under"] == []


def test_a_station_change_still_separates_two_games_of_the_same_pairing():
    """The join needs the gap to be a between-games screen; a controller screen still ends a match."""
    games, rows = two_games(("Buk", "Ariz"), ("Mattiniero", "Spitfire"), ("NINA", "REINA"),
                            ("REINA", "AZUCENA"), STATION_CLEARED)
    sets = grouping(games, rows, header=HEADER)
    assert len(sets) == 2, "a cleared station was merged into one set"
    assert all(s["status"] == "partial" for s in sets)
    assert [s["games"] for s in sets] == [1, 1]
    assert sets[0]["open_under"] == [2], "a first-to-two set closed on one game"


def test_the_format_decides_closure_and_the_recording_decides_everything_else():
    """Same rows, same answers, three formats: what each one makes decidable, stated as a delta."""
    games, rows = two_games(("Ariz", "Buk"), ("Buk", "Ariz"), ("NINA", "REINA"), ("NINA", "REINA"),
                            WITHIN_MATCH)
    closed = {}
    for name, policy, configured in (("first-to-two", POLICY, None),
                                     ("first-to-three", {"default_games_to_win": 3, "overrides": {}}, None),
                                     ("no format", None, None)):
        sets = bg._group_into_sets(games, configured, rows, header=HEADER, format_policy=policy)
        closed[name] = [s["status"] for s in sets]
    assert closed["first-to-two"] == ["complete"]
    assert closed["first-to-three"] == ["partial"], "2 wins cannot satisfy a target of 3"
    assert closed["no format"] == ["partial"]
    # And the grouping itself did not move between them: the format closes sets, it does not cut games.
    assert all(len(sets) == 1 for sets in ([bg._group_into_sets(games, None, rows, header=HEADER,
                                                                format_policy=POLICY)],))


@requires_evidence
def test_the_recorded_interval_fuses_into_two_candidates_and_closes_exactly_one():
    """Executed evidence: the four games of [3200, 4000) are two matches, and the first one is over.

    Run with no format at all, because the claim worth pinning is that this closure does not depend on one:
    the station was cleared on both sides of the first candidate, and that is a fact about the recording.
    The band answer at 3976.47 s is a governed spend and the six from 2026-10-30 are not, which is why the
    three runs are named separately here rather than merged.
    """
    evidence = pip_evidence.evidence(STAGE_B)
    band = bh.saved_readings(BAND_RUNS, ledger=str(TRIAL / "attempts-shared-2026-10-31.json"))
    assert band["inference_sent"] is False and band["attempts_charged_now"] == 0
    provenance = band["provenance"]
    assert provenance["approval"] == "mixed"
    assert provenance["attempts_governed_by_a_cap"] is False
    assert provenance["answers_matched_to_a_charged_attempt"] == 5
    assert provenance["answers_unmatched"] == 6
    assert provenance["incident_recorded"] is True

    sets = bg.track_games(evidence["observations"], rounds_per_game=3, games_per_set=None,
                          header=band, analysis_window=STAGE_B)["sets"]
    assert len(sets) == 2, [s["game_spans"] for s in sets]
    first, second = sets
    assert first["status"] == "complete" and first["concluded_by"] == ["station_cleared"]
    assert first["footage_complete"] is True and first["games_per_set_expected"] is None
    assert first["winner_seat"] == "p2" and first["set_winner"] is None, "a boundary names no champion"
    assert sorted(entry["name"] for entry in first["participant_hypotheses"]["p2"]) == \
        sorted(["Buk", "PWDDA | Ariz"])
    # The second candidate ends where the caller's window ends, not where the recording says the players
    # stopped, so it stays open under every target -- and the record says which kind of unknown that is.
    assert second["status"] == "partial" and second["concluded_by"] == []
    # Rows were sampled past 3,993.99 s (gameplay, a stage intro) and none of them settles the bound: no
    # station cue, and no pair of frames whose clock changed. That is silence, not contradiction, and the
    # record is expected to say which.
    assert second["edges"]["end"]["state"] in ("play_continues_past_this_bound",
                                                "sampled_but_unsettled")
    assert second["edges"]["end"]["observed"] is True


# ---------------------------------------------------------------- 5. denominators


def label_frames():
    return [{"timestamp": 1.0, "frame": "a.jpg", "pips": {"LEFT": 1, "RIGHT": 0}, "row_drawn": True,
             "graded": True},
            {"timestamp": 2.0, "frame": "b.jpg", "pips": {"LEFT": None, "RIGHT": None},
             "row_drawn": False, "graded": False},
            {"timestamp": 3.0, "frame": "c.jpg", "pips": {"LEFT": None, "RIGHT": None},
             "row_drawn": None, "graded": False}]


def observation(at, frame, left, right, drawn=True):
    return [{"timestamp": at, "frame": frame, "label": "LEFT", "lit": left, "drawn": drawn},
            {"timestamp": at, "frame": frame, "label": "RIGHT", "lit": right, "drawn": drawn}]


def test_a_known_absence_is_a_denominator_and_an_unreadable_frame_is_not():
    """The three states the freeze described as one number, separated in the report.

    Frame b is a claim the model can get wrong (say 0-0 with a row drawn and it is a false zero; say "no
    row" and it is right). Frame c is nobody's evidence: 12 frames were the first kind and 4 the second,
    and quoting "16 excluded" beside "39 of 41" counted the 12 twice.
    """
    readings = observation(1.0, "a.jpg", 1, 0) + observation(2.0, "b.jpg", None, None, drawn=False)
    scored = bp.score_readings(readings, label_frames())
    assert scored["frames"] == 2 and scored["unknown_truth_frames"] == 1
    assert scored["scored_visible_count_frames"] == 1 and scored["scored_visible_count_correct"] == 1
    assert scored["scored_known_absent_frames"] == 1 and scored["scored_known_absent_correct"] == 1
    assert scored["ungraded_frames"] == 1
    assert scored["scored_visible_count_frames"] + scored["scored_known_absent_frames"] == scored["frames"]


def test_an_absent_row_answered_with_a_count_is_not_credited_as_an_abstention():
    readings = observation(1.0, "a.jpg", 1, 0) + observation(2.0, "b.jpg", 0, 0)
    scored = bp.score_readings(readings, label_frames())
    assert scored["scored_known_absent_correct"] == 0
    assert scored["per_frame"][1]["correct"] is False


@requires_evidence
def test_the_boundary_route_concludes_nothing_where_the_tape_shows_no_end():
    """The control that keeps "the recording concludes sets" from being a claim with no content.

    Three intervals on this recording hold crop-validated winners and were never read as set trials. None of
    them concludes, because their bounds are an analysis edge or continuing play rather than a station
    change -- which is the difference between a rule that reads evidence and a rule that finds a set wherever
    two games happen to sit. If this ever stops holding while the cues are unchanged, the route has become
    a closer by default.
    """
    for lo, hi in ((44.0, 262.0), (226.0, 430.0), (736.0, 1080.0)):
        evidence = pip_evidence.evidence((lo, hi))
        result = bg.track_games(evidence["observations"], rounds_per_game=3, games_per_set=None,
                                analysis_window=(lo, hi))
        concluded = [s for s in result["sets"] if s["status"] == "complete"]
        assert not concluded, [(lo, hi, s["game_spans"], s["basis"]) for s in concluded]
        for record in result["sets"]:
            assert "station_cleared" not in (record.get("concluded_by") or []), (
                lo, hi, record["game_spans"], record["concluded_by"])
            states = {side: ((record.get("edges") or {}).get(side) or {}).get("state")
                      for side in ("start", "end")}
            assert set(states.values()) <= {"station_cleared", "play_continues_past_this_bound",
                                            "play_continues_before_this_bound", "nothing_observed_after",
                                            "nothing_observed_before", "sampled_but_unsettled",
                                            "between_games_screen_no_station_event",
                                            "between_games_screen_before_this_bound"}, states


@requires_evidence
def test_the_trial_report_separates_its_two_frozen_label_sets():
    """45 frames from the main freeze and 4 from the glare follow-up, scored apart and together."""
    report = json.loads((TRIAL / "trial-evaluation.json").read_text())
    sources = report["label_sources"]
    assert len(sources) == 2, [entry["labels"] for entry in sources]
    by_file = {("follow-up" if "follow-up" in entry["labels"] else "main"): entry for entry in sources}
    main = by_file["main"]
    assert main["scored_visible_count"] == 29 and main["scored_visible_count_correct"] == 27
    assert main["scored_known_absent"] == 12 and main["scored_known_absent_correct"] == 12
    assert main["unknown_truth"] == 4
    follow = by_file["follow-up"]
    assert follow["frames_in_file"] == 4 and follow["frames_correct"] == 4
    assert follow["error_kinds"] == {"exact": 8}, "the four glare neighbours are the cleanest frames here"
    totals = report["scored"]
    assert totals["frames_correct"] == 43 and totals["frames"] == 45
    # The freeze's own metadata stays byte-identical; the correction is a derived view beside it.
    declared = {entry["labels"].split("/")[-2] if "/" in entry["labels"] else "trial": entry
                for entry in report["label_metadata_recounts"]["declared"]}
    assert report["label_metadata_recounts"]["by_content"]["unknown_truth"] == 4
