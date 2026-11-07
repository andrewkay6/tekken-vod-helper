#!/usr/bin/env python3
"""Render the broadcast band for chosen cached frames and stack it into evaluator reading sheets.

The header route's evidence is a 1920x147 strip: event title, stage name, both nameplates, the games
tally, the round-pip row. That strip is too wide to read one at a time across an interval, and a label
for "which game was this" is a claim about a *sequence* of tallies, so this lays several strips down in
time order on one sheet, each stamped with its frame id and timestamp and each carrying the digest of
the exact rendered PNG it shows.

It renders with ``boundary_header.render_band``, the same function the command that sends the strip uses,
so a sheet cannot label a crop the runtime would not have produced.

Reads pixels only: nothing here sends a request, and the sheets are evaluator aids. The file the runtime
reads stays ``annotations-window.json``; a label belongs in the separate frozen label file.

    env PYTHONPATH=. .venv/bin/python scripts/build_band_sheet.py \\
        --trial build/work/bb9/set-end2end-v1 --per-sheet 8
    env PYTHONPATH=. .venv/bin/python scripts/build_band_sheet.py \\
        --frames 3245,3424,3604 --out build/work/bb9/set-end2end-v1/band-sheet
"""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "scripts")):
    import sys
    if entry not in sys.path:
        sys.path.insert(0, entry)

from tekken_vod_helper import boundary_header as bh  # noqa: E402

DEFAULT_FRAME_DIR = "build/work/bb9/detect-1440p-v1/frames"


def frame_time(manifest, frame_id):
    # type: (dict, int) -> float
    """The cached frame's own timestamp, from the manifest that indexes it."""
    for entry in manifest["frames"]:
        if int(entry.get("id", -1)) == frame_id:
            return float(entry["timestamp"])
    raise SystemExit("frame {} is not in {}".format(frame_id, manifest.get("_path")))
    return None


def render(ids, manifest, frame_dir, out_dir, scale):
    # type: (list, dict, Path, Path, int) -> list
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered = []
    for frame_id in ids:
        path = frame_dir / "frame-{}.jpg".format(frame_id)
        if not path.is_file():
            raise SystemExit("cached frame {} is not on disk".format(path))
        stamp = frame_time(manifest, frame_id)
        band = bh.render_band(str(path), out_dir, timestamp=stamp, scale=scale)
        band_path = Path(band["path"] if isinstance(band, dict) else band)
        rendered.append({"frame_id": frame_id, "frame": str(path), "timestamp": stamp,
                         "band": str(band_path),
                         "band_sha256": hashlib.sha256(band_path.read_bytes()).hexdigest()[:16],
                         "band_size": list(Image.open(band_path).size)})
    return rendered


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trial", default="build/work/bb9/set-end2end-v1",
                        help="Trial dir holding selected-frames.json; supplies the window anchors.")
    parser.add_argument("--frames", default=None,
                        help="Comma list of cached frame ids to render instead of the anchors.")
    parser.add_argument("--frame-dir", default=DEFAULT_FRAME_DIR)
    parser.add_argument("--manifest",
                        default=str(ROOT / DEFAULT_FRAME_DIR / "sample-manifest.json"))
    parser.add_argument("--out", default=None, help="Default: <trial>/band-sheet.")
    parser.add_argument("--scale", type=int, default=3, help="Band upscale; 3 matches the header route.")
    parser.add_argument("--per-sheet", type=int, default=8, help="Strips stacked per sheet.")
    parser.add_argument("--width", type=int, default=1200, help="Sheet width the strips are fitted to.")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["_path"] = str(manifest_path)

    if args.frames:
        ids = [int(x) for x in args.frames.split(",") if x.strip()]
        why = "frame ids named on the command line"
    else:
        trial = Path(args.trial)
        selected = json.loads((trial / "selected-frames.json").read_text(encoding="utf-8"))
        # the window's own round-end evidence frame, so the strip and the crops label the same instant
        ids, seen = [], set()
        for entry in selected["requested"]:
            if entry.get("role") != "round_end_evidence_frame":
                continue
            if entry["id"] in seen:
                continue
            seen.add(entry["id"])
            ids.append(entry["id"])
        why = "one cached frame per decision window of {}".format(trial)

    out_dir = Path(args.out or str(Path(args.trial) / "band-sheet"))
    rendered = render(ids, manifest, Path(args.frame_dir), out_dir / "bands", args.scale)

    sheets = []
    per = max(1, args.per_sheet)
    for start in range(0, len(rendered), per):
        chunk = rendered[start:start + per]
        pieces = []
        for entry in chunk:
            image = Image.open(entry["band"])
            height = max(1, int(args.width * image.height / image.width))
            pieces.append((entry, image.resize((args.width, height), Image.LANCZOS)))
        pad, label = 10, 22
        sheet = Image.new("RGB", (args.width + 2 * pad,
                                  sum(p.height for _, p in pieces) + (label + pad) * len(pieces) + pad),
                          "black")
        draw = ImageDraw.Draw(sheet)
        y = pad
        for entry, piece in pieces:
            draw.text((pad + 2, y + 4), "frame-{}  {:.6f}s  {}".format(
                entry["frame_id"], entry["timestamp"], entry["band_sha256"]), fill="yellow")
            sheet.paste(piece, (pad, y + label))
            y += piece.height + label + pad
        sheet_path = out_dir / "bands-{}.png".format(len(sheets))
        sheet.save(sheet_path, format="PNG")
        sheets.append({"path": str(sheet_path), "frames": [e["frame_id"] for e in chunk],
                       "sha256": hashlib.sha256(sheet_path.read_bytes()).hexdigest()[:16]})

    manifest_out = {"kind": "evaluator-band-sheet", "rendered_by": "boundary_header.render_band",
                    "scale": args.scale, "selected_by": why, "frames": len(rendered),
                    "sheets": sheets, "bands": rendered,
                    "note": "an evaluator reading aid: the band tells who the broadcast credited and "
                            "what tally it displayed, never that a round or game ended"}
    (out_dir / "band-sheet.json").write_text(json.dumps(manifest_out, indent=2, sort_keys=True) + "\n",
                                             encoding="utf-8")
    print(json.dumps({k: manifest_out[k] for k in ("frames", "selected_by", "sheets")},
                     indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
