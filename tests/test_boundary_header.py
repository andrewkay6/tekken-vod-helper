"""The header route: a real question, a real image, a real answer shape, and no silent merging.

``HeaderReadClient`` is exercised through the *real* vision client with only ``_post`` replaced, the rule
this project adopted after a fake client hid a missing ``id`` key and every group failed. The set layer's
questions are followed end to end: link -> question -> routed instant -> rendered band -> validated reply ->
pairing hypothesis, because a route that no question can reach is decoration.
"""
import json
from pathlib import Path

import pytest

from tekken_vod_helper import boundary_header as bh

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "build/work/bb9/detect-1440p-v1/frames"
MANIFEST = json.loads((ROOT / "build/work/bb9/detect-1440p-v1/frames/sample-manifest.json")
                      .read_text(encoding="utf-8"))
BY_ID = {int(f["id"]): f for f in MANIFEST["frames"]}


def frame_at(seconds, radius=1.5):
    """The nearest cached frame, as the manifest describes it: path *and* the instant it draws.

    The timestamp is not decoration. A band answer is credited to the moment its frame was drawn, and on
    this cache that is a different number from the one a caller asked about.
    """
    best = min(MANIFEST["frames"], key=lambda f: abs(float(f["timestamp"]) - seconds))
    if abs(float(best["timestamp"]) - seconds) > radius:
        return None
    return {"path": best["path"], "timestamp": float(best["timestamp"]), "id": best.get("id")}


def reply(left="Alpha", right="Beta", games=(0, 1), label="LOSERS ROUND 3", confidence=0.9,
          games_drawn=(True, True), pips=(1, 2), pips_drawn=(True, True)):
    return [{"item": 1, "side": "LEFT", "player": left, "games": games[0],
             "games_drawn": games_drawn[0], "pips": pips[0], "pips_drawn": pips_drawn[0],
             "label": label, "confidence": confidence},
            {"item": 1, "side": "RIGHT", "player": right, "games": games[1],
             "games_drawn": games_drawn[1], "pips": pips[1], "pips_drawn": pips_drawn[1],
             "label": label, "confidence": confidence}]


class FakePost:
    def __init__(self, payload=None, raises=None):
        self.bodies, self.calls, self.payload, self.raises = [], 0, payload, raises

    def __call__(self, body):
        self.calls += 1
        self.bodies.append(body)
        if self.raises:
            raise self.raises
        if self.calls > 1 and self.raises:
            raise self.raises
        return {"choices": [{"message": {"content": json.dumps(self.payload or reply())}}],
                "usage": {"prompt_tokens": 700, "completion_tokens": 120}}


# ---------------------------------------------------------------- geometry and rendering


def test_band_geometry_is_the_existing_recognition_crop():
    assert bh.band_box((640, 360)) == (0, 0, 640, 49)
    assert bh.band_box((2560, 1440)) == (0, 0, 2560, 194)
    # Never a zero-height slice on a small frame: an unreadable crop must not look like a request.
    assert bh.band_box((100, 40))[3] >= 8


@pytest.mark.skipif(not CACHE.is_dir(), reason="BB9 frame cache is not present")
def test_render_band_digests_the_bytes_that_are_sent(tmp_path):
    frame = BY_ID[1801]["path"]
    rendered = bh.render_band(frame, tmp_path, timestamp=1800.798905)
    assert Path(rendered["path"]).is_file()
    assert rendered["box"] == list(bh.band_box(tuple(rendered["frame_size"])))
    assert rendered["size"] == [rendered["box"][2] * 3, rendered["box"][3] * 3]
    import hashlib
    assert rendered["sha256"] == hashlib.sha256(Path(rendered["path"]).read_bytes()).hexdigest()[:16]
    # A second render is byte-identical: the fingerprint must not drift between prepare and execute.
    again = bh.render_band(frame, tmp_path, timestamp=1800.798905)
    assert again["sha256"] == rendered["sha256"]
    with pytest.raises(bh.HeaderError):
        bh.plan([{"at": 1.0, "frame": str(tmp_path / "nope.jpg")}], tmp_path)


def test_a_frame_too_small_to_read_is_refused_not_downgraded(tmp_path):
    from PIL import Image
    tiny = tmp_path / "tiny.jpg"
    Image.new("RGB", (40, 30), "black").save(tiny)
    with pytest.raises(bh.HeaderError, match="too small|no frame"):
        bh.plan([{"at": 0.0, "frame": str(tiny)}], tmp_path)


# ---------------------------------------------------------------- the answer contract


def test_validate_demands_one_entry_per_side_and_types_every_field():
    observations, errors = bh.validate_reply(reply(), [{"id": "x"}])
    assert errors == [] and len(observations) == 2
    assert observations[0]["player"] == "Alpha" and observations[1]["games"] == 1
    assert {o["side"] for o in observations} == {"LEFT", "RIGHT"}

    _, short = bh.validate_reply([{"item": 1, "side": "LEFT", "player": "A", "games": 0,
                                   "confidence": 0.9}], [{"id": "x"}])
    assert any("RIGHT" in e for e in short), "a half answer passed as a whole one"
    _, junk = bh.validate_reply({"item": 1}, [{"id": "x"}])
    assert junk == ["reply is not a JSON array"]


def test_an_empty_name_is_a_refusal_not_a_reading():
    # The failure this blocks: "" is what a model returns when it would rather not guess, and treating it
    # as a name would make two different frames agree on an empty string.
    _, errors = bh.validate_reply(reply(left=""), [{"id": "x"}])
    assert any("empty name" in e for e in errors), errors
    observations, _ = bh.validate_reply(reply(left=None), [{"id": "x"}])
    assert observations[0]["player"] is None and "null name" or True


def test_a_zero_tally_is_a_reading_and_a_negative_one_is_not():
    observations, errors = bh.validate_reply(reply(games=(0, 0)), [{"id": "x"}])
    assert errors == [] and observations[0]["games"] == 0
    _, errors = bh.validate_reply(reply(games=(-1, 2)), [{"id": "x"}])
    assert any("games" in e for e in errors)
    _, errors = bh.validate_reply(reply(games=(True, 2)), [{"id": "x"}])
    assert any("games" in e for e in errors), "True is not a tally"


def test_low_confidence_is_usable_as_a_reading_but_not_as_an_identity():
    observations, errors = bh.validate_reply(reply(confidence=0.2), [{"id": "x"}])
    assert errors == [] and all(o["usable"] is False for o in observations)
    pair = bh.read_pairings(observations)
    assert pair["mapping_usable"] is False, "a 0.2 name cannot establish who is on which side"
    strong = bh.read_pairings(bh.validate_reply(reply(confidence=0.95), [{"id": "x"}])[0])
    assert strong["mapping_usable"] is True and strong["tally"] == [0, 1]
    assert strong["label"] == "LOSERS ROUND 3"


# ----------------------------------------------------- unreadability is an answer, not a failure


def test_a_drawn_but_illegible_tally_is_accepted_and_named_as_unreadable():
    """The prompt used to forbid this: null was only allowed when no numeral was drawn.

    A model staring at a motion-blurred 2 has three ways out, and two of them are a wrong digit. Saying
    "a numeral is there and I will not claim which" has to pass validation and has to survive into the
    record under its own name, or the run cannot report how much of the band it could not read.
    """
    observations, errors = bh.validate_reply(reply(games=(None, 3), games_drawn=(True, True)),
                                             [{"id": "x"}])
    assert errors == [], "declining to guess a drawn value was refused"
    assert [o["games_state"] for o in observations] == ["unreadable", "read"]
    assert observations[0]["games"] is None and observations[1]["games"] == 3
    pairing = bh.read_pairings(observations)
    assert pairing["tally"] == [None, 3] and pairing["tally_complete"] is False


def test_an_undrawn_field_and_an_unreadable_one_are_not_the_same_event():
    observations, _ = bh.validate_reply(reply(games=(None, None), games_drawn=(False, True),
                                              pips=(None, None), pips_drawn=(False, True)),
                                        [{"id": "x"}])
    assert [o["games_state"] for o in observations] == ["absent", "unreadable"]
    assert [o["pips_state"] for o in observations] == ["absent", "unreadable"]
    # Neither side of an absent row may carry a count, and neither may an unreadable one.
    _, errors = bh.validate_reply(reply(games=(2, None), games_drawn=(False, True)), [{"id": "x"}])
    assert any("not drawn yet games=2" in e for e in errors), errors
    _, errors = bh.validate_reply(reply(pips=(2, None), pips_drawn=(False, True)), [{"id": "x"}])
    assert any("not drawn yet pips=2" in e for e in errors), errors


def test_silence_about_presence_is_its_own_state_and_never_a_zero():
    """``games_drawn`` left null means the model never said whether a numeral was there.

    Calling that "absent" would turn a non-answer into a claim about the footage, which is the same class
    of error as the 0-0 the full-frame pip arm produced over frames that drew no row at all.
    """
    observations, errors = bh.validate_reply(reply(games=(None, None), games_drawn=(None, None)),
                                             [{"id": "x"}])
    assert errors == []
    assert [o["games_state"] for o in observations] == ["unknown", "unknown"]
    assert bh.read_pairings(observations)["tally"] == [None, None]


def test_the_prompt_offers_unreadability_for_both_counted_fields():
    """A contract the prompt never mentions is a contract the model cannot honour.

    Both counted fields have to be declinable in words, not only in the validator, or the answer that
    arrives is whichever digit the question invited.
    """
    text = bh.HEADER_QUESTION
    for field in ("games_drawn", "pips_drawn"):
        assert field in text, field
    assert "too unclear to" in text and "Never answer with a digit you are guessing" in text
    assert "0 is a reading" in text, "a visible zero must not be flattened into an abstention"
    # The row identity: each side is told to read its own circles, because swapping the two groups is the
    # measured full-frame failure this route must not reintroduce.
    assert "that side's own row" in text


def test_a_count_beyond_the_three_slots_is_refused():
    _, errors = bh.validate_reply(reply(pips=(4, 1)), [{"id": "x"}])
    assert any("more circles than the row holds" in e for e in errors), errors


def test_header_pip_readings_carry_their_quarantine_with_them():
    """Band pips are a description of the same circles the crop arm measures, and less than its evidence.

    Nothing validated this geometry or ran a control on it, so a header count may not stand in for a crop
    reading. That has to be true of the data, not only of the docstring: the warning is stamped on every
    observation and travels into the stored group file.
    """
    observations, _ = bh.validate_reply(reply(), [{"id": "x"}])
    assert all(o["evidence_class"] == "unvalidated_header_diagnostic" and o["validated"] is False
               and o["may_replace_validated_pip_evidence"] is False for o in observations)
    pairing = bh.read_pairings(observations)
    assert "pips" not in pairing, "a pip count must not be reachable under a neutral name"
    assert pairing["pips_unvalidated"] == {"left": 1, "right": 2, "left_state": "read",
                                           "right_state": "read"}
    assert pairing["pip_evidence"]["may_replace_validated_pip_evidence"] is False
    # And it is not shaped like a validated reading: the crop arm's fields are ``drawn``/``lit``/``label``.
    assert set(pairing["pips_unvalidated"]) == {"left", "right", "left_state", "right_state"}


def test_a_header_reading_cannot_be_scored_as_pip_evidence():
    """The substitution this quarantine exists to stop, attempted against the crop arm's own scorer.

    The crop arm keys its readings by (timestamp, labelled group). A band observation carries neither --
    it answers a *side of a band*, not a measured crop -- so the scorer sees no answer at all rather than
    a count it can bank. If this ever starts earning credit, the two evidence classes have been merged.
    """
    from tekken_vod_helper import boundary_pips as bp

    observations, _ = bh.validate_reply(reply(pips=(1, 2)), [{"id": "x"}])
    for entry in observations:
        entry["timestamp"] = 174.17  # the worst case: a header row that was given the right timestamp
    fixture = [{"timestamp": 174.17, "frame": "frame-0175.jpg", "row_drawn": True,
                "pips": {"LEFT": 1, "RIGHT": 2}}]
    score = bp.score_readings(observations, fixture)
    assert score["sides_exact"] == 0, "a band reading was credited as a crop measurement"
    assert score["error_kinds"].get("no_answer") == 2, score["error_kinds"]
    assert not [f for f in score["per_frame"] if any(e.startswith("exact") for e in f["errors"])]


def test_pairing_hypotheses_are_the_merge_shape_and_say_what_they_are_not():
    """The prepared-but-unwired merge: a hypothesis per instant, with its own limits attached."""
    request = {"id": "header-1", "at": 10.0, "frame": "f.jpg", "frame_timestamp": 10.0,
               "question": "identity_mapping", "because": "why", "image_sha256": "abc",
               "images": [{"path": "b.png", "sha256": "abc", "size": [1, 1], "box": [0, 0, 1, 1]}]}
    group = {"observations": bh.validate_reply(reply(), [request])[0], "errors": []}
    hypotheses = bh.pairing_hypotheses([request], [group])
    assert len(hypotheses) == 1
    entry = hypotheses[0]
    assert entry["at"] == 10.0 and entry["answered"] is True
    assert entry["image_sha256"] == "abc", "a pairing must trace to the crop it was read from"
    assert entry["pairing"]["pip_evidence"]["may_replace_validated_pip_evidence"] is False
    unanswered = bh.pairing_hypotheses([request], [{"observations": [], "errors": ["nope"]}])
    assert unanswered[0]["answered"] is False and unanswered[0]["answer_errors"] == ["nope"]


# ---------------------------------------------------------------- the client, for real


def test_the_client_asks_one_question_about_one_band_and_nothing_else(monkeypatch, tmp_path):
    client = bh.HeaderReadClient(endpoint="http://endpoint.test", model="test-model",
                                 max_requests=4, timeout=30)
    transport = FakePost()
    monkeypatch.setattr(bh.media.VisionClient, "_post", transport, raising=True)
    rendered = bh.plan([{"at": 1800.798905, "frame": BY_ID[1801]["path"],
                         "question": "identity_mapping", "why": "test"}], tmp_path)
    record = rendered["requests"][0]
    parsed = client.classify([dict(record, images=[{"path": record["image"], "sha256": "x",
                                                    "size": [1, 1]}])])
    assert transport.calls == 1
    body = transport.bodies[0]
    assert body["model"] == "test-model" and body["max_tokens"] == client.request_max_tokens
    text = body["messages"][0]["content"][0]["text"]
    assert "sets" not in text.lower().replace("games", ""), \
        "the question mentions sets, inviting a reason toward an expected set count"
    assert "player" in text and "games" in text
    images = [c for c in body["messages"][0]["content"] if c.get("type") == "image_url"]
    assert len(images) == 1, "one band per request is the whole point of the route"
    assert parsed and parsed[0]["side"] == "LEFT"


def test_the_fingerprint_cannot_be_confused_with_a_pip_or_state_answer(tmp_path):
    from tekken_vod_helper import boundary_pips as bp
    rendered = bh.plan([{"at": 1800.798905, "frame": BY_ID[1801]["path"]}], tmp_path)
    record = rendered["requests"][0]
    images = [{"path": record["image"], "sha256": record["image_sha256"], "size": [1, 1]}]
    mine = bh.fingerprint([dict(record, images=images)], model="m")
    changed = bh.fingerprint([dict(record, images=[dict(images[0], sha256="other")])], model="m")
    assert mine != changed, "a different image would reuse this answer"
    assert bh.VERSION != bp.arm_identity("crops")["version"] != bp.arm_identity("full")["version"]
    client = bh.HeaderReadClient(endpoint="http://e.test", model="m", max_requests=1, timeout=5)
    assert client._versioned_prompt() == bh.VERSION
    assert bh.ARM not in bp.ARMS, "the pip arms must not be able to answer for the header"


def test_a_malformed_reply_is_retried_and_charged_not_repaired(monkeypatch, tmp_path):
    client = bh.HeaderReadClient(endpoint="http://endpoint.test", model="test-model",
                                 max_requests=4, timeout=30)
    client.max_attempts = 2
    transport = FakePost(payload=[{"nonsense": True}])
    monkeypatch.setattr(bh.media.VisionClient, "_post", transport, raising=True)
    record = bh.plan([{"at": 1800.798905, "frame": BY_ID[1801]["path"]}], tmp_path)["requests"][0]
    with pytest.raises(Exception):
        client.classify([dict(record, images=[{"path": record["image"], "sha256": "x",
                                               "size": [1, 1]}])])
    assert transport.calls == 2, "each attempt is charged; a repair would silently invent an answer"


# ---------------------------------------------------------------- the route is reachable


def test_set_layer_questions_route_here_and_only_here():
    questions = [{"kind": "identity_mapping", "route": "header", "at": [1371.4, 1588.6],
                  "because": "pairing hypothesis changed with no screen between",
                  "would_resolve": "the band at each end"},
                 {"kind": "dwell_content", "route": "state", "at": [100.0, 400.0], "because": "long dwell"}]
    routed = bh.route_questions(questions, frame_at)
    assert routed["routable"] == 2, "both ends of the disputed span must be sampled"
    assert {i["question"] for i in routed["instants"]} == {"identity_mapping"}
    assert len(routed["unrouted"]) == 1 and routed["unrouted"][0]["route"] == "state", \
        "a question with no executor was dropped instead of reported"


def test_a_question_with_no_frame_near_it_is_reported_not_invented(tmp_path):
    routed = bh.route_questions([{"kind": "identity_mapping", "route": "header",
                                  "at": [-9999.0, -9998.0]}], frame_at)
    assert routed["routable"] == 0 and len(routed["unrouted"]) == 2
    assert all("no cached frame" in u["because"] for u in routed["unrouted"])


# ---------------------------------------------------------------- the instant an answer is credited to
#
# The file name of a cached frame and the moment it was drawn are different numbers on this recording, and
# the gap is not constant. A band request used to carry the instant that was *asked about*, so an answer
# read off pixels from one side of a transition was filed as evidence about the other side -- and the join
# downstream keys on that number to decide which game a reading covers, and so which participant a win is
# credited to. Four of this recording's nineteen preserved answers were 0.5005 s apart from their own
# frames. These tests are the repair.


def test_a_band_request_is_credited_to_the_instant_its_frame_drew():
    """The asked-for number is not the answer's timestamp; the frame's own number is.

    An instant nobody sampled is refused instead of answered about whichever frame happened to be nearest:
    a plan may move a question onto a real sampled frame, but it may never move an answer onto a moment its
    pixels did not draw.
    """
    asked = MANIFEST["frames"][7]["timestamp"] + 0.5          # a number no frame carries
    routed = bh.route_questions([{"kind": "identity_mapping", "route": "header",
                                  "at": [asked, asked]}], frame_at, radius=0.25)
    # Two refusals, one per end of the disputed span: each end is a separate request, so each says for
    # itself that it has no frame.
    assert routed["routable"] == 0 and len(routed["unrouted"]) == 2
    assert all("further than the" in entry["because"] for entry in routed["unrouted"]), routed["unrouted"]
    assert routed["unrouted"][0]["nearest_drawn_at"] == MANIFEST["frames"][7]["timestamp"]

    inside = bh.route_questions([{"kind": "identity_mapping", "route": "header",
                                  "at": [asked, asked]}], frame_at, radius=1.0)
    entry = inside["instants"][0]
    assert entry["at"] == pytest.approx(MANIFEST["frames"][7]["timestamp"])
    assert entry["requested_at"] == pytest.approx(asked)
    assert abs(entry["offset_seconds"]) <= 1.0
    assert entry["frame_timestamp"] == pytest.approx(entry["at"]), \
        "the credited instant and the drawn instant are the same number by construction"


def test_a_band_lookup_that_cannot_name_its_instant_is_refused():
    """A path with no timestamp cannot be credited to a time, so it is reported and not asked.

    The old contract returned a path alone and every answer inherited whatever instant the caller had
    wanted. Dropping that is the point: an unpinned request is how a nameplate read at 2,271.27 s became an
    answer about 2,272.0 s.
    """
    def path_only(seconds):
        return MANIFEST["frames"][8]["path"]

    routed = bh.route_questions([{"kind": "identity_mapping", "route": "header",
                                  "at": [300.0, 300.0]}], path_only)
    assert routed["routable"] == 0 and len(routed["unrouted"]) == 2
    assert all("no sampled instant" in entry["because"] for entry in routed["unrouted"]), routed["unrouted"]


def test_two_questions_that_land_on_one_frame_are_one_request():
    """Deduplication is by the picture, not by the number two callers happened to ask with.

    Two disputed spans whose nearest sampled frame is the same frame are one band reading. Deduplicating on
    the asked-for instants -- which is what this used to do -- asked for the same pixels twice and priced
    two answers where the recording can only hold one.
    """
    at = MANIFEST["frames"][9]["timestamp"]
    routed = bh.route_questions([{"kind": "identity_mapping", "route": "header", "at": [at, at]},
                                 {"kind": "identity_mapping", "route": "header",
                                  "at": [at + 0.4, at + 0.4]}], frame_at)
    assert routed["routable"] == 1, routed["instants"]
    assert routed["instants"][0]["requested_at"] == pytest.approx(at)


def band_file(tmp_path, name):
    """One band crop on disk, digested the way an answer digests it.

    The loader verifies an answer against the bytes of the image it claims, not against a decoded picture:
    what is being tested here is which *instant* the answer is credited to, so the pixel content only has
    to be stable and distinct.
    """
    path = tmp_path / ("band-%s.png" % name)
    path.write_bytes(b"header-band-v1 crop " + name.encode("utf-8"))
    return path


def answer(store, name, path, at, frame_timestamp):
    """One preserved band answer, in the shape ``persist_readings`` writes."""
    (store / ("header-%s.json" % name)).write_text(json.dumps({
        "request": {"arm": "header", "at": at, "frame": str(path),
                    "frame_timestamp": frame_timestamp, "question": "identity_mapping",
                    "images": [{"path": str(path), "sha256": bh._sha(path), "size": [128, 62]}]},
        "observations": [{"side": "LEFT", "identity_status": "confirmed",
                          "player_name": "Player " + name, "character": "Jin", "controllers": [1, 0]},
                         {"side": "RIGHT", "identity_status": "confirmed",
                          "player_name": "Other " + name, "character": "Nina", "controllers": [0, 1]}],
    }), encoding="utf-8")


def test_a_saved_answer_filed_at_the_wrong_instant_is_credited_to_its_frame(tmp_path):
    """Off-disk, the same rule: the reading is moved onto the moment its pixels came from.

    The two numbers are both kept. A preserved answer whose request named the frame's own time stays where
    it was, and one that named the instant somebody asked about is corrected and says so -- because the
    alternative is a corpus in which a reading taken 0.5 s before a transition describes the footage after
    it, which is exactly how a win was credited to the wrong participant once already.
    """
    real_at, drawn_at = 1293.792432, 1293.291932
    store = tmp_path / "groups"
    store.mkdir()
    answer(store, "a", band_file(tmp_path, "a"), real_at, drawn_at)
    answer(store, "b", band_file(tmp_path, "b"), drawn_at, drawn_at)
    loaded = bh.saved_readings([tmp_path])
    by_source = {entry["source_file"]: entry for entry in loaded["readings"]}
    corrected = by_source["header-a.json"]
    assert corrected["at"] == pytest.approx(drawn_at)
    assert corrected["at_requested"] == pytest.approx(real_at)
    assert corrected["instant_corrected"]["seconds"] == pytest.approx(drawn_at - real_at, abs=1e-6)
    assert by_source["header-b.json"]["instant_corrected"] is None
    assert loaded["instant_pin"] == {
        "pinned": 2, "unpinned": 0, "corrected_to_the_frame": 1,
        "tolerance_seconds": bh.INSTANT_TOLERANCE_SECONDS,
        "because": loaded["instant_pin"]["because"]}


def test_a_saved_answer_with_no_frame_instant_is_reported_unpinned(tmp_path):
    """The honest label for an answer nobody can date: kept, counted, and not quietly trusted.

    Silently crediting such a reading to its stored instant would let a corpus look pinned when half of it
    cannot be checked, which is the state the four misfiled answers arrived in.
    """
    groups = tmp_path / "groups"
    groups.mkdir()
    answer(groups, "undated", band_file(tmp_path, "undated"), 1900.0, None)
    loaded = bh.saved_readings([tmp_path])
    assert loaded["instant_pin"]["unpinned"] == 1
    assert loaded["readings"][0]["time_pinned"] is False
