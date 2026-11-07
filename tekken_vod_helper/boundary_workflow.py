"""Boundary detection workflow: propose, detect, and backtest."""
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import local_model
from . import boundary_contract
from .boundary_media import VisionClient, probe_duration, resolve_source, sample_frames, _write_atomic
from .boundary_reasoning import (
    DEFAULT_ADJUDICATION_REQUESTS,
    DEFAULT_COMPLETION_TOKENS,
    DEFAULT_WINDOW_OVERLAP,
    MAX_INPUT_CHARS,
    WINDOW_ALGORITHM,
)
from .models import MatchSegment, ProjectState

_POLICY = Path(__file__).resolve().parents[1] / "docs" / "cutting-policy.md"
_GAMEPLAY = frozenset(["gameplay", "vs_screen", "stage_intro", "character_select"])
_PROMPT = frozenset(["controller_prompt", "controller_disconnected"])


def write_json(path: Path, data: Any) -> None:
    _write_atomic(path, json.dumps(data, indent=2, default=str))


def _validate_match_list(matches: List[Dict[str, Any]]) -> None:
    for i, m in enumerate(matches):
        s, e = m.get("start"), m.get("end")
        if s is None or e is None:
            raise ValueError("Match {} missing start/end".format(i))
        if not math.isfinite(s) or not math.isfinite(e):
            raise ValueError("Match {} non-finite".format(i))
        if s >= e:
            raise ValueError("Match {} start>=end".format(i))
    for i in range(1, len(matches)):
        if matches[i]["start"] < matches[i - 1]["end"]:
            raise ValueError("Match {} overlaps".format(i))


def policy_path(path=None):
    # type: (Optional[Path]) -> Path
    """Where the cutting policy came from, for provenance that names its source."""
    return Path(path) if path is not None else _POLICY


def read_policy(path=None):
    # type: (Optional[Path]) -> str
    """The rules-only cutting policy that goes into every model prompt, read when called."""
    policy = Path(path) if path is not None else _POLICY
    return policy.read_text(encoding="utf-8") if policy.exists() else ""


def _policy_hash() -> str:
    if _POLICY.exists():
        return hashlib.sha256(_POLICY.read_bytes()).hexdigest()[:16]
    return "no_policy"


def propose_from_timeline(
    samples: List[Dict[str, Any]],
    min_confidence: float = 0.75,
    min_set_seconds: float = 30,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    matches: List[Dict[str, Any]] = []
    review: List[Dict[str, Any]] = []
    state = "idle"
    start_idx: Optional[int] = None
    fallback = False
    prompt_run = 0
    prompt_first: Optional[int] = None

    for i, s in enumerate(samples):
        st = s.get("state", "unknown")
        conf = s.get("confidence", 0.0)
        ok = conf >= min_confidence

        if state == "idle":
            if st == "stage_select" and ok:
                state, start_idx, fallback = "potential", i, False
            elif st == "character_select" and ok:
                state, start_idx, fallback = "potential", i, True
            elif st == "unknown" and ok:
                review.append({"type": "ambiguous_start", "timestamp": s["timestamp"],
                               "reason": "unknown at boundary"})
        elif state == "potential":
            if st == "stage_select" and ok:
                start_idx, fallback = i, False
            elif st == "gameplay" and ok:
                state = "in_set"
                prompt_run = 0
            elif st in _PROMPT and ok:
                review.append({
                    "type": "possible_false_start",
                    "start": samples[start_idx]["timestamp"],
                    "end": s["timestamp"],
                    "reason": "insufficient observed play, could be missed sparse gameplay, not proof"
                })
                state = "idle"
        elif state == "in_set":
            if st in _PROMPT and ok:
                prompt_run += 1
                if prompt_run == 1:
                    prompt_first = i
            else:
                prompt_run = 0
            if prompt_run >= 2 and prompt_first is not None:
                end_t = samples[prompt_first]["timestamp"]
                start_t = samples[start_idx]["timestamp"]
                if end_t - start_t >= min_set_seconds:
                    m = _build_match(samples, start_idx, prompt_first, fallback)
                    if m.pop("_fb", False):
                        review.append({"type": "char_select_fallback", "match": m,
                                       "reason": "start not confirmed stage_select"})
                    else:
                        matches.append(m)
                        review.append({
                            "type": "bracket_validity_unverified",
                            "start": start_t,
                            "end": end_t,
                            "reason": "screen-state transitions alone do not verify completed bracket status; false starts may contain gameplay"
                        })
                else:
                    review.append({"type": "too_short", "start": start_t, "end": end_t,
                                   "reason": "duration {:.0f}s < {:.0f}s".format(end_t - start_t, min_set_seconds)})
                state = "idle"
                prompt_run = 0
                prompt_first = None

    if state == "in_set" and start_idx is not None:
        review.append({"type": "unterminated", "start": samples[start_idx]["timestamp"],
                       "reason": "no stable prompt pair"})
    elif state == "potential" and start_idx is not None:
        end_t = samples[-1]["timestamp"] if samples else 0.0
        review.append({
            "type": "possible_false_start",
            "start": samples[start_idx]["timestamp"],
            "end": end_t,
            "reason": "insufficient observed play, could be missed sparse gameplay, not proof"
        })

    matches.sort(key=lambda x: x["start"])
    _validate_match_list(matches)
    return matches, review


def _build_match(samples, si, ei, fb):
    p1 = p2 = c1 = c2 = ""
    for s in samples[si:ei + 1]:
        if s.get("player1") and not p1:
            p1 = s["player1"]
        if s.get("player2") and not p2:
            p2 = s["player2"]
        if s.get("character1") and not c1:
            c1 = s["character1"]
        if s.get("character2") and not c2:
            c2 = s["character2"]
    return {"start": samples[si]["timestamp"], "end": samples[ei]["timestamp"],
            "player1": p1, "player2": p2, "character1": c1, "character2": c2,
            "round_name": "", "notes": "", "_fb": fb}


def _refine(results, src, out, coarse, fine, max_frames, client, ff, issues=None, cached_frames=None, coarse_timestamps=None, dur=None, batch_size=8):
    if not results:
        return results
    windows = []
    for i in range(1, len(results)):
        if results[i]["state"] != results[i - 1]["state"]:
            t = results[i - 1]["timestamp"]
            windows.append((max(0.0, t - coarse), min(src["duration"], t + coarse)))
    if not windows:
        return results
    if dur is None:
        dur = float(src["duration"])
    windows.sort()
    merged = [list(windows[0])]
    for w in windows[1:]:
        if w[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], w[1])
        else:
            merged.append(list(w))
            
    budget = max(0, max_frames - len(results))
    extra: List[Dict[str, Any]] = []
    consecutive_failures = 0
    coarse_ts_set = set(coarse_timestamps) if coarse_timestamps else {r["timestamp"] for r in results}
    
    for idx, (ws, we) in enumerate(merged):
        we = min(we, dur)
        if ws >= we:
            continue
            
        if client.calls >= client.max_requests:
            if issues is not None:
                issues.append({"type": "refinement_stopped", "stage": "budget", "window_start": ws, "window_end": we, "reason": "model budget exhausted"})
            break
            
        if cached_frames:
            cache_window = [f for f in cached_frames if ws <= f["timestamp"] <= we and f["timestamp"] not in coarse_ts_set]
            if not cache_window:
                continue
                
            for b_start in range(0, len(cache_window), batch_size):
                if client.calls >= client.max_requests:
                    if issues is not None:
                        issues.append({"type": "refinement_stopped", "stage": "budget", "window_start": ws, "window_end": we, "reason": "model budget exhausted"})
                    break
                
                remaining_batch = cache_window[b_start:b_start + batch_size]
                batch = remaining_batch[:budget]
                
                if len(batch) == 0:
                    if issues is not None:
                        issues.append({"type": "refinement_stopped", "stage": "budget", "window_start": ws, "window_end": we, "reason": "frame budget exhausted"})
                    break
                
                budget -= len(batch)
                
                try:
                    extra.extend(client.classify(batch))
                    consecutive_failures = 0
                except ValueError as exc:
                    consecutive_failures += 1
                    if issues is not None:
                        issues.append({"type": "refinement_failed", "stage": "classification", "window_start": ws, "window_end": we, "reason": str(exc)})
                    if consecutive_failures >= 3:
                        if issues is not None:
                            issues.append({"type": "refinement_stopped", "stage": "classification", "window_start": ws, "window_end": we, "reason": "3 consecutive classification failures"})
                        break
            if budget <= 0 or client.calls >= client.max_requests:
                if (budget <= 0 and batch and issues is not None
                        and (b_start + len(batch) < len(cache_window) or idx + 1 < len(merged))):
                    issues.append({"type": "refinement_stopped", "stage": "budget", "window_start": ws, "window_end": we, "reason": "frame budget exhausted"})
                break
        else:
            n = min(int(math.ceil((we - ws) / fine)) + 1, budget)
            if n <= 0:
                if issues is not None:
                    issues.append({"type": "refinement_stopped", "stage": "budget", "window_start": ws, "window_end": we, "reason": "frame budget exhausted"})
                break
            budget -= n
            try:
                frames = sample_frames(src, str(out / "ref{:03d}".format(idx)),
                                       start=ws, end=we, interval=fine, max_frames=n, ffmpeg_path=ff)
            except ValueError as exc:
                consecutive_failures += 1
                if issues is not None:
                    issues.append({"type": "refinement_failed", "stage": "extraction", "window_start": ws, "window_end": we, "reason": str(exc)})
                if consecutive_failures >= 3:
                    if issues is not None:
                        issues.append({"type": "refinement_stopped", "stage": "extraction", "window_start": ws, "window_end": we, "reason": "3 consecutive extraction failures"})
                    break
                continue
            if not frames:
                continue
            try:
                extra.extend(client.classify(frames))
            except ValueError as exc:
                consecutive_failures += 1
                if issues is not None:
                    issues.append({"type": "refinement_failed", "stage": "classification", "window_start": ws, "window_end": we, "reason": str(exc)})
                if consecutive_failures >= 3:
                    if issues is not None:
                        issues.append({"type": "refinement_stopped", "stage": "classification", "window_start": ws, "window_end": we, "reason": "3 consecutive classification failures"})
                    break
                continue
            consecutive_failures = 0
            if budget <= 0 or client.calls >= client.max_requests:
                break
            
    if not extra:
        return results
    all_s = sorted(results + extra, key=lambda x: x["timestamp"])
    deduped: List[Dict[str, Any]] = []
    last = -999.0
    for s in all_s:
        if s["timestamp"] != last:
            deduped.append(s)
            last = s["timestamp"]
    return deduped


def _load_manifest(path, src, dur, interval, is_cache_fine=False):
    mp = Path(path)
    if not mp.is_file():
        raise ValueError("manifest not found")
    data = json.loads(mp.read_text(encoding="utf-8"))
    frames = data.get("frames", [])
    if not frames:
        raise ValueError("manifest empty")
    if data.get("source") and src.get("source") and data["source"] != src["source"]:
        raise ValueError("manifest source mismatch")
    
    if is_cache_fine:
        prev_ts = -1.0
        unique_ts = set()
        for f in frames:
            ts = f.get("timestamp")
            if not math.isfinite(ts) or ts < prev_ts:
                raise ValueError("manifest timestamps not monotonic or non-finite")
            if ts in unique_ts:
                raise ValueError("manifest duplicate timestamps")
            unique_ts.add(ts)
            if ts > dur:
                raise ValueError("manifest timestamp exceeds processed duration")
            if not f.get("path"):
                raise ValueError("manifest frame missing path")
            prev_ts = ts
    else:
        if data.get("interval") is not None and abs(float(data["interval"]) - interval) > 0.01:
            raise ValueError("manifest interval mismatch")
    return frames


def _build_project(matches, src, dur, event, out_dir):
    uri = src.get("uri") or ""
    # A recovered run knows its source identity without holding the file, so an empty uri is a
    # blank video path rather than the working directory.
    vp = "" if (not uri or src.get("source", "").startswith("yt:")) else str(Path(uri).resolve())
    players, chars = [], []
    for m in matches:
        for p in (m.get("player1", ""), m.get("player2", "")):
            if p and p not in players:
                players.append(p)
        for c in (m.get("character1", ""), m.get("character2", "")):
            if c and c not in chars:
                chars.append(c)
    return ProjectState(video_path=vp, event_name=event, output_dir=out_dir,
                        duration=dur, players=players, characters=chars,
                        matches=[MatchSegment.from_dict(m) for m in matches])


def finalize_run(out, matches, review_items, src, dur, event_name, policy_hash, model, **kw):
    # type: (Any, List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any], float, str, str, str, Any) -> Dict[str, Any]
    """Write every artifact a boundary run owes downstream consumers, in dependency order.

    Normal detection and saved-timeline recovery share this so a recovered run is scored and
    exported exactly like a live one. run.json is written last because it is the file that declares
    the run finished: an incomplete run gets a review and an honest run record, never predictions or
    a project that would look publishable.

    Keyword args: proposal_algorithm, vision_calls, adjudication_calls, adjudication, counts,
    usage, refinement_status, status, error, cache_enabled, cached_frame_count, extra_paths,
    provenance_extra.
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    status = kw.get("status", "complete")
    adjudication = kw.get("adjudication")
    # The rules a run was finalised under are recorded, not assumed. A caller that was handed a policy
    # elsewhere (a recovery reading a patched path) passes it here so run.json states the same text the
    # model was shown; the hash alone could not distinguish two policies that agree by luck.
    policy = kw.get("policy")
    policy_source = kw.get("policy_source", str(_POLICY))
    paths = {"review": str(out / "review.json")}
    paths.update(kw.get("extra_paths") or {})
    if status == "complete":
        write_json(out / "predictions.json", matches)
        project = _build_project(matches, src, dur, event_name, str(out))
        write_json(out / "project.tvh.json", project.to_dict())
        paths["predictions"] = str(out / "predictions.json")
        paths["project"] = str(out / "project.tvh.json")

    review_payload = {
        "policy_hash": policy_hash, "source_id": src.get("source", ""), "model": model,
        "range": [0.0, dur],
        "counts": kw.get("counts", {}),
        "usage": kw.get("usage", []),
        "items": review_items,
        "cache_enabled": kw.get("cache_enabled", False),
        "cached_frame_count": kw.get("cached_frame_count", 0),
        "status": status,
        "edge_contract": boundary_contract.contract_summary(),
    }
    if isinstance(policy, str) and policy:
        review_payload["policy_chars"] = len(policy)
    if kw.get("policy_source"):
        review_payload["policy_source"] = policy_source
    if adjudication is not None:
        review_payload["adjudication"] = adjudication
    review_payload.update(kw.get("provenance_extra") or {})
    write_json(out / "review.json", review_payload)

    result = {"output_dir": str(out), "match_count": len(matches) if status == "complete" else 0,
              "review_count": len(review_items),
              "model_calls": kw.get("vision_calls", 0) + kw.get("adjudication_calls", 0),
              "vision_model_calls": kw.get("vision_calls", 0),
              "adjudication_model_calls": kw.get("adjudication_calls", 0),
              "status": status, "policy_hash": policy_hash,
              "refinement_status": kw.get("refinement_status", "complete"),
              "source_id": src.get("source", ""), "range": [0.0, dur],
              "proposal_algorithm": kw.get("proposal_algorithm", ""),
              "cache_enabled": kw.get("cache_enabled", False),
              "cached_frame_count": kw.get("cached_frame_count", 0),
              "paths": paths}
    if adjudication is not None:
        result["adjudication"] = adjudication
    if kw.get("error"):
        result["error"] = str(kw["error"])
    result.update(kw.get("result_extra") or {})
    write_json(out / "run.json", result)
    return result


def _adjudication_allowance(temporal, adjudication_requests):
    # type: (bool, Optional[int]) -> int
    """Requests the temporal stage may spend, reserved out of --max-requests before it starts."""
    if not temporal:
        if adjudication_requests not in (None, 0):
            raise ValueError("adjudication_requests needs --temporal")
        return 0
    if adjudication_requests is None:
        return DEFAULT_ADJUDICATION_REQUESTS
    if not (isinstance(adjudication_requests, int) and not isinstance(adjudication_requests, bool)
            and adjudication_requests >= 1):
        raise ValueError("adjudication_requests must be a positive integer")
    return adjudication_requests


def detect(source, output_dir, duration=None, interval=5, fine_step=1,
           max_frames=2000, batch_size=8, max_requests=300,
           endpoint=None, model=None,
           yt_dlp="", ffmpeg_path="", ffprobe_path="",
           event_name="", samples_manifest=None, temporal=False,
           cache_fine=False, max_cache_frames=12000,
           recognition_crops=False, vision_thinking=True, max_attempts=3,
           adjudication_requests=None, adjudication_max_chars=MAX_INPUT_CHARS,
           adjudication_overlap=DEFAULT_WINDOW_OVERLAP,
           adjudication_max_tokens=DEFAULT_COMPLETION_TOKENS):
    endpoint = endpoint or local_model.default_endpoint()
    model = model or local_model.default_model()
    policy_text = read_policy()
    out = Path(output_dir)
    if out.exists() and any(out.iterdir()):
        raise ValueError("output_dir not empty")
    out.mkdir(parents=True, exist_ok=True)
    if not (isinstance(max_frames, int) and max_frames > 0):
        raise ValueError("max_frames must be positive integer")
    if not (isinstance(batch_size, int) and batch_size > 0):
        raise ValueError("batch_size must be positive integer")
    if not (isinstance(max_cache_frames, int) and max_cache_frames > 0):
        raise ValueError("max_cache_frames must be positive integer")
    if not (isinstance(max_requests, int) and max_requests > 0):
        raise ValueError("max_requests must be positive integer")
    allowance = _adjudication_allowance(temporal, adjudication_requests)
    if max_requests <= allowance:
        raise ValueError(
            "max_requests ({}) must leave at least one request for classification once the "
            "adjudication allowance ({}) is reserved".format(max_requests, allowance))
    src = resolve_source(source, yt_dlp=yt_dlp, ffprobe_path=ffprobe_path)
    dur = src["duration"]
    if duration is not None:
        dur = min(dur, float(duration))
        if dur <= 0:
            raise ValueError("clamped duration <= 0")
            
    cached_frames: List[Dict[str, Any]] = []
    coarse_frames: List[Dict[str, Any]] = []
    
    if cache_fine:
        init_interval = min(interval, fine_step)
        predicted_count = int(math.ceil(dur / init_interval)) if dur > 0 else 0
        if predicted_count > max_cache_frames:
            raise ValueError("Predicted fine count {} exceeds max_cache_frames {}".format(predicted_count, max_cache_frames))
        predicted_coarse_count = int(math.ceil(dur / interval))
        if predicted_coarse_count > max_frames:
            raise ValueError("Predicted coarse count {} exceeds max_frames {}".format(predicted_coarse_count, max_frames))
            
        if samples_manifest:
            cached_frames = _load_manifest(samples_manifest, src, dur, init_interval, is_cache_fine=True)
        else:
            cached_frames = sample_frames(src, str(out / "frames"), start=0, end=dur,
                                          interval=init_interval, max_frames=max_cache_frames, ffmpeg_path=ffmpeg_path)
            
        coarse_frames = []
        last_ts = None
        for f in cached_frames:
            ts = f["timestamp"]
            if last_ts is None or ts - last_ts >= interval:
                coarse_frames.append(f)
                last_ts = ts
        if len(coarse_frames) > max_frames:
            raise ValueError("Coarse cache exceeds model frame budget")
            
        client = VisionClient(endpoint=endpoint, model=model,
                              max_requests=max_requests - allowance, output_dir=str(out),
                              crop_regions=recognition_crops, enable_thinking=vision_thinking, policy_path=str(_POLICY),
                              max_attempts=max_attempts)
        issues: List[Dict[str, Any]] = []
        all_res: List[Dict[str, Any]] = []
        budget = max_frames
        for i in range(0, len(coarse_frames), batch_size):
            if budget <= 0 or client.calls >= client.max_requests:
                raise ValueError("Budget exhausted during coarse classification")
            batch = coarse_frames[i:i + batch_size]
            budget -= len(batch)
            all_res.extend(client.classify(batch))
            
        refined = _refine(all_res, src, out, interval, fine_step, max_frames, client, ffmpeg_path, issues=issues,
                          cached_frames=cached_frames, coarse_timestamps=[f["timestamp"] for f in coarse_frames], dur=dur, batch_size=batch_size)
        sample_count = len(cached_frames)
        write_json(out / "timeline.json", all_res)
        write_json(out / "timeline-refined.json", refined)
    else:
        client = VisionClient(endpoint=endpoint, model=model,
                              max_requests=max_requests - allowance, output_dir=str(out),
                              crop_regions=recognition_crops, enable_thinking=vision_thinking, policy_path=str(_POLICY),
                              max_attempts=max_attempts)
        issues: List[Dict[str, Any]] = []
        all_res: List[Dict[str, Any]] = []
        if samples_manifest:
            frames = _load_manifest(samples_manifest, src, dur, interval)
        else:
            frames = sample_frames(src, str(out / "frames"), start=0, end=dur,
                                   interval=interval, max_frames=max_frames, ffmpeg_path=ffmpeg_path)
        budget = max_frames
        for i in range(0, len(frames), batch_size):
            if budget <= 0 or client.calls >= client.max_requests:
                raise ValueError("Budget exhausted during coarse classification")
            batch = frames[i:i + batch_size]
            budget -= len(batch)
            all_res.extend(client.classify(batch))
        write_json(out / "timeline.json", all_res)
        refined = _refine(all_res, src, out, interval, fine_step, max_frames, client, ffmpeg_path, issues=issues, dur=dur, batch_size=batch_size)
        sample_count = len(frames)
        write_json(out / "timeline-refined.json", refined)

    client_policy = getattr(client, "_policy_snapshot", None)
    if isinstance(client_policy, str):
        policy_text = client_policy
    ph = hashlib.sha256(policy_text.encode("utf-8")).hexdigest()[:16]

    matches: List[Dict[str, Any]] = []
    review_items: List[Dict[str, Any]] = []
    adj_provenance: Optional[Dict[str, Any]] = None
    adjudication_error = None
    status = "complete"
    if temporal:
        # Imported here so a caller patching boundary_reasoning.TemporalAdjudicator still reaches
        # the class this function uses.
        from .boundary_reasoning import (
            TemporalAdjudicationError,
            TemporalAdjudicationIncomplete,
            TemporalAdjudicator,
        )
        adjudicator = TemporalAdjudicator(
            endpoint=endpoint, model=model,
            max_requests=allowance, timeout=1800,
            output_dir=str(out),
            max_input_chars=adjudication_max_chars,
            overlap_observations=adjudication_overlap,
            max_completion_tokens=adjudication_max_tokens,
        )
        try:
            matches, review_items, adj_provenance = adjudicator.propose_windowed(refined, policy_text)
            adjudication_calls = int(adj_provenance.get("model_calls", 0))
        except TemporalAdjudicationIncomplete as exc:
            # Windows that did answer are saved for a resume; publishing their partial matches as a
            # finished run is what must never happen.
            matches, review_items, adj_provenance = [], list(exc.review_items), exc.provenance
            adjudication_calls = int((exc.provenance or {}).get("model_calls", 0))
            adjudication_error = str(exc)
            status = "incomplete"
        except TemporalAdjudicationError as exc:
            raise ValueError("Temporal adjudication failed: {}".format(exc))
        if issues:
            review_items.extend(issues)
        proposal_algorithm = (adj_provenance or {}).get("algorithm", WINDOW_ALGORITHM)
    else:
        matches, review_items = propose_from_timeline(refined)
        if issues:
            review_items.extend(issues)
        proposal_algorithm = "gameplay-required-v2"
        adjudication_calls = 0

    return finalize_run(
        out, matches, review_items, src, dur, event_name, ph, model,
        policy=policy_text, policy_source=str(_POLICY),
        proposal_algorithm=proposal_algorithm,
        vision_calls=client.calls,
        adjudication_calls=adjudication_calls,
        adjudication=adj_provenance,
        counts={"sampled": sample_count, "classified": len(all_res), "refined": len(refined)},
        usage=client.usage,
        refinement_status="degraded" if issues else "complete",
        cache_enabled=cache_fine,
        cached_frame_count=len(cached_frames),
        status=status,
        error=adjudication_error,
        extra_paths={"timeline": str(out / "timeline.json")},
    )


def _is_calibration(source, truth_provenance, source_id=""):
    src = source or source_id
    if src:
        if "calibration" in src.lower() or "cal" in src.lower():
            return True
    if truth_provenance and "calibration" in truth_provenance.lower():
        return True
    return False


def _load_truth(truth_path):
    tp = Path(truth_path)
    if not tp.is_file():
        raise ValueError("truth not found")
    return json.loads(tp.read_text(encoding="utf-8"))


def resolve_truth_matches(truth_raw, eval_end=None, project_duration=None):
    # type: (Any, Optional[float], Optional[float]) -> Tuple[List[Dict[str, float]], List[int], List[int]]
    """Derive an end for every truth row that omits one.

    project_duration overrides a project reporting no usable duration; eval_end enables the
    "starts past the evaluated range" fallback and is left None when pre-validating labels.
    """
    truth = truth_raw if isinstance(truth_raw, list) else truth_raw.get("matches", [])
    proj_dur = project_duration
    if proj_dur is None:
        proj_dur = truth_raw.get("duration") if isinstance(truth_raw, dict) else None

    derived_truth_ends = []
    unresolved_truth_ends = []
    resolved_truth = []
    sorted_truth = sorted(enumerate(truth), key=lambda x: x[1].get("start", x[1].get("start_time", 0.0)))

    for i, t in enumerate(truth):
        s = t.get("start", t.get("start_time"))
        e = t.get("end", t.get("end_time"))
        if e is None:
            next_s = None
            position = next((k for k, row in enumerate(sorted_truth) if row[0] == i), -1)
            if position + 1 < len(sorted_truth):
                t2 = sorted_truth[position + 1][1]
                next_s = t2.get("start", t2.get("start_time"))
            if next_s is not None and next_s > s:
                e = next_s
                derived_truth_ends.append(i)
            elif proj_dur is not None and isinstance(proj_dur, (int, float)) and not isinstance(proj_dur, bool) and math.isfinite(proj_dur) and proj_dur > s:
                e = proj_dur
                derived_truth_ends.append(i)
            elif eval_end is not None and isinstance(s, (int, float)) and not isinstance(s, bool) and math.isfinite(s) and s >= eval_end:
                e = None
                unresolved_truth_ends.append(i)
            else:
                raise ValueError(
                    "Truth match {} has end=None, but no next truth start or project duration available "
                    "to resolve it. The project reports duration {!r}; pass the measured recording length "
                    "as project_duration / --truth-duration to score it without editing the labels.".format(i, proj_dur))
        resolved_truth.append({"start": s, "end": e})
    return resolved_truth, derived_truth_ends, unresolved_truth_ends


def score_saved_predictions(result, truth_path, output_dir, source='', truth_duration=None):
    truth_raw = _load_truth(truth_path)
    resolved_truth, derived_truth_ends, unresolved_truth_ends = resolve_truth_matches(
        truth_raw, eval_end=result["range"][1], project_duration=truth_duration)

    pred_path = (result.get("paths") or {}).get("predictions")
    if not pred_path:
        raise ValueError(
            "Run {} has no predictions to score (status {!r}{}). An incomplete run must be resumed "
            "or re-run before scoring; its partial adjudication is kept in adjudication.json, not "
            "published as sets.".format(
                result.get("output_dir", "?"), result.get("status"),
                ": " + str(result.get("error")) if result.get("error") else ""))
    predictions = json.loads(Path(pred_path).read_text(encoding="utf-8"))

    from .boundary_scoring import score_matches
    score = score_matches(predictions, resolved_truth, start=0, end=result["range"][1])

    prov = str(Path(truth_path).resolve())
    source_identity = str(source) + " " + str(result.get("source_id", ""))
    is_calib = "W_rDl7ZWvuQ" in source_identity or "bb7" in source_identity.lower()
    cal = "calibration" if is_calib else "heldout"

    report = {
        "label_provenance": prov,
        "calibration": cal,
        "score": score,
        "cut_metrics": score if isinstance(score, dict) else {"score": score},
        "model_protocol_success": result["status"] == "complete",
        "detect": result,
        "derived_truth_ends": derived_truth_ends,
        "unresolved_truth_ends": unresolved_truth_ends
    }
    if truth_duration is not None:
        report["truth_duration_override"] = truth_duration
    report_path = Path(output_dir) / "backtest-report.json"
    if report_path.exists():
        from uuid import uuid4
        report_path = Path(output_dir) / ("backtest-report-rescore-" + uuid4().hex[:8] + ".json")
    report["report_path"] = str(report_path)
    write_json(report_path, report)
    return report


def backtest(source, truth_path, output_dir, truth_duration=None, **kw):
    truth_raw = _load_truth(truth_path)
    eval_end = _evaluated_end(source, kw.get("duration"), kw.get("ffprobe_path", ""))
    if eval_end is not None:
        # Only possible when the range detect will use is already known, so this cannot
        # change how a clamped prefix run resolves its labels.
        resolve_truth_matches(truth_raw, eval_end=eval_end, project_duration=truth_duration)
    result = detect(source, output_dir, **kw)
    return score_saved_predictions(result, truth_path, output_dir, source=source, truth_duration=truth_duration)


def _evaluated_end(source, explicit_duration=None, ffprobe_path=""):
    # type: (Any, Optional[float], str) -> Optional[float]
    """The end detect() will score against, when it can be had without touching the network."""
    if isinstance(explicit_duration, (int, float)) and not isinstance(explicit_duration, bool) \
            and math.isfinite(explicit_duration) and explicit_duration > 0:
        return float(explicit_duration)
    src = str(source or "").strip()
    if src.startswith(("http://", "https://")):
        return None
    path = Path(src)
    if not path.is_file():
        return None
    # Deliberately narrow: a probe failure means "cannot pre-check", but any programming error
    # here must surface rather than silently disabling the check and costing a full run.
    from .ffmpeg_tools import FfmpegError
    try:
        return float(probe_duration(str(path), ffprobe_path or ""))
    except (FfmpegError, ValueError, OSError):
        return None


def score_from_run(run_path, truth_path, output_dir, source='', truth_duration=None):
    """Score persisted predictions with no model calls, using a run.json from an earlier detect."""
    result = json.loads(Path(run_path).read_text(encoding="utf-8"))
    return score_saved_predictions(result, truth_path, output_dir, source=source, truth_duration=truth_duration)
