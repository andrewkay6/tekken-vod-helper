#!/usr/bin/env python3
"""Render the evaluator's label sheet for a prepared trial. Reads pixels, never sends them.

The probe asks the model about 4x LEFT/RIGHT crops. A label has to be a claim about *those* pixels, so
this script takes the very PNGs the plan would send and lays them out one window at a time, upscaled a
further step for the human (or reviewing model) reading it. Upscaling adds no information the model got
to keep -- it only makes the same 88 lit-or-dark circles legible at a glance -- and the sheet records the
digest of the exact files it was built from, so a label can be traced back to the image that produced it
rather than to a screenshot of something similar.

Sheets are grouped by decision window, because that is the unit a label is a claim about: the anchor
frame, then +2 s, then +4 s, in the order the row is expected to change.

Sends nothing. Writes nothing the runtime reads: the probe's inputs are ``annotations-window.json`` and
the plan, and the labels this supports live in a separate evaluator-only file.

    env PYTHONPATH=. .venv/bin/python scripts/build_label_sheet.py \\
        build/work/bb9/consecutive-games-v3
    env PYTHONPATH=. .venv/bin/python scripts/build_label_sheet.py \\
        build/work/bb9/consecutive-games-v2 --magnification 3
"""
import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
CROP_DIR = "run/crops"


def crop_digest(paths):
    # type: (list) -> str
    """One digest over the exact crop files a trial would send, in the order the plan lists them."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(Path(path).name.encode("utf-8"))
        digest.update(hashlib.sha256(Path(path).read_bytes()).hexdigest().encode("ascii"))
    return digest.hexdigest()[:16]


def sheet_for_window(window, entries, out_path, magnification):
    # type: (int, list, Path, int) -> dict
    """Stack this window's LEFT and RIGHT crops, each labelled with its frame, time and role."""
    pad, label_height = 12, 26
    rows = []
    for entry in entries:
        role = entry.get("selection_role") or entry.get("role") or ""
        for side in ("LEFT", "RIGHT"):
            source = Path(entry["crops"][side])
            image = Image.open(source)
            rows.append({"side": side, "entry": entry, "source": source, "role": role,
                         "image": image.resize((image.width * magnification,
                                                image.height * magnification), Image.LANCZOS)})
    width = max(r["image"].width for r in rows) + 2 * pad
    height = sum(r["image"].height + label_height for r in rows) + pad
    sheet = Image.new("RGB", (width, height), "black")
    draw = ImageDraw.Draw(sheet)
    y = pad // 2
    for row in rows:
        stamp = "{:.3f}s".format(row["entry"]["timestamp"])
        draw.text((pad, y + 6), "{}  {}  {}  {}".format(Path(row["entry"]["frame"]).stem, stamp,
                                                         row["side"], row["role"]),
                  fill="white")
        sheet.paste(row["image"], (pad, y + label_height))
        y += row["image"].height + label_height
    sheet.save(out_path, format="PNG")
    return {"window": window, "path": str(out_path), "frames": [e["frame"] for e in entries],
            "images": [str(r["source"]) for r in rows],
            "sha256": hashlib.sha256(out_path.read_bytes()).hexdigest()[:16]}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("trial", help="Trial directory holding annotations-window.json and run/crops.")
    parser.add_argument("--magnification", type=int, default=2,
                        help="Extra upscale on top of the probe's own 4x. Default 2 (so 8x total).")
    parser.add_argument("--out", default="label-sheet")
    args = parser.parse_args()

    trial = Path(args.trial)
    frames = json.loads((trial / "annotations-window.json").read_text(encoding="utf-8"))["frames"]
    crops = trial / CROP_DIR
    by_window = defaultdict(list)
    images = []
    for entry in frames:
        stem = Path(entry["frame"]).stem
        left, right = crops / "{}-LEFT.png".format(stem), crops / "{}-RIGHT.png".format(stem)
        if not (left.is_file() and right.is_file()):
            raise SystemExit("crop missing for {}; run the probe's --prepare first".format(entry["frame"]))
        entry["crops"] = {"LEFT": str(left), "RIGHT": str(right)}
        images += [str(left), str(right)]
        by_window[entry.get("window")].append(entry)

    out = trial / args.out
    out.mkdir(parents=True, exist_ok=True)
    sheets = [sheet_for_window(window, by_window[window], out / "window-{}.png".format(window),
                               max(1, args.magnification))
              for window in sorted(by_window, key=lambda w: (w is None, w))]
    manifest = {"kind": "evaluator-label-sheet", "trial": str(trial),
                "built_from": {"annotations": str(trial / "annotations-window.json"),
                               "crop_dir": str(crops)},
                "magnification_beyond_the_probe": max(1, args.magnification),
                "frames": len(frames), "windows": len(sheets), "sheets": sheets,
                "crop_files_sha256_of_all": crop_digest(sorted(images)),
                "note": "the sheet is the evaluator's reading aid, not an input: the probe never sees "
                        "it, and the expected counts it supports belong in labels-eval-frozen.json"}
    (trial / "label-sheet.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                                            encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("trial", "frames", "windows",
                                               "crop_files_sha256_of_all")}, indent=2, sort_keys=True))
    print(json.dumps([s["path"] for s in sheets], indent=2))


if __name__ == "__main__":
    main()
