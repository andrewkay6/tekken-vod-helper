"""Upload a full event VOD to YouTube, resumably, then set its thumbnail and playlist.

    .venv/bin/python scripts/upload_full_vod.py \
        --file "/var/lib/ai-sandbox-media/<event>/<event> [1440p].mp4" \
        --title "Basement Brawl #11 (Café Edition) - Full Stream" \
        --privacy unlisted [--description-file d.txt] [--thumbnail t.jpg] [--playlist auto|<id>|none]

Progress and the session URI go to build/work/uploads/<file stem>.state.json, so rerunning the
same command after a crash or reboot resumes instead of starting over. Times in the log are machine
timestamps; convert them to 12-hour time before showing them to Andy.
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tekken_vod_helper import youtube_client as yc  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, type=Path)
    ap.add_argument("--title", required=True)
    ap.add_argument("--description-file", type=Path)
    ap.add_argument("--privacy", choices=["private", "unlisted", "public"], default="unlisted")
    ap.add_argument("--thumbnail", type=Path)
    ap.add_argument("--playlist", default="auto", help="auto (match by event name), a playlist id, or none")
    ap.add_argument("--event", default="", help="event name used to match the playlist when --playlist auto")
    args = ap.parse_args()

    title = yc.fit_video_title(args.title, args.event)
    description = args.description_file.read_text(encoding="utf-8") if args.description_file else ""
    state = ROOT / "build" / "work" / "uploads" / (args.file.stem + ".state.json")
    state.parent.mkdir(parents=True, exist_ok=True)

    started = time.time()
    last = {"t": 0.0, "first": None}

    def progress(sent, total):
        if last["first"] is None:  # a resumed run starts mid-file; rate only counts this run's bytes
            last["first"] = sent
        if time.time() - last["t"] >= 60 or sent == total:
            last["t"] = time.time()
            rate = (sent - last["first"]) / max(time.time() - started, 1) / 1e6
            print("[{}] {:.1f} / {:.1f} GB ({:.0f}%) ~{:.0f} MB/s".format(
                time.strftime("%Y-%m-%d %H:%M:%S"), sent / 1e9, total / 1e9, 100 * sent / total, rate), flush=True)

    video = yc.upload_video(str(args.file), title, description, privacy_status=args.privacy,
                            state_path=str(state), progress=progress)
    vid = video["id"]
    print("uploaded: https://youtu.be/{} ({})".format(vid, args.privacy), flush=True)

    if args.thumbnail:
        yc.set_video_thumbnail(vid, str(args.thumbnail))
        print("thumbnail set", flush=True)

    if args.playlist != "none":
        playlist = args.playlist
        if playlist == "auto":
            playlist = yc.suggest_playlist(args.event or args.title, yc.list_playlists())
            if not playlist:
                print("no confident playlist match; not added (pass --playlist <id>)", flush=True)
        if playlist:
            yc.add_video_to_playlist(vid, playlist)
            print("added to playlist", playlist, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
