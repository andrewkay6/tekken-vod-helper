"""Find candidate thumbnail backgrounds: Tekken stage art, decent resolution, no text, not used before.

Andy's manual routine was: Google "tekken stage", pick a high-resolution image with no text that hasn't been
used. Google Images refuses scripted search, so this uses Wallhaven's public API (SFW only, 16:9, >= 1920x1080).
For each candidate it:

  * asks the local Qwen (--qwen) whether the image has any text, logo or watermark, whether it's a stage rather than
    a character portrait, and whether it shows the SAME STAGE as any reference in docs/thumbnail-backgrounds-used.json
    (Andy's rule is "a stage we haven't used", not "a file we haven't used"), dropping any of those,
  * saves the survivors and a numbered contact sheet to build/work/thumbnail-candidates/<date>/.

    .venv/bin/python scripts/find_thumbnail_backgrounds.py --qwen            # search, filter, contact sheet
    .venv/bin/python scripts/find_thumbnail_backgrounds.py --pick 3 --event "Basement Brawl #11" \
        --dest build/work/bb11/owner-reviewed-20261120/bb11-thumbnail-background.jpg     # use #3, record it

--pick copies the full-resolution file to --dest and appends it to the used list, so it isn't offered again.
"""
import argparse
import base64
import hashlib
import io
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs" / "thumbnail-backgrounds-used.json"
OUT_ROOT = ROOT / "build" / "work" / "thumbnail-candidates"
UA = {"User-Agent": "tvh-thumbnail-finder/1.0"}
QUERIES = ["tekken stage", "tekken 8 stage", "tekken 7 stage", "tekken arena", "tekken background", "tekken 8 environment",
           "tekken scenery", "tekken landscape", "tekken 8"]


def get(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def load_registry():
    if REGISTRY.exists():
        return json.loads(REGISTRY.read_text(encoding="utf-8"))
    return {"note": "Thumbnail backgrounds already used, so the finder never offers them again.", "used": []}


def save_registry(reg):
    REGISTRY.write_text(json.dumps(reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def wallhaven(query, pages=3):
    out = []
    for page in range(1, pages + 1):
        q = urllib.parse.urlencode({"q": query, "categories": "111", "purity": "100", "ratios": "16x9",
                                    "atleast": "1920x1080", "sorting": "relevance", "page": page})
        data = json.loads(get("https://wallhaven.cc/api/v1/search?" + q))
        out += data.get("data", [])
        if page >= data.get("meta", {}).get("last_page", 1):
            break
        time.sleep(1.5)  # API allows 45 requests/minute
    return out


def _b64(img, size=(640, 360)):
    buf = io.BytesIO()
    im = img.convert("RGB")
    im.thumbnail(size)
    im.save(buf, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def qwen_judge(img, refs, endpoint, model):
    """One call: text/logo check, stage-vs-portrait check, and same-stage check against every used reference."""
    content = [{"type": "text", "text": "Reference images of Tekken stages we have ALREADY used, numbered:"}]
    for n, ref in enumerate(refs, 1):
        content += [{"type": "text", "text": "Reference #{}: {}".format(n, ref["stage"])},
                    {"type": "image_url", "image_url": {"url": _b64(Image.open(ROOT / ref["reference"]))}}]
    content += [{"type": "text", "text": "CANDIDATE image:"},
                {"type": "image_url", "image_url": {"url": _b64(img, (960, 540))}},
                {"type": "text", "text": (
                    "About the CANDIDATE only, answer on one line in exactly this form:\n"
                    "TEXT=yes|no STAGE=yes|no SAME=none|<reference number> NAME=<stage name if you recognise it>\n"
                    "TEXT: any visible text, letters, numbers, logos, game titles, watermarks or UI.\n"
                    "STAGE: it is an empty game stage/environment with NO fighters or characters visible "
                    "anywhere (a fight scene, character pose, portrait or poster is STAGE=no).\n"
                    "SAME: it shows the same stage/location as a reference (even from another angle, time of day or "
                    "game version); 'none' if it's a different stage.")}]
    # A reasoning model that spends the whole budget thinking returns no content: keep effort low, budget high.
    body = {"model": model, "max_tokens": 6000, "temperature": 0, "reasoning_effort": "low",
            "messages": [{"role": "user", "content": content}]}
    req = urllib.request.Request(endpoint.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        text = json.loads(r.read())["choices"][0]["message"].get("content") or ""
    line = [l for l in text.splitlines() if "TEXT=" in l.upper()]
    line = (line[-1] if line else text).strip()
    up = line.upper()
    has_text = "TEXT=YES" in up or "TEXT=NO" not in up
    is_stage = "STAGE=YES" in up
    same = None
    if "SAME=" in up:
        tok = up.split("SAME=", 1)[1].split()[0].strip("#,.;")
        same = None if tok in ("NONE", "NO", "") else tok
    name = line.split("NAME=", 1)[1].strip() if "NAME=" in line else ""
    return has_text, is_stage, same, name, line[-160:]


def contact_sheet(items, path):
    w, h, cols = 480, 270, 3
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * (h + 28)), (20, 20, 20))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 20)
    except OSError:
        font = ImageFont.load_default()
    for i, it in enumerate(items):
        x, y = (i % cols) * w, (i // cols) * (h + 28)
        sheet.paste(Image.open(it["preview"]).convert("RGB").resize((w, h)), (x, y))
        draw.text((x + 6, y + h + 3), "#{}  {}".format(it["n"], it["resolution"]), fill=(255, 255, 255), font=font)
    sheet.save(path, quality=88)


def search(args):
    reg = load_registry()
    refs = [u for u in reg["used"] if u.get("reference")]
    used_urls = {u.get("source") for u in reg["used"]}
    out = OUT_ROOT / time.strftime("%Y-%m-%d")
    (out / "preview").mkdir(parents=True, exist_ok=True)
    seen, items = set(), []
    for query in QUERIES:
        for w in wallhaven(query):
            if w["id"] in seen or w["path"] in used_urls:
                continue
            seen.add(w["id"])
            if len(items) >= args.limit or len(seen) > args.max_checked:
                break
            try:
                prev = Image.open(io.BytesIO(get(w["thumbs"]["large"])))
            except Exception as exc:  # noqa: BLE001 - one bad thumbnail must not stop the search
                print("skip {}: {}".format(w["id"], exc))
                continue
            item = {"id": w["id"], "source": w["path"], "page": w["url"], "resolution": w["resolution"],
                    "query": query}
            if args.qwen:
                has_text, is_stage, same, name, raw = qwen_judge(prev, refs, args.endpoint, args.model)
                item.update(qwen_text=has_text, qwen_stage=is_stage, qwen_same=same, stage_name=name, qwen_raw=raw)
                if has_text or not is_stage or same:
                    print("drop {}: {}".format(w["id"], raw))
                    continue
            p = out / "preview" / "{}.jpg".format(w["id"])
            prev.convert("RGB").save(p, quality=90)
            item["preview"] = str(p)
            items.append(item)
            print("keep {} {} ({})".format(w["id"], w["resolution"], query))
    for n, it in enumerate(items, 1):
        it["n"] = n
    (out / "candidates.json").write_text(json.dumps(items, indent=2), encoding="utf-8")
    if items:
        contact_sheet(items, out / "contact-sheet.jpg")
    print("{} candidates -> {}".format(len(items), out / "contact-sheet.jpg"))


def pick(args):
    out = OUT_ROOT / (args.date or time.strftime("%Y-%m-%d"))
    items = json.loads((out / "candidates.json").read_text(encoding="utf-8"))
    it = next(i for i in items if i["n"] == args.pick)
    data = get(it["source"], timeout=120)
    img = Image.open(io.BytesIO(data)).convert("RGB")
    dest = Path(args.dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, quality=95)
    reg = load_registry()
    ref = Path("docs/thumbnail-backgrounds") / "{}.jpg".format(
        "".join(c if c.isalnum() else "-" for c in args.event.lower()).strip("-"))
    small = img.copy()
    small.thumbnail((640, 360))
    small.save(ROOT / ref, quality=85)
    reg["used"].append({"event": args.event, "stage": args.stage or it.get("stage_name") or "", "reference": str(ref),
                        "source": it["source"], "page": it["page"], "resolution": it["resolution"],
                        "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(), "file": str(dest),
                        "picked": time.strftime("%Y-%m-%d")})
    save_registry(reg)
    print("saved {} ({}) and recorded it as used".format(dest, it["resolution"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=9, help="candidates to keep")
    ap.add_argument("--max-checked", type=int, default=80, help="stop after judging this many images")
    ap.add_argument("--qwen", action="store_true", help="drop images the local Qwen sees text/logos in")
    ap.add_argument("--endpoint", default="http://127.0.0.1:8000")
    ap.add_argument("--model", default="qwen3.8-flash-next")
    ap.add_argument("--pick", type=int, help="candidate number from the contact sheet")
    ap.add_argument("--event", default="")
    ap.add_argument("--dest")
    ap.add_argument("--stage", default="", help="short description of the stage (stored for future comparisons)")
    ap.add_argument("--date", help="candidate folder date (default today)")
    args = ap.parse_args()
    if args.pick:
        if not args.dest or not args.event:
            ap.error("--pick needs --dest and --event")
        pick(args)
    else:
        search(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
