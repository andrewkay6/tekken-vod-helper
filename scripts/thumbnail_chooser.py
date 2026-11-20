"""Web page to choose an event's thumbnail background from the finder's candidates.

Each candidate is shown as a finished match thumbnail (a real match from the event, with the overlay and
portraits) next to the raw background. Picking one and pressing Submit downloads the full-resolution image to
--dest and records the stage in docs/thumbnail-backgrounds-used.json, so it is never offered again.

Binds 127.0.0.1:8787 by default: the same local port the review page uses, which Tailscale Serve already
publishes at https://<review-host>:8445/ (stop the review server first; one page at a time).

    .venv/bin/python scripts/thumbnail_chooser.py \
        --candidates build/work/thumbnail-candidates/2026-11-20 \
        --project "build/work/bb11/owner-reviewed-20261120/<event>.owner-reviewed.tvh.json" \
        --event "Basement Brawl #11" --dest build/work/bb11/owner-reviewed-20261120/bb11-thumbnail-background.jpg

After a submit the page shows the choice; regenerate the clip thumbnails with the export's metadata pass.
"""
import argparse
import html
import io
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from PIL import Image  # noqa: E402

import find_thumbnail_backgrounds as finder  # noqa: E402
from tekken_vod_helper.models import ProjectState  # noqa: E402
from tekken_vod_helper.thumbnails import make_thumbnail  # noqa: E402


def prepare(args):
    cand_dir = Path(args.candidates)
    items = json.loads((cand_dir / "candidates.json").read_text(encoding="utf-8"))
    if args.exclude:
        items = [i for i in items if i["n"] not in args.exclude]
    full_dir, sample_dir = cand_dir / "full", cand_dir / "sample"
    full_dir.mkdir(exist_ok=True)
    sample_dir.mkdir(exist_ok=True)
    state = ProjectState.from_dict(json.loads(Path(args.project).read_text(encoding="utf-8")))
    matches = state.sorted_matches()
    match = matches[args.sample_match - 1] if args.sample_match else matches[-1]
    for it in items:
        full = full_dir / "{}.jpg".format(it["id"])
        if not full.exists():
            Image.open(io.BytesIO(finder.get(it["source"], timeout=120))).convert("RGB").save(full, quality=92)
        sample = sample_dir / "{}.jpg".format(it["id"])
        make_thumbnail("", match, match.start, match.end, str(sample), args.portraits, "",
                       event_name=state.event_name, background_path=str(full))
        it["full"], it["sample"] = str(full), str(sample)
        print("rendered #{} {}".format(it["n"], it["id"]), flush=True)
    return items, match, state


PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Choose thumbnail background</title><style>
body{{font-family:system-ui,sans-serif;background:#111;color:#eee;margin:0;padding:16px}}
h1{{font-size:20px;margin:0 0 4px}} p{{color:#aaa;margin:4px 0 16px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:16px}}
label{{display:block;background:#1c1c1c;border:3px solid #333;border-radius:10px;padding:8px;cursor:pointer}}
label:has(input:checked){{border-color:#e33}} img{{width:100%;border-radius:6px;display:block}}
.raw{{width:32%;display:inline-block;margin-top:6px;opacity:.85}} .meta{{font-size:13px;color:#bbb;margin-top:6px}}
input[type=radio]{{transform:scale(1.4);margin-right:8px}}
button{{position:sticky;bottom:12px;margin-top:16px;font-size:18px;padding:12px 28px;background:#e33;color:#fff;border:0;border-radius:8px}}
.done{{background:#173;padding:16px;border-radius:10px;font-size:18px}}</style></head><body>
<h1>{title}</h1><p>Sample uses {sample}. Tap one, then Submit. You can also tap a sample to open it full size.</p>
{body}</body></html>"""


def render_page(items, match, state, chosen):
    title = "Thumbnail background — " + html.escape(state.event_name)
    sample = html.escape("{} vs {} ({})".format(match.player1, match.player2, match.round_name))
    if chosen:
        body = ('<div class="done">Saved #{n} as the background ({res}). It\'s recorded as used, so it won\'t be '
                'offered again. You can close this page.</div><img style="max-width:960px;margin-top:16px" '
                'src="/img/sample/{id}">').format(n=chosen["n"], res=html.escape(chosen["resolution"]), id=chosen["id"])
        return PAGE.format(title=title, sample=sample, body=body)
    cards = []
    for it in items:
        cards.append(
            '<label><div><input type="radio" name="n" value="{n}" required><b>#{n}</b> {name}</div>'
            '<a href="/img/sample/{id}" target="_blank"><img src="/img/sample/{id}"></a>'
            '<img class="raw" src="/img/full/{id}"><div class="meta">{res} · <a style="color:#8af" href="{page}" '
            'target="_blank">source</a></div></label>'.format(
                n=it["n"], id=it["id"], res=html.escape(it["resolution"]), page=html.escape(it["page"]),
                name=html.escape(it.get("stage_name") or "")))
    body = '<form method="post" action="/choose"><div class="grid">{}</div><button>Submit</button></form>'.format(
        "".join(cards))
    return PAGE.format(title=title, sample=sample, body=body)


def serve(args, items, match, state):
    lock = threading.Lock()
    chosen = {}
    by_id = {it["id"]: it for it in items}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="text/html; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            for prefix in ("/tvh-review", ""):
                if path.startswith(prefix + "/img/"):
                    kind, _, ident = path[len(prefix) + 5:].partition("/")
                    it = by_id.get(ident)
                    if it and kind in ("sample", "full"):
                        return self._send(200, Path(it[kind]).read_bytes(), "image/jpeg")
                    return self._send(404, b"not found", "text/plain")
            return self._send(200, render_page(items, match, state, chosen.get("item")).encode())

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            form = parse_qs(self.rfile.read(min(length, 4096)).decode())
            try:
                n = int(form.get("n", [""])[0])
                it = next(i for i in items if i["n"] == n)
            except (ValueError, StopIteration):
                return self._send(400, b"pick one of the candidates", "text/plain")
            with lock:
                if not chosen:
                    record_choice(args, it)
                    chosen["item"] = it
                    print("CHOSEN #{} {} {}".format(it["n"], it["id"], it["source"]), flush=True)
            self.send_response(303)
            self.send_header("Location", "./")
            self.end_headers()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print("serving http://{}:{}/  ({} candidates)".format(args.host, args.port, len(items)), flush=True)
    server.serve_forever()


def record_choice(args, it):
    dest = Path(args.dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img = Image.open(it["full"]).convert("RGB")
    img.save(dest, quality=95)
    reg = finder.load_registry()
    ref = Path("docs/thumbnail-backgrounds") / "{}.jpg".format(
        "".join(c if c.isalnum() else "-" for c in args.event.lower()).strip("-"))
    small = img.copy()
    small.thumbnail((640, 360))
    small.save(ROOT / ref, quality=85)
    import hashlib
    import time
    reg["used"].append({"event": args.event, "stage": it.get("stage_name") or "", "reference": str(ref),
                        "source": it["source"], "page": it["page"], "resolution": it["resolution"],
                        "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(), "file": str(dest),
                        "picked": time.strftime("%Y-%m-%d"), "picked_via": "thumbnail_chooser web page"})
    finder.save_registry(reg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--project", required=True, help="owner-reviewed .tvh.json (its matches are used for samples)")
    ap.add_argument("--event", required=True, help="event label for the used-backgrounds list")
    ap.add_argument("--dest", required=True, help="where the chosen full-resolution background is saved")
    ap.add_argument("--sample-match", type=int, default=0, help="match number for the sample (default: last)")
    ap.add_argument("--exclude", type=int, nargs="*", default=[], help="candidate numbers to leave out")
    ap.add_argument("--portraits", default=str(ROOT / "portraits"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()
    items, match, state = prepare(args)
    serve(args, items, match, state)


if __name__ == "__main__":
    sys.exit(main())
