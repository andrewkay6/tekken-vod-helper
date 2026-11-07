"""Audit an evaluator key against the frame manifest, and rebuild the strips from the frames it names.

Why this exists: ``build/work/bb9/boundary-review-2026-11-06/blinded-edge-key.json`` spelled each tile's
filename from a rounded second (``frame-%04d.jpg % (int(t) + 1)``). That is only true when the cache step is
exactly 1.000 s. BB9's cache is the source's 1001/1000 interval, so the claimed frame sat progressively
later than the instant it was said to show -- 5.005 s at 5495.490 and 6.006 s at 6486.480 -- and the clock
values read off those tiles were credited to the wrong instants. Every derived count in the plans
("five late starts", "2-7 s inside a live round", "two contradictory readings of the same instant") came
out of that mapping, and the correct answer was obtainable offline for the cost of a manifest lookup.

The rule this script enforces is the one the resolver in ``boundary_source`` encodes: an instant is
answered by looking it up in the manifest, which is the timestamp authority, and the file it names is
digested so a swapped or missing JPEG cannot pass unnoticed. Nothing here sends a request.

    python3 scripts/audit_frame_identities.py \\
        --manifest build/work/bb9/detect-1440p-v1/frames/sample-manifest.json \\
        --key build/work/bb9/boundary-review-2026-11-06/blinded-edge-key.json \\
        --out build/work/bb9/review-ui-followup-2026-11-06 --sheet
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / ".qwen" / "tmp" / "pylibs")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from tekken_vod_helper import boundary_header as bh      # noqa: E402
from tekken_vod_helper import boundary_source as bs      # noqa: E402


def audit(cache, tiles):
    # type: (dict, list) -> list
    """One row per tile: what was claimed, what the manifest actually says, and the signed distance."""
    rows = []
    for tile in tiles:
        at = float(tile["t"])
        resolved = bs.resolve_frame(cache, at, disclose=True)
        claimed = tile.get("frame") or ""
        claimed_id = int("".join(ch for ch in Path(claimed).stem if ch.isdigit()) or 0)
        entry = cache.get("by_id", {}).get(claimed_id)
        row = {"kind": tile.get("kind"), "label": tile.get("label"), "claimed_at": at,
               "claimed_frame": claimed,
               "claimed_frame_manifest_timestamp": float(entry["timestamp"]) if entry else None,
               "claimed_frame_offset_seconds": (round(float(entry["timestamp"]) - at, 6)
                                                if entry else None),
               "correct_frame": resolved["frame"], "correct_frame_id": resolved["id"],
               "correct_frame_actual_at": resolved["actual_at"],
               "correct_frame_offset_seconds": resolved["offset_seconds"],
               "within_tolerance": resolved["within_tolerance"],
               "ambiguous_tie": resolved.get("ambiguous_tie"),
               "sparse_region": resolved.get("sparse_region"),
               "correct_frame_sha256": resolved.get("sha256"),
               "correct_frame_path": resolved.get("path"),
               "refused_because": resolved.get("refused_because"),
               "mapping_was_wrong": bool(claimed and resolved.get("frame")
                                         and Path(claimed).name != resolved["frame"]),
               "inference_sent": False}
        rows.append(row)
    return rows


def sheet(rows, out_dir, scale=3, limit=None):
    """A contact sheet of the frames the manifest actually assigns to these instants."""
    from PIL import Image, ImageDraw

    wanted = [row for row in rows if row["within_tolerance"]]
    if limit:
        wanted = wanted[:limit]
    bands = []
    for row in wanted:
        band = bh.render_band(row["correct_frame_path"], out_dir / "bands",
                              timestamp=row["claimed_at"], scale=scale)
        bands.append((row, band))
    if not bands:
        return None
    width = max(int(band["size"][0]) for _row, band in bands)
    height = sum(int(band["size"][1]) + 22 for _row, band in bands)
    from PIL import Image as Base
    canvas = Base.new("RGB", (width, height), (16, 16, 20))
    draw = ImageDraw.Draw(canvas)
    y = 0
    for row, band in bands:
        image = Image.open(band["path"])
        canvas.paste(image, (0, y))
        draw.text((6, y + 4), "%s  %s  t=%.3f s  frame=%s  actual=%.6f  offset=%+.6f" % (
            row["label"], row["kind"], row["claimed_at"], row["correct_frame"],
            row["correct_frame_actual_at"], row["correct_frame_offset_seconds"]),
            fill=(255, 232, 140))
        y += int(band["size"][1]) + 22
    target = out_dir / "manifest-aligned-tiles.png"
    canvas.save(target, format="PNG")
    return {"path": str(target), "tiles": len(bands), "size": [canvas.width, canvas.height],
            "sha256": bh._sha(target), "bands": [band["path"] for _row, band in bands],
            "inference_sent": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--key", required=True, help="evaluator key JSON with a 'tiles' list")
    parser.add_argument("--out", required=True)
    parser.add_argument("--sheet", action="store_true", help="also rebuild the aligned contact sheet")
    parser.add_argument("--scale", type=int, default=3)
    args = parser.parse_args()

    cache = bs.load_cache_index(args.manifest)
    key_path = Path(args.key)
    payload = json.loads(key_path.read_text(encoding="utf-8"))
    tiles = payload.get("tiles") or payload
    rows = audit(cache, tiles)
    wrong = [row for row in rows if row["mapping_was_wrong"]]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_kind = {}
    for row in rows:
        bucket = by_kind.setdefault(row["kind"], {"tiles": 0, "wrong": 0, "worst_offset": 0.0})
        bucket["tiles"] += 1
        bucket["wrong"] += int(row["mapping_was_wrong"])
        offset = abs(row["claimed_frame_offset_seconds"] or 0.0)
        bucket["worst_offset"] = round(max(bucket["worst_offset"], offset), 6)
    report = {"inference_sent": False,
              "manifest": cache["path"], "key": str(key_path),
              "manifest_source": cache.get("source"), "frames_in_cache": cache["count"],
              "modal_step_seconds": cache["irregular"]["step_median"],
              "tiles": len(rows), "tiles_mapped_to_the_wrong_frame": len(wrong),
              "worst_claimed_offset_seconds": max((abs(r["claimed_frame_offset_seconds"] or 0.0)
                                                   for r in rows), default=0.0),
              "by_kind": by_kind,
              "unreadable": [row["label"] for row in rows if not row["within_tolerance"]],
              "labels_are_runtime_input": False,
              "rows": rows}
    target = out_dir / "frame-identity-audit.json"
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.sheet:
        made = sheet(rows, out_dir, scale=args.scale)
        if made:
            report["sheet"] = made
            target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("audited %d tiles: %d named the wrong frame, worst %.3f s (report: %s)"
          % (report["tiles"], report["tiles_mapped_to_the_wrong_frame"],
             report["worst_claimed_offset_seconds"], target))
    for kind, bucket in sorted(by_kind.items()):
        print("  %-22s %d tiles, %d wrong, worst %+.3f s"
              % (kind, bucket["tiles"], bucket["wrong"], bucket["worst_offset"]))
    if report["unreadable"]:
        print("  outside the cache's reach: %s" % ", ".join(report["unreadable"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
