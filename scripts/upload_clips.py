"""Upload an event's exported match clips to YouTube, set their thumbnails, and add them to the event playlist.

Reads the export's `_youtube_uploads/manifest.json`. Clips go up in REVERSE match order (Grand Final first) so that
appending to the playlist gives the precedent order: full VOD, Grand Final, ..., Winners Round 1 (BB10).
Each clip's resumable upload state lives in <export>/upload/jobs/<upload_id>/, so a rerun resumes a partial
upload and skips finished steps (upload, thumbnail, playlist) instead of duplicating them.

Run it only after scripts/recut_smartcut.py has made the clips frame-exact.

    .venv/bin/python scripts/upload_clips.py --export build/work/bb11/export-owner-reviewed-20261120 \
        --playlist PLaJauTVXmu8c --privacy public
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tekken_vod_helper import youtube_client as yc  # noqa: E402


def log(msg):
    print(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", required=True, type=Path)
    ap.add_argument("--playlist", required=True, help="playlist id to append clips to")
    ap.add_argument("--privacy", choices=["private", "unlisted", "public"], required=True)
    ap.add_argument("--only", type=int, nargs="*", help="match numbers to do (default: all)")
    args = ap.parse_args()

    entries = json.loads((args.export / "output" / "_youtube_uploads" / "manifest.json").read_text())["entries"]
    metas = [(e, json.loads(Path(e["metadata_path"]).read_text(encoding="utf-8"))) for e in entries]
    metas.sort(key=lambda em: -em[1]["match_index"])
    failed = []
    for e, meta in metas:
        n = meta["match_index"]
        if args.only and n not in args.only:
            continue
        job = args.export / "upload" / "jobs" / e["upload_id"]
        job.mkdir(parents=True, exist_ok=True)
        done_path = job / "done.json"
        done = json.loads(done_path.read_text()) if done_path.exists() else {}
        try:
            if not done.get("video_id"):
                smart = Path(e["video_path"]).with_suffix(".smartcut.json")
                if not smart.exists() and not list(Path(e["video_path"]).parent.glob(Path(e["video_path"]).stem + "*.smartcut.json")):
                    raise RuntimeError("no smartcut receipt next to the clip; run recut_smartcut.py first")
                log("#{} uploading: {}".format(n, meta["title"]))
                last = {"t": 0}

                def progress(sent, total, n=n):
                    if time.time() - last["t"] > 60 or sent == total:
                        last["t"] = time.time()
                        log("#{}   {:.2f} / {:.2f} GB".format(n, sent / 1e9, total / 1e9))

                video = yc.upload_video(e["video_path"], meta["title"], meta["description"], tags=meta.get("tags"),
                                        privacy_status=args.privacy, state_path=str(job / "upload-state.json"),
                                        progress=progress)
                done["video_id"] = video["id"]
                done_path.write_text(json.dumps(done, indent=2))
                log("#{} uploaded https://youtu.be/{}".format(n, video["id"]))
            if not done.get("thumbnail"):
                yc.set_video_thumbnail(done["video_id"], e["thumbnail_path"])
                done["thumbnail"] = True
                done_path.write_text(json.dumps(done, indent=2))
                log("#{} thumbnail set".format(n))
            if not done.get("playlist"):
                for attempt in range(5):
                    try:
                        yc.add_video_to_playlist(done["video_id"], args.playlist)
                        break
                    except yc.YouTubeApiError:
                        if attempt == 4:
                            raise
                        time.sleep(10)
                done["playlist"] = args.playlist
                done_path.write_text(json.dumps(done, indent=2))
                log("#{} added to playlist".format(n))
        except Exception as exc:  # noqa: BLE001 - keep going; report at the end, rerun resumes
            failed.append(n)
            log("#{} FAILED: {}".format(n, str(exc)[:300]))
    log("DONE: {} failed {}".format("none" if not failed else len(failed), failed or ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
