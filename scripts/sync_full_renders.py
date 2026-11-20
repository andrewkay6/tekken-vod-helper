"""Download large character renders from each tekken.com fighter page.

These are reference assets only (knees-up / full-body cut-outs, usually
2000-4300 px tall). The app and thumbnail exporter do NOT use them; the
primary portraits still come from scripts/sync_roster.py.

Each fighter page names its renders inconsistently, so the best image is
picked by filename pattern and size, with per-fighter overrides below.
A manifest.json records the source page, URL, filename and size of each.
"""

import argparse
import json
from pathlib import Path
import re
import sys
import urllib.request

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tekken_vod_helper.roster_import import DEFAULT_ROSTER_URL, fetch_roster_from_url  # noqa: E402

USER_AGENT = {"User-Agent": "Mozilla/5.0"}
ASSET_RE = re.compile(
    r'"fileName":"([^"]+)","mimeType":"[^"]+","url":"([^"]+)".*?"height":(\d+),"width":(\d+)'
)
GOOD = re.compile(
    r"wall-art-lrg|character-art-lrg|art-lrg|fighter-full|-full|dlc-\d+|fighter-800px|"
    r"not-cropped|Character@1x|Heihachi@2x|cropped|1500px|wall-art-med"
)
BAD = re.compile(
    r"権利|thumbnail|Thumbnail|maxres|Screenshot|mobile|select|Portrait|learn-more|@\.5x|-sm\.|bv-"
)
# Exact source filename to use when the pattern picks the wrong image.
OVERRIDES = {
    "Feng": "Feng-Wei-character-art.webp",
    # No clean cut-out on his page; this is a promo shot with a stage background.
    "Dragunov": "TK8_Dragunov_1.webp",
}
# King's wall art is embedded on every page, so only King may use it.
SHARED_ON_ALL_PAGES = "king-character-wall-art"


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=USER_AGENT), timeout=60) as response:
        return response.read()


def page_assets(slug: str):
    text = fetch("https://tekken.com/fighters/" + slug).decode("utf8", "ignore").replace("&q;", '"')
    assets = {}
    for file_name, url, height, width in ASSET_RE.findall(text):
        assets[url] = (file_name, int(width), int(height))
    return [(url, *value) for url, value in assets.items()]


def pick_render(name: str, assets):
    override = OVERRIDES.get(name)
    if override:
        matches = [asset for asset in assets if asset[1] == override]
        return matches[0] if matches else None
    candidates = [a for a in assets if GOOD.search(a[1]) and not BAD.search(a[1])]
    if name != "King":
        candidates = [a for a in candidates if SHARED_ON_ALL_PAGES not in a[1]]
    return max(candidates, key=lambda a: a[2] * a[3]) if candidates else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=DEFAULT_ROSTER_URL, help="Roster page URL")
    parser.add_argument("--out", default="portraits_full", help="Output folder")
    parser.add_argument("--only-missing", action="store_true", help="Skip fighters already in the manifest")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    for character in fetch_roster_from_url(args.url):
        if args.only_missing and character.name in manifest:
            continue
        page = "https://tekken.com/fighters/" + character.slug
        chosen = pick_render(character.name, page_assets(character.slug))
        if not chosen:
            print("{}: no large render found on {}".format(character.name, page))
            continue
        url, file_name, width, height = chosen
        target = out / (re.sub(r"[^A-Za-z0-9]+", "_", character.name).strip("_") + (Path(file_name).suffix or ".webp"))
        target.write_bytes(fetch(url))
        manifest[character.name] = {
            "file": target.name,
            "source": file_name,
            "url": url,
            "width": width,
            "height": height,
            "page": page,
        }
        print("{:12} {}x{} {}".format(character.name, width, height, file_name))

    manifest_path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n")
    print("Saved {} renders in {}".format(len(manifest), out.resolve()))


if __name__ == "__main__":
    main()
