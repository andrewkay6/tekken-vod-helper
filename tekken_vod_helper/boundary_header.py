"""Read the broadcast scoreboard band -- the non-pip route for set-membership questions.

Why this module exists. The set layer can now name the evidence a disputed link is missing (a pairing
change with no screen between the games, a side swap, a dwell longer than a round clock), but until now
those questions had nowhere to go: the pipeline's only executable vision question was the round-win pip
row. Deferring a question by name is honest, and it is not recovery. This is the route.

What the band holds, established by reading the crops directly rather than trusting the classifier's
text. Each side carries a **player name** and a **large numeral** (the games-won tally -- the set counter
the layer has never had); the centre carries the **event and round label** ("LOSERS ROUND 3"), the round
clock, and the two groups of round-win pips. Two inspections make the value concrete: at **2602.6 s** the
band reads ``PWDDA | Ariz [0]`` against ``Mattiniero [1]`` under ``LOSERS ROUND 3``, a different pairing
from the one the operator's span list assigns to that time; at **3922.9 s** it reads
``Mattiniero [0]``/``Spitfire2929 [0]`` under ``WINNERS FINAL`` while the round clock is mid-fall, so a
previous pairing can still be on screen when a new match's round is already running.

The discipline is inherited from the pip arm, not restated: ask what is drawn and nothing else, so the
answer cannot be reasoned toward; treat a null as a complete answer and an empty string as a refusal;
digest the exact bytes sent; and reuse ``VisionClient`` so the attempt budget, the unusable-reply retry,
the transport-uncertain rule and the provenance records are the ones already trusted.

One discipline is *not* inherited, and it is the reason a header reading can never be banked as a pip
reading: nothing about this band has been validated against a frozen crop geometry or a control arm. So
every observation carries ``UNVALIDATED_PIP_PROVENANCE``, and its win-pip counts are reported as
``pips_unvalidated`` -- a description of what the scoreboard drew, not evidence about a round.

``plan``, ``render_band`` and ``load_trial`` are offline. The requests go out through
``persist_readings``, reached from the command line by ``scripts/run_header_trial.py`` -- prepare,
execute, restart -- which is where an endpoint, a ledger and a human have to be named deliberately.
"""
import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import boundary_budget
from . import boundary_media as media
from . import boundary_windows
from .boundary_pips import _sha

ARM = "header"
VERSION = "header-band-v1"
SIDES = ("LEFT", "RIGHT")

#: A band answer is about the instant the frame it was rendered from was **drawn**, which on this cache is
#: not the instant a caller asked about: the file name and the sampled time are different numbers, and the
#: gap between them is not constant. Filing an answer under the asked-for number credited pixels taken
#: 0.5005 s early with a later moment, and the join downstream keys on that number to decide which game a
#: reading covers -- i.e. which participant a win is credited to. The tolerance is therefore the same one
#: the crop route already refuses an annotation outside
#: (:data:`tekken_vod_helper.boundary_windows.ANNOTATION_TIME_TOLERANCE`), shared rather than restated so
#: the two routes cannot disagree about when a frame was drawn.
INSTANT_TOLERANCE_SECONDS = boundary_windows.ANNOTATION_TIME_TOLERANCE
#: How far a frame may sit from the instant asked about and still be an answer *for* it. The band trials
#: already refuse beyond this radius; the number only became real once the planner enforced it instead of
#: documenting it.
FRAME_SNAP_RADIUS_SECONDS = 1.0


def _instant(value):
    # type: (Any) -> Optional[float]
    """A usable sampled instant, or None. ``None`` means "this answer cannot be credited to a time"."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    seconds = float(value)
    return seconds if seconds == seconds and seconds != float("inf") else None


#: The band, in the same fractions ``boundary_media`` already uses for its ``broadcast-scoreboard``
#: recognition crop, extended a little past 12 % of frame height because the round-win pips at the base of
#: the nameplates sit just below it and they are what ties a tally to a game.
BAND_Y = 0.135
#: Below this the band cannot hold a name and a numeral together, so no request is built from it. The
#: 640x360 cache yields 49 px and the 1440p master 194 px; the floor only exists to catch a frame that is
#: not a broadcast frame at all.
MIN_BAND_HEIGHT = 24
#: Below this, the field is reported and not used: a name read at 0.3 is a guess about pixels, and the
#: whole reason for this route is that guesses about names were being treated as identities.
USABLE_CONFIDENCE = 0.5

#: Every counted field comes as a pair: the value, and whether the thing being counted is drawn at all.
#: Without the second half the question forces an invented number -- "null only when absent" leaves a model
#: staring at a blurred numeral with three ways to a wrong digit and none to a shrug, and a digit it did not
#: read is a claim about the footage. ``X_drawn: true`` beside ``X: null`` is that shrug, made explicit and
#: scoreable.
HEADER_QUESTION = (
    "Every image is the top band of a Tekken 8 broadcast frame, upscaled. Report only what is drawn in "
    "it.\n"
    "\n"
    "side: LEFT is the player whose name sits on the left half of the band; RIGHT is the other.\n"
    "player: the exact text of that side's name, or null if it is not legible. Do not complete, correct "
    "or guess a truncated name: \"PWDDA | Az\" and \"PWDDA | Ariz\" are different readings, so transcribe "
    "what is drawn.\n"
    "games_drawn: true if a numeral is drawn beside that name, false if that place is empty, null if you "
    "cannot tell whether one is there.\n"
    "games: the integer drawn there. 0 is a reading, so answer 0 when zero is drawn. Answer null whenever "
    "you are not claiming a number -- because none is drawn, or because the drawn one is too unclear to "
    "read. Never answer with a digit you are guessing.\n"
    "pips_drawn: true if that side's row of three circles is drawn, false if it is not drawn or is covered, "
    "null if you cannot tell.\n"
    "pips: how many circles in that side's own row are bright-filled, or null if you are not claiming a "
    "count. A dark empty ring is not a win. Read only that side's row: the other side has its own entry.\n"
    "label: the round or match text across the top centre (for example \"LOSERS ROUND 3\"), or null.\n"
    "confidence: 0.0-1.0 for that side's fields.\n"
    "\n"
    "Answer JSON only: [{\"item\": <int>, \"side\": \"LEFT\"|\"RIGHT\", \"player\": <string|null>, "
    "\"games\": <int|null>, \"games_drawn\": <true|false|null>, \"pips\": <int|null>, "
    "\"pips_drawn\": <true|false|null>, \"label\": <string|null>, \"confidence\": <float>}] with exactly "
    "one entry per side of every item.\n"
    "\n"
    "null is a complete answer about a value you will not claim. Inventing a number to fill a field is "
    "not.\n"
)

#: The four states a counted field can come back in. ``unreadable`` is the one that did not exist before: a
#: drawn value the model declines to name is evidence about the footage, and collapsing it into ``absent``
#: would let an occluded band read as a zeroed scoreboard.
READ_STATES = ("read", "unreadable", "absent", "unknown")


def route_questions(questions, frame_at, radius=FRAME_SNAP_RADIUS_SECONDS):
    # type: (Sequence[Dict[str, Any]], Any, float) -> Dict[str, Any]
    """Turn the set layer's routed questions into header requests, and say what could not be routed.

    ``frame_at(seconds)`` returns the cached frame nearest that time **as the manifest describes it** --
    ``{"path": ..., "timestamp": ...}`` -- or None. The timestamp is not a convenience. The instant a
    question asks about and the instant the chosen frame was drawn are different numbers on this cache, and
    a request filed under the asked-for number credits whichever moment the pixels come from with an answer
    given about another one. So the routed record's ``at`` is the frame's own sampled instant -- the number
    the evidence join keys on -- and the number that was asked for survives beside it as ``requested_at``,
    with the distance in ``offset_seconds``.

    Two cases are refused rather than absorbed. A frame further than ``radius`` from the asked instant is
    not an answer for it, so a cache gap cannot be spanned silently; and a lookup that cannot say when its
    frame was drawn cannot be credited to any instant at all. Both come back in ``unrouted`` naming the
    nearest drawn time, so a plan never drops a question it lacked a frame for and never invents a time for
    one it had.

    Every question whose route is ``header`` becomes a request for the frames at each end of the disputed
    span -- a single sample cannot distinguish "the pairing changed" from "the header was mid-change on this
    frame", which is the whole ambiguity being investigated.
    """
    instants, unrouted = [], []
    for question in questions or ():
        if question.get("route") != ARM:
            unrouted.append({"kind": question.get("kind"), "route": question.get("route"),
                             "because": "no executor for route {!r} in this build".format(
                                 question.get("route"))})
            continue
        span = question.get("at") or []
        if not isinstance(span, (list, tuple)) or not span:
            unrouted.append({"kind": question.get("kind"), "route": ARM,
                             "because": "the question names no span to look at"})
            continue
        for asked in (span[0], span[-1]):
            asked = float(asked)
            found = frame_at(asked)
            path = found.get("path") if isinstance(found, dict) else found
            drawn = _instant(found.get("timestamp")) if isinstance(found, dict) else None
            if not path:
                unrouted.append({"kind": question.get("kind"), "route": ARM, "at": asked,
                                 "because": "no cached frame within {:.1f} s of {:.3f} s".format(
                                     radius, asked)})
                continue
            if drawn is None:
                unrouted.append({"kind": question.get("kind"), "route": ARM, "at": asked,
                                 "frame": str(path),
                                 "because": "{} carries no sampled instant, so an answer about it could be "
                                           "credited to no time".format(Path(str(path)).name)})
                continue
            offset = drawn - asked
            if abs(offset) > radius:
                unrouted.append({"kind": question.get("kind"), "route": ARM, "at": asked,
                                 "frame": str(path), "nearest_drawn_at": drawn,
                                 "because": "the nearest cached frame draws {:.6f} s, which is {:.3f} s "
                                            "from the {:.3f} s asked for -- further than the {:.1f} s a "
                                            "band answer may be credited across".format(
                                                drawn, offset, asked, radius)})
                continue
            instants.append({"at": drawn, "requested_at": asked, "offset_seconds": round(offset, 6),
                             "frame": str(path), "frame_timestamp": drawn,
                             "frame_id": found.get("id") if isinstance(found, dict) else None,
                             "question": question.get("kind"),
                             "why": question.get("because"),
                             "would_resolve": question.get("would_resolve")})
    seen, unique = set(), []
    for entry in instants:
        # Deduplicated by the frame whose instant gets credited: two questions that land on one frame are
        # one request, not two answers about one picture.
        key = (entry["frame"], round(entry["at"], 6))
        if key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    return {"routable": len(unique), "unrouted": unrouted, "instants": unique}


class HeaderError(RuntimeError):
    """A request that cannot be built. Raised before anything is spent, never after."""


def band_box(frame_size):
    # type: (Tuple[int, int]) -> Tuple[int, int, int, int]
    """The scoreboard band for a frame of this size, in pixels."""
    w, h = frame_size
    return (0, 0, w, max(int(round(BAND_Y * h)), 8))


def render_band(frame, out_dir, timestamp=None, scale=3):
    # type: (Any, Any, Optional[float], int) -> Dict[str, Any]
    """Crop and upscale the band from one frame, and digest the bytes that were written.

    The digest is of the crop that leaves this function. A label or a provenance record pointing at the
    uncropped frame would be describing a different picture, which is the error that made an earlier pip
    label wrong at a boundary frame.
    """
    from PIL import Image

    path = Path(str(frame))
    if not path.is_file():
        raise HeaderError("no frame to read at {}".format(path))
    out = Path(str(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    image = Image.open(path).convert("RGB")
    box = band_box(image.size)
    crop = image.crop(box)
    # A band this short cannot show a name and a numeral at any upscale, and asking about it would spend
    # an attempt on a picture that has no answer in it. The cache's 49 px band clears it comfortably.
    if crop.width < 64 or crop.height < MIN_BAND_HEIGHT:
        raise HeaderError("band crop is {}x{} at {}; too small to read a nameplate from".format(
            crop.width, crop.height, path))
    big = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.LANCZOS)
    target = out / "{}-band.png".format(path.stem)
    big.save(target, format="PNG")
    return {"frame": str(path), "frame_size": list(image.size), "path": str(target), "box": list(box),
            "size": [big.width, big.height], "scale": int(scale), "sha256": _sha(target),
            "timestamp": float(timestamp) if timestamp is not None else None}


def plan(instants, out_dir, scale=3):
    # type: (Sequence[Dict[str, Any]], Any, int) -> Dict[str, Any]
    """Render and describe header requests. Offline: nothing is sent and no ledger is opened.

    Each input is ``{"at": seconds, "frame": path, "question": kind, "because": text}`` (``why`` is
    accepted as the same field, which is what ``route_questions`` emits). The question kinds come from the
    set layer's own routed list (``identity_mapping``, ``transition_evidence``), so every header request
    traces back to a specific disagreement between two games rather than to a suspicion someone had while
    reading a timeline. Dropping that field would leave an answer with no question attached to it.
    """
    requests = []
    for entry in instants:
        asked = _instant(entry.get("at"))
        # The frame's own instant wins wherever it is known: it is the moment the pixels came from, and an
        # answer is only worth the instant it describes. ``at`` used to be whatever the caller asked for,
        # which on four preserved answers was 0.5005 s past the frame that produced them.
        drawn = _instant(entry.get("frame_timestamp"))
        if drawn is None:
            drawn = asked
        rendered = render_band(entry["frame"], out_dir, timestamp=drawn, scale=scale)
        requests.append({"id": "header-{}@{:.3f}s".format(Path(rendered["frame"]).stem,
                                                          rendered["timestamp"] or 0.0),
                         "arm": ARM, "question": entry.get("question") or "identity_mapping",
                         "because": entry.get("because") or entry.get("why"),
                         "at": rendered["timestamp"],
                         "requested_at": asked,
                         "offset_seconds": entry.get("offset_seconds")
                         if entry.get("offset_seconds") is not None
                         else (None if (drawn is None or asked is None)
                               else round(drawn - asked, 6)),
                         "frame": rendered["frame"],
                         # The frame's own timestamp, which on this cache is not the same number as the
                         # instant it was chosen for. A label or an answer has to say which of the two it
                         # describes: an off-by-one between them once moved a pip label across a HUD
                         # transition, and a band reading is exactly as good as its frame identity.
                         "frame_timestamp": _instant(entry.get("frame_timestamp")),
                         "frame_id": entry.get("frame_id"),
                         "time_pinned": _instant(entry.get("frame_timestamp")) is not None,
                         "selected_by": entry.get("selected_by"),
                         "image": rendered["path"], "image_sha256": rendered["sha256"],
                         "images": [rendered]})
    return {"kind": "header-request-plan", "arm": ARM, "version": VERSION,
            "prompt_sha256": hashlib.sha256(HEADER_QUESTION.encode("utf-8")).hexdigest()[:16],
            "band_fractions": [0.0, 0.0, 1.0, BAND_Y],
            "requests_planned": len(requests), "images_total": len(requests),
            "completion_tokens_per_request": HeaderReadClient.request_max_tokens,
            # Said here, where the plan is written, rather than only in the report: what a run is allowed
            # to stop for is part of what it measures.
            "stops_when": ["attempt_allowance_reached", "wall_deadline_exceeded",
                           "transport_uncertain_stops_without_retrying",
                           "every_answer_persisted_before_the_next_is_sent"],
            "never_stops_when": ["a reading disagrees with an evaluator label -- the run reads no labels, "
                                 "and a bad transcription is a result it reports and continues from"],
            "what_this_cannot_show": [
                "that a set was recovered: these are transcriptions of one band at a few instants, and "
                "nothing here groups games",
                "that a win-pip count in the band measures a round: band pips are unvalidated diagnostics, "
                "not the crop arm's measurements",
                "the final tally of any set: an unreadable value is reported as unreadable",
            ],
            "requests": requests, "inference_sent": False}


def fingerprint(records, model=None):
    # type: (Sequence[Dict[str, Any]], Optional[str]) -> str
    """Identity of one header request: the arm, the prompt, the model and the bytes actually sent.

    The image digest is in here on purpose. A resumed run must not accept an answer written about a
    different crop, and a crop of the same timestamp from another source (the master, say) is a different
    picture with a different claim on it.
    """
    payload = [ARM, VERSION, model or "",
               [[i["path"], i["sha256"], i["size"]] for r in records for i in r["images"]]]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


#: Stamped onto every header observation so a reader cannot pick up a band reading without its warning.
#: Header pip counts describe the same row of circles the crop arm measures, but under no frozen geometry
#: and with no control arm, so they are a diagnostic and never a substitute for validated pip evidence.
UNVALIDATED_PIP_PROVENANCE = {
    "evidence_class": "unvalidated_header_diagnostic",
    "validated": False,
    "may_replace_validated_pip_evidence": False,
    "note": "a band reading, not a crop measurement: it can name a pairing and a displayed tally, and it "
            "cannot settle a round, a game or a winner",
}


def _field_state(value, drawn, name, side, item, errors):
    # type: (Any, Any, str, str, Any, List[str]) -> Tuple[Optional[int], str]
    """One counted field, its drawn-state, and the contradiction between them.

    A number where the field is reported not drawn is rejected outright: that is the exact shape of an
    invented reading, and it is why the full-frame pip run used to answer ``0-0`` over frames that drew no
    row at all. A null beside ``drawn: true`` is *not* an error -- it is the answer this question exists to
    permit, recorded as ``unreadable`` so a reader can count how often the band could not be read instead of
    pretending an occluded scoreboard said zero.
    """
    if drawn is not None and not isinstance(drawn, bool):
        errors.append("item {} {}: {}_drawn {!r} is not true, false or null".format(
            item, side, name, drawn))
        drawn = None
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
        errors.append("item {} {}: {} {!r} is not a non-negative integer or null".format(
            item, side, name, value))
        value = None
    if value is not None and drawn is False:
        errors.append("item {} {}: {} is not drawn yet {}={} -- a count for a field said to be absent is a "
                      "different claim, not a weak reading".format(item, side, name, name, value))
        value = None
    if value is not None:
        return value, "read"
    if drawn is True:
        return None, "unreadable"
    if drawn is False:
        return None, "absent"
    return None, "unknown"


def validate_reply(reply, items):
    # type: (Any, Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]
    """Exactly one entry per side per item, typed, with no empty strings standing in for readings.

    Stricter than "is it JSON" by design, and no stricter than honesty allows. The reason the band is read
    is that the pipeline stopped guessing names, so an empty name or a tally for a numeral that was never
    drawn are different claims rather than weak ones, and they have to fail. Unreadability is the opposite
    case: a complete answer, which must never be what a retry loop punishes.
    """
    errors = []  # type: List[str]
    if not isinstance(reply, list):
        return [], ["reply is not a JSON array"]
    expected = [(index + 1, side) for index in range(len(items)) for side in ("LEFT", "RIGHT")]
    seen = set()
    out = []  # type: List[Dict[str, Any]]
    for entry in reply:
        if not isinstance(entry, dict):
            errors.append("reply item is not an object")
            continue
        item, side = entry.get("item"), entry.get("side")
        if (item, side) not in expected:
            errors.append("unexpected item/side {!r}/{!r}".format(item, side))
            continue
        seen.add((item, side))
        player = entry.get("player")
        if player is not None and not isinstance(player, str):
            errors.append("item {} {}: player {!r} is not a string or null".format(item, side, player))
            player = None
        if isinstance(player, str) and not player.strip():
            errors.append("item {} {}: an empty name is not a reading, and not a null either".format(
                item, side))
            player = None
        games, games_state = _field_state(entry.get("games"), entry.get("games_drawn"), "games",
                                          side, item, errors)
        pips, pips_state = _field_state(entry.get("pips"), entry.get("pips_drawn"), "pips",
                                        side, item, errors)
        if pips is not None and pips > 3:
            errors.append("item {} {}: pips {} is more circles than the row holds".format(
                item, side, pips))
            pips, pips_state = None, "unknown"
        confidence = entry.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) \
                or not 0.0 <= float(confidence) <= 1.0:
            errors.append("item {} {}: confidence {!r} is not 0.0-1.0".format(item, side, confidence))
            confidence = None
        usable = confidence is not None and float(confidence) >= USABLE_CONFIDENCE
        record = {"item": item, "side": side, "player": (player or "").strip() or None,
                  "player_legible": bool((player or "").strip()),
                  "games": games, "games_state": games_state,
                  "pips": pips, "pips_state": pips_state,
                  "label": (entry.get("label") or None),
                  "confidence": float(confidence) if confidence is not None else None,
                  "usable": bool(usable)}
        record.update(UNVALIDATED_PIP_PROVENANCE)
        out.append(record)
    for pair in expected:
        if pair not in seen:
            errors.append("item {} {} missing from reply".format(*pair))
    return out, errors


def _side_read(observation):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """One side's answer, with the states a caller needs kept apart.

    ``games_state`` is where "0 was drawn", "nothing was drawn" and "a numeral was drawn that I could not
    read" stop being the same event. Collapsing them is how a covered scoreboard became a scoreline.
    """
    return {"player": observation.get("player"),
            "player_legible": observation.get("player_legible"),
            "games": observation.get("games"), "games_state": observation.get("games_state"),
            "confidence": observation.get("confidence"), "usable": observation.get("usable", False)}


def read_pairings(observations):
    # type: (Sequence[Dict[str, Any]]) -> Dict[str, Any]
    """Fold two side-answers into one pairing hypothesis, and say what it cannot support.

    A pairing is a *hypothesis* about which persistent identity sits on which screen side. Two names that
    differ only in case or team tag are recorded as such instead of being silently unified, because that
    unification is exactly the judgement the layer keeps getting wrong on its own.

    The win-pip counts come back under ``pips_unvalidated`` and nowhere else. They describe the same row of
    circles the crop arm reads, but this band has no frozen geometry, no control and no measured accuracy --
    so a caller that needs a pip count has to go and read the crop, and this record is how it knows.
    """
    sides = {o.get("side"): o for o in observations}
    left, right = sides.get("LEFT") or {}, sides.get("RIGHT") or {}
    return {
        "left": _side_read(left), "right": _side_read(right),
        "label": (left or right).get("label"),
        "pips_unvalidated": {"left": left.get("pips"), "right": right.get("pips"),
                             "left_state": left.get("pips_state"),
                             "right_state": right.get("pips_state")},
        "pip_evidence": dict(UNVALIDATED_PIP_PROVENANCE),
        # Whether this pairing can carry a win at all: one unusable side is enough to make the mapping
        # approximate, which is the honest default rather than an exception to argue about later.
        "mapping_usable": bool(left.get("usable") and right.get("usable")),
        "tally": [left.get("games"), right.get("games")],
        "tally_complete": left.get("games") is not None and right.get("games") is not None,
    }


def pairing_hypotheses(requests, groups):
    # type: (Sequence[Dict[str, Any]], Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]
    """The run-report shape: one pairing hypothesis per answered instant, timestamped.

    Used by ``scripts/run_header_trial.py`` when it writes ``report-header.json``. It is *not* the route
    the set layer reads -- that is ``saved_readings`` plus ``boundary_games._band_map``, which credits wins
    and still cannot move a grouping decision. Keeping the two apart matters: this one describes a run, the
    other is evidence, and a caller that reached for the wrong one would be reading a report as if it were
    an answer.
    """
    out = []
    for request, group in zip(requests, groups):
        observations = (group or {}).get("observations") or []
        pairing = read_pairings(observations)
        out.append({"at": request.get("at"), "frame": request.get("frame"),
                    "frame_timestamp": request.get("frame_timestamp"),
                    "frame_id": request.get("frame_id"),
                    "image_sha256": request.get("image_sha256"),
                    "question": request.get("question"), "because": request.get("because"),
                    "pairing": pairing,
                    "answer_errors": (group or {}).get("errors") or [],
                    "answered": bool(observations)})
    return out


class HeaderReadClient(media.VisionClient):
    """One band image per request, both sides answered in the same reply.

    Subclasses the vision client so the attempt budget, the retry-on-unusable-reply loop, the
    transport-uncertain rule and the provenance records stay the ones already trusted. Overriding
    ``prompt_version`` is what keeps a header answer from being replayable as a pip or state answer: the
    fingerprint derives from it, so the three prompt families cannot share a cache.
    """

    prompt_version = VERSION
    # The reply is two short objects. A reasoning model that spends the budget thinking returns no
    # content at all, and this question has no reason to think.
    request_max_tokens = 1500
    request_reasoning_effort = "low"

    @property
    def arm(self):
        # type: () -> str
        return ARM

    def _build_content(self, policy_text, frames, crop_regions=None):
        # type: (str, Sequence[Dict[str, Any]], Optional[bool]) -> List[Dict[str, Any]]
        """The band crops and nothing else: no full frame alongside them, no narrative about matches."""
        listing = "\n".join(
            "item {}: the scoreboard band of {}".format(index, image["path"])
            for index, frame in enumerate(frames, 1) for image in frame["images"])
        content = [{"type": "text", "text": HEADER_QUESTION + "\nImages to read:\n" + listing + "\n"}]
        for index, frame in enumerate(frames, 1):
            for image in frame["images"]:
                raw = Path(image["path"]).read_bytes()
                mime = "image/png" if image["path"].lower().endswith(".png") else "image/jpeg"
                content.append({"type": "text", "text": "item {} image {}".format(
                    index, Path(image["path"]).name)})
                content.append({"type": "image_url",
                                "image_url": {"url": "data:{};base64,".format(mime)
                                              + base64.b64encode(raw).decode("ascii")}})
        return content

    def _parse_output(self, text, frames):
        # type: (str, Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]
        """Structural gate only, exactly as the pip arm does it: JSON, covering what was sent.

        Field values are judged by ``validate_reply``, which is what a caller counts as a failed group.
        Retrying a semantically odd reading here would spend the allowance re-asking a question the model
        answered truthfully -- a null name is a valid answer about an illegible nameplate.
        """
        body = (text or "").strip()
        if body.startswith("```"):
            body = re.sub(r"^```\w*\n?", "", body)
            body = re.sub(r"\n?```$", "", body).strip()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError("header reply is not JSON: {}".format(exc))
        if not isinstance(payload, list):
            raise ValueError("header reply is {} not an array".format(type(payload).__name__))
        asked = sorted((index, side) for index in range(1, len(frames) + 1)
                       for side in SIDES)
        answered = sorted((entry.get("item"), entry.get("side")) for entry in payload
                          if isinstance(entry, dict))
        if asked != answered:
            raise ValueError("header reply covers {} of {} expected answers".format(
                len(set(answered)), len(asked)))
        return payload


# --------------------------------------------------------------------------- execution path

#: What the run directory calls its frozen promise.
MANIFEST_NAME = "prepared.json"
TRIAL_KIND = "header-trial-spec"

#: A runtime spec must not be an evaluation file wearing a hat. If the document that builds the requests
#: also carries the answers, the trial has read its own truth into the run and the comparison is over before
#: it starts -- and the stop rules quietly become label checks.
FORBIDDEN_TRIAL_KEYS = ("labels", "expected", "games_won", "reported", "answer", "answers", "truth",
                        "score", "names")


def load_trial(path):
    # type: (Any) -> Dict[str, Any]
    """Read a runtime request spec, refusing anything that smells like evaluator truth."""
    document = json.loads(Path(str(path)).read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("kind") != TRIAL_KIND:
        raise HeaderError("{} is not a header trial spec (kind {!r} expected)".format(
            path, TRIAL_KIND))
    if not document.get("instants"):
        raise HeaderError("{} lists no instants".format(path))
    for entry in document["instants"]:
        smuggled = [key for key in FORBIDDEN_TRIAL_KEYS if key in entry]
        if smuggled:
            raise HeaderError("trial spec carries evaluator truth in {} -- keep the labels in the "
                              "evaluation file and pass only what to look at".format(smuggled))
    return document


def group_filename(fingerprint_value="*"):
    # type: (str) -> str
    """The name one answered band sits under, and the glob that finds them."""
    return "header-{}.json".format(fingerprint_value)


def request_fingerprint(request, model=None):
    # type: (Dict[str, Any], Optional[str]) -> str
    return fingerprint([request], model=model)


def run_manifest(requests, model, endpoint, scale, attempt_allowance, spec_version=None):
    # type: (Sequence[Dict[str, Any]], str, str, int, int, Optional[str]) -> Dict[str, Any]
    """What this run promised before it sent anything, so a later command can be checked against it.

    The image digests are in here for the same reason the pip manifest holds its crop boxes: a resumed run
    that would send a different picture than the one it is reusing answers for is not a resume, and nothing
    on disk would say so.
    """
    return {
        "kind": "header_band_run",
        "arm": ARM,
        "version": VERSION,
        "spec_version": spec_version,
        "model": model,
        "endpoint": endpoint,
        "scale": int(scale),
        "attempt_allowance": int(attempt_allowance),
        "prompt_sha256": hashlib.sha256(HEADER_QUESTION.encode("utf-8")).hexdigest()[:16],
        "requests": [{"id": r["id"], "at": r.get("at"), "frame": r.get("frame"),
                      "frame_timestamp": r.get("frame_timestamp"), "frame_id": r.get("frame_id"),
                      "selected_by": r.get("selected_by"), "question": r.get("question"),
                      "image": r["image"], "image_sha256": r["image_sha256"],
                      "size": r["images"][0]["size"]}
                     for r in requests],
    }


def manifest_problems(prepared, current):
    # type: (Optional[Dict[str, Any]], Dict[str, Any]) -> List[str]
    """Every way a re-invocation differs from what was prepared, as readable refusals."""
    if not prepared:
        return ["no prepared run in this directory: run --prepare first"]
    problems = []
    for key in ("spec_version", "model", "endpoint", "scale", "attempt_allowance", "prompt_sha256",
                "version", "arm"):
        if prepared.get(key) != current.get(key):
            problems.append("{} changed since prepare: {} -> {}".format(
                key, prepared.get(key), current.get(key)))
    if prepared.get("requests") != current.get("requests"):
        problems.append("the instants, frames or rendered band images changed since prepare")
    return problems


def recorded_answers(out_dir):
    # type: (Any) -> List[str]
    """Files that mean attempts were spent in this directory."""
    out = Path(str(out_dir))
    store = out / "groups"
    if not store.is_dir():
        return []
    return sorted(str(p.relative_to(out)) for p in store.glob(group_filename()))


#: How a preserved answer is described to anyone who reads it. The distinction is not pedantry: an
#: off-ledger spend cannot be laundered into a governed one by re-reading it, and a caller that pretends
#: otherwise would report an unapproved run as the approved trial.
SAVED_KIND = "header-saved-readings"


def _ledger_claim(readings, ledger):
    # type: (List[Dict[str, Any]], Any) -> Dict[str, Any]
    """Describe these answers by the ledger that counted them, or say why it does not.

    The claim is checked against the ledger's own entries: an answer is governed only if some charged
    attempt names the frame it reads. A path that merely exists is not provenance -- pointing the flag at
    an unrelated ledger would otherwise launder an off-books run into a capped one.

    Governance is therefore **all-or-nothing over the answers loaded together**: a directory holding five
    charged answers and six that nothing counted is a mixed load, and reporting it as governed because
    *some* answer matched is the laundering this function exists to prevent -- which is exactly what it did
    on 2026-10-31, when a directory assembled from three runs printed ``attempts_governed_by_a_cap: true``
    over the six unapproved 2026-10-30 responses inside it.
    """
    path = Path(str(ledger))
    if not path.is_file():
        raise HeaderError("no attempt ledger at {}: cannot claim these answers were counted".format(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("kind") != "attempt-ledger":
        raise HeaderError("{} is not an attempt ledger".format(path))
    charged = {str(e.get("request") or ""): e for e in data.get("entries") or []
               if e.get("state") == "answered"}
    matched = []
    for reading in readings:
        # The string the ledger cut its tag from: the frame this answer was given for, by name. Two call
        # sites cut that tag two ways -- `run_header_trial.py` charges a bare ``frame-2401`` while
        # `investigate-games` charges the route-scoped ``header:frame-2401`` -- so matching only the bare
        # stem reported a run that *had* been counted as an ungoverned one. Both spellings are tried,
        # neither is assumed, and an answer no entry names still counts as unrecorded.
        stem = Path(str(reading.get("frame") or "")).stem or None
        entry = None
        if stem:
            entry = charged.get(stem) or charged.get("%s:%s" % (ARM, stem))
        reading["charged_to_ledger"] = entry is not None
        if entry is not None:
            matched.append(entry)
    ungoverned = len(readings) - len(matched)
    governed = bool(matched) and not ungoverned
    origins = sorted({str(r.get("origin_directory") or "?") for r in readings})
    if governed:
        approval, disclosure = "governed", (
            "every answer here names an attempt this ledger charged before it was sent; re-reading them "
            "charges nothing again")
    elif matched:
        approval, disclosure = "mixed", (
            "%d of %d answers name a charged attempt in %s and %d name none, so this load is NOT governed "
            "as a whole: the unmatched answers came from %s and keep whatever status their own run had "
            "(for the 2026-10-30 band responses, sent without a ledger and recorded in that directory's "
            "RECORD.json)" % (len(matched), len(readings), path, ungoverned,
                              ", ".join(origins) if origins else "unlisted directories"))
    else:
        approval, disclosure = "ungoverned", (
            "the ledger at %s has no answered attempt naming these frames, so they are not governed by it"
            % path)
    return {
        "ledger": str(path),
        "approval": approval,
        "ledger_recorded": governed,
        # True only when every answer loaded here was counted. Any is not a quantifier a provenance field
        # may carry: consumers read this as "the spend was bounded", and a mixed load was not.
        "attempts_governed_by_a_cap": governed,
        "attempts_governed_by_a_deadline": governed,
        "ledger_cap": data.get("cap"),
        "ledger_minutes": data.get("minutes"),
        "ledger_deadline_at": data.get("deadline_at"),
        "attempts_charged_in_ledger": data.get("attempts_charged"),
        "answers_matched_to_a_charged_attempt": len(matched),
        "answers_unmatched": ungoverned,
        "answers_from_separate_runs": origins,
        "incident_recorded": any(r.get("origin_incident_recorded") for r in readings),
        "model": (readings[0].get("pairing") or {}).get("model"),
        "disclosure": disclosure,
    }


def _per_origin_provenance(origins, readings, ledger, own):
    # type: (List[Dict[str, Any]], List[Dict[str, Any]], Any, Any) -> Dict[str, Any]
    """Give each origin its own ledger claim, then say what the load is as a whole.

    ``own`` is ``(directory, ledger)``: the store this very command wrote, and the ledger that charged it.
    A single ledger cannot describe a load assembled from answers this run paid for and answers it borrowed
    from runs that were charged somewhere else -- and one sentence about the whole load is how a governed
    61-attempt run came to print "no ledger is claimed for them" over eight answers sitting in its own
    ``attempts.json``, counting them as ``answers_with_no_record_of_their_spend``. The borrowed answers are
    not the run's history and the run's answers are not historical.

    The aggregate keeps every key consumers already read, with the all-or-nothing meaning preserved:
    ``attempts_governed_by_a_cap`` is true only when nothing in the load escaped a ledger.
    """
    own_directory, own_ledger = str(Path(str(own[0]))), own[1]
    claims, governed, unrecorded, incidents = [], 0, 0, 0
    for origin in origins:
        mine = [r for r in readings if str(r.get("origin_directory")) == origin["directory"]]
        applicable = own_ledger if origin["directory"] == own_directory else ledger
        if applicable:
            claim = _ledger_claim(mine, applicable)
        elif origin["incident_recorded"]:
            claim = {"ledger": origin["incident_ledger"], "approval": "incident_recorded",
                     "ledger_recorded": False, "answers_matched_to_a_charged_attempt": 0,
                     "answers_unmatched": len(mine)}
            for reading in mine:
                reading["charged_to_ledger"] = False
        else:
            claim = {"ledger": None, "approval": "ungoverned", "ledger_recorded": False,
                     "answers_matched_to_a_charged_attempt": 0, "answers_unmatched": len(mine)}
            for reading in mine:
                reading["charged_to_ledger"] = False
        origin["ledger"] = claim["ledger"]
        origin["answers_charged_to_the_named_ledger"] = claim["answers_matched_to_a_charged_attempt"]
        claims.append(claim)
        governed += claim["answers_matched_to_a_charged_attempt"]
        # An origin whose spend was written down as an incident is not an origin with no record of it --
        # that is the whole reason RECORD.json exists. Counting it in both columns would move the six
        # unapproved answers from "spend nobody authorised" to "spend nobody mentioned", which is the
        # softer claim and the false one.
        if claim["approval"] == "ungoverned" and not origin["incident_recorded"]:
            unrecorded += len(mine)
        if origin["incident_recorded"]:
            incidents += len(mine)
            if claim["approval"] != "governed":
                claim["approval"] = "incident_recorded"
        # Read last: this is the field a consumer sees per origin, and it has to carry the rewrite above
        # rather than the raw claim from before it.
        origin["ledger_claim"] = claim["approval"]
    total = len(readings)
    states = {c["approval"] for c in claims}
    approval = ("governed" if governed == total else
                "mixed" if len(states) > 1 else (states.pop() if states else "ungoverned"))
    fresh = next((o for o in origins if o["directory"] == own_directory), None)
    fresh_count = fresh["answers"] if fresh else 0
    extra = ("" if not unrecorded else
             "; %d answer(s) sit in a directory with no ledger and no incident record, and remain "
             "unrecorded" % unrecorded)
    disclosure = ("%d of %d band answers here were sent by this command and are charged to its own ledger "
                  "(%s); the other %d are preserved from earlier runs and keep those runs' histories, "
                  "which this load does not speak for%s"
                  % (fresh_count, total, own_ledger, total - fresh_count, extra))
    return {
        "ledger": str(own_ledger) if own_ledger else "none passed for this run",
        "approval": approval,
        "ledger_recorded": governed == total,
        "attempts_governed_by_a_cap": governed == total,
        "attempts_governed_by_a_deadline": governed == total,
        "answers_matched_to_a_charged_attempt": governed,
        "answers_unmatched": total - governed,
        "answers_with_an_incident_record": incidents,
        "answers_with_no_record_of_their_spend": unrecorded,
        "answers_from_separate_runs": [o["directory"] for o in origins],
        "this_run": ({"directory": own_directory, "ledger": str(own_ledger), "answers": fresh_count}
                     if fresh else None),
        "incident_recorded": incidents > 0,
        "model": (readings[0].get("pairing") or {}).get("model"),
        "disclosure": disclosure,
        "per_origin": [{k: o.get(k) for k in ("directory", "answers", "incident_recorded", "ledger",
                                              "ledger_claim", "answers_charged_to_the_named_ledger")}
                       for o in origins],
    }


def response_directories(value):
    # type: (Any) -> List[str]
    """Split a `--responses` value into the run directories it names.

    The argument has always been "the run's own directory", and every caller that needed three runs used to
    solve that by copying their answers into one folder -- which is precisely how six unapproved responses
    came to be reported as ledger-governed on 2026-10-31. Listing the runs keeps each answer's origin, and
    therefore its approval status, attached to the answer.
    """
    items = [item.strip() for item in str(value).split(",")] if value is not None else []
    roots = [item for item in items if item]
    if not roots:
        raise HeaderError("no response directory named")
    missing = [item for item in roots if not (Path(item) / "groups").is_dir()]
    if missing:
        raise HeaderError("not a header response directory (no groups/): {}".format(", ".join(missing)))
    return roots


def _load_directory(root):
    # type: (Path) -> tuple
    """One run's answers, verified against the crops they claim. No request, no write, no move."""
    store = root / "groups"
    if not store.is_dir():
        raise HeaderError("{} has no groups/ directory of band answers".format(root))
    incident = None
    if (root / "RECORD.json").is_file():
        incident = json.loads((root / "RECORD.json").read_text(encoding="utf-8"))
    readings, discarded = [], []
    for path in sorted(store.glob(group_filename())):
        saved = json.loads(path.read_text(encoding="utf-8"))
        request = saved.get("request") or {}
        at = request.get("at")
        if saved.get("errors"):
            discarded.append({"file": path.name, "at": at, "from": str(root),
                              "because": "the stored answer is a failure: {}".format(
                                  "; ".join(saved.get("errors") or []))})
            continue
        mismatched = []
        for image in saved.get("images") or []:
            on_disk = Path(image["path"])
            if not on_disk.is_file():
                mismatched.append("{} is gone".format(image["path"]))
            elif _sha(on_disk) != image.get("sha256"):
                mismatched.append("{} is now {} but the answer was given for {}".format(
                    image["path"], _sha(on_disk)[:16], image.get("sha256")))
        if mismatched:
            discarded.append({"file": path.name, "at": at, "from": str(root),
                              "because": "; ".join(mismatched)})
            continue
        observations = saved.get("observations") or []
        if not observations:
            discarded.append({"file": path.name, "at": at, "from": str(root),
                              "because": "no side was answered"})
            continue
        # The stored instant is the number the run was *asked* for; ``frame_timestamp`` is the number its
        # pixels were drawn at. Four of this recording's preserved answers are the same request under the
        # two, 0.5005 s apart, and the join below reads ``at`` to decide which game a reading covers -- so
        # the reading is credited to the frame it came from, and the correction is kept where a reviewer
        # can see it instead of the answer quietly describing a different moment.
        pinned = _instant(request.get("frame_timestamp"))
        asked = _instant(at)
        corrected = (pinned is not None and asked is not None
                     and abs(pinned - asked) > INSTANT_TOLERANCE_SECONDS)
        readings.append({"at": pinned if corrected else at,
                         "at_requested": asked,
                         "instant_corrected": ({"was": asked, "now": pinned,
                                                "seconds": round(pinned - asked, 6),
                                                "because": "the answer was filed at the instant that was "
                                                           "asked about; the band it was rendered from was "
                                                           "drawn at this one"}
                                               if corrected else None),
                         "time_pinned": pinned is not None,
                         "frame": request.get("frame"), "frame_id": request.get("frame_id"),
                         "frame_timestamp": pinned if pinned is not None
                         else request.get("frame_timestamp"),
                         "question": request.get("question"), "because": request.get("because"),
                         "image_sha256": (saved.get("images") or [{}])[0].get("sha256"),
                         "source_file": path.name,
                         # read_pairings carries the pip quarantine with it, so a consumer cannot pick up
                         # a band count without also picking up what it is not.
                         "pairing": read_pairings(observations)})
    readings.sort(key=lambda r: (r.get("at") is None, r.get("at")))
    return readings, discarded, incident


def saved_readings(directory, ledger=None, own_run=None):
    # type: (Any, Any, Any) -> Dict[str, Any]
    """Load one or more preserved response directories as evidence. Read-only, and honest about ledgers.

    This is how band answers that already exist get used: nothing here sends a request, opens an attempt
    ledger, or moves a file. What it does insist on is *identity* -- a stored answer whose image digest no
    longer matches the crop on disk is a claim about pixels nobody sent, so it is dropped into `discarded`
    with the reason rather than silently trusted.

    Give it **the runs, not a merged copy of them.** A directory assembled by copying answers out of three
    runs is one `groups/` folder whose provenance is now only whatever a sidecar says, and on 2026-10-31
    such a directory reported its contents ledger-governed while six of the eleven answers inside it were
    the unapproved 2026-10-30 spend. Naming the directories keeps each answer's origin -- and its approval
    status -- attached to the answer, so a mixed corpus has to be described as mixed.
    """
    roots = [Path(str(item)) for item in
             (directory if isinstance(directory, (list, tuple, set)) else [directory])]
    readings, discarded, origins = [], [], []
    for root in roots:
        loaded, dropped, incident = _load_directory(root)
        for reading in loaded:
            reading["origin_directory"] = str(root)
            reading["origin_incident_recorded"] = incident is not None
        readings.extend(loaded)
        discarded.extend(dropped)
        origins.append({"directory": str(root), "answers": len(loaded),
                        "incident_recorded": incident is not None,
                        "incident_ledger": (incident or {}).get("measured", {}).get("ledger",
                                                                                    "not recorded here")})
    readings.sort(key=lambda r: (r.get("at") is None, r.get("at")))
    if not readings:
        raise HeaderError("{} holds no reusable band answers: every stored group was a failure, had no "
                          "observations, or no longer matches the crop it claims to describe"
                          .format(", ".join(str(r) for r in roots)))
    unclaimed = [o["directory"] for o in origins if not o["incident_recorded"]]
    if own_run is not None:
        # Per origin, because a load that mixes borrowed answers with answers this command paid for has
        # two different histories and the older one is not a description of the whole.
        provenance = _per_origin_provenance(origins, readings, ledger, own_run)
    elif ledger is not None:
        provenance = _ledger_claim(readings, ledger)
    else:
        provenance = {
            "ledger": "; ".join(o["incident_ledger"] for o in origins if o["incident_recorded"])
                      or "not recorded here",
            # Mixed is a real state, not a rounding error: one of these directories may hold an incident
            # record while its neighbour holds a capped run, and a single boolean cannot say which answers
            # carry which history. `origins` below is where each answer's own status is kept.
            "approval": "mixed" if len(unclaimed) < len(origins) and unclaimed else "ungoverned",
            "ledger_recorded": False,
            "attempts_governed_by_a_cap": False,
            "attempts_governed_by_a_deadline": False,
            "incident_recorded": any(o["incident_recorded"] for o in origins),
            "answers_with_an_incident_record": sum(o["answers"] for o in origins if o["incident_recorded"]),
            "answers_with_no_record_of_their_spend": sum(o["answers"] for o in origins
                                                         if not o["incident_recorded"]),
            "model": (readings[0].get("pairing") or {}).get("model"),
            "disclosure": "these responses were preserved from a run that has already happened; they are "
                          "re-read, never re-sent, and no ledger is claimed for them"
                          + ("" if not unclaimed else "; no ledger is claimed for %s either"
                             % ", ".join(unclaimed)),
        }
    for origin in origins:
        own = [r for r in readings if r.get("origin_directory") == origin["directory"]]
        origin["answers_charged_to_the_named_ledger"] = sum(1 for r in own if r.get("charged_to_ledger"))
    provenance["origins"] = origins
    return {
        "kind": SAVED_KIND, "arm": ARM, "version": VERSION,
        "source_directory": str(roots[0]) if len(roots) == 1 else [str(r) for r in roots],
        "source_directories": [str(r) for r in roots],
        "readings": readings, "discarded": discarded,
        "reused_without_resending": len(readings),
        # How many of these answers can be credited to a moment at all, and how many had to be moved onto
        # the frame they were drawn from. A corpus where most readings are unpinned is a corpus whose
        # attributions are guesses, and that has to be a number in the artifact rather than a footnote.
        "instant_pin": {"pinned": sum(1 for r in readings if r.get("time_pinned")),
                        "unpinned": sum(1 for r in readings if not r.get("time_pinned")),
                        "corrected_to_the_frame": sum(1 for r in readings
                                                      if r.get("instant_corrected")),
                        "tolerance_seconds": INSTANT_TOLERANCE_SECONDS,
                        "because": "a band answer describes the instant its frame was drawn, not the "
                                   "instant somebody asked about"},
        "inference_sent": False,
        "attempts_charged_now": 0,
        "provenance": provenance,
        "cannot": ["be counted as this session's attempt budget",
                   "be re-asked to produce a ledger-backed duplicate of itself",
                   "supply validated win-pip evidence: see pip_evidence on every pairing here"],
    }


def persist_readings(requests, out_dir, client=None, max_requests=8, endpoint=None, model=None,
                     timeout=180, client_factory=None):
    # type: (Sequence[Dict[str, Any]], Any, Optional[Any], int, Optional[str], Optional[str], int, Optional[Any]) -> Dict[str, Any]
    """Send the prepared bands one at a time, persisting every answer so a resume asks nothing twice.

    Deliberately close to ``boundary_pips.persist_probe`` rather than a refactor of it: that loop has 51
    real answers on disk behind it, and reworking it to share a helper is not worth the risk of moving one
    of them. What is shared is the machinery that matters -- the ``VisionClient`` retry and
    transport-uncertain rule, the ledger the caller attaches, and one group file per answer.

    Two rules here are *not* the pip loop's, both from the review:

    * no stop rule reads an expected value. A run halts on the cap, the deadline, or a transport whose
      answer will never arrive. A wrong transcription is a result, reported with its consequences --
      stopping a batch because the model disagreed with a label would make that label the run's referee
      instead of its measurement;
    * an attempt the ledger refuses writes no group file. A deferral is not an answer, and saving one would
      let a later resume reload it as money already spent.
    """
    out = Path(str(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    store = out / "groups"
    store.mkdir(parents=True, exist_ok=True)
    allowance = int(max_requests)
    if client is None:
        factory = client_factory or (
            lambda: HeaderReadClient(endpoint=endpoint, model=model, max_requests=allowance,
                                     timeout=timeout, output_dir=str(out)))
        client = factory()
    asked_model = getattr(client, "model", None) or model
    observations, errors, groups = [], [], []
    resumed = failed = answered = 0
    stopped = None  # type: Optional[str]
    for request in list(requests)[:allowance]:
        fingerprint_value = request_fingerprint(request, asked_model)
        path = store / group_filename(fingerprint_value)
        if path.is_file():
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("errors"):
                failed += 1
            else:
                observations.extend(saved.get("observations") or [])
                answered += 1
            resumed += 1
            groups.append(saved)
            continue
        if getattr(client, "calls", 0) >= allowance:
            stopped = "attempt_allowance_reached"
            break
        try:
            reply = client.classify([request])
        except boundary_budget.BudgetStopped as exc:
            stopped = exc.reason
            errors.append("{}: {}".format(request["id"], exc))
            break
        except ValueError as exc:
            record = _write_group(path, request, fingerprint_value, None, [], str(exc), asked_model)
            groups.append(record)
            errors.append("{}: {}".format(request["id"], exc))
            failed += 1
            if isinstance(exc, media.TransportUncertain):
                stopped = "transport_uncertain"
                break
            continue
        parsed, problems = validate_reply(reply, [request])
        record = _write_group(path, request, fingerprint_value, reply, parsed,
                              "; ".join(problems), asked_model)
        groups.append(record)
        if problems:
            failed += 1
            errors.extend(problems)
        else:
            observations.extend(parsed)
            answered += 1
    planned = min(len(list(requests)), allowance)
    declined = [o for o in observations if o.get("games_state") == "unreadable"
                or o.get("pips_state") == "unreadable"]
    return {"arm": ARM, "model": asked_model,
            "status": "stopped" if stopped else "complete",
            "stopped_reason": stopped,
            "errors": errors,
            "requests_sent": getattr(client, "calls", 0),
            "attempt_allowance": allowance,
            "groups_planned": planned,
            "groups_answered": answered,
            "groups_reused": resumed,
            "groups_failed": failed,
            "not_attempted": max(0, planned - answered - failed),
            # Counted, never treated as a failure to stop on: how much of the band the model could not
            # read is the number that says whether this route is worth funding again.
            "sides_declining_a_value": len(declined),
            "usage": list(getattr(client, "usage", []) or []),
            "observations": observations,
            "groups": groups,
            "evidence_class": UNVALIDATED_PIP_PROVENANCE["evidence_class"]}


def _write_group(path, request, fingerprint_value, reply, observations, error, model=None):
    # type: (Path, Dict[str, Any], str, Any, Sequence[Dict[str, Any]], Optional[str], Optional[str]) -> Dict[str, Any]
    """One answer, its identity, and everything that would have to match for it to mean the same thing."""
    payload = {"kind": "header-group", "arm": ARM, "version": VERSION,
               "fingerprint": fingerprint_value, "model": model,
               "request": {"id": request["id"], "at": request.get("at"), "frame": request.get("frame"),
                           "frame_timestamp": request.get("frame_timestamp"),
                           # Both instants, always: an answer that only knows the number it was asked for
                           # cannot later be checked against the frame it was rendered from, which is how
                           # four preserved answers came to describe a moment 0.5005 s away from their own.
                           "requested_at": request.get("requested_at"),
                           "offset_seconds": request.get("offset_seconds"),
                           "time_pinned": request.get("time_pinned"),
                           "frame_id": request.get("frame_id"), "question": request.get("question"),
                           "because": request.get("because"),
                           "selected_by": request.get("selected_by")},
               "images": [{"path": i["path"], "sha256": i["sha256"], "size": i["size"],
                           "box": i["box"]} for i in request["images"]],
               "observations": list(observations), "reply": reply,
               # The quarantine travels with the stored answer, so a reader who never opened this module
               # still cannot mistake a band count for a measured pip row.
               "pip_evidence": dict(UNVALIDATED_PIP_PROVENANCE),
               "errors": [error] if error else []}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return payload
