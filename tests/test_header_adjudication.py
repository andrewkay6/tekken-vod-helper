"""Saved band readings into the adjudication layer: who won, not whether the set ended.

The band route exists because a nameplate is the only persistent participant identity this recording
offers, and a side swap used to end as "wins are per screen side, shrug". These tests hold the integration
to one narrow claim: a reading inside a game says which person sat on which side, and therefore which
person a win belongs to -- while changing nothing about whether those games are one set.

Half of this file is the negative half, because that is where this feature does damage if it is wrong. A
band reading may not create a boundary, may not move a verdict, may not outvote the pip rows that decided
a game, and may not promote itself over the overlay hypothesis when the two disagree: that case stays
contested and stays a question.

The interesting case is not inside a candidate. A swap makes the link between two games *ambiguous*, which
splits them, so the two games coexist only in the alternative joined reading -- and that is exactly where a
per-player tally is at stake. So the tests check both: each candidate's own wins, and what the joined
reading would credit if the boundary turned out not to be there.

Everything here reads preserved responses (or fixtures shaped exactly like them). No test constructs a
client, and none could send anything.
"""
import json
from pathlib import Path

import pytest

from tekken_vod_helper import boundary_games as bg
from tekken_vod_helper import boundary_header as bh

ROOT = Path(__file__).resolve().parents[1]
PRESERVED = ROOT / "build/work/bb9/header-trial-v1/unapproved-execute-2026-10-30"


def game(index, winner, start=None, end=None):
    item = {"start": (index * 200.0 if start is None else start),
            "end": (index * 200.0 + 150.0 if end is None else end),
            "status": "complete", "winner": winner, "counts_toward_set": True}
    item["conclusion_event_at"] = item["end"]
    return item


def rows(items):
    out = []
    for item, left, right, score in items:
        for at in (item["start"] + 10, item["start"] + 80, item["end"] - 10):
            out.append({"timestamp": at, "screen_state": "gameplay",
                        "overlay": {"player1": left, "player2": right, "score": list(score)}})
    return out


def reading(at, left, right, pips=(None, None), label=None):
    """A saved reading in the exact shape ``saved_readings`` produces, quarantine included."""
    payload = []
    for side, player, lit in (("LEFT", left, pips[0]), ("RIGHT", right, pips[1])):
        payload.append({"item": 1, "side": side, "player": player,
                        "games": None, "games_drawn": None,
                        "pips": lit, "pips_drawn": lit is not None, "label": label, "confidence": 0.9})
    parsed, errors = bh.validate_reply(payload, [{"id": "x"}])
    assert errors == []
    return {"at": at, "frame": "frame.jpg", "frame_id": None, "frame_timestamp": at,
            "image_sha256": "sha-{}".format(at), "question": "identity_mapping",
            "pairing": bh.read_pairings(parsed)}


def header(*readings):
    return {"kind": bh.SAVED_KIND, "arm": bh.ARM, "version": bh.VERSION,
            "source_directory": "test", "readings": list(readings), "discarded": [],
            "inference_sent": False, "attempts_charged_now": 0,
            "provenance": {"ledger": "test fixture"}, "reused_without_resending": len(readings)}


# Two games where the players exchanged sticks, each won on the side holding Alpha. The overlay shows the
# swap, and the side-only count is actively misleading: p1 then p2 reads as one win each.
SWAPPED = [game(0, "p1"), game(1, "p2")]
SWAPPED_ROWS = rows([(SWAPPED[0], "Alpha", "Beta", [0, 0]), (SWAPPED[1], "Beta", "Alpha", [1, 0])])


def grouped(games, observation_rows, evidence=None):
    return bg._group_into_sets(games, 3, observation_rows, header=evidence)


def alternatives(sets):
    return [a for s in sets for a in s["alternative_groupings"]]


# ---------------------------------------------------------------- the load path


def test_the_preserved_responses_load_without_sending_anything():
    """Item 1 of the review, exercised on the real directory rather than on a fixture of it."""
    saved = bh.saved_readings(PRESERVED)
    assert saved["kind"] == bh.SAVED_KIND
    assert len(saved["readings"]) == 6 and saved["discarded"] == []
    assert saved["inference_sent"] is False and saved["attempts_charged_now"] == 0
    assert saved["reused_without_resending"] == 6
    # The incident travels with the data: no re-reading of these answers may imply a ledger governed them.
    assert saved["provenance"]["incident_recorded"] is True
    assert saved["provenance"]["ledger_recorded"] is False
    assert saved["provenance"]["attempts_governed_by_a_cap"] is False
    assert "not passed" in saved["provenance"]["ledger"]
    assert "be counted as this session's attempt budget" in " ".join(saved["cannot"])
    assert [round(float(r["at"]), 1) for r in saved["readings"]] == \
        [3247.2, 3426.4, 3606.6, 3786.8, 3934.9, 3949.9]
    for entry in saved["readings"]:
        assert entry["image_sha256"], "a reading must name the crop it came from"
        assert entry["pairing"]["pip_evidence"]["may_replace_validated_pip_evidence"] is False


def test_a_stored_answer_that_no_longer_matches_its_crop_is_dropped_not_trusted(tmp_path):
    """Identity is checked on read, because an answer is a claim about specific pixels."""
    groups = PRESERVED / "groups"
    source = json.loads(sorted(groups.glob(bh.group_filename()))[0].read_text(encoding="utf-8"))
    store = tmp_path / "groups"
    store.mkdir()
    (store / "header-forged.json").write_text(
        json.dumps(dict(source, images=[dict(source["images"][0], sha256="0" * 16)])), encoding="utf-8")
    with pytest.raises(bh.HeaderError, match="no reusable band answers"):
        bh.saved_readings(tmp_path)
    # And a stored failure is not an answer either.
    (store / "header-forged.json").write_text(json.dumps(dict(source, errors=["bad reply"])),
                                              encoding="utf-8")
    with pytest.raises(bh.HeaderError, match="no reusable band answers"):
        bh.saved_readings(tmp_path)


def test_a_directory_with_no_group_store_is_a_refusal_not_an_empty_corpus(tmp_path):
    with pytest.raises(bh.HeaderError, match="no groups/ directory"):
        bh.saved_readings(tmp_path)


# ---------------------------------------------------------------- what the band may settle


def test_a_confirmed_swap_moves_a_win_to_the_person_rather_than_the_side():
    """The claim this integration exists for.

    Side-only, the joined reading of these two games would be one win each. With a band reading inside
    each, the same two wins are both Alpha's, because Alpha held the left for the first game and the right
    for the second. The side tally is not rewritten -- it is what was observed -- the credit sits beside
    it, labelled as coming from the band.
    """
    plain = grouped(SWAPPED, SWAPPED_ROWS)
    assert [s["game_wins"] for s in plain] == [{"p1": 1, "p2": 0}, {"p1": 0, "p2": 1}]
    assert all("band_mapping" not in alt for alt in alternatives(plain))

    evidence = header(reading(10.0, "Alpha", "Beta"), reading(210.0, "Beta", "Alpha"))
    assisted = grouped(SWAPPED, SWAPPED_ROWS, evidence=evidence)
    assert [s["game_wins"] for s in assisted] == [{"p1": 1, "p2": 0}, {"p1": 0, "p2": 1}], \
        "the observed side tally was overwritten"
    for candidate in assisted:
        attribution = candidate["attribution"]
        assert attribution["unit"] == "broadcast_identity_hypotheses"
        assert attribution["mapping_source"] == "header_band_saved"
        assert attribution["safe_to_read_as_players"] is True
        assert attribution["game_wins_by_identity"] == {"Alpha": 1, "Beta": 0}
        assert attribution["overlay_only_unit"] in ("persistent_players", "screen_sides_only")
        assert all(g["crop_sha256"] for g in attribution["per_game"]), \
            "a credited win must trace to the crop that named it"

    joined = alternatives(assisted)
    assert joined, "the joined reading is where a swap attribution bites, and it went missing"
    for alt in joined:
        assert alt["band_mapping"]["status"] == "swap_confirmed"
        # A score names both players, so a reader can ask whether the other one played.
        assert alt["band_attribution_if_joined"]["game_wins_by_identity"] == {"Alpha": 2, "Beta": 0}
        assert alt["band_mapping_is_approximate"] is True


def test_the_swap_answer_names_who_not_whether():
    """A resolved mapping must leave the boundary question exactly as doubtful as it was."""
    plain = grouped(SWAPPED, SWAPPED_ROWS)
    assisted = grouped(SWAPPED, SWAPPED_ROWS,
                       evidence=header(reading(10.0, "Alpha", "Beta"),
                                       reading(210.0, "Beta", "Alpha")))
    for before, after in zip(plain, assisted):
        cut = after["cut_after"] or after["cut_before"]
        assert cut["verdict"] == "ambiguous", "the band turned a mapping answer into a boundary"
        assert [q["kind"] for q in cut["questions"]] == ["identity_mapping"]
        assert after["status"] == before["status"] and after["membership"] == before["membership"]
        assert after["start"] == before["start"] and after["end"] == before["end"]
    assert len(assisted) == len(plain) == 2


def test_spelling_drift_in_a_tag_still_credits_the_same_player():
    """RebiX/Rebix and a truncated tag are measured behaviour, so identity comparison is tolerant.

    Comparing strings exactly would manufacture a third player out of a spelling, and then a score nobody
    played.
    """
    games = [game(0, "p1"), game(1, "p2")]
    observation_rows = rows([(games[0], "PWDDA | Ariz", "RebiX", [0, 0]),
                             (games[1], "Rebix", "PWDDA | Az", [1, 0])])
    evidence = header(reading(10.0, "PWDDA | Ariz", "RebiX"), reading(210.0, "Rebix", "PWDDA | Az"))
    joined = alternatives(grouped(games, observation_rows, evidence=evidence))
    assert joined and joined[0]["band_mapping"]["status"] == "swap_confirmed"
    assert joined[0]["band_attribution_if_joined"]["game_wins_by_identity"] == \
        {"PWDDA | Ariz": 2, "RebiX": 0}


def test_a_reading_outside_every_game_span_is_never_borrowed():
    """A band read after the players exchanged sticks describes the game behind it, not this one."""
    evidence = header(reading(5000.0, "Alpha", "Beta"))
    assisted = grouped(SWAPPED, SWAPPED_ROWS, evidence=evidence)
    assert all("game_wins_by_identity" not in s["attribution"] for s in assisted)
    for alt in alternatives(assisted):
        assert alt["band_mapping"]["status"] == "uncovered"
        assert "no band reading falls inside" in alt["band_mapping"]["because"]
        assert alt["band_mapping"]["needs_footage"]["route"] == "header"
        assert alt["band_attribution_if_joined"] is None


def test_a_band_covering_one_game_of_the_pair_leaves_the_joint_credit_unresolved():
    evidence = header(reading(10.0, "Alpha", "Beta"))
    assisted = grouped(SWAPPED, SWAPPED_ROWS, evidence=evidence)
    credited = [s for s in assisted if s["attribution"].get("game_wins_by_identity")]
    assert [s["start"] for s in credited] == [0.0], "a game nobody read became attributable"
    assert credited[0]["attribution"]["game_wins_by_identity"] == {"Alpha": 1, "Beta": 0}
    for alt in alternatives(assisted):
        assert alt["band_mapping"]["status"] == "uncovered" and "later game" in \
            alt["band_mapping"]["because"]
        joined = alt["band_attribution_if_joined"] or {}
        assert "game_wins_by_identity" not in joined, "half a mapping became a scoreline"
        assert "no band reading falls inside" in joined.get("withheld", ""), joined


def test_a_band_that_disagrees_with_the_overlay_is_contested_and_neither_side_wins():
    """The band is a broadcast display, not an authority over the classification rows.

    Here it insists the sides never exchanged while the overlay says they did. That is a contradiction
    about who won what, and promoting either witness would be a guess about which source this recording is
    lying about.
    """
    evidence = header(reading(10.0, "Alpha", "Beta"), reading(210.0, "Alpha", "Beta"))
    assisted = grouped(SWAPPED, SWAPPED_ROWS, evidence=evidence)
    for alt in alternatives(assisted):
        assert alt["band_mapping"]["status"] == "contested"
        assert "game_wins_by_identity" not in (alt["band_attribution_if_joined"] or {})
        assert alt["band_mapping"]["needs_footage"]["route"] == "header"
    for candidate in assisted:
        # The contested pair is never credited jointly; an individually-read game still is, and that is a
        # different claim, so the record has to keep them apart.
        if candidate["start"] == 200.0:
            assert "game_wins_by_identity" not in candidate["attribution"]


def test_a_band_that_names_only_one_side_cannot_credit_a_win():
    evidence = header(reading(10.0, None, "Beta"), reading(210.0, "Beta", None))
    assisted = grouped(SWAPPED, SWAPPED_ROWS, evidence=evidence)
    assert all("game_wins_by_identity" not in s["attribution"] for s in assisted)
    joined = alternatives(assisted)
    assert joined[0]["band_mapping"]["status"] == "uncovered"
    assert "named only one side" in joined[0]["band_mapping"]["because"]
    withheld = joined[0]["band_attribution_if_joined"] or {}
    assert "game_wins_by_identity" not in withheld and withheld.get("withheld")


# ---------------------------------------------------------------- what it may never do


def test_a_band_pip_row_cannot_decide_a_game_or_who_won_it():
    """The quarantine has to survive integration, or six band readings silently become a scoreline.

    The band confidently claims three lit circles for Alpha while the game layer counted one win per side.
    The candidates' ``game_wins`` stay the crop arm's, and the band's limits are printed wherever its
    evidence is used.
    """
    evidence = header(reading(10.0, "Alpha", "Beta", pips=(3, 0)),
                      reading(210.0, "Beta", "Alpha", pips=(0, 3)))
    assisted = grouped(SWAPPED, SWAPPED_ROWS, evidence=evidence)
    assert [s["game_wins"] for s in assisted] == [{"p1": 1, "p2": 0}, {"p1": 0, "p2": 1}]
    assert [s["games"] for s in assisted] == [1, 1]
    for candidate in assisted:
        attribution = candidate["attribution"]
        assert attribution["identity_evidence_class"] == "unvalidated_header_diagnostic"
        assert attribution["cannot_settle"], "the band's own limits must be stated where it is used"
        assert "win-dot" in " ".join(attribution["cannot_settle"])


def test_no_band_evidence_reproduces_the_recording_only_output_exactly():
    """The default path is untouched by this feature's existence."""
    for candidate in grouped(SWAPPED, SWAPPED_ROWS):
        assert candidate["cut_mapping_resolution"] == []
        assert "band_readings_used" not in candidate["attribution"]
        assert "mapping_source" not in candidate["attribution"]
        assert all("band_mapping" not in alt for alt in candidate["alternative_groupings"])


def test_band_evidence_changes_no_grouping_shape():
    """Candidates, spans, statuses and verdicts: identical with the band on and off.

    Tested as an equality rather than promised in a docstring, because "the band only answers who" is
    exactly the kind of claim a later refactor quietly breaks.
    """
    evidence = header(reading(10.0, "Alpha", "Beta"), reading(210.0, "Beta", "Alpha"))

    def shape(sets):
        return [(s["start"], s["end"], s["status"], s["membership"], s["games"], s["game_wins"],
                 s["game_winners"], [link["verdict"] for link in s["links"]],
                 [q["kind"] for q in s["questions"]],
                 [(a or {}).get("verdict") for a in (s["cut_before"], s["cut_after"])],
                 [(alt["at"], alt["joined_with"], alt["would_merge"], alt["reading"],
                   alt["why_not_primary"]) for alt in s["alternative_groupings"]])
                for s in sets]

    assert shape(grouped(SWAPPED, SWAPPED_ROWS, evidence=evidence)) == \
        shape(grouped(SWAPPED, SWAPPED_ROWS))


def test_track_games_reports_the_band_route_as_read_only_reuse():
    observations = bg.build_observations(SWAPPED_ROWS)
    evidence = header(reading(10.0, "Alpha", "Beta"), reading(210.0, "Beta", "Alpha"))
    out = bg.track_games(observations, rounds_per_game=3, games_per_set=3, header=evidence)
    route = out["evidence_routes"]["header_band"]
    assert route["supplied"] is True and route["route"] == "saved_readings_reused"
    assert route["inference_sent"] is False and route["attempts_charged_now"] == 0
    assert route["readings"] == 2
    assert "whether a set ended" in " ".join(route["never_resolves"])
    assert out["evidence_routes"]["bracket"] == {"supplied": False, "route": "recording_only"}
    assert all(s["attribution"].get("identity_evidence_class") == "unvalidated_header_diagnostic"
               for s in out["sets"])
    assert bg.track_games(observations, rounds_per_game=3, games_per_set=3)["evidence_routes"][
        "header_band"] == {"supplied": False, "route": "no_band_evidence"}


def test_the_preserved_swap_is_the_case_the_integration_was_written_for():
    """Not a fixture: the real reading at 3247.2 s and the real reading at 3426.4 s.

    The band credits ``PWDDA | Ariz`` on the left at 3247.2 s and on the right at 3426.4 s, under the same
    LOSERS QUARTER-FINAL label, with the displayed tally moving 0-0 to 0-1. That is the measured instance
    of "the same two players exchanged sides inside one set", and it is why a win cannot be stored as a
    side number.
    """
    saved = bh.saved_readings(PRESERVED)
    first = next(r for r in saved["readings"] if abs(float(r["at"]) - 3247.243829) < 0.01)
    second = next(r for r in saved["readings"] if abs(float(r["at"]) - 3426.42282) < 0.01)
    assert first["pairing"]["left"]["player"] == "PWDDA | Ariz"
    assert second["pairing"]["right"]["player"] == "PWDDA | Ariz"
    assert first["pairing"]["tally"] == [0, 0] and second["pairing"]["tally"] == [0, 1]

    games = [game(0, "p1", start=3240.0, end=3400.0), game(1, "p2", start=3420.0, end=3600.0)]
    overlay = rows([(games[0], "PWDDA | Ariz", "Buk", [0, 0]),
                    (games[1], "Buk", "PWDDA | Ariz", [0, 1])])
    joined = alternatives(grouped(games, overlay, evidence=header(first, second)))
    assert joined[0]["band_mapping"]["status"] == "swap_confirmed"
    # Both games were won on the screen side holding Ariz, which is two different sides: counted by side
    # the pair splits 1-1, counted by who the broadcast credited, it is 2-0.
    assert joined[0]["band_attribution_if_joined"]["game_wins_by_identity"] == \
        {"PWDDA | Ariz": 2, "Buk": 0}
    assert joined[0]["band_mapping"]["band"]["images"] == [first["image_sha256"],
                                                          second["image_sha256"]]


# ---------------------------------------------------------------- the public pipeline


class _QuietClient:
    """A client that must never be reached by these tests: it fails the run if it is asked anything."""

    def __init__(self, *args, **kwargs):
        self.calls = 0
        self.observations = []

    def classify(self, group):
        raise AssertionError("the read-only header channel sent a request")


def _workspace(tmp_path, rows, interval=1.0):
    """A synthetic cache and timeline on disk, so the orchestration reads real files."""
    end = max(float(r["timestamp"]) for r in rows)
    frames = []
    for index in range(int(end / interval) + 2):
        stamp = index * interval
        path = tmp_path / "frames" / "frame-{:05d}.jpg".format(index + 1)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xd8\xff\xe0fake")
        frames.append({"id": index + 1, "timestamp": stamp, "path": str(path)})
    manifest = tmp_path / "sample-manifest.json"
    manifest.write_text(json.dumps({"interval": interval, "frames": frames}), encoding="utf-8")
    timeline = tmp_path / "timeline.json"
    timeline.write_text(json.dumps(rows), encoding="utf-8")
    return timeline, manifest


def test_the_public_planner_carries_the_band_channel_without_sending_anything(tmp_path, monkeypatch):
    """``investigate-games --dry-run`` costs no inference, and adding band evidence keeps that true."""
    from tekken_vod_helper import boundary_investigate as bi
    rows = [{"timestamp": 0.0, "state": "gameplay", "confidence": 0.9, "player1": "Ann",
             "player2": "Zoe", "evidence": "timer 60; fighters engaged"},
            {"timestamp": 60.0, "state": "gameplay", "confidence": 0.9, "player1": "Ann",
             "player2": "Zoe", "evidence": "timer 20; fighters engaged"}]
    timeline, manifest = _workspace(tmp_path, rows)
    monkeypatch.setattr(bi, "RoundFactsClient", _QuietClient)
    plan = bi.plan_saved_investigation(timeline, manifest, duration=120.0,
                                       output_dir=tmp_path / "plan", max_requests=0,
                                       header=bh.saved_readings(PRESERVED))
    assert plan["source"]["header_responses"] == str(PRESERVED)
    trace = json.loads((tmp_path / "plan" / "games-trace-before.json").read_text(encoding="utf-8"))
    assert trace["evidence_routes"]["header_band"]["route"] == "saved_readings_reused"
    assert trace["evidence_routes"]["header_band"]["inference_sent"] is False


def test_the_public_run_records_the_band_and_refuses_a_resume_that_changed_it(tmp_path, monkeypatch):
    """Two things at once: the run names the answers it used, and a resume cannot quietly drop them.

    Attribution is evidence about *who*, so changing the band corpus between a run and its resume would
    rewrite the meaning of every credited win in the saved groups. The identity check is what stops that
    being a silent edit.
    """
    from tekken_vod_helper import boundary_investigate as bi
    rows = [{"timestamp": 0.0, "state": "gameplay", "confidence": 0.9, "player1": "Ann",
             "player2": "Zoe", "evidence": "timer 60; fighters engaged"},
            {"timestamp": 60.0, "state": "gameplay", "confidence": 0.9, "player1": "Zoe",
             "player2": "Ann", "evidence": "timer 20; fighters engaged"}]
    timeline, manifest = _workspace(tmp_path, rows)
    saved = bh.saved_readings(PRESERVED)
    monkeypatch.setattr(bi, "RoundFactsClient", _QuietClient)
    out = tmp_path / "inv"
    result = bi.investigate_saved_games(timeline, manifest, duration=120.0, output_dir=out,
                                       max_requests=0, batch_frames=6, header=saved,
                                       client_factory=lambda allowance: _QuietClient(), routes=("state",))
    evidence = result["header_evidence"]
    assert evidence["supplied"] is True and evidence["inference_sent"] is False
    assert evidence["attempts_charged_now"] == 0 and evidence["readings"] == 6
    assert "whether a set ended" in " ".join(evidence["never_resolves"])
    identity = json.loads((out / "investigation-inputs.json").read_text(encoding="utf-8"))
    assert identity["header_responses"] == str(PRESERVED)
    assert identity["header_evidence_sha256"]

    # Same inputs, different band answers: refused, and named.
    substituted = dict(saved, readings=list(saved["readings"])[:3])
    with pytest.raises(ValueError, match="header_evidence_sha256|different inputs"):
        bi.investigate_saved_games(timeline, manifest, duration=120.0, output_dir=out,
                                   max_requests=0, batch_frames=6, header=substituted, resume=True,
                                   client_factory=lambda allowance: _QuietClient(), routes=("state",))


def test_the_band_never_moves_a_boundary_in_the_public_pipeline(tmp_path, monkeypatch):
    """The same run with and without band evidence must group identically down to the link verdicts."""
    from tekken_vod_helper import boundary_investigate as bi
    rows = [{"timestamp": 0.0, "state": "gameplay", "confidence": 0.9, "player1": "Ann",
             "player2": "Zoe", "evidence": "timer 60; fighters engaged"},
            {"timestamp": 70.0, "state": "gameplay", "confidence": 0.9, "player1": "Zoe",
             "player2": "Ann", "evidence": "timer 30; fighters engaged"}]
    timeline, manifest = _workspace(tmp_path, rows)
    monkeypatch.setattr(bi, "RoundFactsClient", _QuietClient)
    saved = bh.saved_readings(PRESERVED)

    def group(run_dir, header):
        result = bi.investigate_saved_games(timeline, manifest, duration=120.0,
                                            output_dir=run_dir, max_requests=0, batch_frames=6,
                                            header=header,
                                            client_factory=lambda allowance: _QuietClient(), routes=("state",))
        trace = json.loads((run_dir / "games-trace-after.json").read_text(encoding="utf-8"))
        return [(s["start"], s["end"], s["status"], [l["verdict"] for l in s["links"]])
                for s in trace["sets"]], result["requests_sent"]

    with_band, sent_with = group(tmp_path / "with", saved)
    without, sent_without = group(tmp_path / "without", None)
    assert with_band == without
    assert sent_with == sent_without == 0, "the read-only channel asked the model something"


# ---------------------------------------------------------------- who counts as identified


def unusable(at, left, right, confidence=0.2):
    """A reading that named both sides and refused to stand behind them."""
    entry = reading(at, left, right)
    pairing = dict(entry["pairing"], mapping_usable=False)
    pairing["left"] = dict(pairing["left"], confidence=confidence, usable=False)
    pairing["right"] = dict(pairing["right"], confidence=confidence, usable=False)
    return dict(entry, pairing=pairing)


def test_a_low_confidence_reading_credits_nobody():
    """`mapping_usable` is the answer's own claim about carrying an identity, and it is binding.

    Before this, usability was "two non-empty strings came back", so a 0.2 transcription put a win in a
    player's name. The band route exists to stop guessing identities; a refused name stays refused here.
    """
    games, observation_rows = games_with_rounds(1, ["p1"])
    candidate = grouped(games, observation_rows, evidence=header(unusable(60.0, "Alice", "Bob")))[0]
    assert "game_wins_by_identity" not in candidate["attribution"]
    withheld = candidate["attribution"]["attribution_withheld"]
    assert "mapping_usable was false" in withheld, withheld
    assert candidate["attribution"]["unit"] == "screen_sides_only"
    assert candidate["attribution"]["safe_to_read_as_players"] is False
    assert candidate["attribution"]["identity_support"]["band_creditable_games"] == 0


def test_two_readings_inside_one_game_that_name_different_players_are_contested():
    """The tie this replaces was a sort order.

    A game holding a reading before the stick swap and one after is a game whose sides are not known, and
    taking whichever row came first credited the win to whoever happened to sort first.
    """
    games, observation_rows = games_with_rounds(1, ["p1"])
    conflict = header(reading(60.0, "Alice", "Bob"), reading(90.0, "Alice", "Charlie"))
    candidate = grouped(games, observation_rows, evidence=conflict)[0]
    assert "game_wins_by_identity" not in candidate["attribution"]
    reason = candidate["attribution"]["attribution_withheld"]
    assert "2 readings name different pairings" in reason, reason
    support = candidate["attribution"]["identity_support"]
    assert support["band_creditable_games"] == 0 and support["games"] == 1
    # And both readings stay in the record: the disagreement is the finding, not an obstacle to it.
    cover = bg._band_map(conflict, games)[round(float(games[0]["start"]), 6)]
    assert cover["arrangements"] == 2 and cover["usable"] is False
    assert [c["sides"]["p2"] for c in cover["conflicting_readings"]] == ["Bob", "Charlie"]
    assert [c["image_sha256"] for c in cover["conflicting_readings"]] == ["sha-60.0", "sha-90.0"]


def test_repeated_agreeing_readings_are_reported_as_support_not_average():
    """Two independent looks that say the same thing are stronger than one, and the record says how many."""
    games, observation_rows = games_with_rounds(1, ["p1"])
    agree = header(reading(60.0, "Alice", "Bob"), reading(90.0, "Alice", "Bob"))
    attribution = grouped(games, observation_rows, evidence=agree)[0]["attribution"]
    assert attribution["game_wins_by_identity"] == {"Alice": 1, "Bob": 0}
    assert attribution["support"]["games_with_agreeing_duplicate_readings"] == 1
    assert attribution["per_game"][0]["readings_agreeing"] == 2
    # Spelling drift is still one arrangement, so a flickering tag cannot manufacture a conflict.
    drift = header(reading(60.0, "Alice", "Bob"), reading(90.0, "Alce", "Bob"))
    drifted = grouped(games, observation_rows, evidence=drift)[0]["attribution"]
    assert drifted["game_wins_by_identity"] == {"Alice": 1, "Bob": 0}


# ---------------------------------------------------------------- participants decide the target


def games_with_rounds(count, winners, start=0.0, span=200.0, names=None):
    """A run of concluded games whose own rows name nobody, as a set between stick swaps looks."""
    made, observation_rows = [], []
    for index in range(count):
        item = {"start": start + index * span, "end": start + index * span + 150.0,
                "status": "complete", "winner": winners[index], "counts_toward_set": True,
                "conclusion_event_at": start + index * span + 150.0}
        made.append(item)
        # An empty overlay is the case that matters: no nameplate, so no pairing hypothesis, so the
        # grouping joins the games and only the band can say who won them.
        observation_rows.append({"timestamp": item["start"] + 20.0, "screen_state": "gameplay",
                                 "overlay": {}})
    return made, observation_rows


def test_one_player_winning_on_alternating_sides_closes_the_configured_set():
    """The reason a target cannot be tested against screen sides once the mapping is known.

    Three games, won p1, p2, p1, with the band naming Alice on the winning side each time: that is a 3-0
    set, and it is 2-1 on sides, which never reaches three and would sit open forever. The band does not
    decide that a set ended -- the observed wins do -- it decides whose they are.
    """
    games, observation_rows = games_with_rounds(3, ["p1", "p2", "p1"])
    evidence = header(reading(20.0, "Alice", "Beth"), reading(220.0, "Beth", "Alice"),
                      reading(420.0, "Alice", "Beth"))
    sets = bg._group_into_sets(games, 3, observation_rows, header=evidence)
    assert len(sets) == 1, "three games the evidence joins were still split"
    winner = sets[0]
    assert winner["game_wins"] == {"p1": 2, "p2": 1}, "the observed side tally was rewritten"
    assert winner["deciding_wins_unit"] == "identities"
    assert winner["deciding_wins"] == {"Alice": 3, "Beth": 0}
    assert winner["status"] == "complete" and winner["closed_by_target"] is True
    assert winner["basis"].startswith("participant_reached_target")
    assert winner["set_winner"] == "Alice" and winner["winner_unit"] == "broadcast_identity_hypothesis"


def test_the_same_three_games_without_the_band_do_not_close():
    """The control for the test above: identical games, no identity evidence, no won set."""
    games, observation_rows = games_with_rounds(3, ["p1", "p2", "p1"])
    sets = bg._group_into_sets(games, 3, observation_rows)
    assert len(sets) == 1
    record = sets[0]
    assert record["deciding_wins_unit"] == "sides"
    assert record["status"] == "partial" and record["set_winner"] is None
    assert record["winner_unit"] is None
    assert any("per-side game wins reaching 3" in item for item in record["missing_evidence"])


def test_an_unresolved_mapping_never_publishes_a_player_winner():
    """A contested or partial mapping may not close a set on a person's name either."""
    games, observation_rows = games_with_rounds(3, ["p1", "p2", "p1"])
    half_read = header(reading(20.0, "Alice", "Beth"), reading(220.0, "Beth", "Alice"))
    sets = bg._group_into_sets(games, 3, observation_rows, header=half_read)
    record = sets[0]
    assert record["status"] == "partial" and record["set_winner"] is None
    assert record["deciding_wins_unit"] == "sides"
    assert any("which participant holds each counted win" in item
               for item in record["missing_evidence"]), record["missing_evidence"]
    assert "no band reading falls inside" in record["attribution"]["attribution_withheld"]


def test_a_reading_that_is_unusable_is_reported_apart_from_a_reading_that_is_missing():
    """"Nobody looked", "nobody could read it" and "the readings disagree" need different follow-ups."""
    games, observation_rows = games_with_rounds(1, ["p1"])
    missing = grouped(games, observation_rows, evidence=header(reading(9000.0, "A", "B")))[0]
    unreadable = grouped(games, observation_rows, evidence=header(unusable(20.0, "Alice",
                                                                          "Beth")))[0]
    disagree = grouped(games, observation_rows,
                       evidence=header(reading(20.0, "Alice", "Beth"),
                                        reading(60.0, "Alice", "Cleo")))[0]
    reasons = [record["attribution"]["attribution_withheld"]
               for record in (missing, unreadable, disagree)]
    assert "no band reading falls inside" in reasons[0]
    assert "mapping_usable was false" in reasons[1]
    assert "name different pairings" in reasons[2]
    assert len(set(reasons)) == 3, reasons
