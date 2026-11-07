"""Recover boundary decisions from a saved classified timeline, without any vision work.

A long recording can classify completely and still die at its last step because the whole refined
timeline does not fit one adjudication prompt. This module recovers exactly that step from files
that already exist: it hashes the saved timeline, splits it into size-bounded windows, reconciles
the proposals across them and writes the same predictions, project, review and run artifacts a live
detection would have produced.

It resolves no source, extracts no frame and asks the vision model nothing, so a recovery can never
repeat the hours it is recovering from. What the saved files do not record stays unrecorded here:
refinement issues that the original run kept in memory are reported as missing, not imagined.
"""
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import boundary_workflow, local_model
from .boundary_reasoning import (
    DEFAULT_ADJUDICATION_REQUESTS,
    DEFAULT_COMPLETION_TOKENS,
    DEFAULT_WINDOW_OVERLAP,
    MAX_INPUT_CHARS,
    TemporalAdjudicationError,
    TemporalAdjudicationIncomplete,
    TemporalAdjudicator,
    WINDOW_ALGORITHM,
)

RECOVERY_ALGORITHM = "saved-timeline-recovery-v1"
INPUTS_FILENAME = "recovery-inputs.json"
_CHUNKS_DIRNAME = "adjudication"

# Changing any of these changes the prompts that produced a saved chunk, so a cache holding them
# cannot be reused. Everything else in the manifest is recorded but only compared for reporting.
STRICT_INPUT_KEYS = (
    "adjudication_algorithm",
    "timeline",
    "timeline_sha256",
    "timeline_rows",
    "policy_hash",
    "model",
    "endpoint",
    "max_input_chars",
    "overlap_observations",
)

REFINEMENT_HISTORY_NOTE = (
    "Recovered from a saved timeline. The run that produced it kept its refinement budget and "
    "failure issues in memory and never persisted them, so which work was skipped is unknown; "
    "full time-span coverage is not evidence of completed refinement."
)


def _sha256_file(path):
    # type: (Path) -> str
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_saved_timeline(timeline_path):
    # type: (Any) -> List[Dict[str, Any]]
    """Read a saved classified timeline, rejecting anything too damaged to adjudicate."""
    path = Path(timeline_path)
    if not path.is_file():
        raise ValueError("timeline not found: {}".format(path))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("timeline is not readable JSON: {}".format(exc))
    if isinstance(data, dict):
        data = data.get("observations")
    if not isinstance(data, list) or not data:
        raise ValueError("timeline holds no observations")
    previous = None  # type: Optional[float]
    for i, row in enumerate(data):
        if not isinstance(row, dict):
            raise ValueError("timeline row {} is not an object".format(i))
        ts = row.get("timestamp")
        if not isinstance(ts, (int, float)) or isinstance(ts, bool) or not math.isfinite(ts):
            raise ValueError("timeline row {} has a non-finite timestamp".format(i))
        if previous is not None and ts < previous:
            raise ValueError("timeline row {} goes backwards in time ({} < {})".format(i, ts, previous))
        previous = ts
    return data


def _policy_snapshot(policy_path=None):
    # type: (Optional[str]) -> str
    """The rules-only policy text, read through the workflow so a patched path is honoured."""
    return boundary_workflow.read_policy(policy_path)


def _policy_hash(policy_text):
    # type: (str) -> str
    return hashlib.sha256(policy_text.encode("utf-8")).hexdigest()[:16]


def _adjudicator(endpoint, model, requests, max_input_chars, overlap, output_dir=None,
                 chunk_dir=None, timeout=1800, max_completion_tokens=DEFAULT_COMPLETION_TOKENS):
    # type: (str, str, int, int, int, Optional[Path], Optional[Path], int, int) -> TemporalAdjudicator
    return TemporalAdjudicator(
        endpoint=endpoint, model=model, max_requests=requests, timeout=timeout,
        output_dir=str(output_dir) if output_dir else None,
        chunk_dir=str(chunk_dir) if chunk_dir else None,
        max_input_chars=max_input_chars, overlap_observations=overlap,
        max_completion_tokens=max_completion_tokens,
    )


def plan_saved_timeline(timeline_path, duration=None, endpoint=None, model=None,
                        requests=None, max_input_chars=MAX_INPUT_CHARS,
                        overlap=DEFAULT_WINDOW_OVERLAP, policy_path=None,
                        max_completion_tokens=DEFAULT_COMPLETION_TOKENS):
    # type: (Any, Optional[float], Optional[str], Optional[str], Optional[int], int, int) -> Dict[str, Any]
    """Report the windows, prompt sizes, coverage and call allowance a recovery would use.

    Touches no model, no frame cache and no source: this is the number Andy approves before the
    machine is occupied, and it is computed from the same planner the live recovery uses.
    """
    rows = load_saved_timeline(timeline_path)
    policy_text = _policy_snapshot(policy_path)
    endpoint = local_model.chat_completions_url(endpoint or local_model.default_endpoint())
    model = model or local_model.default_model()
    requests = DEFAULT_ADJUDICATION_REQUESTS if requests is None else _check_requests(requests)
    adjudicator = _adjudicator(endpoint, model, requests, max_input_chars, overlap,
                               max_completion_tokens=max_completion_tokens)
    _, windows = adjudicator.plan(rows, policy_text)
    span = (windows[0]["core_start"], windows[-1]["core_end"])
    return {
        "status": "plan",
        "algorithm": RECOVERY_ALGORITHM,
        "adjudication_algorithm": WINDOW_ALGORITHM,
        "model": model,
        "endpoint": endpoint,
        "request_allowance": requests,
        "max_input_chars": max_input_chars,
        "completion_tokens_per_call": max_completion_tokens,
        "overlap_observations": overlap,
        "observations": len(rows),
        "first_timestamp": rows[0].get("timestamp"),
        "last_timestamp": rows[-1].get("timestamp"),
        "duration": duration,
        "policy_hash": _policy_hash(policy_text),
        "timeline_sha256": _sha256_file(Path(timeline_path)),
        "window_count": len(windows),
        "prompt_chars_max": max(w["prompt_chars"] for w in windows),
        "prompt_chars_total": sum(w["prompt_chars"] for w in windows),
        "core_coverage_complete": (span == (0, len(rows))),
        "context_shortfall_observations": sum(w["context_shortfall"] for w in windows),
        "unsnapped_boundaries": sum(1 for w in windows
                                    if not w["break_snapped"] and not w["is_last"]),
        "windows": [{k: v for k, v in w.items() if k != "note"} for w in windows],
        "notes": [REFINEMENT_HISTORY_NOTE],
    }


class RecoveryIncomplete(ValueError):
    """A recovery wrote its artifacts and still cannot publish sets: resume it or widen the budget."""

    def __init__(self, message, result=None):
        # type: (str, Optional[Dict[str, Any]]) -> None
        super().__init__(message)
        self.result = result or {}


def _check_requests(requests):
    # type: (Any) -> int
    if not (isinstance(requests, int) and not isinstance(requests, bool) and requests >= 1):
        raise ValueError("adjudication requests must be a positive integer")
    return requests


def _inputs_manifest(values):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """Everything that must still be true for a saved chunk cache to describe the same work."""
    return {
        "algorithm": RECOVERY_ALGORITHM,
        "adjudication_algorithm": WINDOW_ALGORITHM,
        "timeline": values["timeline"],
        "timeline_sha256": values["timeline_sha256"],
        "timeline_rows": values["timeline_rows"],
        "policy_hash": values["policy_hash"],
        "model": values["model"],
        "endpoint": values["endpoint"],
        "request_allowance": values["requests"],
        "max_input_chars": values["max_input_chars"],
        "completion_tokens_per_call": values["max_completion_tokens"],
        "overlap_observations": values["overlap"],
        "source_id": values["source_id"],
        "duration": values["duration"],
        "event_name": values["event_name"],
    }


def _check_output_dir(out, resume):
    # type: (Path, bool) -> None
    if out.exists() and any(out.iterdir()):
        if not resume:
            raise ValueError(
                "output_dir is not empty; pass --resume to reuse its saved adjudication chunks")
        if not (out / _CHUNKS_DIRNAME).is_dir():
            raise ValueError("--resume needs the saved chunks in {}/{}".format(out, _CHUNKS_DIRNAME))
        previous = out / INPUTS_FILENAME
        if not previous.is_file():
            raise ValueError("--resume needs {} written by the interrupted recovery".format(previous))
    elif resume:
        raise ValueError("--resume needs an output_dir already holding a partial recovery")


def _check_resume_inputs(out, manifest):
    # type: (Path, Dict[str, Any]) -> Dict[str, Any]
    """Only inputs that change the prompts may invalidate a saved chunk cache.

    The request allowance and the run's labels are deliberately not strict: widening a budget is the
    usual reason to resume, and a corrected event name must not re-pay for the saved calls.
    """
    path = out / INPUTS_FILENAME
    if not path.is_file():
        return {"strict_matched": True, "written": True, "metadata_changed": {}}
    saved = json.loads(path.read_text(encoding="utf-8"))
    stale = {key: {"saved": saved.get(key), "current": value}
             for key, value in manifest.items()
             if key in STRICT_INPUT_KEYS and saved.get(key) != value}
    if stale:
        raise ValueError(
            "Saved chunks came from different inputs; refusing to mix provenance: {}".format(
                json.dumps(stale, indent=2)))
    changed = {key: {"saved": saved.get(key), "current": value}
               for key, value in manifest.items()
               if key not in STRICT_INPUT_KEYS and saved.get(key) != value}
    return {"strict_matched": True, "written": False, "metadata_changed": changed}


def adjudicate_saved_timeline(timeline_path, output_dir, source_id, duration, event_name="",
                              source_path="", endpoint=None, model=None, requests=None,
                              max_input_chars=MAX_INPUT_CHARS, overlap=DEFAULT_WINDOW_OVERLAP,
                              timeout=1800, provenance=None, resume=False, policy_path=None,
                              max_completion_tokens=DEFAULT_COMPLETION_TOKENS):
    # type: (Any, Any, str, float, str, str, Optional[str], Optional[str], Optional[int], int, int, int, Optional[Dict[str, Any]], bool, Optional[str]) -> Dict[str, Any]
    """Adjudicate a saved timeline into a fresh output directory. Makes no vision requests.

    Args:
        timeline_path: Classified timeline JSON from an earlier detect run (list of observations).
        output_dir: Must be empty. Its ``adjudication/`` chunks make an interrupted run resumable.
        source_id: Identity of the recording the timeline came from, recorded verbatim.
        duration: Recording length in seconds. Caller-provided; nothing here probes a file.
        provenance: Free-form links (paths, hashes, ids) recorded with the run.
        resume: Reuse verified chunks already in output_dir instead of re-asking the model.
        policy_path: Cutting policy to adjudicate with. Defaults to the repository's policy file.

    Raises:
        RecoveryIncomplete: after writing the partial artifacts, when a window went unanswered.
    """
    rows = load_saved_timeline(timeline_path)
    if not str(source_id or "").strip():
        raise ValueError("source_id is required so a recovered run names what it came from")
    if not isinstance(duration, (int, float)) or isinstance(duration, bool) \
            or not math.isfinite(duration) or duration <= 0:
        raise ValueError("duration must be a positive finite number of seconds")
    endpoint = local_model.chat_completions_url(endpoint or local_model.default_endpoint())
    model = model or local_model.default_model()
    requests = DEFAULT_ADJUDICATION_REQUESTS if requests is None else _check_requests(requests)
    if max_input_chars <= 0 or not isinstance(max_input_chars, int):
        raise ValueError("max_input_chars must be a positive integer")
    if not isinstance(overlap, int) or isinstance(overlap, bool) or overlap < 0:
        raise ValueError("overlap must be a non-negative integer")
    if not (isinstance(max_completion_tokens, int) and not isinstance(max_completion_tokens, bool)
            and max_completion_tokens >= 1):
        raise ValueError("max_completion_tokens must be a positive integer")

    policy_text = _policy_snapshot(policy_path)
    out = Path(output_dir)
    timeline_path = Path(timeline_path).resolve()
    manifest = _inputs_manifest({
        "timeline": str(timeline_path),
        "timeline_sha256": _sha256_file(timeline_path),
        "timeline_rows": len(rows),
        "policy_hash": _policy_hash(policy_text),
        "model": model,
        "endpoint": endpoint,
        "requests": requests,
        "max_input_chars": max_input_chars,
        "max_completion_tokens": max_completion_tokens,
        "overlap": overlap,
        "source_id": str(source_id),
        "duration": float(duration),
        "event_name": str(event_name or ""),
    })
    _check_output_dir(out, resume)
    out.mkdir(parents=True, exist_ok=True)
    resume_check = _check_resume_inputs(out, manifest)
    boundary_workflow.write_json(out / INPUTS_FILENAME, manifest)

    adjudicator = _adjudicator(endpoint, model, requests, max_input_chars, overlap,
                               output_dir=out, chunk_dir=out / _CHUNKS_DIRNAME, timeout=timeout,
                               max_completion_tokens=max_completion_tokens)
    matches: List[Dict[str, Any]] = []
    review: List[Dict[str, Any]] = []
    adjudication: Optional[Dict[str, Any]] = None
    error = None
    status = "complete"
    try:
        matches, review, adjudication = adjudicator.propose_windowed(rows, policy_text)
    except TemporalAdjudicationIncomplete as exc:
        review, adjudication, error, status = list(exc.review_items), exc.provenance, str(exc), "incomplete"
    except TemporalAdjudicationError as exc:
        error, status = str(exc), "incomplete"
        adjudication = {"algorithm": WINDOW_ALGORITHM, "model": model, "error": str(exc),
                        "model_calls": adjudicator.calls, "usage": adjudicator.usage}
    review.append({
        "type": "recovered_refinement_history",
        "disposition": "uncertain",
        "reason": REFINEMENT_HISTORY_NOTE,
    })
    result = boundary_workflow.finalize_run(
        out, matches, review,
        {"uri": str(source_path or ""), "source": str(source_id), "duration": float(duration)},
        float(duration), event_name, manifest["policy_hash"], model,
        policy=policy_text,
        policy_source=str(boundary_workflow.policy_path(policy_path)),
        proposal_algorithm=WINDOW_ALGORITHM,
        vision_calls=0,
        adjudication_calls=int((adjudication or {}).get("model_calls", 0)),
        adjudication=adjudication,
        counts={"recovered_observations": len(rows), "classified": None, "refined": None,
                "sampled": None},
        usage=[],
        refinement_status="not_recorded",
        cache_enabled=False,
        cached_frame_count=0,
        status=status,
        error=error,
        extra_paths={"timeline": str(timeline_path), "inputs": str(out / INPUTS_FILENAME)},
        result_extra={
            "recovered_from": {
                "timeline": str(timeline_path),
                "timeline_sha256": manifest["timeline_sha256"],
                "observations": len(rows),
                "source_video": str(source_path or ""),
                "provenance": dict(provenance or {}),
            },
            "vision_work_performed": False,
            "vision_usage_note": "Classification belongs to the linked run; this recovery made no "
                                 "vision requests and reports none of its usage.",
            "recovery": {"algorithm": RECOVERY_ALGORITHM, "resumed_inputs_check": resume_check,
                         "request_allowance": requests},
        },
    )
    if status != "complete":
        # Artifacts and resumable chunks are already on disk; the exit status still has to fail.
        # The advice has to match the cause: widening a budget does nothing for a call that ran out
        # of completion tokens, and re-running after a timeout can stack work on a busy machine.
        unfinished = [w for w in (adjudication or {}).get("windows", [])
                      if w.get("status") not in (None, "ok", "resumed")]
        kinds = {w.get("status") for w in unfinished}
        if "unresolved_truncation" in kinds:
            hint = ("raise --adjudication-max-tokens (the model used its whole completion budget on "
                    "reasoning); re-running unchanged will repeat the same empty reply")
        elif "unresolved_timeout" in kinds:
            hint = ("check that the endpoint is still idle before --resume: a timed-out request may "
                    "still be generating there")
        elif "not_attempted" in kinds:
            hint = "widen --adjudication-requests and re-run with --resume"
        else:
            hint = "inspect run.json and adjudication/chunk-*.json, then re-run with --resume"
        raise RecoveryIncomplete(
            "{} — no sets were published. Progress is saved in {}; {}".format(error, out, hint),
            result)
    return result
