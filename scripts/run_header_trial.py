#!/usr/bin/env python3
"""Ask the broadcast scoreboard band who is playing and what the tally says -- prepare, execute, resume.

This is the public command behind the header route: ``boundary_header.py`` builds and validates the
question, and this is what sends it. It exists because a client class plus unit tests is not an executor --
the six-request set trial was advertised with ``--arms header`` for a command that never accepted that arm,
which is the difference between a route and a reachable route.

Three steps, because a spent request allowance cannot be unspent:

    --prepare   render each band, write the exact plan and an immutable ``prepared.json``. Costs no
                inference, and leaves the directory executable.
    --execute   send the prepared bands into a run whose manifest still matches its inputs. A band already
                answered cleanly is reused, so an interrupted run resumes by asking nothing.
    --restart   delete this run's own group files and reports, then stop. Explicit, because discarding
                recorded answers means the next --execute re-spends the allowance.

``--execute`` refuses when the spec, model, endpoint, scale or rendered images moved after ``--prepare``:
resuming a half-spent allowance against different inputs would mix two experiments into one figure with
nothing on disk saying so.

What this command does **not** do, on purpose:

* it reads no expected values. The trial spec says which instants to look at and why; the labels that
  judge the answers live in a separate evaluation file, and the run refuses a spec that carries them. A
  stop rule keyed to a label would make the label the run's referee instead of its measurement, so a bad
  transcription is a reported result and the batch continues;
* it decides a set. Six band readings are six transcriptions: the outcome is scored by
  ``scripts/evaluate_header_trial.py``, and the merge into the grouping rules is prepared but not wired.

Usage:
    env PYTHONPATH=. .venv/bin/python scripts/run_header_trial.py \
        --trial build/work/bb9/header-trial-v1/trial.json \
        --output-dir build/work/bb9/header-trial-v1/run --prepare
    env PYTHONPATH=. .venv/bin/python scripts/run_header_trial.py \
        --trial build/work/bb9/header-trial-v1/trial.json \
        --output-dir build/work/bb9/header-trial-v1/run \
        --budget-file build/work/bb9/header-trial-v1/attempts.json \
        --budget-cap 6 --budget-minutes 5 --execute
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tekken_vod_helper import boundary_budget as budget  # noqa: E402
from tekken_vod_helper import boundary_header as bh  # noqa: E402
from tekken_vod_helper import boundary_windows as bw  # noqa: E402
from tekken_vod_helper import local_model  # noqa: E402


def manifest_path_for(spec, root):
    # type: (dict, Path) -> Path
    """The cache index a spec's frames are claimed to come from, resolved once and used twice.

    The same path feeds the frame lookup and the identity check below, which is the point: a run whose
    verification consulted a different index from its selection would pass by construction.
    """
    path = Path(spec["frame_manifest"])
    return path if path.is_absolute() else root / path


def resolve_frames(spec, root):
    # type: (dict, Path) -> list
    """Turn each spec instant into the cached frame nearest it, recording both timestamps.

    The label and the answer are about the *frame*, so the frame's own timestamp travels with the request.
    On this cache the two numbers differ, and an earlier experiment learned that the hard way: a pip label
    attached to an off-by-one index sat on the other side of a HUD transition from the footage it claimed.
    """
    manifest_path = manifest_path_for(spec, root)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    frames = manifest["frames"]
    radius = float(spec.get("radius_seconds", 1.0))
    instants = []
    for entry in spec["instants"]:
        want = float(entry["at"])
        best = min(frames, key=lambda f: abs(float(f["timestamp"]) - want))
        offset = abs(float(best["timestamp"]) - want)
        if offset > radius:
            raise SystemExit("no cached frame within {} s of {:.3f} s (nearest is {:.3f} s away): "
                             "refusing to answer for a different instant".format(radius, want, offset))
        path = Path(best["path"])
        if not path.is_absolute():
            path = root / path
        if not path.is_file():
            raise SystemExit("manifest frame {} is not on disk".format(path))
        instants.append({"at": want, "frame": str(path), "frame_timestamp": float(best["timestamp"]),
                         "frame_id": best.get("id"), "offset_seconds": round(offset, 6),
                         "question": entry.get("question", "identity_mapping"),
                         "because": entry.get("because"), "selected_by": entry.get("selected_by")})
    return instants


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trial", required=True, help="Runtime spec: which instants to read, and why.")
    parser.add_argument("--output-dir", required=True, help="Directory for this run.")
    parser.add_argument("--max-requests", type=int, default=None,
                        help="Bands to ask about. Default: every instant in the spec, and the command "
                             "refuses a spec longer than the ledger cap.")
    parser.add_argument("--scale", type=int, default=3, help="Upscale applied to the band crop.")
    parser.add_argument("--model", default=None, help="Default: $QWEN_MODEL or the Flash Next id.")
    parser.add_argument("--endpoint", default=None)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--budget-file", default=None,
                        help="Shared attempt ledger: one cap and one wall-time deadline across every "
                             "command and every resume. Without it, --max-requests is per-process only.")
    parser.add_argument("--budget-cap", type=int, default=budget.DEFAULT_CAP,
                        help="Total attempts the ledger may charge, retries included.")
    parser.add_argument("--budget-minutes", type=float, default=budget.DEFAULT_MINUTES,
                        help="One deadline, stamped at the first send and obeyed by every later command.")
    parser.add_argument("--purpose", default="primary", help="Why these attempts are spent.")
    step = parser.add_mutually_exclusive_group(required=True)
    step.add_argument("--prepare", action="store_true",
                      help="Render the bands and write the plan and manifest. Costs no inference.")
    step.add_argument("--execute", action="store_true",
                      help="Send the requests into a prepared run whose inputs still match.")
    step.add_argument("--restart", action="store_true",
                      help="Discard this run's group files and reports, then stop.")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    spec = bh.load_trial(args.trial)
    if args.execute and not args.budget_file:
        # Refused because it happened once: an --execute with no ledger sends real requests that nothing
        # counts, so the cap and the single wall-time deadline -- the whole governance the trial is
        # approved under -- are simply not in force. Pass a --budget-file, or do not spend.
        raise SystemExit("--execute requires --budget-file: attempts that no ledger counts are attempts "
                         "nobody approved, and --max-requests alone resets every process")
    instants = resolve_frames(spec, root)
    max_requests = args.max_requests or len(instants)
    if max_requests < len(instants):
        raise SystemExit("--max-requests {} would silently drop {} of the {} prepared instants; a "
                         "partial header trial is not the trial that was approved".format(
                             max_requests, len(instants) - max_requests, len(instants)))
    if args.budget_file and max_requests > args.budget_cap:
        raise SystemExit("the plan asks for {} bands but the ledger cap is {}: raise the cap "
                         "deliberately, or trim the spec, rather than let a request stop mid-run".format(
                             max_requests, args.budget_cap))

    out = Path(args.output_dir)
    model = args.model or local_model.default_model()
    endpoint = args.endpoint or local_model.default_endpoint()
    prepared_path = out / bh.MANIFEST_NAME
    plan_path = out / "plan-header.json"

    if args.restart:
        if not prepared_path.is_file():
            raise SystemExit("nothing to restart: {} has no {}".format(out, bh.MANIFEST_NAME))
        removed = []
        store = out / "groups"
        if store.is_dir():
            for path in store.glob(bh.group_filename()):
                removed.append(str(path.relative_to(out)))
                path.unlink()
        for name in ("result-header.json", "report-header.json"):
            if (out / name).is_file():
                removed.append(name)
                (out / name).unlink()
        print(json.dumps({"step": "restart", "removed": sorted(removed),
                          "remaining_answers": bh.recorded_answers(out),
                          "warning": "the group files are the only record of what was answered; deleting "
                                     "them means the next --execute re-asks and re-spends the allowance"},
                         indent=2, sort_keys=True))
        return

    # The plan is rendered before the manifest is compared, because the manifest has to describe the
    # images that would actually be sent -- their digests, not a promise about them.
    rendered = bh.plan(instants, out / "bands", scale=args.scale)
    requests = rendered["requests"]
    # Same rule the pip probe applies, against the same index: the band crop named for frame N has to be the
    # band crop of frame N *at the instant the cache recorded for N*. Here the pairs are produced from the
    # manifest, so a mismatch means something between this run and the cache moved -- a hand-edited spec, a
    # stale prepared manifest, a frame folder swapped under a plan -- and a band answer filed at a time it
    # was not sampled at credits a win to the wrong game.
    band_manifest = manifest_path_for(spec, root)
    checked = bw.verify_annotations(
        [{"frame": r["frame"], "timestamp": r["frame_timestamp"]} for r in requests],
        manifest=str(band_manifest))
    if checked["problems"]:
        raise SystemExit("prepared band requests do not match the frame manifest; nothing was sent:\n  "
                         + "\n  ".join(checked["problems"])
                         + "\n%s is the index; it is not adjusted to fit a plan." % band_manifest)
    current = bh.run_manifest(requests, model, endpoint, args.scale, max_requests,
                              spec_version=spec.get("version"))
    current["frame_identity"] = {k: checked[k] for k in ("entries", "verified_against_manifest",
                                                         "verified_native_source", "manifest",
                                                         "tolerance_seconds")}
    stored = (json.loads(prepared_path.read_text(encoding="utf-8"))
              if prepared_path.is_file() else None)
    problems = bh.manifest_problems(stored, current)

    if args.prepare:
        answers = bh.recorded_answers(out)
        if stored is not None and problems and answers:
            raise SystemExit("cannot re-prepare over recorded answers that mean something else:\n{}\n\n"
                             "--restart to discard them, or use a fresh --output-dir.".format(
                                 "\n".join(problems)))
        out.mkdir(parents=True, exist_ok=True)
        (out / "plan-header.json").write_text(json.dumps(rendered, indent=2, sort_keys=True),
                                              encoding="utf-8")
        prepared_path.write_text(json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps({"step": "prepare", "output_dir": str(out), "executed": False,
                          "model": model, "endpoint": endpoint, "spec_version": spec.get("version"),
                          "requests_planned": len(requests), "images_total": rendered["images_total"],
                          "attempt_allowance": max_requests,
                          "completion_tokens_ceiling": len(requests)
                                                       * bh.HeaderReadClient.request_max_tokens,
                          "manifest": str(prepared_path),
                          "instants": [{"at": r["at"], "frame_id": r["frame_id"],
                                        "frame_timestamp": r["frame_timestamp"],
                                        "offset_seconds": r["offset_seconds"],
                                        "question": r["question"], "image_sha256": r["image_sha256"]}
                                       for r in requests],
                          "budget": {"ledger": args.budget_file, "cap": args.budget_cap,
                                     "minutes": args.budget_minutes,
                                     "note": "the ledger is opened by the first --execute, not here"}},
                         indent=2, sort_keys=True))
        return

    if problems:
        raise SystemExit("execute refused:\n{}\n\nrun --prepare first, review {}, then --execute. If "
                         "the inputs really did move, use a fresh --output-dir.".format(
                             "\n".join(problems), plan_path.name))

    summary = {"step": "execute", "output_dir": str(out), "executed": True, "model": model,
               "endpoint": endpoint, "spec_version": spec.get("version"),
               "requests_planned": len(requests), "groups_resumed": 0, "status": None}
    client = None
    ledger = None
    if args.budget_file:
        ledger = budget.AttemptLedger.open(args.budget_file, cap=args.budget_cap,
                                           minutes=args.budget_minutes,
                                           label="{}:{}".format(out.name, args.purpose))
        # An attempt left hanging by a killed process is reconciled against the answers actually on disk
        # before anything may be spent, so a crash mid-request can never be re-sent silently -- and a
        # resume inherits both the remaining attempts and the same single deadline.
        store = out / "groups"
        answered = {}
        for request in requests:
            path = store / bh.group_filename(bh.request_fingerprint(request, model))
            if path.is_file():
                answered[budget.request_tag({"crop": [request["image"]]})] = path.stat().st_mtime
        report = ledger.reconcile_in_flight(answered)
        if report["closed"]:
            summary["in_flight_reconciled"] = len(report["closed"])
        if report["unattributable"]:
            summary["attempts_kept_uncertain"] = report["unattributable"]
        summary["budget"] = ledger.summary()
        owed = len(requests) - len(answered)
        if owed > ledger.remaining:
            raise SystemExit(
                "refusing to start into {}: {} bands are still owed and the shared ledger at {} has {} "
                "of {} attempts left (deadline {}). Ask for more budget or defer the rest deliberately "
                "-- do not spend a later command's attempts.".format(
                    out, owed, ledger.path, ledger.remaining, ledger.cap, ledger.deadline_at))
        summary["groups_owed_by_this_command"] = owed
        client = budget.attach(bh.HeaderReadClient(endpoint=endpoint, model=model,
                                                   max_requests=max_requests, timeout=args.timeout,
                                                   output_dir=str(out)), ledger,
                               purpose=args.purpose)
    result = bh.persist_readings(requests, out, client=client, max_requests=max_requests,
                                 endpoint=endpoint, model=model, timeout=args.timeout)
    (out / "result-header.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, default=str), encoding="utf-8")
    pairings = bh.pairing_hypotheses(requests, result["groups"])
    (out / "report-header.json").write_text(json.dumps(
        {"kind": "header-band-report", "arm": bh.ARM, "version": bh.VERSION,
         "model": model, "endpoint": endpoint, "spec_version": spec.get("version"),
         "run": {k: result[k] for k in ("status", "stopped_reason", "requests_sent", "groups_planned",
                                        "groups_answered", "groups_reused", "groups_failed",
                                        "not_attempted", "sides_declining_a_value", "errors")},
         "evidence_class": result["evidence_class"],
         "pairings": pairings,
         "claims": "what the broadcast band displayed at these instants, as one model read it",
         "cannot_claim": ["a set boundary: these are transcriptions, not membership",
                          "a game winner: the pip counts here are band readings, not the validated "
                          "crop measurements",
                          "the final tally: an unreadable value is reported as unreadable"],
         "independent_of_recording": False},
        indent=2, sort_keys=True, default=str), encoding="utf-8")
    summary["run"] = {k: result[k] for k in ("status", "stopped_reason", "requests_sent",
                                             "groups_planned", "groups_answered", "groups_reused",
                                             "groups_failed", "not_attempted",
                                             "sides_declining_a_value", "errors")}
    summary["groups_resumed"] = result["groups_reused"]
    summary["tokens"] = tokens_used(result)
    summary["completion_tokens_ceiling"] = len(requests) * bh.HeaderReadClient.request_max_tokens
    if ledger is not None:
        summary["budget"] = ledger.summary()
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))


def tokens_used(result):
    # type: (dict) -> dict
    """Measured usage from the charged attempts, ceiling aside."""
    totals = {"completion": 0, "prompt": 0, "reasoning": 0, "records": 0}
    for entry in result.get("usage") or []:
        usage = (entry or {}).get("tokens") or {}
        totals["records"] += 1
        totals["completion"] += int(usage.get("completion_tokens") or 0)
        totals["prompt"] += int(usage.get("prompt_tokens") or 0)
        totals["reasoning"] += int((usage.get("completion_tokens_details") or {})
                                   .get("reasoning_tokens") or 0)
    return totals


if __name__ == "__main__":
    main()
