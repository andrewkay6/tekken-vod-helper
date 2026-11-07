#!/usr/bin/env python3
"""Render source-resolution pip crops from the master recording, with the frame identity written down.

Offline image work only: this decodes, crops, digests and reports. It never contacts a model, never opens
a label file, and never writes outside ``--out``. The master itself is opened read-only.

    # prove the cache-to-master mapping on the awkward samples and render nothing else
    .venv/bin/python scripts/render_source_crops.py --align-only

    # render named instants and named cache frames into a new directory
    .venv/bin/python scripts/render_source_crops.py --target at=1800.800500:role=kao_flash \\
        --target cache_id=1801:role=control

Every image is identified by the master frame index, the decoded presentation timestamp, its distance
from the requested instant, the alignment uncertainty, the crop box in source pixels and the same box as
the fractions the cache arm used, plus a sha256 of the PNG actually written. The cache-to-master mapping
is *proved by content* on the sampled frames and the fitted rule is quoted as a measured residual: the
project has already been moved by an off-by-one between a cached frame and a master frame once, and a
constant copied forward is how that happens again.
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tekken_vod_helper import boundary_source as bs  # noqa: E402

DEFAULT_MASTER = ROOT / "build/work/bb9/oct22-1440p.mp4"
DEFAULT_MANIFEST = ROOT / "build/work/bb9/detect-1440p-v1/frames/sample-manifest.json"
DEFAULT_OUT = ROOT / "build/work/bb9/source-crops-v1"


def parse_target(text, defaults=None) -> Dict[str, Any]:
    # type: (str, Optional[Dict[str, Any]]) -> Dict[str, Any]
    """``key=value`` pairs joined by ``:``, e.g. ``at=1371.001:role=pre_flash:reason=...``."""
    target = dict(defaults or {})
    for piece in text.split(":"):
        if not piece:
            continue
        key, _, value = piece.partition("=")
        if not value:
            raise SystemExit("target {!r} is not key=value".format(text))
        key = key.strip()
        if key == "at":
            target["at_seconds"] = float(value)
        elif key == "cache_id":
            target["cache_id"] = int(value)
        elif key in ("role", "reason", "label"):
            target[key if key != "label" else "label"] = value
        else:
            raise SystemExit("unknown target key {!r} in {!r}; use at, cache_id, role, reason".format(
                key, text))
    if "at_seconds" not in target and "cache_id" not in target:
        raise SystemExit("target {!r} names neither at=<seconds> nor cache_id=<id>".format(text))
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--master", default=str(DEFAULT_MASTER))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--align-sample", action="append", type=int, default=None,
                        help="Cache frame id to content-match. Repeatable. Default: first, last, the "
                             "uneven steps, the missing files, and six frames across the recording.")
    parser.add_argument("--target", action="append", default=[],
                        help="What to render: at=<seconds> or cache_id=<id>, with optional "
                             "role=/reason=. Repeatable.")
    parser.add_argument("--targets-file", default=None,
                        help="JSON list of targets, for a batch too long for the command line.")
    parser.add_argument("--purpose", default="unspecified: say what question these crops answer")
    parser.add_argument("--scale", type=int, default=1,
                        help="Integer upscale of the native crop. 1 keeps the delivered size equal to "
                             "the cache arm's 4x upscale, which is what makes the two arms comparable.")
    parser.add_argument("--align-only", action="store_true",
                        help="Prove the mapping and write the artifacts without rendering any crop.")
    parser.add_argument("--execution-set", default=None, metavar="GROUPS_DIR",
                        help="Instead of rendering crops for inspection, write the runtime input for "
                             "the public runner: annotations.json (frames = decoded master PNGs) and "
                             "pairing.json (manifest id, saved group file, every image digest). POINT "
                             "THIS AT THE groups/ DIRECTORY OF THE RUN WHOSE ANSWERS ARE PAIRED.")
    parser.add_argument("--candidate", action="append", default=[],
                        help="cache_id=<id>[:role=control|probe][:reason=...], for --execution-set. A "
                             "candidate whose instant has no confident saved answer gets no expectation "
                             "and can never be a control.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be decoded; reads nothing but the manifest.")
    parser.add_argument("--ffmpeg-path", default="")
    parser.add_argument("--ffprobe-path", default="")
    args = parser.parse_args(argv)

    master, manifest = Path(args.master), Path(args.manifest)
    if not manifest.is_file():
        raise SystemExit("no sample manifest at {}".format(manifest))
    cache = bs.load_cache_index(manifest)
    irregular = cache["irregular"]
    targets = [parse_target(t) for t in args.target]
    if args.targets_file:
        targets += json.loads(Path(args.targets_file).read_text(encoding="utf-8"))
    if not args.align_only and not args.execution_set and not targets:
        raise SystemExit("nothing to render: pass --target, --targets-file, --execution-set, or "
                         "--align-only")
    if args.dry_run:
        print(json.dumps({"would_probe": str(master), "manifest": str(manifest),
                          "cache_frames": cache["count"],
                          "cache_irregular": irregular,
                          "samples": args.align_sample or "auto: first, last, uneven, missing, six across",
                          "targets": targets, "out": args.out,
                          "inference_sent": False,
                          "note": "alignment must be proved before any crop is trusted, so the sample "
                                  "set is decoded even in a real run"}, indent=2, sort_keys=True))
        return 0
    if not master.is_file():
        raise SystemExit("no master recording at {}: nothing to render from".format(master))

    facts = bs.probe_master(master, ffprobe_path=args.ffprobe_path)
    print("master {}x{} @ {} fps, {:.1f} s, {:.1f} GB".format(
        facts["width"], facts["height"], facts["avg_frame_rate"].replace(":", "/"),
        facts["duration"] or 0.0, facts["bytes"] / 1e9), file=sys.stderr)
    alignment = bs.align(cache, facts, master, Path(args.out) / "scratch",
                         samples=args.align_sample, ffmpeg_path=args.ffmpeg_path)
    print("alignment: {} samples matched, rule {} on {} of them, worst residual {} s, uncertainty {} s"
          .format(alignment["samples_matched"], "agreed" if not alignment["rule_disagreed"] else "DISAGREED",
                  alignment["rule_agreed"], alignment["worst_rule_residual_seconds"],
                  alignment["uncertainty_seconds"]), file=sys.stderr)
    for refusal in alignment["samples_refused"]:
        print("  refused sample {}: {}".format(refusal["cache_id"], refusal["because"]),
              file=sys.stderr)
    if alignment["rule_disagreed"]:
        # Not fatal, and not silent either: the per-frame table below is what the renderer consults, so a
        # rule that fails on one sample means that sample is answered from its own proof.
        print("  the fitted rule is wrong for {} sample(s); those frames render from the matched table"
              .format(alignment["rule_disagreed"]), file=sys.stderr)
    rendered = {"kind": "source_crop_render", "schema": bs.SCHEMA, "scale": args.scale,
                "records": [], "refusals": []}
    if args.execution_set:
        groups = Path(args.execution_set)
        if not groups.is_dir():
            raise SystemExit("--execution-set needs the groups/ directory of the run whose answers are "
                             "paired; {} is not a directory".format(groups))
        candidates = [parse_target(text) for text in args.candidate]
        if not candidates:
            raise SystemExit("--execution-set needs at least one --candidate cache_id=<id>")
        built = bs.build_execution_set(candidates, alignment, cache, groups, Path(args.out),
                                       ffmpeg_path=args.ffmpeg_path, purpose=args.purpose)
        print(json.dumps({
            "written": built, "scale": 1, "inference_sent": False,
            "next_command": ("env PYTHONPATH=. .venv/bin/python scripts/run_pip_crop_probe.py "
                             "--annotations {a} --output-dir <RUN_DIR> --arms crops --scale 1 "
                             "--max-requests {n} --model qwen3.8-flash-next "
                             "--budget-file <RUN_DIR>/attempts.json --budget-cap {cap} "
                             "--budget-minutes 15 --purpose source-smoke --prepare").format(
                                 a=built["annotations"], n=max(1, len(built["planned"])),
                                 cap=max(1, len(built["planned"])))}, indent=2, sort_keys=True,
            default=str))
        return 0
    if not args.align_only:
        rendered = bs.render(targets, alignment, Path(args.out) / "crops", cache=cache,
                             ffmpeg_path=args.ffmpeg_path, scale=args.scale)
    written = bs.write_artifacts(args.out, alignment, rendered, purpose=args.purpose)
    plan = json.loads(Path(written["plan"]).read_text(encoding="utf-8"))
    print(json.dumps({"written": written, "requests_planned": plan["requests_planned"],
                      "images_planned": plan["images_planned"], "refusals": plan["refusals"],
                      "alignment_uncertainty_seconds": alignment["uncertainty_seconds"],
                      "cache_irregular": {k: v for k, v in irregular.items()
                                          if k in ("step_min", "step_max", "uneven_step_count",
                                                   "missing_count")},
                      "inference_sent": False}, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
