"""Can the model read the round-win pips when it is shown only the pip row?

The first pilot established what this model/input/prompt configuration did on 52 full 640x360 frames:
the round clock and the ROUND banners were right in every instance, while the win-dot row was wrong on
most frames it attempted, including the frame that decided the game. It did not establish why.
"Too small" is a hypothesis, not a result: enlarging the row by hand makes it obvious to a person,
which fits a resolution limit and equally fits the row simply losing an attention contest inside a
busy frame.

This module tests that one variable, and tests it with a control. Both arms ask the same narrow
question about the same frames and differ only in what they show:

* ``crops`` -- the left pip group and the right pip group, enlarged, nothing else;
* ``full`` -- the whole 640x360 frame, the condition the first pilot used.

Without the ``full`` arm this experiment could not attribute a better score to cropping at all,
because it would also be changing the question: the first pilot asked for six fields plus prose and
spent 71% of its completion budget reasoning before answering any of them. Same frames, same question,
one variable.

Nothing here decides a round, a game or a set, and nothing here is a set engine: it is a measurement of
whether one visual primitive is readable. Expected answers live in a separate annotations file, read
off enlarged crops by eye before any crop was sent, with ambiguous frames recorded as excluded rather
than resolved -- filling a gap from monotonicity would manufacture the ground truth being tested.
"""
import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import boundary_budget
from . import boundary_media

#: Crop boxes inside a 640x360 cache frame, stored as fractions of width and height so a re-extracted
#: cache at another resolution still finds the row. Measured from a coordinate grid drawn across
#: frame-0175.jpg (174.17s): the three left circles occupy x 203-270, the countdown
#: numerals span x 283-340, and the three right circles occupy x 348-410, within y 11-33.
#:
#: Both boxes overlap the edge of the clock so the crop itself shows which end is the centre of the
#: HUD. The first pilot's characteristic error was reporting the two sides in the wrong order, and a
#: crop of bare circles can be assigned left or right only by trusting the text label beside it -- the
#: thing under test, not a control for it.
LEFT_BOX = (214.0 / 640, 11.0 / 360, 302.0 / 640, 33.0 / 360)
RIGHT_BOX = (344.0 / 640, 11.0 / 360, 424.0 / 640, 33.0 / 360)
GROUPS = (("LEFT", LEFT_BOX), ("RIGHT", RIGHT_BOX))

#: Spans the two groups occupy, read off a coordinate grid drawn across frame-0175.jpg and
#: cross-checked by the HUD's own symmetry about x=320 (222-282 mirrors 358-418). Wide
#: enough that a few pixels of error cannot make a crop pass while clipping a circle, narrow enough
#: that a box drifting off the row still fails. The crop boxes above bracket these and the clock, and
#: a rendered preview of every crop is written beside the run so this stays checkable by eye.
LEFT_SPAN_X = (225, 286)
RIGHT_SPAN_X = (363, 410)
CLOCK_X = (295, 355)
ROW_Y = (11, 34)
FRAME_SIZE = (640, 360)

#: Three slots: a game is won at three rounds under the configured format. Told to the model so it
#: counts against a fixed frame of reference instead of inventing a scale.
SLOTS_PER_GROUP = 3
CROP_SCALE = 4
CROP_VERSION = "pip-crops-v1"
FULL_VERSION = "pip-fullframe-v1"
ARMS = ("crops", "full")

#: The whole question. Nothing about rounds, games, scores or players: every one of those is an
#: invitation to reason toward an expected answer instead of reading the pixels in front of it.
PIP_QUESTION = (
    "Every image is the round-win pip row from a Tekken 8 HUD: a row of three circles. A round already\n"
    "won is a bright filled circle. A round not yet won is a dark empty ring.\n"
    "The edge of the large countdown numeral may appear at the inner side of the row: that is the\n"
    "round clock, NOT a pip, and must never be counted.\n"
    "\n"
    "For each labelled image report:\n"
    "  drawn: true only if the three circles are actually present in the image. During round start,\n"
    "    replay and victory transitions the row is not drawn at all -- answer drawn=false, lit=null.\n"
    "    Never answer 0 for a row you cannot see: 0 means six dark circles were looked at.\n"
    "  lit: how many of the three circles are bright filled rather than dark rings. Count them. Pips\n"
    "    light outward from the centre of the HUD, so the clock-adjacent circle fills first.\n"
    "  positions: the 1-based slot numbers that are lit, counted from the slot nearest the clock.\n"
    "  confidence: float 0-1 that you distinguished lit from unlit.\n"
    "If the circles are present but you cannot tell which are lit, answer drawn=true, lit=null, low\n"
    "confidence. Do not report fighters, names, scores, timers or commentary, and do not infer a count\n"
    "from what the round number or the score might imply.\n"
    'Return a JSON array only: [{"item": <int>, "label": <string>, "drawn": <bool|null>,\n'
    '"lit": <int|null>, "positions": [<int>, ...], "confidence": <float>}]'
)


def question(arm):
    # type: (str) -> str
    """The shared question, with only the labelling of what is shown differing per arm."""
    if arm == "crops":
        return ("Each item below is a magnified crop of ONE side of the pip row, labelled LEFT or "
                "RIGHT.\n" + PIP_QUESTION)
    if arm == "full":
        return ("Each item below is one whole video frame, labelled FRAME. Read only the round-win pip "
                "row inside it, and answer twice per frame: once with label LEFT for the group on the "
                "left of the clock, once with label RIGHT for the group on its right.\n" + PIP_QUESTION)
    raise ValueError("unknown arm {!r} (expected one of {})".format(arm, ", ".join(ARMS)))


def arm_identity(arm):
    # type: (str) -> Dict[str, Any]
    """What makes one request comparable to another.

    Geometry and scale are part of the identity, not incidental: the same frame cropped two ways is
    two different pictures, and a resumed run must never answer a new question with the old answer.
    """
    return {
        "arm": arm,
        "version": CROP_VERSION if arm == "crops" else FULL_VERSION,
        "prompt_sha256": hashlib.sha256(question(arm).encode("utf-8")).hexdigest()[:16],
        "groups": {label: [round(v, 8) for v in box] for label, box in GROUPS},
        "scale": CROP_SCALE if arm == "crops" else 1,
        "slots_per_group": SLOTS_PER_GROUP,
    }


def _pixels(box, frame_size=FRAME_SIZE):
    # type: (Sequence[float], Tuple[int, int]) -> Tuple[int, int, int, int]
    w, h = frame_size
    return (int(round(box[0] * w)), int(round(box[1] * h)),
            int(round(box[2] * w)), int(round(box[3] * h)))


def crop_rectangles(frame_size=FRAME_SIZE):
    # type: (Tuple[int, int]) -> Dict[str, Tuple[int, int, int, int]]
    """Pixel rectangle per group, validated to contain all three slots of that group."""
    w, h = frame_size
    scale_x = w / float(FRAME_SIZE[0])
    scale_y = h / float(FRAME_SIZE[1])
    boxes = {label: _pixels(box, frame_size) for label, box in GROUPS}
    for label, (near, far) in (("LEFT", LEFT_SPAN_X), ("RIGHT", RIGHT_SPAN_X)):
        x0, _y0, x1, _y1 = boxes[label]
        # The span is quoted in the 640-wide reference, so it has to be scaled before it can supervise a
        # box cut from a 2560-wide master. Comparing the unscaled numbers rejected every source-resolution
        # crop the first time the master was rendered: "the LEFT group spans x 225-286 but its crop
        # 856-1208 would clip it" -- where 856-1208 is exactly the box the master's own row needs.
        near, far = int(round(near * scale_x)), int(round(far * scale_x))
        if not (x0 <= near and far <= x1):
            raise ValueError("the {} group spans x {}-{} but its crop {}-{} would clip it".format(
                label, near, far, x0, x1))
    for label, box in boxes.items():
        # Same scale correction as above: CLOCK_X is the clock's x-range in the 640 reference plus a
        # 10 px buffer, and this is the check that stops a crop being sent whose side cannot be told
        # from the picture. Unscaled it compares 856 against 338 and refuses every master-resolution
        # crop, which is how this first met the 2560-wide frame.
        near, far = int(round(CLOCK_X[0] * scale_x)), int(round(CLOCK_X[1] * scale_x))
        if not (box[0] < far and box[2] > near):
            raise ValueError("the {} crop shows no part of the clock, so its side cannot be "
                             "told from the image".format(label))
    top, bottom = ROW_Y[0] * scale_y, ROW_Y[1] * scale_y
    if bottom - top < 4:
        raise ValueError("pip row is only {:.0f}px tall at {}x{}".format(bottom - top, w, h))
    return boxes


def write_crops(frame_path, out_dir, scale=CROP_SCALE, timestamp=None):
    # type: (Any, Any, int, Optional[float]) -> Dict[str, Any]
    """Enlarge both pip groups from one cache frame into PNGs. The cache itself is never written.

    PNG, not the classifier's JPEG: the source frame is already lossy, and re-encoding a 94x20 crop at
    JPEG quality smears exactly the boundary between a bright filled circle and a dark ring that the
    whole question turns on.

    ``id`` is not decoration. The inherited vision client stamps its usage and provenance records with
    ``frame["id"]``, and it reads that on the success path, so a record without one does not fail
    visibly -- the base class files the resulting KeyError alongside a malformed reply, burns the
    attempt and retries. Every group in the run then fails while looking like the model's fault.
    """
    from PIL import Image
    out = Path(str(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    source = Path(str(frame_path))
    image = Image.open(source).convert("RGB")
    stamp_text = "untracked" if timestamp is None else "{:.2f}".format(float(timestamp))
    record = {"id": "{}@{}s".format(source.stem, stamp_text),
              "frame": str(source), "frame_size": list(image.size),
              "source_sha256": _sha(source), "scale": scale, "crops": {}}
    for label, rect in crop_rectangles(image.size).items():
        crop = image.crop(rect)
        big = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
        path = out / "{}-{}.png".format(source.stem, label)
        big.save(path, format="PNG")
        record["crops"][label] = {"path": str(path), "box": list(rect), "size": list(big.size)}
    return record


def _sha(path):
    # type: (Path) -> str
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def build_records(frames, out_dir, scale=CROP_SCALE):
    # type: (Sequence[Dict[str, Any]], Any, int) -> List[Dict[str, Any]]
    """Crop every frame once, up front, so the plan describes files that already exist.

    ``frames`` is the annotations list: {"frame", "timestamp", "pips", "row_drawn", ...}. Timestamps
    must come from there and never be recomputed from the filename -- cache frames sit 1.000986 s
    apart, so integer filename arithmetic produces 55.00 s for a frame annotated at 55.05 s, and every
    expected value then silently fails to join (a plan that reads "expected: None" for all eight).
    """
    records = []
    for entry in frames:
        path = entry.get("frame")
        stamp = entry.get("timestamp")
        if not path or stamp is None:
            raise ValueError("annotation entry needs a frame path and a timestamp: {!r}".format(entry))
        if not Path(str(path)).is_file():
            raise ValueError("annotated frame is not on disk: {}".format(path))
        record = dict(write_crops(path, out_dir, scale, timestamp=float(stamp)),
                      timestamp=float(stamp))
        record["expected"] = entry.get("pips")
        # Kept as-is: None means "nobody labelled this frame", which is a third state between "the row
        # is drawn" and "the row is absent". Coercing it through bool() turned an unannotated frame into
        # an absent-row truth, so a model abstention on a frame nobody graded scored correctly_absent.
        record["row_drawn"] = entry.get("row_drawn", True)
        # ``role: "control"`` is what makes a run stop itself. It has to survive into the record or the
        # gate below has nothing to look at and the batch spends every attempt before anyone notices the
        # framing was wrong.
        record["role"] = entry.get("role")
        records.append(record)
    return records


def control_problems(record, observations):
    # type: (Dict[str, Any], Sequence[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]
    """Every way this answered control group disagrees with the truth paired to it, or None.

    A control is only a control when its expected value comes from **the same instant, the same box and
    the same side-of-the-image semantics**. The disagreement it looks for is therefore about presence and
    count only; a match is evidence that the framing renders the same thing twice, not that either read
    is right about the fight.
    """
    expect = record.get("expected") or {}
    answered = {o.get("label"): o for o in observations}
    problems = []
    for side, want in sorted(expect.items()):
        if want is None:
            continue                       # withheld truth: nothing to match, so nothing to claim
        entry = answered.get(side) or {}
        if entry.get("drawn") is not True or entry.get("lit") != want:
            problems.append({"side": side, "expected": want, "answered": entry.get("lit"),
                             "answered_drawn": entry.get("drawn"),
                             "confidence": entry.get("confidence")})
    if not any(value is not None for value in expect.values()):
        problems.append({"side": None, "expected": None, "answered": sorted(answered),
                         "because": "a control frame with nothing expected of it cannot fail, which "
                                    "makes it a request and not a control"})
    return problems or None


def is_cache_framing(group, frame_size=FRAME_SIZE):
    # type: (Dict[str, Any], Tuple[int, int]) -> bool
    """Whether this answered group describes the 640x360 sample cache rather than the master.

    A pip series has to be read in one framing. A master crop of the *same instant* is a second row at
    that instant, and the tracker reads two rows at one time as the dots moving -- so replaying a trial
    over every crop directory on disk would silently gain duplicate evidence at exactly the instants
    where a source-framing experiment answered the same frame. Group files that predate the master
    framing carry no ``frame_size`` and are cache readings by construction.
    """
    declared = group.get("frame_size")
    if not declared:
        return True
    return list(declared) == list(frame_size)


def group_filename(arm, fingerprint="*"):
    # type: (str, Any) -> str
    """The name one answered group is stored under, and the glob that finds them.

    Owned here because the caller that deletes them for a restart has to agree with the writer: a
    ``--restart`` matching no files reports success while leaving every answer in place, and the next
    ``--execute`` then silently reuses them instead of re-spending the allowance.
    """
    return "{}-{}.json".format(arm, fingerprint)


def group_fingerprint(records, arm, model=None):
    # type: (Sequence[Dict[str, Any]], str, Optional[str]) -> str
    """Request identity: these frames, shown this way, to this model, under this question.

    The rendered crop size is hashed, not just the path, because ``--scale`` can differ from
    ``CROP_SCALE`` while ``arm_identity`` still reports the constant; without it two different pictures
    would share one fingerprint. ``model`` belongs here for the same reason a resumed run with a
    different ``--model`` must not count the previous model's replies as evidence about this one --
    the whole comparison rests on which arm produced which answer.
    """
    payload = json.dumps({
        "identity": arm_identity(arm),
        "model": model,
        "items": [[r["frame"], r["source_sha256"],
                   sorted([r["crops"][label]["path"], list(r["crops"][label]["size"])]
                          if arm == "crops" else [""] for label, _ in GROUPS)]
                  for r in records]}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def plan_requests(records, arm, max_requests=8, model=None):
    # type: (Sequence[Dict[str, Any]], str, int, Optional[str]) -> Dict[str, Any]
    """Exactly which images would be sent, one frame per request, before any request exists.

    One frame per request even though the crops are tiny. The error classes under test are cross-side
    confusion and cross-frame flicker, and packing several frames into one image list makes a wrong
    answer ambiguous about which frame or which side was misread -- which is the whole measurement.
    """
    taken = list(records)[:max_requests]
    groups = [{"group": i, "frame": r["frame"], "timestamp": r.get("timestamp"),
               "images": (len(GROUPS) if arm == "crops" else 1),
               "image_paths": ([r["crops"][label]["path"] for label, _ in GROUPS]
                               if arm == "crops" else [r["frame"]]),
               "fingerprint": group_fingerprint([r], arm, model)}
              for i, r in enumerate(taken)]
    return {
        "kind": "pip_read_probe",
        "status": "plan",
        "arm": arm,
        "arm_identity": arm_identity(arm),
        "question": question(arm),
        "frames_requested": len(taken),
        "frames_deferred": max(0, len(records) - len(taken)),
        "deferred_frames": [r["frame"] for r in records[max_requests:]],
        "groups": groups,
        "requests_planned": len(groups),
        "images_total": sum(g["images"] for g in groups),
        "attempt_allowance": max_requests,
        "completion_tokens_per_request": PipReadClient.request_max_tokens,
        "stops_when": ["attempt_allowance_reached",
                       "transport_uncertain_stops_without_retrying",
                       "every_group_persisted_before_the_next_is_sent"],
        "what_this_cannot_show": [
            "that a whole game or set can be reconstructed: this asks only about one row of circles",
            "that any particular frame's score is right: expected values are a manual read, "
            "recorded separately and challengeable",
        ],
    }


def run_manifest(records, arms, scale, max_requests, model=None, annotation_version=None):
    # type: (Sequence[Dict[str, Any]], Sequence[str], int, int, Optional[str], Optional[str]) -> Dict[str, Any]
    """What this run promised before it sent anything, so a later invocation can be checked against it.

    A prepared run is only resumable if the inputs are still the inputs. Reusing crop answers after the
    annotations changed would score new claims against old truth, and reusing them after the geometry
    changed would mix two pictures under one number -- both silently. The model is recorded because an
    arm comparison between two models is not the comparison that was approved.
    """
    return {
        "kind": "pip_crop_run",
        "annotation_version": annotation_version,
        "model": model,
        "scale": int(scale),
        "attempt_allowance": int(max_requests),
        "arms": sorted(arms),
        "arm_identities": {arm: arm_identity(arm) for arm in arms},
        "frames": [{"frame": r["frame"], "timestamp": r.get("timestamp"),
                    "source_sha256": r.get("source_sha256"),
                    "row_drawn": r.get("row_drawn"), "expected": r.get("expected"),
                    "crops": {label: [r["crops"][label]["path"], list(r["crops"][label]["size"]),
                                      r["crops"][label]["box"]] for label, _ in GROUPS}}
                   for r in records],
    }


def manifest_problems(prepared, current):
    # type: (Optional[Dict[str, Any]], Dict[str, Any]) -> List[str]
    """Every way a re-invocation differs from what was prepared, as readable refusals."""
    if not prepared:
        return ["no prepared run in this directory: run --prepare first"]
    problems = []
    for key in ("annotation_version", "model", "scale", "attempt_allowance", "arms"):
        if prepared.get(key) != current.get(key):
            problems.append("{} changed since prepare: {} -> {}".format(
                key, prepared.get(key), current.get(key)))
    if prepared.get("frames") != current.get("frames"):
        problems.append("frames, crops or expected values changed since prepare")
    for arm in current.get("arms") or []:
        if (prepared.get("arm_identities") or {}).get(arm) != current["arm_identities"][arm]:
            problems.append("arm {} question or geometry changed since prepare".format(arm))
    return problems


LABELS = ("LEFT", "RIGHT")


def validate_reply(reply, items, arm="crops"):
    # type: (Any, Sequence[Dict[str, Any]], str) -> Tuple[List[Dict[str, Any]], List[str]]
    """Types, labels, slot ranges -- and a refusal to accept a count for a row said to be absent.

    ``drawn: false`` with a number in ``lit`` is a contradiction inside the reply itself, not a
    reading, and it is the failure mode this probe exists to catch: the full-frame run answered 0-0 on
    frames where no pip row was drawn at all.
    """
    errors = []  # type: List[str]
    if not isinstance(reply, list):
        return [], ["reply is not a JSON array"]
    expected = [(index + 1, label) for index in range(len(items)) for label in LABELS]
    out = []  # type: List[Dict[str, Any]]
    for entry in reply:
        if not isinstance(entry, dict):
            errors.append("reply item is not an object")
            continue
        item, label = entry.get("item"), entry.get("label")
        if label not in LABELS:
            errors.append("label {!r} is not LEFT or RIGHT".format(label))
            continue
        drawn = entry.get("drawn")
        if drawn is not None and not isinstance(drawn, bool):
            errors.append("item {} {}: drawn {!r} is not a boolean or null".format(item, label, drawn))
            drawn = None
        lit = entry.get("lit")
        if lit is not None and (isinstance(lit, bool) or not isinstance(lit, int)
                                or not 0 <= lit <= SLOTS_PER_GROUP):
            errors.append("item {} {}: lit {!r} is not an integer 0-{} or null".format(
                item, label, lit, SLOTS_PER_GROUP))
            lit = None
        positions = entry.get("positions")
        if positions is not None and (not isinstance(positions, list) or any(
                isinstance(p, bool) or not isinstance(p, int) or not 1 <= p <= SLOTS_PER_GROUP
                for p in positions)):
            errors.append("item {} {}: positions {!r} are not slot numbers 1-{}".format(
                item, label, positions, SLOTS_PER_GROUP))
            positions = None
        confidence = entry.get("confidence")
        if confidence is not None and (isinstance(confidence, bool)
                                       or not isinstance(confidence, (int, float))
                                       or not 0.0 <= confidence <= 1.0):
            errors.append("item {} {}: confidence {!r} is not 0-1".format(item, label, confidence))
            confidence = None
        if drawn is False and (lit not in (None, 0) or (positions or [])):
            errors.append("item {} {}: row reported not drawn yet lit={} {}".format(
                item, label, lit, positions or []))
            lit, positions = None, None
        if isinstance(lit, int) and isinstance(positions, list) and len(positions) != lit:
            errors.append("item {} {}: lit {} contradicts positions {}".format(
                item, label, lit, positions))
        record = items[item - 1] if isinstance(item, int) and 1 <= item <= len(items) else {}
        out.append({"item": item, "label": label, "arm": arm,
                    "timestamp": record.get("timestamp"), "frame": record.get("frame"),
                    "drawn": drawn, "lit": lit, "positions": positions,
                    "confidence": confidence, "raw": dict(entry)})
    if sorted((o["item"], o["label"]) for o in out) != sorted(expected):
        errors.append("reply covers {} of {} expected item/label pairs".format(
            len(out), len(expected)))
    return out, errors


class PipReadClient(boundary_media.VisionClient):
    """Asks one question about the pip row, in either arm, and nothing else about the frame.

    A subclass of the vision client so the request budget, the retry-on-unusable-reply loop, the
    transport-uncertain rule and the provenance records stay the ones already trusted. Each arm carries
    its own prompt version: a crop answer must never be replayable as a full-frame answer, nor either
    as a state classification from the banked BB9 batches.
    """

    arm = "crops"
    request_max_tokens = 3000
    request_reasoning_effort = "low"

    def __init__(self, arm=None, *args, **kwargs):
        # type: (Optional[str], *Any, **Any) -> None
        """Set the arm on the instance before the base class snapshots request identity.

        ``prompt_version`` is a property that reads ``self.arm``, and the fingerprint derives from it, so
        the arm cannot be an afterthought. Validating here also turns a typo into a refusal rather than a
        crops answer filed under the full-frame identity, which is the one mix-up this experiment cannot
        afford.
        """
        arm = self.arm if arm is None else arm
        if arm not in ARMS:
            raise ValueError("unknown arm {!r}; expected one of {}".format(arm, list(ARMS)))
        self.arm = arm
        super().__init__(*args, **kwargs)

    @property
    def prompt_version(self):
        # type: () -> str
        return arm_identity(self.arm)["version"]

    def _build_content(self, policy_text, frames, crop_regions=None):
        # type: (str, Sequence[Dict[str, Any]], Optional[bool]) -> List[Dict[str, Any]]
        """The crops for this arm, each labelled, and never the full frame alongside them.

        Keeping the full frame out of the ``crops`` request is the point of the experiment: with both
        in view there is no way to tell whether the crop helped or the model simply drifted back to
        reading the frame it was already failing on.
        """
        listing = []
        for index, frame in enumerate(frames, 1):
            listing.append("item {}: {}".format(index, _describe(frame, self.arm)))
        content = [{"type": "text",
                    "text": question(self.arm) + "\n\nImages to read:\n" + "\n".join(listing) + "\n"}]
        for index, frame in enumerate(frames, 1):
            for label, path in _images(frame, self.arm):
                raw = Path(str(path)).read_bytes()
                mime = "image/png" if str(path).lower().endswith(".png") else "image/jpeg"
                content.append({"type": "text", "text": "item {} label={}: ".format(index, label)})
                content.append({"type": "image_url", "image_url": {
                    "url": "data:{};base64,".format(mime) + base64.b64encode(raw).decode("ascii")}})
        return content

    def _parse_output(self, text, frames):
        # type: (str, Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]
        """Structural gate only: parseable JSON about exactly the images that were sent.

        Field values are judged by ``validate_reply``, which is what the caller counts as a failed
        group. Retrying a semantically odd reading here would spend the allowance re-asking a question
        the model answered truthfully -- null is a valid answer about an undrawn row.
        """
        body = (text or "").strip()
        if body.startswith("```"):
            body = re.sub(r"^```\w*\n?", "", body)
            body = re.sub(r"\n?```$", "", body).strip()
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ValueError("pip reply is not JSON: {}".format(exc))
        if not isinstance(payload, list):
            raise ValueError("pip reply is {} not an array".format(type(payload).__name__))
        asked = sorted((index, label) for index in range(1, len(frames) + 1) for label in LABELS)
        answered = sorted((entry.get("item"), entry.get("label")) for entry in payload
                          if isinstance(entry, dict))
        if asked != answered:
            raise ValueError("pip reply covers {} of {} expected answers".format(
                len(set(answered)), len(asked)))
        return payload


def _describe(record, arm):
    # type: (Dict[str, Any], str) -> str
    if arm == "crops":
        return 'LEFT "{}" RIGHT "{}"'.format(record["crops"]["LEFT"]["path"],
                                             record["crops"]["RIGHT"]["path"])
    return 'full frame "{}"'.format(record["frame"])


def _images(record, arm):
    # type: (Dict[str, Any], str) -> List[Tuple[str, str]]
    """Crops arm: two labelled images. Full arm: the frame once, and the reply still carries both
    labels -- the arms have to differ in framing alone. Duplicating the frame in the control would
    add a second difference and make any gap unattributable."""
    if arm == "crops":
        return [(label, record["crops"][label]["path"]) for label, _ in GROUPS]
    return [("FRAME", record["frame"])]


def persist_probe(records, out_dir, arm, client=None, max_requests=8, endpoint=None, model=None,
                  timeout=1800, client_factory=None):
    # type: (Sequence[Dict[str, Any]], Any, str, Optional[Any], int, Optional[str], Optional[str], int, Optional[Any]) -> Dict[str, Any]
    """Run one arm, persisting every group so an interrupted probe resumes by asking nothing.

    Each group is written before the next is sent. A stored group is only reused if it came back
    clean: reloading a rejected reply as evidence would let a failed answer inflate the accuracy
    figure, which is the one thing this probe must not do to itself.
    """
    out = Path(str(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    store = out / "groups"
    store.mkdir(parents=True, exist_ok=True)
    allowance = int(max_requests)
    if client is None:
        factory = client_factory or (
            lambda: PipReadClient(arm=arm, endpoint=endpoint, model=model, max_requests=allowance,
                                  timeout=timeout, output_dir=str(out)))
        client = factory()
    # The resolved model, not the flag: a resume under a different model has to miss these fingerprints
    # and re-ask, or it would present one model's eyes as another's.
    asked_model = getattr(client, "model", None) or model
    observations, errors, sent = [], [], 0
    resumed, failed, answered = 0, 0, 0
    stopped = None  # type: Optional[str]
    planned_records = list(records)[:allowance]
    # A control is only worth its attempt if it runs before anything that depends on it. Ordering is
    # enforced rather than advised: with the control third in the plan, a bad framing costs three requests
    # and the run still reports a score.
    controls = [r for r in planned_records if (r or {}).get("role") == "control"]
    if controls and planned_records and planned_records[0] is not controls[0]:
        raise ValueError("a control frame has to be planned first, but {} is at position {} of {} "
                         "requests".format(controls[0].get("frame"),
                                           planned_records.index(controls[0]), len(planned_records)))
    for record in planned_records:
        fingerprint = group_fingerprint([record], arm, asked_model)
        path = store / group_filename(arm, fingerprint)
        if path.is_file():
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("errors"):
                failed += 1
            else:
                observations.extend(saved.get("observations") or [])
                answered += 1
            resumed += 1
            if (record or {}).get("role") == "control":
                # Re-checked on a resumed group too, and before any new send: the reason a resume is
                # cheap is that the control is already answered, and if that answer is wrong the rest of
                # the plan is worthless however many attempts are left.
                problems = control_problems(record, saved.get("observations") or [])
                if problems:
                    errors.append("control group disagrees with its paired truth: {}".format(problems))
                    stopped = "control_mismatch"
                    break
            continue
        if getattr(client, "calls", 0) >= allowance:
            stopped = "attempt_allowance_reached"
            break
        try:
            reply = client.classify([record])
        except boundary_budget.BudgetStopped as exc:
            # The shared ledger said stop. No group file is written for this record: a deferral is not
            # an answer, and saving one would let a later resume reload it as a spent attempt.
            stopped = exc.reason
            errors.append("{}: {}".format(record["frame"], exc))
            break
        except ValueError as exc:
            _write(path, arm, record, fingerprint, None, [], str(exc), asked_model)
            errors.append("{}: {}".format(record["frame"], exc))
            failed += 1
            if isinstance(exc, boundary_media.TransportUncertain):
                # The endpoint may still be generating: stopping is the honest answer, and the
                # evidence for the attempt is already on disk.
                stopped = "transport_uncertain"
                break
            continue
        parsed, problems = validate_reply(reply, [record], arm)
        _write(path, arm, record, fingerprint, reply, parsed, "; ".join(problems), asked_model)
        if problems:
            failed += 1
            errors.extend(problems)
        else:
            observations.extend(parsed)
            answered += 1
        if (record or {}).get("role") == "control":
            mismatch = control_problems(record, parsed)
            if mismatch:
                # The group file stays on disk: what the model answered is evidence about this framing
                # even when the answer is the reason the batch stops.
                errors.append("control frame answered {} instead of its paired truth: {}".format(
                    mismatch, record.get("expected")))
                stopped = "control_mismatch"
                break
    planned = min(len(records), allowance)
    return {"arm": arm,
            "status": "stopped" if stopped else "complete",
            "stopped_reason": stopped,
            # Named separately because "stopped" has three very different causes and only one of them is
            # about the model: an allowance spent, a request that may still be generating on the server,
            # and a control that failed to reproduce the truth paired to it.
            "control": ({"role": "control", "frame": controls[0].get("frame"),
                         "expected": controls[0].get("expected"),
                         "outcome": ("passed" if not stopped or stopped != "control_mismatch"
                                     else "failed")}
                        if controls else None),
            "model": asked_model,
            "errors": errors,
            "requests_sent": getattr(client, "calls", sent),
            "attempt_allowance": allowance,
            "groups_planned": planned,
            "groups_answered": answered,
            "groups_reused": resumed,
            "groups_failed": failed,
            # answered and failed already include the groups reloaded from disk, so subtracting
            # ``resumed`` as well counts them twice and only reads right when nothing was resumed.
            "not_attempted": max(0, planned - answered - failed),
            # Every attempt that produced a response is in here, accepted or rejected, so the run reports
            # the compute it actually used instead of the ceiling it was allowed. An attempt whose reply
            # could not be parsed is still billed; ``usage[].accepted`` separates the two figures rather
            # than merging them. A request that never came back has no usage block at all and is counted
            # as unknown, never as zero.
            "usage": list(getattr(client, "usage", []) or []),
            "observations": observations}


def _write(path, arm, record, fingerprint, reply, observations, error, model=None):
    # type: (Path, str, Dict[str, Any], str, Any, Sequence[Dict[str, Any]], Optional[str], Optional[str]) -> None
    images = []
    for label, part in sorted((record.get("crops") or {}).items()):
        target = Path(part["path"])
        images.append({"label": label, "path": part["path"], "size": list(part.get("size") or []),
                       "sha256": _sha(target) if target.is_file() else None})
    path.write_text(json.dumps({"arm": arm, "fingerprint": fingerprint, "frame": record["frame"],
                                "model": model,
                                # What the answer was given about, in pixels. Without the source digest
                                # and the delivered crop digests a group file cannot be tied back to the
                                # images that produced it, which is the whole reason for keeping it.
                                "frame_size": record.get("frame_size"),
                                "scale": record.get("scale"),
                                "source_sha256": record.get("source_sha256"),
                                "crop_images": images,
                                "role": record.get("role"),
                                "timestamp": record.get("timestamp"),
                                # ``arm_identity`` reports the arm's constant scale, which is 4 for crops
                                # and true of every cache-arm answer on disk. The master framing sends
                                # scale 1 and is still comparable because its box is already 4x the
                                # reference, so each answer also records the scale and pixel size that
                                # left the machine -- otherwise a resumed reader has to trust a comment.
                                "identity": dict(arm_identity(arm), scale_as_sent=record.get("scale")),
                                "reply": reply,
                                "observations": list(observations),
                                "errors": [error] if error else []},
                               indent=2, sort_keys=True, default=str), encoding="utf-8")


ERROR_CLASSES = ("false_zero", "missed_pips", "extra_pips", "side_swap",
                 "pip_invented_on_undrawn_row", "row_invented", "row_presence_unknown",
                 "row_missed", "unreadable", "no_answer")


def score_readings(observations, annotations):
    # type: (Sequence[Dict[str, Any]], Sequence[Dict[str, Any]]) -> Dict[str, Any]
    """Compare readings with the per-frame labels, naming the error class on each side.

    One accuracy number would hide the distinction that decides the experiment. ``false_zero`` (a real
    row read as empty) and ``pip_invented_on_undrawn_row`` (an absent row read as a score) are both
    "wrong", but only one of them is fixed by handing the model better pixels, and a configuration that
    answers 0-0 for both an undrawn row and a drawn-but-empty row has not seen the difference at all.
    """
    by_key = {(o.get("timestamp"), o.get("label")): o for o in observations}
    per_frame, totals = [], {}
    ungraded = []
    for frame in annotations:
        t = frame["timestamp"]
        if "pips" not in frame:
            # A run whose annotations carry no truth is a deliberate shape, not a broken one: keeping the
            # expected counts out of the file the runtime reads is how labels stay evaluator-only. It has
            # to end cleanly and say it was not scored, because a spend that finishes with a traceback
            # loses its own report -- every answer and every usage figure survives on disk, and the run
            # still reads as a failure. Grading happens afterwards, offline, against the label file.
            ungraded.append({"timestamp": t, "frame": frame.get("frame"), "reported": None,
                             "reason": "this run's annotations carry no evaluator labels, so nothing here "
                                       "is scored; grade offline against the separate label file"})
            continue
        truth, drawn = frame["pips"], frame.get("row_drawn", True)
        if drawn is None and all(v is None for v in (truth.get("LEFT"), truth.get("RIGHT"))):
            # Unknown truth: three distinct states, and abstention cannot be credited against a frame
            # nobody labelled. Reported separately, never inside frames/frames_correct.
            reported = [by_key.get((t, s), {}).get("lit") for s in LABELS]
            ungraded.append({"timestamp": t, "frame": frame.get("frame"), "reported": reported,
                             "reason": "no independent label: unknown truth, absent HUD and unreadable "
                                       "observed HUD are three different states"})
            continue
        errors, got = [], {}
        for side in LABELS:
            observed = by_key.get((t, side))
            value = None if observed is None else observed.get("lit")
            got[side] = value
            expected = truth.get(side)
            if observed is None:
                errors.append("no_answer:" + side)
            elif not drawn:
                # The count decides, not the model's claim about the row. A reply that says
                # "not drawn" and still returns 0 has asserted a scoreline for pixels that are not
                # there -- which is the exact full-frame failure this probe is meant to catch, so it
                # must not be scored as a clean abstention.
                if value is not None:
                    errors.append("pip_invented_on_undrawn_row:" + side)
                elif observed.get("drawn") is False:
                    # Only an explicit "no circles are drawn here" earns the abstention. Credit has to
                    # be claimed, not merely be consistent with the truth.
                    errors.append("correctly_absent:" + side)
                elif observed.get("drawn") is True:
                    # A row the model insists is present but cannot read is a presence error: it saw
                    # something that is not on screen. Coercing it to a correct abstention would let a
                    # configuration that hallucinates a HUD score clean frames.
                    errors.append("row_invented:" + side)
                else:
                    errors.append("row_presence_unknown:" + side)
            elif observed.get("drawn") is False:
                errors.append("row_missed:" + side)
            elif expected is None or value is None:
                errors.append("unreadable:" + side)
            elif value == expected:
                errors.append("exact:" + side)
            elif value == 0:
                errors.append("false_zero:" + side)
            elif value < expected:
                errors.append("missed_pips:" + side)
            else:
                errors.append("extra_pips:" + side)
        left, right = truth.get("LEFT"), truth.get("RIGHT")
        read = (got.get("LEFT"), got.get("RIGHT"))
        if drawn and None not in (left, right) and None not in read and left != right \
                and read == (right, left):
            errors.append("side_swap")
        for error in errors:
            key = error.split(":")[0]
            totals[key] = totals.get(key, 0) + 1
        hard = [e for e in errors if e.split(":")[0] in ERROR_CLASSES]
        per_frame.append({"timestamp": t, "frame": frame.get("frame"), "row_drawn": drawn,
                          "expected": [left, right],
                          "reported": [got.get("LEFT"), got.get("RIGHT")],
                          "errors": errors, "correct": not hard})
    visible = [f for f in per_frame if f["row_drawn"]]
    absent = [f for f in per_frame if not f["row_drawn"]]
    return {"frames": len(per_frame), "ungraded_frames": len(ungraded), "ungraded": ungraded,
            "accuracy_is_reportable": bool(per_frame),
            "frames_correct": sum(1 for f in per_frame if f["correct"]),
            # Three denominators, because one number invites the wrong comparison. A frame whose row is
            # *known not to be drawn* is graded for a different claim than a frame whose four slots can be
            # counted, and neither is a frame nobody could label. Quoting "39/41 correct" beside "16
            # excluded" double-counts 12 absent-row frames -- once as scored, once as withheld -- which is
            # exactly the sort of denominator drift that makes a rate unreadable after the fact.
            "scored_visible_count_frames": len(visible),
            "scored_visible_count_correct": sum(1 for f in visible if f["correct"]),
            "scored_known_absent_frames": len(absent),
            "scored_known_absent_correct": sum(1 for f in absent if f["correct"]),
            "unknown_truth_frames": len(ungraded),
            "sides_exact": totals.get("exact", 0),
            "sides_total": len(per_frame) * len(LABELS),
            "error_kinds": totals, "per_frame": per_frame,
            "denominator_note": "scored_visible_count_frames + scored_known_absent_frames == frames; "
                                "unknown_truth_frames are outside all three and never inside a rate",
            "caveat": "expected values are one observer's read of enlarged crops, recorded before any "
                      "crop was sent; ambiguous frames are excluded rather than resolved"}


def compare_arms(crop_result, full_result, annotations):
    # type: (Dict[str, Any], Dict[str, Any], Sequence[Dict[str, Any]]) -> Dict[str, Any]
    """The two arms side by side, and what the difference does and does not license saying."""
    crops = score_readings(crop_result.get("observations") or [], annotations)
    full = score_readings(full_result.get("observations") or [], annotations)
    verdict = ("crops did not help: more pixels of the same row did not make it readable"
               if crops["frames_correct"] <= full["frames_correct"]
               else "crops helped on this fixture, which is one observer's 8 frames")
    return {"crops": crops, "full": full,
            "delta_frames_correct": crops["frames_correct"] - full["frames_correct"],
            # Frame by frame rather than as two totals: eight frames is a diagnostic fixture, and the
            # question a reviewer can actually check is which specific frames changed hands between
            # the arms, not whether one number beat another.
            "paired": [{"timestamp": c["timestamp"], "row_drawn": c["row_drawn"],
                        "expected": c["expected"],
                        "crops": {"reported": c["reported"], "correct": c["correct"],
                                  "errors": c["errors"]},
                        "full": {"reported": f["reported"], "correct": f["correct"],
                                 "errors": f["errors"]},
                        "changed": c["correct"] != f["correct"]}
                       for c, f in zip(crops["per_frame"], full["per_frame"])],
            "frames_changed": sum(1 for c, f in zip(crops["per_frame"], full["per_frame"])
                                  if c["correct"] != f["correct"]),
            "verdict": verdict,
            "does_not_establish": [
                "that resolution was the cause: both arms differ in framing, and a win for crops is "
                "consistent with attention being the cause too",
                "anything beyond these 8 frames or this one game",
                "a winner, a game, or a set",
            ]}
