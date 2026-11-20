"""Loading saved observations: what qualifies, what gets refused, and what a refusal must never become.

The seam only earns its keep if the refusals are enforced, so every case here is one the loader has to catch:
an unrelated string with a frame attached, a wrong side, a wrong instant, a wrong source, a mismatched frame
digest, a declaration inferred from rows rather than read from pixels, and a declaration that tries to make
itself an acceptance or a bound identity.

The last group matters most. A machine that read a nameplate has produced a fact about displayed text; the
same file, edited to add `bound_participant_identity: true` or `accepted_by: "operator"`, is a machine
asserting an authority it does not have.
"""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from tekken_vod_helper import boundary_observations as obs

FRAME = "frames/frame-2601.jpg"

#: Overrides on the observation itself, versus overrides inside its scope. Two lists, because the loader
#: treats a field in the wrong place as a claim the entry did not make -- which is what the tests want.
TOP_FIELDS = ("kind", "raw_text", "observed_text", "reviewed_by", "settled_by", "evidence_tier",
              "accepted_by", "bound_participant_identity", "requires_operator_confirmation",
              "authoritative")
SCOPE_KEYS = ("source", "event", "side", "at", "frame_file", "frame_sha256", "game_span")


@pytest.fixture
def root(tmp_path):
    directory = tmp_path / "frames"
    directory.mkdir()
    (directory / "frame-2601.jpg").write_bytes(b"a real frame of pixels, presumably")
    return tmp_path


def digest(root):
    return hashlib.sha256((root / FRAME).read_bytes()).hexdigest()


def entry(root, top=None, **overrides):
    base = {"kind": obs.TEXT_KIND, "raw_text": "PWDDA | Az", "observed_text": "PWDDA | Ariz",
            "reviewed_by": "machine", "settled_by": "saved_frame_pixels",
            "evidence_tier": "machine_display_text", "accepted_by": None,
            "bound_participant_identity": False, "requires_operator_confirmation": True,
            "scope": {"source": "recording.mp4", "event": "Basement Brawl 9", "side": "p1", "at": 2602.6,
                      "frame_file": FRAME, "frame_sha256": digest(root)}}
    for key, value in overrides.items():
        if key in SCOPE_KEYS:
            base["scope"][key] = value
        else:
            assert key in TOP_FIELDS, key
            base[key] = value
    for key, value in (top or {}).items():
        base[key] = value
    return base


def write(root, entries, name="observations.json"):
    path = root / name
    path.write_text(json.dumps({"observations": entries}), encoding="utf-8")
    return path


def load(root, entries, context=None, **kwargs):
    return obs.load(write(root, entries), root=root, context=context or {"source": "recording.mp4",
                                                                         "event": "Basement Brawl 9"},
                    **kwargs)


def test_an_observed_reading_of_text_is_accepted_and_says_what_it_is_not(root):
    loaded = load(root, [entry(root)])
    assert [row["observed_text"] for row in loaded["accepted"]] == ["PWDDA | Ariz"]
    assert loaded["rejected"] == []
    corrections = obs.text_corrections(loaded)
    assert corrections[0]["side"] == "p1" and corrections[0]["at"] == 2602.6
    assert corrections[0]["bound_participant_identity"] is False
    assert corrections[0]["accepted_by"] is None and corrections[0]["reviewed_by"] == "machine"
    assert loaded["accepted"][0]["requires_operator_confirmation"] is True


def test_a_declaration_asking_for_identity_or_acceptance_is_refused(root):
    for field, value in (("bound_participant_identity", True), ("accepted_by", "operator"),
                         ("authoritative", True)):
        loaded = load(root, [entry(root, **{field: value})])
        assert loaded["rejected"], field
        why = loaded["rejected"][0]["why"]
        assert "declare" in why, (field, why)
        smuggled = entry(root)
        smuggled["scope"][field] = value
        assert load(root, [smuggled])["rejected"], field  # not admissible via scope either


def test_nonempty_but_unrelated_text_is_not_a_correction(root):
    loaded = load(root, [entry(root, observed_text="Completely Different Player")])
    assert not loaded["accepted"]
    assert "not a variant" in loaded["rejected"][0]["why"]


def test_an_observation_that_corrects_nothing_is_refused(root):
    assert not load(root, [entry(root, observed_text="PWDDA | Az")])["accepted"]
    assert not load(root, [entry(root, observed_text="  ")])["accepted"]
    assert not load(root, [entry(root, raw_text="")])["accepted"]


def test_wrong_side_wrong_time_wrong_source_and_wrong_hash_are_all_refused(root):
    good = entry(root)
    assert load(root, [good])["accepted"]
    cases = [
        ("side", entry(root, side="left"), "scope.side"),
        ("source", entry(root, source="other-recording.mp4"), "scope.source"),
        ("event", entry(root, event="Basement Brawl 10"), "scope.event"),
        ("hash", entry(root, frame_sha256="0" * 64), "frame_sha256"),
        ("frame", entry(root, frame_file="frames/frame-9999.jpg"), "readable file"),
    ]
    for label, bad, expect in cases:
        loaded = load(root, [bad])
        assert not loaded["accepted"], label
        assert expect in loaded["rejected"][0]["why"], (label, loaded["rejected"][0]["why"])
    # a missing instant would apply everywhere, so it is not admissible at all
    widened = entry(root)
    widened["scope"]["apply_to_all_sets"] = True
    assert load(root, [widened])["rejected"], "an unknown scope field was accepted"
    missing_time = copy.deepcopy(good)
    del missing_time["scope"]["at"]
    assert not load(root, [missing_time])["accepted"]
    # and the instant must be inside the declared span when one is given
    outside = entry(root, at=10.0, game_span=[2600.0, 2700.0])
    assert not load(root, [outside])["accepted"], "an instant outside its own span"


def test_a_guess_is_not_an_observation(root):
    inferred = entry(root, settled_by="inferred from neighbouring rows")
    loaded = load(root, [inferred])
    assert not loaded["accepted"]
    assert "saved_frame_pixels" in loaded["rejected"][0]["why"]


def test_an_undigested_kind_and_a_stage_entry_without_a_candidate_are_refused(root):
    assert not load(root, [entry(root, kind="vibes")])["accepted"]
    stage = entry(root, kind=obs.STAGE_KIND, raw_text="", observed_text="LOSERS FINAL")
    assert not load(root, [stage])["accepted"], "a stage label must name the candidate it is offered to"
    stage["candidates"] = [[2600.0, 2700.0]]
    loaded = load(root, [stage])
    assert loaded["accepted"], loaded["rejected"]


def test_stage_readings_take_the_shape_the_format_policy_already_consumes(root):
    stage = entry(root, top={"candidates": [[2600.0, 2700.0]]}, kind=obs.STAGE_KIND, raw_text="",
                  observed_text="LOSERS FINAL", at=2650.0, side=None)
    loaded = load(root, [stage])
    assert loaded["accepted"], loaded["rejected"]
    readings = obs.stage_readings(loaded)
    row = readings[0]
    assert row["at"] == 2650.0 and row["pairing"] == {"label": "LOSERS FINAL"}
    # The candidate scope has to be on the record the tracker consumes, not only inside its provenance:
    # `_bucket_formats` enforces this key, and a label scoped to one set must not narrow a neighbour.
    assert row["candidates"] == [[2600.0, 2700.0]]
    assert row["provenance"]["candidates"] == [[2600.0, 2700.0]]
    assert row["observation_id"] == obs.observation_id(stage)
    assert row["provenance"]["reviewed_by"] == "machine"
    assert row["provenance"]["bound_participant_identity"] is False
    assert any("whole candidate" in line for line in row["provenance"]["does_not_establish"])


def test_a_stage_label_declared_for_one_candidate_does_not_narrow_another(root):
    """The scope is enforced where the label is consumed, or declaring it is decoration."""
    from tekken_vod_helper import boundary_games as bg

    stage = entry(root, top={"candidates": [[2600.0, 2700.0]]}, kind=obs.STAGE_KIND, raw_text="",
                  observed_text="LOSERS FINAL", at=2650.0, side=None)
    reading = obs.stage_readings(load(root, [stage]))[0]
    named = bg._reading_offered_to_candidate(reading, 2600.0, 2700.0)
    neighbour = bg._reading_offered_to_candidate(reading, 2640.0, 3200.0)
    elsewhere = bg._reading_offered_to_candidate(reading, 2700.5, 3200.0)
    assert named is True
    assert neighbour is True, "an overlapping candidate is the one the label was offered to"
    assert elsewhere is False, "a label must not narrow a set its author never named"
    # A band answer from the header run declares nothing, so it keeps its old timestamp-only scope.
    assert bg._reading_offered_to_candidate({"at": 2650.0, "pairing": {"label": "X"}}, 1.0, 9999.0) is True
    broken = dict(reading, candidates=[["not", "seconds"], "nope"])
    assert bg._reading_offered_to_candidate(broken, 2600.0, 2700.0) is False


def test_corrections_are_narrowed_by_the_recording_being_processed(root):
    loaded = load(root, [entry(root), entry(root, source="another.mp4")])
    assert len(loaded["accepted"]) == 1, loaded["rejected"]
    ours = obs.text_corrections(loaded, source="recording.mp4")
    assert len(ours) == 1
    assert obs.text_corrections(loaded, source="some other file") == []
    assert len(obs.text_corrections(loaded, event="Basement Brawl 9")) == 1
    assert obs.text_corrections(loaded, event="elsewhere") == []


def test_describe_lists_refusals_by_reason_so_a_bad_file_cannot_fail_quietly(root):
    loaded = load(root, [entry(root), entry(root, observed_text="Unrelated Entirely"),
                         entry(root, settled_by="hope")])
    summary = obs.describe(loaded)
    assert summary["accepted"] == 1 and len(summary["rejected"]) == 2
    assert summary["kinds"][obs.TEXT_KIND] == 1
    assert all(row["why"] for row in summary["rejected"])
    assert summary["sha256"] == hashlib.sha256(
        (root / "observations.json").read_bytes()).hexdigest()


def test_the_module_names_no_recording_player_or_timestamp():
    """No per-recording shortcut: everything specific has to arrive through the file."""
    source = Path(obs.__file__).read_text(encoding="utf-8")
    for banned in ("PWDDA", "derpingtoast", "Basement Brawl", "2602.6", "5485.48", "frame-2601",
                   "detect-1440p"):
        assert banned not in source, banned


# ------------------------------------------------------------- what the tracker did with a reading


def correction(**over):
    """One admitted text observation, in the shape :func:`boundary_observations.text_corrections` emits."""
    row = {"observation_id": "obs-a", "side": "p1", "at": 10.0, "raw_text": "Alpha Sig",
           "observed_text": "Alpha Sigma", "frame_file": "frames/frame-2601.jpg",
           "frame_sha256": "d" * 64, "reviewed_by": "machine",
           "evidence_tier": "machine_display_text", "accepted_by": None,
           "bound_participant_identity": False}
    row.update(over)
    return row


#: Timestamp, screen state, left nameplate, win dots, round clock. Two fights and a versus screen, which is
#: all any of these cases needs: one row a reading corrects, one row that merely looks back at it, and one
#: row that was never a nameplate at all.
TAPE = [(10.0, "gameplay", "Alpha Sig", [0, 0], 59), (40.0, "gameplay", "Alpha Sig", [0, 0], 30),
        (100.0, "gameplay", "Alpha Sig", [1, 0], 10),
        (2264.0, "vs_screen", "Menu Name", [0, 0], 60), (2270.0, "gameplay", "Alpha Sig", [0, 0], 59)]


def _name_sequence(lo, hi, **over):
    from tekken_vod_helper import boundary_games as bg
    return bg._name_sequence(bg.build_observations(
        [{"timestamp": at, "screen_state": state, "hud_present": True,
          "characters": ["ALISA", "REINA"], "win_dots": wins, "round_clock": clock,
          "player1": p1, "player2": "Beth", "score1": wins[0], "score2": wins[1],
          "character1": "Alisa", "character2": "Reina"}
         for at, state, p1, wins, clock in TAPE]), lo, hi, **over)


def test_a_reading_taken_on_a_versus_screen_is_off_play_evidence_not_a_correction():
    """The instant can be admissible and still not be a nameplate. Both halves have to survive.

    The loader was already accepting this observation and the tracker was then dropping it on the floor, which
    is the worst of the three possible answers: the reading exists, and no report says where it went.
    """
    sequence = _name_sequence(2260.0, 2280.0, window_start=2260.0,
                              text_corrections=[correction(at=2264.0, raw_text="Menu Name",
                                                            observed_text="Menu Namez",
                                                            observation_id="obs-vs")])
    side = sequence["sides"]["p1"]
    assert side["display_text_corrections_applied"] == [], "a menu row was treated as a nameplate"
    assert [row["role"] for row in side["off_play_text_observations"]] == ["off_play"]
    row = side["off_play_text_observations"][0]
    assert row["row_text"] == "Menu Name" and row["observed_text"] == "Menu Namez"
    assert row["frame_sha256"] == "d" * 64 and row["accepted_by"] is None
    assert row["bound_participant_identity"] is False
    # The trajectory, which is what the identity and winner reasoning reads, is untouched.
    assert [event["to"] for event in side["change_events"]] == ["Alpha Sig"]


def test_a_correction_quoted_as_carry_in_context_says_so_instead_of_looking_applied_twice():
    """One instant, two windows: the game that contains it, and the next one that looks back at it."""
    inside = _name_sequence(10.0, 110.0, window_start=10.0, text_corrections=[correction()])
    assert [row["role"] for row in inside["sides"]["p1"]["display_text_corrections_applied"]] == \
        ["within_game"]
    lookback = _name_sequence(40.0, 110.0, window_start=10.0, previous_play_end=110.0,
                              text_corrections=[correction()])
    quoted = lookback["sides"]["p1"]["display_text_corrections_applied"]
    assert [row["role"] for row in quoted] == ["carry_in_context"], quoted
    assert quoted[0]["observation_id"] == "obs-a", "the quote must trace back to the one reading"
    assert quoted[0]["at"] == 10.0


def test_one_reading_mentioned_by_two_games_is_one_observation_with_two_references():
    from tekken_vod_helper import boundary_identity as bi
    rows = [{"start": 10.0, "nameplate_timing": _name_sequence(10.0, 110.0, window_start=10.0,
                                                              text_corrections=[correction()])},
            {"start": 40.0, "nameplate_timing": _name_sequence(40.0, 110.0, window_start=10.0,
                                                              previous_play_end=110.0,
                                                              text_corrections=[correction()])}]
    seen = bi.display_text_observations(rows)
    assert len(seen) == 1, [row["roles"] for row in seen]
    assert seen[0]["reference_count"] == 2 and seen[0]["observation_id"] == "obs-a"
    # one observation, two mentions: the strongest thing it did is the headline, the rest is listed beside it
    assert seen[0]["role"] == "within_game" and sorted(seen[0]["roles"]) == ["carry_in_context",
                                                                            "within_game"]
    assert sorted(seen[0]["mentioned_in"]) == [10.0, 40.0]
    assert seen[0]["identity_established"] is False
    assert seen[0]["role_meaning"] == bi.READING_ROLE_MEANINGS["within_game"]


def test_an_observation_whose_row_disagrees_with_it_is_refused_and_says_which_string_was_where():
    """A valid side, a real instant and a correct frame hash are still not a correct association.

    The row is what ties a reading to this recording's classifier output. If the string differs, the
    observation is about some other reading: applying it would rewrite pixels nobody compared, and dropping it
    silently would hide that somebody looked and disagreed.
    """
    from tekken_vod_helper import boundary_identity as bi
    sequence = _name_sequence(10.0, 110.0, window_start=10.0,
                              text_corrections=[correction(raw_text="Completely Other Tag")])
    side = sequence["sides"]["p1"]
    assert side["display_text_corrections_applied"] == []
    assert [row["role"] for row in side["display_text_correction_mismatches"]] == \
        ["not_applied_row_text_mismatch"]
    mismatch = side["display_text_correction_mismatches"][0]
    assert mismatch["row_text"] == "Alpha Sig" and mismatch["declared_text"] == "Completely Other Tag"
    assert "neither string is applied" in mismatch["why"]
    assert [event["to"] for event in side["change_events"]] == ["Alpha Sig"] * len(side["change_events"])
    assert bi.display_text_observations([{"nameplate_timing": sequence}]) == []
    refused = bi.display_text_correction_refusals([{"nameplate_timing": sequence}])
    assert len(refused) == 1 and refused[0]["observation_id"] == "obs-a"


def test_spacing_and_case_differences_are_still_the_same_reading():
    """The guard compares readings, not bytes: a stray space is not a different nameplate."""
    sequence = _name_sequence(10.0, 110.0, window_start=10.0,
                              text_corrections=[correction(raw_text="  alpha  sig  ")])
    assert [row["role"] for row in sequence["sides"]["p1"]["display_text_corrections_applied"]] == \
        ["within_game"]


def _admitted(observation_id, at, raw, observed):
    return {"kind": obs.TEXT_KIND, "observation_id": observation_id, "raw_text": raw,
            "observed_text": observed, "reviewed_by": "machine", "settled_by": "saved_frame_pixels",
            "evidence_tier": "machine_display_text",
            "scope": {"source": "recording.mp4", "side": "p1", "at": at, "frame_file": "f",
                      "frame_sha256": "d" * 64}}


def test_disposition_separates_admitted_from_applied_from_repeated():
    loaded = {"accepted": [_admitted("obs-a", 10.0, "Alpha Sig", "Alpha Sigma"),
                           _admitted("obs-b", 2264.0, "Menu Name", "Menu Namez")], "rejected": []}
    sets = [{"start": 10.0, "attribution_confidence": {"display_text_observations": [
        {"observation_id": "obs-a", "role": "within_game", "reference_count": 2, "at": 10.0,
         "side": "p1"}]}},
        {"start": 200.0, "attribution_confidence": {"display_text_observations": [
            {"observation_id": "obs-a", "role": "carry_in_context", "reference_count": 1, "at": 10.0,
             "side": "p1"}]}}]
    summary = obs.disposition(loaded, sets)
    assert summary["admitted_text_observations"] == 2
    assert summary["candidate_references"] == 3, "two mentions in one candidate plus one quoted carry-in"
    by_id = {row["observation_id"]: row for row in summary["observations"]}
    assert by_id["obs-a"]["disposition"] == "applied_within_game"
    assert by_id["obs-a"]["reference_count"] == 3
    assert sorted(by_id["obs-a"]["candidates"]) == [10.0, 200.0]
    assert by_id["obs-a"]["roles"] == ["carry_in_context", "within_game"]
    assert by_id["obs-b"]["disposition"] == "unused" and by_id["obs-b"]["why_not"]
    assert summary["counts"] == {"applied_within_game": 1, "carry_in_context_only": 0, "off_play_only": 0,
                                 "refused_on_row_text": 0, "unused": 1}
    assert summary["identity_bound_by_observations"] == 0 and summary["accepted_by_operator"] == 0
    assert "one observation and two references" in summary["rule"]


def test_disposition_calls_a_versus_screen_reading_off_play_and_never_applied():
    loaded = {"accepted": [_admitted("obs-b", 2264.0, "Menu Name", "Menu Namez")], "rejected": []}
    sets = [{"start": 2260.0, "attribution_confidence": {"display_text_observations": [
        {"observation_id": "obs-b", "role": "off_play", "reference_count": 1, "at": 2264.0,
         "side": "p1"}]}}]
    summary = obs.disposition(loaded, sets)
    row = summary["observations"][0]
    assert row["disposition"] == "off_play_only" and row["supports_no_winner"] is True
    assert any("non-gameplay" in line for line in row["why_not"])
    assert summary["counts"]["off_play_only"] == 1


def test_disposition_counts_a_refused_reading_as_refused_not_applied():
    loaded = {"accepted": [_admitted("obs-a", 10.0, "Alpha Sig", "Alpha Sigma")], "rejected": []}
    sets = [{"start": 10.0, "attribution_confidence": {"display_text_correction_refusals": [
        {"observation_id": "obs-a", "role": "not_applied_row_text_mismatch", "reference_count": 1,
         "at": 10.0, "side": "p1", "why": "the row carries something else"}]}}]
    summary = obs.disposition(loaded, sets)
    assert summary["counts"]["refused_on_row_text"] == 1
    assert summary["observations"][0]["disposition"] == "refused_on_row_text"


def test_a_reference_that_names_no_admitted_observation_is_reported_not_counted():
    loaded = {"accepted": [], "rejected": []}
    sets = [{"start": 10.0, "attribution_confidence": {"display_text_observations": [
        {"observation_id": "invented", "role": "within_game", "reference_count": 1, "at": 10.0,
         "side": "p1"}]}}]
    summary = obs.disposition(loaded, sets)
    assert summary["candidate_references"] == 0 and summary["admitted_text_observations"] == 0
    assert [row["observation_id"] for row in summary["untraceable_references"]] == ["invented"]


# ---------------------------------------------------------------- what a run may apply an observation to


def test_a_run_that_does_not_name_its_recording_applies_nothing(root):
    loaded = load(root, [entry(root)])
    assert loaded["accepted"], "the fixture itself should be admissible"
    without = obs.apply_to(loaded)
    assert without["text_corrections"] == [] and without["stage_readings"] == []
    assert without["applied_anything"] is False
    assert "no recording named" in without["refused"][0]["why"]
    right = obs.apply_to(loaded, source="recording.mp4")
    assert len(right["text_corrections"]) == 1 and right["applied_anything"] is True
    wrong = obs.apply_to(loaded, source="someone-elses-recording.mp4")
    assert wrong["text_corrections"] == [] and wrong["applied_anything"] is False
    assert wrong["refused"][0]["text_observations"] == 1


def test_a_duplicate_entry_is_one_observation_and_the_duplicate_is_refused(root):
    loaded = load(root, [entry(root), entry(root)])
    assert len(loaded["accepted"]) == 1, "the same reading listed twice is one reading"
    assert len(loaded["rejected"]) == 1 and "duplicate" in loaded["rejected"][0]["why"]
    assert len(obs.text_corrections(loaded)) == 1
    assert obs.describe(loaded)["unique_text_observations"] == 1


def test_malformed_entries_are_refused_rather_than_raising(root):
    bad = [entry(root, at="soon"), entry(root, at=float("nan")), entry(root, at=float("inf")),
           entry(root, game_span=[50.0, 10.0]), entry(root, game_span=["a", "b"]),
           "a bare string", 7]
    loaded = load(root, bad)
    assert loaded["accepted"] == []
    assert len(loaded["rejected"]) == len(bad), [row["why"] for row in loaded["rejected"]]
    assert all(row["why"] for row in loaded["rejected"])
    assert obs.text_corrections(loaded) == []
    widened = entry(root)
    widened["scope"] = "recording.mp4"
    assert not load(root, [widened])["accepted"]
    stage = entry(root, kind=obs.STAGE_KIND, raw_text="", observed_text="LOSERS FINAL", side=None,
                  top={"candidates": [["nope", "nope"]]})
    refused = load(root, [stage])
    assert not refused["accepted"] and "finite seconds" in refused["rejected"][0]["why"]


def _mini_timeline():
    """One game, one seat, one row whose text was corrected -- small enough to run inside a test."""
    return [{"timestamp": 10.0, "screen_state": "gameplay", "hud_present": True,
             "characters": ["ALISA", "REINA"], "win_dots": [0, 0], "round_clock": 59,
             "player1": "PWDDA | Az", "player2": "Mattiniero", "score1": 0, "score2": 0,
             "character1": "Alisa", "character2": "Reina"},
            {"timestamp": 15.0, "screen_state": "gameplay", "hud_present": True,
             "characters": ["ALISA", "REINA"], "win_dots": [0, 0], "round_clock": 54,
             "player1": "PWDDA | Az", "player2": "Mattiniero", "score1": 0, "score2": 0,
             "character1": "Alisa", "character2": "Reina"}]


def test_observations_survive_recovery_project_save_and_the_review_card(tmp_path):
    """The seam has to be reachable from the ordinary entry points, or it is a scratch-driver feature.

    recovery -> candidate_records -> import_recovery -> merge_into_project -> ProjectState round trip -> the
    review card's own evidence block: the corrected text, the frame it came from and the two disclaimers must
    all still be there at the end, and nothing may have claimed an identity or an acceptance on the way.
    """
    import hashlib

    from tekken_vod_helper import boundary_evidence as be
    from tekken_vod_helper import boundary_games as bg
    from tekken_vod_helper import boundary_review as review
    from tekken_vod_helper.models import ProjectState

    frame = tmp_path / "frame-0011.jpg"
    frame.write_bytes(b"pixels")
    document = {"observations": [{
        "kind": obs.TEXT_KIND, "raw_text": "PWDDA | Az", "observed_text": "PWDDA | Ariz",
        "reviewed_by": "machine", "settled_by": "saved_frame_pixels",
        "evidence_tier": "machine_display_text", "accepted_by": None, "authoritative": False,
        "bound_participant_identity": False, "requires_operator_confirmation": True,
        "scope": {"source": "recording.mp4", "side": "p1", "at": 10.0, "frame_file": "frame-0011.jpg",
                  "frame_sha256": hashlib.sha256(frame.read_bytes()).hexdigest()}}]}
    path = tmp_path / "observations.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    root = tmp_path
    loaded = obs.load(path, root=root, context={"source": "recording.mp4"})
    assert len(loaded["accepted"]) == 1, loaded["rejected"]
    corrections = obs.text_corrections(loaded, source="recording.mp4")

    observations = bg.build_observations(_mini_timeline())
    tracked = {"assisted": bg.track_games(observations, rounds_per_game=3, games_per_set=2,
                                          text_corrections=corrections),
               "plain": None, "unformatted": None}
    sets = tracked["assisted"]["sets"]
    assert sets, "the mini timeline produced no candidate"
    reported = [row for game in sets for row in
                ((game.get("attribution_confidence") or {}).get("display_text_observations") or [])]
    assert reported, "the tracker applied no correction anywhere"
    assert reported[0]["observed_text"] == "PWDDA | Ariz" and reported[0]["row_text"] == "PWDDA | Az"
    assert reported[0]["bound_participant_identity"] is False and reported[0]["accepted_by"] is None

    records = be.candidate_records({"sets": sets})
    imported = review.import_recovery({"kind": "set-recovery", "candidates": records},
                                     source="recovery.json")
    project = review.merge_into_project({}, imported["segments"], event="Test Event")
    again = ProjectState.from_dict(json.loads(json.dumps(project))).to_dict()
    for stage, doc in (("saved project", project), ("project state round trip", again)):
        rows = (doc["matches"][0]["recovery"].get("attribution_confidence") or {}).get(
            "display_text_observations") or []
        assert rows, stage
        assert rows[0]["frame_sha256"], stage
        assert "PWDDA | Ariz" in json.dumps(doc), stage
    excerpt = review.review_queue(again)[0]["evidence"]
    assert excerpt.get("attribution_confidence", {}).get("score") is not None


def test_a_correction_never_reaches_the_grouping_layer_as_a_boundary_or_a_winner():
    """Text is text: correcting a string may not move a boundary, a target or a verdict."""
    from tekken_vod_helper import boundary_games as bg
    observations = bg.build_observations(_mini_timeline())
    kwargs = {"rounds_per_game": 3, "games_per_set": 2}
    without = bg.track_games(copy.deepcopy(observations), **kwargs)["sets"]
    with_corrected = bg.track_games(copy.deepcopy(observations), text_corrections=[
        {"side": "p1", "at": 10.0, "observed_text": "PWDDA | Ariz"}], **kwargs)["sets"]
    assert len(without) == len(with_corrected)
    for plain, fixed in zip(without, with_corrected):
        keep = ("attribution_confidence", "display_text_observations", "character_bindings")
        assert {key: value for key, value in plain.items() if key not in keep} == \
            {key: value for key, value in fixed.items() if key not in keep}


def test_two_unique_observations_survive_the_normal_path_with_their_real_roles(tmp_path):
    """CLI -> recovery -> saved project -> API, with both readings keeping the role they actually earned.

    This is the case the loader-only test could not see: one observation corrects a gameplay nameplate and the
    other is a valid reading of a versus screen. Both are admissible, only one alters gameplay text, and a
    report that said "two corrections applied" would be claiming a second thing happened. Every layer between
    the file and the card has to keep that distinction, so it is checked at each one.
    """
    import importlib.util

    from tekken_vod_helper import boundary_evidence as be
    from tekken_vod_helper import boundary_games as bg
    from tekken_vod_helper import boundary_review as review
    from tekken_vod_helper.models import ProjectState

    frame = tmp_path / "frame-02641.jpg"
    frame.write_bytes(b"the versus screen, at half a megapixel or whatever")
    digest = hashlib.sha256(frame.read_bytes()).hexdigest()
    rows = [{"timestamp": at, "screen_state": state, "hud_present": True,
             "characters": ["ALISA", "REINA"], "win_dots": [0, 0], "round_clock": clock,
             "player1": p1, "player2": "Beth", "score1": 0, "score2": 0,
             "character1": "Alisa", "character2": "Reina"}
            for at, state, p1, clock in [(10.0, "gameplay", "Alpha Sig", 59),
                                         (15.0, "gameplay", "Alpha Sig", 54),
                                         (2264.0, "vs_screen", "Menu Name", 60),
                                         (2270.0, "gameplay", "Menu Name", 59),
                                         (2275.0, "gameplay", "Menu Name", 54)]]
    document = {"observations": [
        {"kind": obs.TEXT_KIND, "raw_text": "Alpha Sig", "observed_text": "Alpha Sigma",
         "reviewed_by": "machine", "settled_by": "saved_frame_pixels",
         "evidence_tier": "machine_display_text", "accepted_by": None, "authoritative": False,
         "bound_participant_identity": False, "requires_operator_confirmation": True,
         "scope": {"source": "recording.mp4", "side": "p1", "at": 10.0, "frame_file": "frame-02641.jpg",
                   "frame_sha256": digest}},
        {"kind": obs.TEXT_KIND, "raw_text": "Menu Name", "observed_text": "Menu Namez",
         "reviewed_by": "machine", "settled_by": "saved_frame_pixels",
         "evidence_tier": "machine_display_text", "accepted_by": None, "authoritative": False,
         "bound_participant_identity": False, "requires_operator_confirmation": True,
         "scope": {"source": "recording.mp4", "side": "p1", "at": 2264.0, "frame_file": "frame-02641.jpg",
                   "frame_sha256": digest}}]}
    path = tmp_path / "observations.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    loaded = obs.load(path, root=tmp_path, context={"source": "recording.mp4"})
    assert len(loaded["accepted"]) == 2, loaded["rejected"]
    handed = obs.apply_to(loaded, source="recording.mp4")["text_corrections"]
    assert len(handed) == 2 and len({row["observation_id"] for row in handed}) == 2

    tracked = bg.track_games(bg.build_observations(rows), rounds_per_game=3, games_per_set=2,
                             text_corrections=handed)
    sets = tracked["sets"]
    assert sets
    summary = obs.disposition(loaded, sets, source="recording.mp4")
    assert summary["admitted_text_observations"] == 2
    assert summary["counts"]["applied_within_game"] == 1
    assert summary["counts"]["off_play_only"] == 1, [row["roles"] for row in summary["observations"]]
    assert summary["counts"]["refused_on_row_text"] == 0 and summary["untraceable_references"] == []

    records = be.candidate_records({"sets": sets})
    imported = review.import_recovery({"kind": "set-recovery", "candidates": records},
                                     source="recovery.json")
    project = review.merge_into_project({}, imported["segments"], event="Test Event")
    again = ProjectState.from_dict(json.loads(json.dumps(project))).to_dict()
    spec = importlib.util.spec_from_file_location(
        "tvh_review_api", str(Path(__file__).resolve().parents[1]
                              / "prototypes/mobile-review/server.py"))
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)

    for stage, doc in (("saved project", project), ("project state round trip", again)):
        rows_out = (doc["matches"][0]["recovery"].get("attribution_confidence") or {}).get(
            "display_text_observations") or []
        assert len(rows_out) == 2, (stage, rows_out)
        assert len({row["observation_id"] for row in rows_out}) == 2, stage
        by_text = {row["observed_text"]: row for row in rows_out}
        assert by_text["Alpha Sigma"]["role"] == "within_game", stage
        assert by_text["Menu Namez"]["role"] == "off_play", stage
        assert by_text["Menu Namez"]["reference_count"] >= 1
        assert all(row["accepted_by"] is None and not row["bound_participant_identity"]
                   for row in rows_out), stage

    # the mobile card's own projection, which is what a reviewer actually reads
    projected = api.confidence_summary(again["matches"][0]["recovery"])
    assert len(projected["display_text_observations"]) == 2
    by_text = {row["observed_text"]: row for row in projected["display_text_observations"]}
    assert by_text["Alpha Sigma"]["role"] == "within_game"
    assert by_text["Menu Namez"]["role"] == "off_play"
    assert "supports no winner" in by_text["Menu Namez"]["role_meaning"] or \
        "versus screen" in by_text["Menu Namez"]["role_meaning"]
    assert not any(row["bound_participant_identity"] or row["accepted_by"]
                   for row in projected["display_text_observations"])
    assert projected["display_text_correction_refusals"] == []
