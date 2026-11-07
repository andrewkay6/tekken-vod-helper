"""Cut the pip-row crops out of the **master recording** instead of the 640x360 sample cache.

Why the master, measured rather than assumed. The crop arm the project validated sends the model a 4x
upscale of an 88x22 box taken from a 640x360 cache frame: 1,936 real pixels, enlarged to 352x88. The
same box inside the 2560x1440 master holds 352x88 real pixels -- **16x the sampled detail** behind the
same delivered size, through the same geometry and the same question. That is a difference in what was
measured, not in how hard the model was asked, and it is the one comparison this project has never run.

Why frame *identity* is the hard part, and why no ``60*(N-1)`` appears in this file. A cached frame is
identified by the sample manifest's own id, path and timestamp; a master frame is identified by the
decoder's presentation timestamps. Claiming one maps to the other by arithmetic is exactly the mistake
that once moved a label across a HUD transition, so the mapping here is **proved by content**: a bracket
of master frames is decoded at the cache's resolution, compared against the cached JPEG, and the winner
is the cached frame's identity -- with the distance to the runner-up published as the alignment
uncertainty. The arithmetic is then quoted as a measured residual ("the fitted rule agreed on n of n
samples, worst case x s"), and a render whose decoded timestamp is not the requested instant fails
instead of rounding.

Three more things this insists on, because each was a real defect elsewhere in the project:

* a frame that is *not* in the cache can still be rendered -- ``at_seconds`` names an instant, and the
  sub-second position inside a K.O. flash is exactly what a 1 Hz cache cannot express;
* an absent cache entry, a non-uniform step, or a decoded frame that does not match its own reported
  timestamp is written into the artifact, never skipped quietly;
* every image written here is digested, and the geometry is recorded both in source pixels and as the
  fractions the cache arm used, so a reviewer can see that the two arms ask one question.

Nothing here sends a request, opens a label file, or writes to the cache: the master is only ever read.
"""
import hashlib
import json
import re
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import boundary_pips as bp
from .ffmpeg_tools import FfmpegError, require_tool

SCHEMA = "source-crops-v1"
#: A different picture of the same instant deserves its own framing identity, or a resumed run could
#: answer a master crop with a cache crop's reply.
SOURCE_ANNOTATION_VERSION = "source-crops-v1"
#: Below this, the saved answer at an instant is describing light on the sensor rather than a row it read
#: cleanly, and cannot be the thing a control is matched against. The five glare frames of the executed
#: trial answered at 0.60-0.70; the clear ones at 0.80-0.95.
CONTROL_MIN_CONFIDENCE = 0.80
#: Decoding at full resolution costs ~11 MB per PNG frame, so the content match that establishes
#: identity runs at the cache's own size -- which is the only size at which "is this the cached frame?"
#: is even a well-posed question.
MATCH_SIZE = (640, 360)
#: Seconds of tape on either side of a cached instant to decode while matching. Two seconds reaches
#: +/-59 master frames at 59.94 fps: wider than any plausible seek or rounding error, far narrower than
#: the full-recording scan that would be the alternative.
MATCH_BRACKET_SECONDS = 2.0
#: A margin below this many gray levels between best and second-best leaves the neighbouring master
#: frames live candidates, so the render says +/-1 frame rather than +/-0.
AMBIGUOUS_MARGIN_MAD = 2.0

_SHOWINFO_RE = re.compile(r"pts:\s*(-?\d+)\s+pts_time:\s*([0-9.eE+-]+)")


def _sha256(path) -> str:
    # type: (Any) -> str
    return hashlib.sha256(Path(str(path)).read_bytes()).hexdigest()


def _fraction(text) -> Optional[Fraction]:
    # type: (Any) -> Optional[Fraction]
    text = str(text or "").strip()
    if not text or text in ("0/0", "N/A"):
        return None
    try:
        value = Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def probe_master(path, ffprobe_path="") -> Dict[str, Any]:
    """What the decoder says the master is: geometry, rate, and the exact rationals behind them.

    ``avg_frame_rate`` and ``time_base`` are kept as rationals because a second-to-frame mapping has to be
    exact or has to be reported as unknown. 19001/317 is not 60, and a float at that difference drifts by
    several master frames across a 6,928 s recording.
    """
    ffprobe = require_tool(ffprobe_path, "ffprobe")
    cmd = [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
           "stream=codec_name,width,height,avg_frame_rate,r_frame_rate,time_base,nb_frames,duration,"
           "pix_fmt", "-of", "json", str(path)]
    done = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding="utf-8", errors="replace")
    if done.returncode != 0:
        raise FfmpegError((done.stderr or "ffprobe failed").strip()[:400])
    stream = (json.loads(done.stdout or "{}").get("streams") or [{}])[0]
    if not stream.get("width"):
        raise FfmpegError("ffprobe found no video stream in {}".format(path))
    fps = _fraction(stream.get("avg_frame_rate")) or _fraction(stream.get("r_frame_rate"))
    time_base = _fraction(stream.get("time_base"))
    if not fps or not time_base:
        raise FfmpegError("ffprobe reported no frame rate or no time base for {}".format(path))
    stat = Path(str(path)).stat()
    return {
        # A 56 GB file is not hashed whole for a crop probe: this is a fingerprint that identifies the
        # stream and the bytes, and the per-image digests below identify everything actually used.
        "path": str(path), "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
        "codec": stream.get("codec_name"), "pix_fmt": stream.get("pix_fmt"),
        "width": int(stream["width"]), "height": int(stream["height"]),
        "avg_frame_rate": "{}:{}".format(fps.numerator, fps.denominator),
        "fps": float(fps), "time_base": "{}:{}".format(time_base.numerator, time_base.denominator),
        "time_base_seconds": float(time_base),
        "duration": float(stream["duration"]) if stream.get("duration") else None,
        "declared_frames": int(stream["nb_frames"])
        if str(stream.get("nb_frames") or "").isdigit() else None,
    }


def seconds_of_index(facts, index) -> float:
    # type: (Dict[str, Any], int) -> float
    """The presentation time of master frame ``index``, in the stream's own arithmetic."""
    num, den = _fps_pair(facts)
    return float(Fraction(int(index), 1) * Fraction(den, num))


def master_index_at(facts, at_seconds) -> int:
    """The master frame whose presentation time is nearest this instant.

    Rounding, not flooring: a seek to 1800.798905 s should land on the frame that *starts* at that time,
    and the residual against the decoded pts is reported by the caller rather than hidden here.
    """
    num, den = _fps_pair(facts)
    return int((Fraction(str(at_seconds)) * Fraction(num, den) + Fraction(1, 2)).__floor__())


def _fps_pair(facts) -> Tuple[int, int]:
    text = facts.get("avg_frame_rate")
    if isinstance(text, str) and ":" in text:
        num, den = text.split(":")
        return int(num), int(den)
    value = Fraction(str(facts["fps"])).limit_denominator(100000)
    return value.numerator, value.denominator


def decode_frames(path, t0, t1, out_dir, facts, ffmpeg_path="", scale_to=None,
                  tag="m") -> List[Dict[str, Any]]:
    """Every master frame in [t0, t1) as a PNG, named by its decoded presentation timestamp.

    Both ``-ss`` and ``-t`` go on the **input** side: with ``-copyts`` an output-side ``-t`` compares
    against the copied timestamps (~1,263 s into this recording) and silently encodes nothing. The index
    comes from the logged pts, so a frame is never identified by the offset it was sought at, and a
    mismatch between the timestamps showninfo logged and the files written is an error, not a shrug.
    """
    ffmpeg = require_tool(ffmpeg_path, "ffmpeg")
    out = Path(str(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    for stale in out.glob("{}*.png".format(tag)):
        stale.unlink()
    span = max(float(t1) - float(t0), 2.0 * float(facts["time_base_seconds"]))
    vf = "showinfo" + (",scale={}:{}".format(scale_to[0], scale_to[1]) if scale_to else "")
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "info",
           "-ss", "{:.6f}".format(max(0.0, float(t0))),
           "-t", "{:.6f}".format(span), "-i", str(path), "-copyts", "-an", "-vf", vf,
           "-fps_mode", "passthrough", "-f", "image2", str(out / "{}%06d.png".format(tag))]
    # ``-nostdin`` and DEVNULL are not decoration: ffmpeg listens on stdin for interactive keys, and a
    # run whose parent's stdout is a pipe blocked there for minutes waiting for a keypress nobody could
    # see. A child of this module must never be able to ask the terminal anything.
    done = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    seen = [(int(m.group(1)), float(m.group(2))) for m in _SHOWINFO_RE.finditer(done.stderr or "")]
    files = sorted(out.glob("{}*.png".format(tag)))
    if not files:
        raise FfmpegError("decoded nothing from {} in [{:.3f}, {:.3f}): {}".format(
            path, float(t0), float(t1), (done.stderr or "")[-300:]))
    if len(files) != len(seen):
        raise FfmpegError("logged {} frame timestamps but wrote {} files".format(len(seen), len(files)))
    frames = []
    for fpath, (ticks, logged) in zip(files, seen):
        pts = ticks * float(facts["time_base_seconds"])
        frames.append({"png": str(fpath), "pts_ticks": ticks, "pts_time": pts,
                       "logged_pts_time": logged, "index": master_index_at(facts, pts)})
    return frames


def load_cache_index(manifest_path) -> Dict[str, Any]:
    """The sample manifest as an index, plus where the cache is not well-behaved.

    ``irregular`` is a report, not a warning to read later. A manifest whose steps are not all equal, or
    whose listed file is not on disk, changes what "the frame at 1800.8 s" means -- and a mapping proved
    only on the tidy middle of a clean cache proves nothing about the edges of a real one.
    """
    path = Path(str(manifest_path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    frames = payload.get("frames") or []
    steps, jumps, missing = [], [], []
    if len(frames) > 1:
        # Median over the *steps*, not over the frames, and at full precision: this indexed
        # ``[len(frames) // 2]`` into a list one shorter than ``frames``, so a two-frame cache -- a short
        # clip, a fresh sample run, any test fixture -- died with IndexError before it could report
        # anything. Rounding the step to whole seconds made a 1.001 s cache look like jumps of 0 and 1 too.
        steps = [round(float(b["timestamp"]) - float(a["timestamp"]), 6)
                 for a, b in zip(frames, frames[1:])]
        modal = sorted(steps)[len(steps) // 2]
        for (earlier, later), delta in zip(zip(frames, frames[1:]), steps):
            if abs(delta - modal) > 0.01:
                jumps.append({"from": earlier["id"], "to": later["id"], "seconds": round(delta, 6)})
    missing = [int(e["id"]) for e in frames if not Path(e["path"]).is_file()]
    ordered = sorted(steps)
    return {"path": str(path), "source": payload.get("source"), "interval": payload.get("interval"),
            "start": payload.get("start"), "end": payload.get("end"), "frames": frames,
            "by_id": {int(f["id"]): f for f in frames}, "count": len(frames),
            "irregular": {"step_min": round(ordered[0], 6) if ordered else None,
                          "step_max": round(ordered[-1], 6) if ordered else None,
                          "step_median": round(ordered[len(ordered) // 2], 6) if ordered else None,
                          "uneven_steps": jumps[:12], "uneven_step_count": len(jumps),
                          "missing_files": missing[:12], "missing_count": len(missing)}}


def resolve_frame(cache, at_seconds, tolerance=None, digest=True, disclose=False):
    # type: (Any, float, Optional[float], bool, bool) -> Dict[str, Any]
    """The cached frame that shows this instant, named by the manifest and never by arithmetic.

    The sample manifest is the timestamp authority. Its ids are a *sequence*, not a clock: BB9 was sampled
    at the source's 1000/1001 frame interval, so id ``n`` sits at about ``(n-1) x 1.001`` seconds, and
    anyone who spells a filename from a rounded second invents a frame that is later than the instant they
    asked about -- by 5.005 s at 5495.490 and by 6.006 s at 6486.480, which is how a whole blinded edge
    sheet came to be read at the wrong instants. So this looks the instant up in the manifest, returns the
    path the manifest lists, and publishes the signed offset between the two instead of hiding it.

    ``tolerance`` defaults to half the cache's modal step: an instant further from the nearest frame than
    that is not covered by the cache at all, and a name for it is a fabrication. Out-of-tolerance or
    off-disk frames raise :class:`FrameNotFound` unless ``disclose`` is set, in which case the refusal
    comes back as data with the reason and the neighbours that *were* available.
    """
    index = cache if isinstance(cache, dict) and cache.get("frames") is not None \
        else load_cache_index(cache)
    frames = index.get("frames") or []
    if not frames:
        raise FrameNotFound("the cache index holds no frames, so no instant can be resolved")
    wanted = float(at_seconds)
    by_time = sorted(frames, key=lambda entry: float(entry["timestamp"]))
    steps = sorted(abs(float(b["timestamp"]) - float(a["timestamp"]))
                   for a, b in zip(by_time, by_time[1:]))
    modal = steps[len(steps) // 2] if steps else 0.0
    window = float(tolerance) if tolerance is not None else (modal / 2.0 if modal else 0.5)
    ordered = sorted(by_time, key=lambda entry: abs(float(entry["timestamp"]) - wanted))
    best, runner = ordered[0], (ordered[1] if len(ordered) > 1 else None)
    offset = float(best["timestamp"]) - wanted
    ambiguous = runner is not None and abs(float(runner["timestamp"]) - wanted) - abs(offset) < 1e-9
    path = str(best.get("path") or "")
    reasons: List[str] = []
    if abs(offset) > window + 1e-12:
        reasons.append("the nearest cached frame is %.6f s from the requested instant, outside the "
                       "tolerance of %.6f s (half the cache's %.6f s step)"
                       % (offset, window, modal or window * 2))
    if not path:
        reasons.append("the manifest entry carries no path")
    elif not Path(path).is_file():
        reasons.append("the manifest names %s, which is not on disk" % path)
    neighbours = [_frame_stub(entry, wanted) for entry in ordered[1:3]]
    sparse = _is_sparse(index, best, modal)
    record = {"requested_at": wanted, "actual_at": float(best["timestamp"]),
              "offset_seconds": round(offset, 6), "tolerance_seconds": round(window, 6),
              "modal_step_seconds": round(modal, 6), "within_tolerance": not reasons,
              "ambiguous_tie": ambiguous, "sparse_region": sparse, "id": best.get("id"),
              "frame": Path(path).name if path else "", "path": path,
              "source": index.get("source"), "manifest": index.get("path"),
              "nearest_alternatives": neighbours, "inference_sent": False}
    if reasons:
        record["refused_because"] = reasons
        if not disclose:
            raise FrameNotFound("cannot resolve %.6f s: %s" % (wanted, "; ".join(reasons)))
    if digest and path and Path(path).is_file():
        record["sha256"] = _sha256(path)
    return record


def resolve_frames(cache, at_seconds, **kwargs):
    # type: (Any, Sequence[float]) -> List[Dict[str, Any]]
    """Resolve a list of instants, refusing or disclosing per instant (never one failure for all)."""
    disclose = kwargs.pop("disclose", True)
    return [resolve_frame(cache, at, disclose=disclose, **kwargs) for at in at_seconds]


def _frame_stub(entry, wanted):
    # type: (Dict[str, Any], float) -> Dict[str, Any]
    return {"id": entry.get("id"), "frame": Path(str(entry.get("path") or "")).name,
            "actual_at": float(entry["timestamp"]),
            "offset_seconds": round(float(entry["timestamp"]) - wanted, 6)}


def _is_sparse(index, entry, modal):
    # type: (Dict[str, Any], Dict[str, Any], float) -> bool
    """True when this frame sits next to a hole: the cache skipped time around the instant.

    A 1 Hz cache with a 12-second gap proves nothing about what happened inside it, and a resolution that
    quietly returns the frame before the gap reads as coverage. Reported per frame, because the manifest's
    own ``irregular`` summary lists jumps by id and nobody looks them up while reading a tile.
    """
    if not modal:
        return False
    frames = sorted(index.get("frames") or [], key=lambda item: float(item["timestamp"]))
    position = next((i for i, item in enumerate(frames) if item is entry), None)
    if position is None:
        return False
    for neighbour in (frames[position - 1] if position else None,
                      frames[position + 1] if position + 1 < len(frames) else None):
        if neighbour is None:
            continue        # the ends of the tape have one neighbour, not a hole on the missing side
        if abs(float(neighbour["timestamp"]) - float(entry["timestamp"])) > 2.0 * modal + 1e-9:
            return True
    return False


class FrameNotFound(ValueError):
    """No cached frame may stand for this instant, and a filename must not be invented for it."""


def match_cache_frame(cache_entry, facts, master, work_dir, ffmpeg_path="",
                      bracket=MATCH_BRACKET_SECONDS, size=MATCH_SIZE) -> Dict[str, Any]:
    """Which master frame *is* this cached JPEG -- decided by pixels, with the runner-up published.

    Each candidate is downscaled to the cache's own size before comparison, because that is the only
    like-for-like question available: the cache holds the master shrunk, so matching asks whether
    shrinking the candidate reproduces the pixels on disk. The margin to the second-best is reported as
    the alignment uncertainty rather than being spent as a proof, because on the disputed BB9 frames the
    runner-up sat 0.06-0.12 behind the winner and MAD alone cannot exclude a neighbouring frame.
    """
    from PIL import Image, ImageChops

    t = float(cache_entry["timestamp"])
    frames = decode_frames(master, t - bracket, t + bracket, Path(str(work_dir)) / "bracket", facts,
                           ffmpeg_path=ffmpeg_path, scale_to=size, tag="b")
    cached = Image.open(cache_entry["path"]).convert("L")
    if cached.size != tuple(size):
        cached = cached.resize(size, Image.LANCZOS)

    def mad(frame):
        other = Image.open(frame["png"]).convert("L")
        if other.size != cached.size:
            other = other.resize(cached.size, Image.LANCZOS)
        hist = ImageChops.difference(cached, other).histogram()
        return sum(i * c for i, c in enumerate(hist)) / float(cached.width * cached.height)

    # key= and not the tuple: two candidates tie exactly on a static shot (a menu held for seconds), and
    # sorting the pair would then compare the frame dicts and die with TypeError. The tie is information,
    # not an error -- it is the strongest possible statement that one frame cannot be picked out.
    scored = sorted(((mad(f), i, f) for i, f in enumerate(frames)), key=lambda row: (row[0], row[1]))
    for frame in frames:
        Path(frame["png"]).unlink()               # the bracket is scratch: 240 frames per sample is not
    best = scored[0]
    runner = scored[1] if len(scored) > 1 else None
    fitted = master_index_at(facts, t)
    winner = best[2]
    margin = None if runner is None else round(runner[0] - best[0], 3)
    return {"cache_id": int(cache_entry["id"]), "cache_timestamp": t,
            "cache_path": cache_entry["path"], "candidates": len(frames),
            "matched_index": winner["index"], "matched_pts_time": winner["pts_time"],
            "residual_seconds_vs_cache_timestamp": round(t - winner["pts_time"], 6),
            "mad": round(best[0], 3),
            "runner_up_mad": None if runner is None else round(runner[0], 3),
            "mad_margin": margin,
            "tied_candidates": sum(1 for row in scored if row[0] == best[0]),
            "fitted_rule_index": fitted, "fitted_rule_agrees": fitted == winner["index"],
            "fitted_rule_residual_seconds": round(seconds_of_index(facts, fitted) - t, 6),
            "ambiguity_frames": 0 if (margin is not None and margin > AMBIGUOUS_MARGIN_MAD) else 1}


def align(cache, facts, master, work_dir, samples=None, ffmpeg_path="") -> Dict[str, Any]:
    """Prove the cache-to-master mapping on named samples, including the awkward ones.

    ``samples`` defaults to the set a reviewer would attack: the first and last frames of the cache, the
    steps furthest from the median, frames whose file is missing, and evenly spaced frames across the
    whole recording. A sample that cannot be matched is reported as a refusal with its reason; the fitted
    rule is never quietly widened to cover it.
    """
    frames = list(cache["frames"])
    irregular = cache["irregular"]
    chosen = [int(s) for s in (samples or [])]
    if not chosen:
        # A small cache is matched in full: sampling is the expensive part only when the recording is
        # long, and pretending a 4-frame cache needs a representative subset would be ceremony.
        if len(frames) <= 12:
            chosen = [int(f["id"]) for f in frames]
        else:
            chosen = [int(frames[0]["id"]), int(frames[-1]["id"])]
            chosen += [int(frames[1]["id"]), int(frames[-2]["id"])]
            stride = max(1, len(frames) // 6)
            chosen += [int(frames[i]["id"]) for i in range(0, len(frames), stride)]
            extremes = sorted(irregular.get("uneven_steps") or [],
                              key=lambda j: -abs(j["seconds"] - (irregular.get("step_median") or 0)))[:2]
            chosen += [int(j["to"]) for j in extremes]
            chosen += [int(i) for i in (irregular.get("missing_files") or [])[:2]]
    table, refusals = [], []
    for cid in dict.fromkeys(chosen):
        entry = cache["by_id"].get(cid)
        if entry is None:
            refusals.append({"cache_id": cid,
                             "because": "not in the manifest, so it has no timestamp to map and no "
                                        "pixels to match"})
            continue
        if not Path(entry["path"]).is_file():
            refusals.append({"cache_id": cid, "path": entry["path"],
                             "because": "the manifest lists this frame but the file is not on disk: "
                                        "nothing was matched, so nothing is claimed about it"})
            continue
        table.append(match_cache_frame(entry, facts, master, work_dir, ffmpeg_path=ffmpeg_path))
    agreed = [row for row in table if row["fitted_rule_agrees"]]
    widened = max((int(row["ambiguity_frames"]) for row in table), default=1)
    return {
        "method": "content match: the master frame that downscales closest to the cached JPEG, in a "
                  "+/-{:.1f} s bracket, at the cache's own {}x{}".format(
                      MATCH_BRACKET_SECONDS, MATCH_SIZE[0], MATCH_SIZE[1]),
        "rule": "index = round(t * {})".format(facts["avg_frame_rate"]),
        "rule_is_a_hypothesis": "yes: it is tested against every matched sample below, and the worst "
                                "residual is published rather than assumed away",
        "samples_matched": len(table), "samples_requested": len(dict.fromkeys(chosen)),
        "samples_refused": refusals,
        "rule_agreed": len(agreed), "rule_disagreed": len(table) - len(agreed),
        "worst_rule_residual_seconds": max((abs(float(r["fitted_rule_residual_seconds"]))
                                            for r in table), default=None),
        "worst_content_margin": min((r["mad_margin"] for r in table if r["mad_margin"] is not None),
                                    default=None),
        "uncertainty_seconds": round((widened + 0.5) / float(facts["fps"]), 6),
        "uncertainty_meaning": "how far apart the reported instant and the pixels actually rendered could "
                               "be. One master frame is added whenever a content match left a "
                               "runner-up close behind; the half-frame the rounding itself cannot see is "
                               "inside that.",
        "per_frame": table,
        "cache": dict({k: cache.get(k) for k in ("path", "source", "interval", "count")},
                      **irregular),
        "master": {k: facts.get(k) for k in ("path", "bytes", "mtime_ns", "width", "height",
                                             "avg_frame_rate", "fps", "time_base",
                                             "time_base_seconds", "duration", "declared_frames",
                                             "codec", "pix_fmt")},
    }


def render(targets, alignment, out_dir, master=None, cache=None, ffmpeg_path="", scale=1,
           keep_decoded_frames=False) -> Dict[str, Any]:
    """Render each target from the master and write one provenance-bearing record per image set.

    A target names either a cache frame (``cache_id``) or a bare instant (``at_seconds``). A cache id is
    resolved through the alignment table when a sample covers it -- that frame's identity is *proved* --
    and otherwise through the manifest's own timestamp, which the record then marks as unproved. Both
    routes are honest; a third that silently claimed a proof it never ran is what this refuses to do.

    ``scale`` defaults to 1 for a reason measurable with a ruler: the validated cache arm delivers a
    352x88 image (an 88x22 box upscaled 4x) and the master's own box for the same fractions *is* 352x88,
    so scale 1 sends the same size through the same question and differs only in the pixels underneath.
    """
    from PIL import Image

    facts = alignment["master"]
    master = master or facts["path"]
    out = Path(str(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    per_frame = {int(row["cache_id"]): row for row in alignment.get("per_frame") or []}
    by_id = (cache or {}).get("by_id") or {}
    uncertainty = float(alignment.get("uncertainty_seconds") or (0.5 / float(facts["fps"])))
    boxes = bp.crop_rectangles((int(facts["width"]), int(facts["height"])))
    records = []
    for target in targets:
        at = target.get("at_seconds")
        cid = target.get("cache_id")
        row = per_frame.get(int(cid)) if cid is not None else None
        if at is None:
            listed = by_id.get(int(cid)) if cid is not None else None
            if listed is None and row is not None:
                # The alignment table is a proof about a manifest entry, so it can name the instant when
                # the manifest itself was not handed over. Nothing is invented here: either route reports
                # in `identity_provenance` whether the frame was content-matched or merely positioned.
                at = float(row["cache_timestamp"])
            elif listed is not None:
                at = float(listed["timestamp"])
            else:
                records.append({"target": target.get("label"), "cache_id": cid, "rendered": False,
                                "because": "no at_seconds, and cache id {} is neither in the manifest "
                                           "nor in the alignment table, so there is no instant to ask "
                                           "for".format(cid)})
                continue
        index = master_index_at(facts, at)
        content_matched = bool(row is not None
                               and abs(at - float(row["cache_timestamp"])) < 1e-9
                               and int(row["matched_index"]) == index)
        provenance_note = ("the master frame this cache JPEG is, proved by content match"
                           if content_matched else
                           "positioned by the manifest timestamp and the stream's frame rate; this "
                           "instant was never content-matched, so its identity is arithmetic")
        frames = decode_frames(master, seconds_of_index(facts, index),
                               seconds_of_index(facts, index + 1), out / "frames", facts,
                               ffmpeg_path=ffmpeg_path, tag="f{}".format(index))
        chosen = next((f for f in frames if f["index"] == index), None)
        if chosen is None:
            records.append({"target": target.get("label"), "requested_at_seconds": float(at),
                            "master_index": index, "rendered": False,
                            "because": "the decoder returned no frame {} for {:.6f} s (got {})".format(
                                index, float(at), [f["index"] for f in frames][:8])})
            continue
        drift = abs(chosen["pts_time"] - seconds_of_index(facts, index))
        if drift > float(facts["time_base_seconds"]) * 1.5:
            records.append({"target": target.get("label"), "requested_at_seconds": float(at),
                            "master_index": index, "rendered": False,
                            "because": "the decoded frame's own timestamp is {:.6f} s from where the "
                                       "stream says it is; refusing to round a discrepancy".format(drift)})
            continue
        source_png = Path(chosen["png"])
        image = Image.open(source_png).convert("RGB")
        if image.size != (int(facts["width"]), int(facts["height"])):
            raise FfmpegError("decoded {} where the probe says {}x{}".format(
                image.size, facts["width"], facts["height"]))
        record = {
            "id": "master-{}@{:.6f}s".format(index, chosen["pts_time"]),
            "frame": str(source_png),
            "frame_size": list(image.size),
            "source_sha256": _sha256(source_png),
            "scale": int(scale),
            "rendered_from": "master",
            "master_index": index,
            "decoded_pts_ticks": chosen["pts_ticks"],
            "decoded_pts_time": chosen["pts_time"],
            "requested_at_seconds": float(at),
            "cache_frame": cid,
            "content_matched": content_matched,
            "identity_provenance": provenance_note,
            "alignment_uncertainty_seconds": round(uncertainty, 6),
            "role": target.get("role"),
            "reason": target.get("reason"),
            "crops": {},
        }
        for name, box in boxes.items():
            crop = image.crop(box)
            big = crop.resize((crop.width * int(scale), crop.height * int(scale)), Image.LANCZOS) \
                if int(scale) != 1 else crop
            path = out / "src-{}-{}.png".format(index, name)
            big.save(path, format="PNG")
            record["crops"][name] = {
                "path": str(path), "box": list(box), "size": list(big.size),
                "box_fractions": [round(box[0] / float(facts["width"]), 8),
                                  round(box[1] / float(facts["height"]), 8),
                                  round(box[2] / float(facts["width"]), 8),
                                  round(box[3] / float(facts["height"]), 8)],
                "sha256": _sha256(path),
                "source_pixels": crop.width * crop.height,
                "sent_pixels": big.width * big.height,
            }
        records.append(record)
    if not keep_decoded_frames:
        for stale in (out / "frames").glob("f*.png"):
            stale.unlink()
        try:
            (out / "frames").rmdir()
        except OSError:
            pass
    return {"kind": "source_crop_render", "schema": SCHEMA, "scale": int(scale),
            "geometry_fractions": {name: [round(v, 8) for v in box] for name, box in bp.GROUPS},
            "boxes_in_source_pixels": {name: list(box) for name, box in boxes.items()},
            "records": records,
            "refusals": [r for r in records if not r.get("crops")]}


def records_for_requests(rendered) -> List[Dict[str, Any]]:
    # type: (Dict[str, Any]) -> List[Dict[str, Any]]
    """The rendered image sets in the shape ``boundary_pips.plan_requests`` already consumes."""
    return [dict(r, timestamp=float(r["decoded_pts_time"]))
            for r in rendered["records"] if r.get("crops")]


def saved_answer_at(groups_dir, timestamp, tolerance=1e-6):
    # type: (Any, float, float) -> Optional[Dict[str, Any]]
    """The group file answered at **this** instant, with the digests of the images behind it.

    Looked up by the timestamp the group itself records, never by a filename or a neighbouring frame:
    the proposal this replaces was wrong precisely because a candidate at 1801.799905 s was paired with
    an answer at 1802.800905 s. No match is reported as no match, and an absent answer is a fact about
    the candidate list, not a license to borrow one from a second away.
    """
    store = Path(str(groups_dir))
    if not store.is_dir():
        return None
    for path in sorted(store.glob("*.json")):
        group = json.loads(path.read_text(encoding="utf-8"))
        stamp = group.get("timestamp")
        if stamp is None or abs(float(stamp) - float(timestamp)) > tolerance:
            continue
        crops = group.get("crop_images") or []
        if not crops:
            # Older group files (the executed trial) recorded no digests, only the frame they read.
            # Their crops are still findable where the runner wrote them, and a pairing that cannot
            # produce the digest of the image actually sent has to say which route it took.
            crop_dir = store.parent
            stem = Path(str(group.get("frame") or "x")).stem
            crops = []
            for label, _box in bp.GROUPS:
                candidate = crop_dir / "{}-{}.png".format(stem, label)
                crops.append({"label": label, "path": str(candidate),
                              "sha256": _sha256(candidate) if candidate.is_file() else None,
                              "missing": not candidate.is_file(),
                              "digest_source": "recovered by the runner's naming convention; the group "
                                               "file recorded no digests"})
        return {"group_file": str(path), "fingerprint": group.get("fingerprint"),
                "arm": group.get("arm"), "model": group.get("model"),
                "frame": group.get("frame"), "timestamp": float(stamp),
                "identity": group.get("identity"),
                "observations": [{k: o.get(k) for k in ("label", "drawn", "lit", "confidence")}
                                 for o in group.get("observations") or []],
                "errors": group.get("errors") or [], "crop_images": crops,
                "source_frame_sha256": _sha256(group["frame"])
                if group.get("frame") and Path(group["frame"]).is_file() else None}
    return None


def pair_candidate(cache_id, alignment, cache, groups_dir, role, reason, confident_only=True):
    # type: (int, Dict[str, Any], Dict[str, Any], Any, str, str, bool) -> Dict[str, Any]
    """One candidate, stated completely: manifest frame, saved response, both sets of digests.

    ``expected`` is filled **only** from a saved answer at the same instant, and only when that answer
    drew both sides at or above ``CONTROL_MIN_CONFIDENCE``. A withheld or shaky response yields a
    candidate with no expectation, which is a legitimate route question and cannot be scored as a control.
    """
    entry = cache["by_id"].get(int(cache_id))
    if entry is None:
        raise ValueError("cache id {} is not in the manifest, so nothing about it can be "
                         "paired".format(cache_id))
    row = next((r for r in alignment.get("per_frame") or []
                if int(r["cache_id"]) == int(cache_id)), None)
    saved = saved_answer_at(groups_dir, float(entry["timestamp"]))
    expected, basis = None, None
    if saved is None:
        basis = ("nothing saved at {:.6f} s: this candidate cannot be a control, and no neighbouring "
                 "instant may be spoken for it".format(float(entry["timestamp"])))
    elif saved["errors"]:
        basis = "answered at this instant, but the group carries errors: {}".format(saved["errors"])
    elif confident_only:
        lit = {o["label"]: o["lit"] for o in saved["observations"]}
        drawn = {o["label"]: o["drawn"] for o in saved["observations"]}
        confident = [o.get("confidence") for o in saved["observations"]]
        if lit and all(lit.values()) and all(drawn.values()) and confident \
                and min(confident) >= CONTROL_MIN_CONFIDENCE:
            expected = lit
            basis = ("the saved answer {} for cache frame {} at {:.6f} s: both sides drawn, every side "
                     "at confidence >= {:.2f}".format(str(saved["fingerprint"])[:8], cache_id,
                                                       saved["timestamp"], CONTROL_MIN_CONFIDENCE))
        else:
            basis = ("no expectation: this instant's answer is {}{}".format(
                "an abstention" if not any(drawn.values()) else "not confident enough to be a control",
                "" if lit else " and never came back at all"))
    else:
        basis = "expectation deliberately withheld from a saved answer at this instant"
    return {
        "role": role, "reason": reason,
        "cache": {"id": int(cache_id), "path": entry["path"], "timestamp": float(entry["timestamp"]),
                  "jpeg_sha256": _sha256(entry["path"]) if Path(entry["path"]).is_file() else None,
                  "group_file": (saved or {}).get("group_file"),
                  "fingerprint": (saved or {}).get("fingerprint"),
                  "saved_observations": (saved or {}).get("observations"),
                  "crop_images": (saved or {}).get("crop_images"),
                  "source_frame_sha256": (saved or {}).get("source_frame_sha256")},
        "master": {"index": (row or {}).get("matched_index"),
                   "pts_time": (row or {}).get("matched_pts_time"),
                   "content_matched": row is not None,
                   "mad": (row or {}).get("mad"), "mad_margin": (row or {}).get("mad_margin"),
                   "ambiguity_frames": (row or {}).get("ambiguity_frames"),
                   "uncertainty_seconds": alignment.get("uncertainty_seconds")},
        "expected": expected, "expectation_basis": basis,
        "is_control": expected is not None and role == "control",
    }


def build_execution_set(candidates, alignment, cache, groups_dir, out_dir, ffmpeg_path="",
                        purpose=""):
    # type: (Sequence[Dict[str, Any]], Dict[str, Any], Dict[str, Any], Any, Any, str, str) -> Dict[str, Any]
    """Decode each paired candidate's master frame and write what the public runner consumes.

    Two files, deliberately apart. ``annotations.json`` is the runtime input, in the shape
    ``scripts/run_pip_crop_probe.py`` already takes: ``frame`` is the decoded **master** PNG, so the
    runner crops it through ``boundary_pips.write_crops`` exactly as it crops a cache JPEG and only the
    pixels underneath change. ``pairing.json`` is the evaluator's record -- manifest id, saved group
    file, both sets of image digests, the alignment proof -- and the runner never reads it. That
    separation is what stops a truth about one instant being spent at another.

    The decoded PNGs stay on disk: they are the frames the run was prepared from, and deleting them
    would leave a manifest whose inputs cannot be re-digested.
    """
    out = Path(str(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    facts = alignment["master"]
    prepared, refused = [], []
    for spec in candidates:
        pair = pair_candidate(spec["cache_id"], alignment, cache, groups_dir,
                              spec.get("role", "probe"), spec.get("reason", ""))
        index = pair["master"]["index"]
        if index is None:
            refused.append(dict(pair, because="no content match for this cache frame, so its master "
                                              "identity is unknown and it will not be sent"))
            continue
        stamp = pair["cache"]["timestamp"]
        decoded = decode_frames(facts["path"], seconds_of_index(facts, index),
                                seconds_of_index(facts, index + 1), out / "frames", facts,
                                ffmpeg_path=ffmpeg_path, tag="m{}".format(index))
        frame = next((f for f in decoded if f["index"] == index), None)
        if frame is None:
            refused.append(dict(pair, because="the decoder returned no master frame {}".format(index)))
            continue
        drift = abs(frame["pts_time"] - stamp)
        if drift > 0.5 / float(facts["fps"]):
            refused.append(dict(pair, because="decoded frame is {:.6f} s from the instant asked for "
                                              "({:.6f} vs {:.6f})".format(
                                                  drift, frame["pts_time"], stamp)))
            continue
        pair["master"].update({"decoded_pts_ticks": frame["pts_ticks"],
                               "decoded_pts_time": frame["pts_time"],
                               "master_png": frame["png"],
                               "master_png_sha256": _sha256(frame["png"]),
                               "drift_seconds": round(drift, 9)})
        prepared.append(pair)

    # Control first, because a control answered third is three requests spent on an untested framing.
    prepared.sort(key=lambda p: (p["role"] != "control", float(p["cache"]["timestamp"])))
    frames = [{"frame": p["master"]["master_png"], "timestamp": p["master"]["decoded_pts_time"],
               # A dict of nulls, not a null dict: that is the shape the annotations file already uses
               # for "no truth for this frame", and ``score_readings`` reads it that way. A bare None
               # here is indistinguishable from a missing entry when the run is scored.
               "pips": p["expected"] or {"LEFT": None, "RIGHT": None},
               "row_drawn": None if p["expected"] is None else True,
               "role": p["role"], "basis": p["expectation_basis"]} for p in prepared]
    annotations = {
        "kind": "pip-row-annotations",
        # A new version string, because this is a different picture of the same instant: reusing
        # ``pip-crops-v1`` would let a resumed run answer a master crop with a cache crop's reply.
        "version": SOURCE_ANNOTATION_VERSION,
        "framing": "master video decoded at native resolution, cropped by the validated geometry",
        "scale": 1,
        "purpose": purpose,
        "selected_by": "every candidate paired with the saved answer at its own instant, or with nothing",
        "provenance": {"master": facts.get("path"), "manifest": cache.get("path"),
                       "pairing": str(out / "pairing.json"), "groups_dir": str(groups_dir)},
        "frames": frames,
    }
    (out / "annotations.json").write_text(json.dumps(annotations, indent=2, sort_keys=True,
                                                     default=str), encoding="utf-8")
    # The whole match table, not just the rows this set consumed: "the rule agreed on 14 of 14 across the
    # recording" is a claim a reviewer should be able to check without decoding the master again.
    (out / "source-alignment.json").write_text(json.dumps(alignment, indent=2, sort_keys=True,
                                                          default=str), encoding="utf-8")
    (out / "pairing.json").write_text(json.dumps({
        "kind": "source_crop_pairing", "version": SOURCE_ANNOTATION_VERSION,
        "rule": alignment.get("rule"),
        "alignment_uncertainty_seconds": alignment.get("uncertainty_seconds"),
        "control_first": bool(prepared) and prepared[0]["role"] == "control",
        "planned": prepared, "refused": refused,
        "what_this_is_not": "a label file. An expectation here is a saved model answer at the same "
                            "instant, which is evidence about the framing and not about the fight.",
    }, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return {"annotations": str(out / "annotations.json"), "pairing": str(out / "pairing.json"),
            "planned": [{"cache_id": p["cache"]["id"], "role": p["role"],
                         "master_index": p["master"]["index"], "expected": p["expected"]}
                        for p in prepared],
            "refused": [{"cache_id": r["cache"]["id"], "because": r["because"]} for r in refused],
            "control": next((p["cache"]["id"] for p in prepared if p["role"] == "control"), None)}


def write_artifacts(out_dir, alignment, rendered, purpose) -> Dict[str, Any]:
    # type: (Any, Dict[str, Any], Dict[str, Any], str) -> Dict[str, Any]
    out = Path(str(out_dir))
    out.mkdir(parents=True, exist_ok=True)
    (out / "source-alignment.json").write_text(json.dumps(alignment, indent=2, sort_keys=True,
                                                          default=str), encoding="utf-8")
    (out / "source-crop-records.json").write_text(json.dumps(rendered, indent=2, sort_keys=True,
                                                             default=str), encoding="utf-8")
    planned = [r for r in rendered["records"] if r.get("crops")]
    (out / "source-crop-plan.json").write_text(json.dumps({
        "kind": "source_crop_plan", "schema": SCHEMA, "purpose": purpose,
        "arm": "crops",
        "arm_identity": bp.arm_identity("crops"),
        "rendered_from": "master at native resolution; the cache arm's 4x upscale of a downscaled frame "
                         "is a different picture and is not merged into this plan",
        "requests_planned": len(planned), "images_planned": 2 * len(planned),
        "requests": [{"id": r["id"], "frame": r["frame"],
                      "requested_at_seconds": r["requested_at_seconds"],
                      "decoded_pts_time": r["decoded_pts_time"],
                      "master_index": r["master_index"], "cache_frame": r["cache_frame"],
                      "content_matched": r["content_matched"],
                      "identity_provenance": r["identity_provenance"], "role": r.get("role"),
                      "reason": r.get("reason"),
                      "image_paths": [part["path"] for part in r["crops"].values()],
                      "image_sha256": [part["sha256"] for part in r["crops"].values()],
                      "source_frame_sha256": r["source_sha256"]} for r in planned],
        "refusals": rendered["refusals"],
        "alignment": {"rule": alignment.get("rule"), "samples_matched": alignment.get("samples_matched"),
                      "rule_agreed": alignment.get("rule_agreed"),
                      "rule_disagreed": alignment.get("rule_disagreed"),
                      "worst_rule_residual_seconds": alignment.get("worst_rule_residual_seconds"),
                      "uncertainty_seconds": alignment.get("uncertainty_seconds"),
                      "samples_refused": alignment.get("samples_refused")},
        "immutability": "nothing outside this directory was written; the cache frames, the saved crop "
                        "answers and the frozen label file are read-only inputs",
        "inference_sent": False,
    }, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return {"alignment": str(out / "source-alignment.json"),
            "records": str(out / "source-crop-records.json"),
            "plan": str(out / "source-crop-plan.json")}
