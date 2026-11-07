#!/usr/bin/env python3
"""Run the pip-crop probe: same frames, same question, only the framing differs.

Two arms over the eight independently annotated frames of BB9's first game:

    crops   the LEFT and RIGHT pip groups, enlarged 4x, with the near edge of the clock in view
    full    the whole 640x360 frame, labelled the same way

``full`` is the control, not a repeat. The first pilot asked six questions of one frame at once, so a
better score on crops alone could have meant "the crop helped" or merely "the question got easier".
Holding the question fixed and changing only the framing is what lets a difference be attributed to
framing at all -- and if the two arms score the same, cropping is dead and more pixels were never the
problem.

Three public steps, because a spent request allowance cannot be unspent:

    --prepare   render crops, write the exact plan and a ``prepared.json`` manifest. No inference.
    --execute   send the requests into a prepared run whose manifest still matches the inputs. Groups
                already answered cleanly are reused, so an interrupted run resumes by asking nothing.
    --restart   discard this probe's own group files and reports for the named arms, then stop. Needs a
                matching manifest, and stays explicit because deleting recorded answers is not a default.

``--execute`` refuses when the annotations, geometry, question, scale, model or endpoint moved after
``--prepare``: resuming a half-spent allowance against different inputs would mix two experiments into
one accuracy figure with nothing on disk saying so.

Usage:
    env PYTHONPATH=. .venv/bin/python scripts/run_pip_crop_probe.py \
        --annotations build/work/bb9/pip-crops-v1/annotations.json \
        --output-dir build/work/bb9/pip-crops-v2 --prepare
    env PYTHONPATH=. .venv/bin/python scripts/run_pip_crop_probe.py \
        --annotations build/work/bb9/pip-crops-v1/annotations.json \
        --output-dir build/work/bb9/pip-crops-v2 --execute
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tekken_vod_helper import boundary_budget as budget  # noqa: E402
from tekken_vod_helper import boundary_media as bm  # noqa: E402
from tekken_vod_helper import boundary_pips as bp  # noqa: E402
from tekken_vod_helper import boundary_windows as bw  # noqa: E402
from tekken_vod_helper import local_model  # noqa: E402

MANIFEST = "prepared.json"


def recorded_answers(out):
    # type: (Path) -> list
    """Paths that mean attempts were spent in this directory."""
    if not out.is_dir():
        return []
    found = [str(p.relative_to(out)) for p in out.glob("result-*.json")]
    found += [str(p.relative_to(out)) for p in out.glob("comparison.json")]
    for arm in bp.ARMS:
        store = out / arm / "groups"
        if store.is_dir():
            found += ["{} ({})".format(store.relative_to(out), p.name) for p in
                      sorted(store.glob(bp.group_filename(arm)))]
    return sorted(found)


def tokens_used(result):
    # type: (dict) -> dict
    """Measured usage from every attempt that came back, plus the accepted-reply split.

    The four flat keys are the all-attempt total and stay the headline, because that is what the box
    spent; a rejected reply that was retried was still billed. ``accepted_replies`` and
    ``rejected_replies_retried`` name the split so a scoring figure is never mistaken for a spend figure,
    and ``records_without_usage`` keeps an absent usage block reported as absent rather than as zero.
    """
    summary = bm.summarize_usage(result.get("usage") or [])
    all_attempts = summary["all_attempts"]
    return {"completion": all_attempts["completion"], "prompt": all_attempts["prompt"],
            "reasoning": all_attempts["reasoning"], "records": all_attempts["records"],
            "records_with_usage": all_attempts["records_with_usage"],
            "records_without_usage": all_attempts["records_without_usage"],
            "usage_complete": all_attempts["complete"],
            "accepted_replies": summary["accepted_replies"],
            "rejected_replies_retried": summary["rejected_replies_retried"],
            "accepted_only": summary["accepted_only"],
            "rejected_only": summary["rejected_only"], "all_attempts": all_attempts}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--annotations", required=True,
                        help="Per-frame expected pip counts, recorded independently of the model.")
    parser.add_argument("--output-dir", required=True, help="Directory for this run.")
    parser.add_argument("--arms", default="crops,full",
                        help="Comma list of crops and/or full. Both is the honest comparison.")
    parser.add_argument("--max-requests", type=int, default=8,
                        help="Attempt allowance per arm. One request per frame.")
    parser.add_argument("--model", default=None, help="Default: $QWEN_MODEL or the Flash Next id.")
    parser.add_argument("--endpoint", default=None)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--scale", type=int, default=bp.CROP_SCALE)
    parser.add_argument("--frame-manifest", default=bw.DEFAULT_MANIFEST,
                        help="The cache index every annotated frame has to appear in, with the timestamp "
                             "the annotation claims. Prepare and execute both refuse before rendering when "
                             "a pair disagrees; the index is not adjusted to fit a plan.")
    parser.add_argument("--budget-file", default=None,
                        help="Shared attempt ledger: one cap and one wall-time deadline across every "
                             "command and every resume. Without it, --max-requests is per-process only.")
    parser.add_argument("--budget-cap", type=int, default=budget.DEFAULT_CAP,
                        help="Total attempts the ledger may charge, retries and follow-up included.")
    parser.add_argument("--budget-minutes", type=float, default=budget.DEFAULT_MINUTES,
                        help="One deadline, stamped at the first send and obeyed by every later command.")
    parser.add_argument("--purpose", default="primary",
                        help="Why these attempts are spent: primary, follow-up, ...")
    step = parser.add_mutually_exclusive_group(required=True)
    step.add_argument("--prepare", action="store_true",
                      help="Render crops and write the plan and manifest. Costs no inference.")
    step.add_argument("--execute", action="store_true",
                      help="Send the requests into a prepared run whose inputs still match.")
    step.add_argument("--restart", action="store_true",
                      help="Discard this probe's group files and reports, then stop.")
    args = parser.parse_args()

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    unknown = [a for a in arms if a not in bp.ARMS]
    if unknown:
        # Spelled out for the one name that gets asked a lot: the first set trial advertised this command
        # with `--arms header`, and an "unknown arm" reply never told anybody where the route lived.
        pointer = (" -- the header band is a different question with different records, so it has its own "
                   "command: scripts/run_header_trial.py --prepare/--execute"
                   if "header" in unknown else "")
        raise SystemExit("unknown arm(s) {}; expected {}{}".format(unknown, list(bp.ARMS), pointer))
    if len(set(arms)) != len(arms):
        raise SystemExit("arm named twice: {}".format(arms))
    if args.execute and not args.budget_file:
        # The same guard the header command got, and for the same reason discovered by breaking it: with
        # no ledger, --max-requests is per-process, so every invocation re-arms the allowance and nothing
        # records what was spent. An attempt that no ledger counts is an attempt nobody approved.
        raise SystemExit("--execute requires --budget-file: attempts that no ledger counts are attempts "
                         "nobody approved, and --max-requests alone resets every process")

    out = Path(args.output_dir)
    # Resolved rather than left None: the manifest has to name the model the group fingerprints carry,
    # or "the default" means whatever it had become by the time someone resumes the run.
    model = args.model or local_model.default_model()
    endpoint = args.endpoint or local_model.default_endpoint()

    annotations = json.loads(Path(args.annotations).read_text(encoding="utf-8"))
    # Every frame named here has to be the frame whose timestamp is written beside it, checked against the
    # cache's own index before a single crop is rendered. The filename is not the time (the cache samples
    # 1.000986 s apart, not 1.000 s) and the timestamp is what joins a reading to a round, so an entry
    # extrapolated from the window next door would render real pixels and file them under an instant that
    # never had them -- which is how one trial put game 1's frames inside game 2's replay.
    checked = bw.verify_annotations(annotations["frames"], manifest=args.frame_manifest,
                                    framing=annotations.get("framing") or annotations.get("version"))
    if checked["problems"]:
        raise SystemExit("annotations do not match the frame manifest; nothing was rendered or sent:\n  "
                         + "\n  ".join(checked["problems"])
                         + "\nFix the annotation to name the frame it means. --frame-manifest "
                           "%s is the index; it is not adjusted to fit a plan." % args.frame_manifest)
    # The annotations file is the single source of frames, timestamps and expected values: asking the
    # filename for a timestamp would silently mis-join every row against every other.
    records = bp.build_records(annotations["frames"], out / "crops", scale=args.scale)
    current = bp.run_manifest(records, arms, args.scale, args.max_requests, model=model,
                              annotation_version=annotations.get("version"))
    current["endpoint"] = endpoint
    current["frame_identity"] = {k: checked[k] for k in ("entries", "verified_against_manifest",
                                                          "verified_native_source", "manifest",
                                                          "tolerance_seconds")}
    annotated = {round(float(r["timestamp"]), 3): r for r in records}
    path_manifest = out / MANIFEST
    prepared = (json.loads(path_manifest.read_text(encoding="utf-8"))
                if path_manifest.is_file() else None)
    problems = bp.manifest_problems(prepared, current)

    if args.restart:
        if problems:
            raise SystemExit("restart refused -- these answers are not the ones this directory "
                             "promised:\n{}\n\nUse a fresh --output-dir instead.".format(
                                 "\n".join(problems)))
        removed = []
        for arm in arms:
            store = out / arm / "groups"
            if not store.is_dir():
                continue
            for path in store.glob(bp.group_filename(arm)):
                removed.append(str(path.relative_to(out)))
                path.unlink()
            for name in ("result-{}.json".format(arm), "score-{}.json".format(arm)):
                if (out / name).is_file():
                    removed.append(name)
                    (out / name).unlink()
        if "comparison.json" in recorded_answers(out):
            (out / "comparison.json").unlink()
            removed.append("comparison.json")
        print(json.dumps({"step": "restart", "removed": sorted(set(removed)), "arms": arms,
                          "remaining_answers": recorded_answers(out),
                          "warning": "group files are the only record of what was answered; deleting "
                                     "them means the next --execute re-asks and re-spends the allowance"},
                         indent=2, sort_keys=True))
        return

    if args.prepare:
        if problems and recorded_answers(out):
            raise SystemExit("cannot re-prepare over recorded answers that mean something else:\n{}"
                             "\n\n--restart to discard them, or use a fresh --output-dir.".format(
                                 "\n".join(problems)))
        out.mkdir(parents=True, exist_ok=True)
        summary = {"step": "prepare", "output_dir": str(out), "executed": False,
                   "model": model, "endpoint": endpoint,
                   "annotation_version": annotations.get("version"),
                   "frames": len(records),
                   "re_prepared_over_answers": bool(recorded_answers(out)),
                   "arms": {}}
        for arm in arms:
            plan = bp.plan_requests(records, arm, max_requests=args.max_requests, model=model)
            (out / "plan-{}.json".format(arm)).write_text(json.dumps(plan, indent=2, sort_keys=True),
                                                          encoding="utf-8")
            summary["arms"][arm] = {
                "requests_planned": plan["requests_planned"],
                "images_total": plan["images_total"],
                "attempt_allowance": plan["attempt_allowance"],
                "completion_tokens_ceiling": plan["requests_planned"]
                                             * plan["completion_tokens_per_request"]}
        summary["totals"] = {
            "attempts": sum(a["requests_planned"] for a in summary["arms"].values()),
            "images": sum(a["images_total"] for a in summary["arms"].values()),
            "completion_tokens_ceiling": sum(a["completion_tokens_ceiling"]
                                             for a in summary["arms"].values())}
        summary["frames_planned"] = [
            {"t": g["timestamp"], "images": g["images"],
             "row_drawn": annotated[round(g["timestamp"], 3)]["row_drawn"],
             "expected": annotated[round(g["timestamp"], 3)]["expected"]}
            for g in bp.plan_requests(records, arms[0], args.max_requests, model=model)["groups"]
            if round(g["timestamp"], 3) in annotated]
        path_manifest.write_text(json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")
        summary["manifest"] = str(path_manifest)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return

    if problems:
        raise SystemExit("execute refused:\n{}\n\nrun --prepare first, review plan-<arm>.json, then "
                         "--execute. If the inputs really did move, use a fresh --output-dir.".format(
                             "\n".join(problems)))
    summary = {"step": "execute", "output_dir": str(out), "executed": True, "model": model,
               "endpoint": endpoint, "annotation_version": annotations.get("version"),
               "attempt_allowance_per_arm": args.max_requests, "arms": {}, "deferred_arms": [],
               "groups_resumed": 0}
    ledger = None
    if args.budget_file:
        ledger = budget.AttemptLedger.open(args.budget_file, cap=args.budget_cap,
                                           minutes=args.budget_minutes,
                                           label="{}:{}".format(out.name, args.purpose))
        # An attempt left hanging by a previous process is reconciled against the answers actually on
        # disk before this command may spend, so a kill mid-request can never be re-sent silently --
        # and a resume inherits both the remaining attempts and the same deadline.
        answered = {}
        for arm in arms:
            store = out / arm / "groups"
            if not store.is_dir():
                continue
            for record in records:
                path = store / bp.group_filename(arm, bp.group_fingerprint([record], arm, model))
                if path.is_file():
                    answered[budget.request_tag({"crop": [record["crops"]["LEFT"]["path"],
                                                          record["crops"]["RIGHT"]["path"]]})] = \
                        path.stat().st_mtime
        report = ledger.reconcile_in_flight(answered)
        if report["closed"]:
            summary["in_flight_reconciled"] = len(report["closed"])
        if report["unattributable"]:
            # Charged, kept, and named: an attempt whose reply cannot be found is not written off because
            # some later attempt of the same request did get one.
            summary["attempts_kept_uncertain"] = report["unattributable"]
        summary["budget"] = ledger.summary()
        # The ledger governs sends, not the plan. Rewinding --max-requests to what is left would change
        # the run's identity and make the resume impossible to authorise, so the prepared allowance
        # stands and every attempt is refused at the socket instead, by reserve().
        summary["attempt_allowance_this_command"] = args.max_requests
    results = {}
    ceiling = 0
    # One uncertain transport stops the whole comparison, not just the arm that hit it. The endpoint
    # may still be generating that request, and the second arm would queue behind work nobody is
    # waiting for -- on a single-model box that costs the first request's time and proves nothing.
    halted = None
    for arm in arms:
        plan = bp.plan_requests(records, arm, max_requests=args.max_requests, model=model)
        if halted:
            deferred = {"arm": arm, "status": "deferred", "reason": halted,
                        "requests_sent": 0, "requests_planned": plan["requests_planned"],
                        "attempt_allowance_unspent": plan["requests_planned"],
                        "observations": [],
                        "resume": "re-run --execute once the endpoint is idle; this arm has no "
                                  "group files, so it starts clean"}
            (out / "deferred-{}.json".format(arm)).write_text(
                json.dumps(deferred, indent=2, sort_keys=True), encoding="utf-8")
            summary["arms"][arm] = deferred
            summary["deferred_arms"].append(arm)
            continue
        ceiling += plan["requests_planned"] * plan["completion_tokens_per_request"]
        stale = out / "deferred-{}.json".format(arm)
        if stale.is_file():
            # A deferral that outlives its cause reads like the arm is still on hold.
            stale.unlink()
        client = None
        if ledger is not None:
            # What a resume owes is the groups with no file, not the whole plan: comparing the plan
            # against the remaining attempts would refuse a one-frame repair for want of fourteen.
            store = out / arm / "groups"
            owed = sum(1 for record in records
                       if not (store / bp.group_filename(
                           arm, bp.group_fingerprint([record], arm, model))).is_file())
            if owed > ledger.remaining:
                raise SystemExit(
                    "refusing to start arm {} into {}: {} groups are still owed and the shared ledger "
                    "at {} has {} of {} attempts left (deadline {}). Ask for more budget or defer the "
                    "excess deliberately -- do not spend a later command's attempts.".format(
                        arm, out, owed, ledger.path, ledger.remaining, ledger.cap, ledger.deadline_at))
            summary["groups_owed_by_this_command"] = owed
            client = budget.attach(bp.PipReadClient(
                arm=arm, endpoint=endpoint, model=model, max_requests=args.max_requests,
                timeout=args.timeout, output_dir=str(out / arm)), ledger, purpose=args.purpose)
        result = bp.persist_probe(records, out / arm, arm, max_requests=args.max_requests,
                                  endpoint=endpoint, model=model, timeout=args.timeout,
                                  client=client)
        results[arm] = result
        (out / "result-{}.json".format(arm)).write_text(
            json.dumps(result, indent=2, sort_keys=True, default=str), encoding="utf-8")
        if result["stopped_reason"] == "transport_uncertain":
            halted = ("transport_uncertain in arm {} after {} request(s): the server may still be "
                      "processing, so no further requests were sent".format(
                          arm, result["requests_sent"]))
            summary["halted"] = halted
        elif result["stopped_reason"] == "control_mismatch":
            # The control is this run's claim that the framing shows what the paired truth shows. If it
            # failed, every further request measures the framing rather than the footage, so the next
            # arm is not deferred for the server's sake but because its answer would mean nothing.
            halted = ("control_mismatch in arm {}: the control frame did not reproduce the reading "
                      "paired to it at the same instant, so no request that depends on this framing "
                      "was sent".format(arm))
            summary["halted"] = halted
            summary["control"] = result.get("control")
        score = bp.score_readings(result["observations"], annotations["frames"])
        (out / "score-{}.json".format(arm)).write_text(json.dumps(score, indent=2, sort_keys=True),
                                                       encoding="utf-8")
        summary["arms"][arm] = {"requests_planned": plan["requests_planned"],
                                "images_planned": plan["images_total"],
                                "requests_sent": result["requests_sent"],
                                # Passed, failed or absent: whether the run was entitled to continue is
                                # a separate fact from how many frames it ended up scoring.
                                "control": result.get("control"),
                                "groups_answered": result["groups_answered"],
                                "groups_reused": result["groups_reused"],
                                "groups_failed": result["groups_failed"],
                                "not_attempted": result["not_attempted"],
                                "stopped_reason": result["stopped_reason"],
                                "status": result["status"],
                                "errors": result["errors"],
                                "tokens": tokens_used(result),
                                "score": {k: score[k] for k in ("frames", "frames_correct",
                                                                "sides_exact", "sides_total",
                                                                "error_kinds", "ungraded_frames",
                                                                "accuracy_is_reportable")
                                                           if k in score},
                                "ungraded": score.get("ungraded", []),
                                "per_frame": score["per_frame"]}
        summary["groups_resumed"] += result["groups_reused"]
    summary["completion_tokens_ceiling"] = ceiling
    if ledger is not None:
        # Every attempt this command charged, whether or not it produced an answer.
        summary["budget"] = ledger.summary()

    if {"crops", "full"} <= set(arms):
        if {"crops", "full"} != set(results):
            # A one-armed comparison is not a comparison: the verdict string would be computed from an
            # empty observation list on one side and read as "crops helped".
            summary["comparison"] = "not run: {} never executed".format(
                ", ".join(sorted({"crops", "full"} - set(results))))
        else:
            comparison = bp.compare_arms(results["crops"], results["full"], annotations["frames"])
            (out / "comparison.json").write_text(json.dumps(comparison, indent=2, sort_keys=True),
                                                  encoding="utf-8")
            summary["verdict"] = comparison["verdict"]
            summary["does_not_establish"] = comparison["does_not_establish"]
            summary["paired_per_frame"] = comparison.get("paired")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
