import argparse
import csv
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRATCH_ROOT = Path(r"C:\tmp\tekken-vod-helper-backtests")
DEFAULT_YT_DLP = Path(r"C:\tmp\tekken-vod-helper-tools\yt-dlp.exe")
DEFAULT_COOKIE_SOURCE = Path(r"C:\Users\Andrew-Work\Downloads\a.txt")


@dataclass
class VodJob:
    key: str
    url: str
    title: str = ""
    project: str = ""


def main() -> int:
    args = _parse_args()
    scratch_root = Path(args.scratch_root)
    video_dir = scratch_root / "videos"
    log_dir = scratch_root / "logs"
    video_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    if args.status:
        return _print_status(scratch_root)
    if args.background:
        return _start_background(args, scratch_root)

    yt_dlp = Path(args.yt_dlp)
    if not yt_dlp.exists():
        raise SystemExit("yt-dlp was not found at {}".format(yt_dlp))

    cookies_path = _prepare_cookies(Path(args.cookies_source), scratch_root / "cookies.netscape.txt")
    jobs = _load_jobs(Path(args.manifest), args.channel_url, yt_dlp, cookies_path, args.channel_limit)
    if args.limit:
        jobs = jobs[: args.limit]
    if not jobs:
        raise SystemExit("No VOD jobs found.")

    summary: List[Dict[str, object]] = []
    print("Prepared {} VOD job{}.".format(len(jobs), "" if len(jobs) == 1 else "s"), flush=True)
    for index, job in enumerate(jobs, start=1):
        print("[{}/{}] {}".format(index, len(jobs), job.key), flush=True)
        job_output = REPO_ROOT / "backtests" / "reports" / ("batch-" + job.key)
        job_output.mkdir(parents=True, exist_ok=True)
        video_path = _download_vod(job, yt_dlp, cookies_path, video_dir, log_dir, args.format, args.player_client)
        try:
            report = _run_backtest(job, video_path, job_output, args)
            summary.append(report)
        finally:
            if not args.keep_videos:
                _unlink_quietly(video_path)

    summary_path = REPO_ROOT / "backtests" / "reports" / "batch-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("Wrote {}".format(summary_path), flush=True)
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download VODs to scratch and run boundary backtests.")
    parser.add_argument("--manifest", default=str(REPO_ROOT / "backtests" / "vod_manifest.json"))
    parser.add_argument("--channel-url", default="https://www.youtube.com/@KWTekken/videos")
    parser.add_argument("--channel-limit", type=int, default=40)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--scratch-root", default=str(SCRATCH_ROOT))
    parser.add_argument("--yt-dlp", default=str(DEFAULT_YT_DLP))
    parser.add_argument("--cookies-source", default=str(DEFAULT_COOKIE_SOURCE))
    parser.add_argument("--format", default="18/best[height<=480][ext=mp4]/best[height<=480]/best")
    parser.add_argument("--player-client", default="android")
    parser.add_argument("--start-reference", default=str(REPO_ROOT / "backtests" / "reference" / "start"))
    parser.add_argument("--end-reference", default=str(REPO_ROOT / "backtests" / "reference" / "end"))
    parser.add_argument("--progress-every", type=int, default=150)
    parser.add_argument("--timeline-step", type=float, default=5.0)
    parser.add_argument("--keep-videos", action="store_true", help="Keep downloaded scratch videos after each report.")
    parser.add_argument("--no-save-candidate-frames", action="store_true", help="Do not save project-mode candidate frames.")
    parser.add_argument("--timeline-save-frames", action="store_true", help="Save timeline samples into dataset/predicted folders.")
    parser.add_argument("--background", action="store_true", help="Start the batch in the background and write logs to scratch.")
    parser.add_argument("--status", action="store_true", help="Print status for the current background batch.")
    return parser.parse_args()


def _start_background(args: argparse.Namespace, scratch_root: Path) -> int:
    log_dir = scratch_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    pid_path = scratch_root / "batch.pid"
    log_path = log_dir / "batch-run.log"
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        *[item for item in sys.argv[1:] if item != "--background"],
    ]
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    pid_path.write_text(str(process.pid), encoding="utf-8")
    print("Started boundary backtest batch as PID {}.".format(process.pid), flush=True)
    print("Log: {}".format(log_path), flush=True)
    return 0


def _print_status(scratch_root: Path) -> int:
    log_path = scratch_root / "logs" / "batch-run.log"
    pid_path = scratch_root / "batch.pid"
    summary_path = REPO_ROOT / "backtests" / "reports" / "batch-summary.json"
    if pid_path.exists():
        print("PID: {}".format(pid_path.read_text(encoding="utf-8").strip()), flush=True)
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        print("Last log lines:", flush=True)
        for line in lines[-40:]:
            print(line, flush=True)
    else:
        print("No batch log found at {}".format(log_path), flush=True)
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        print("Completed reports: {}".format(len(summary)), flush=True)
        for item in summary[-8:]:
            print(
                "  {key}: {mode}, {match_count} candidates, {report}".format(**item),
                flush=True,
            )
    return 0


def _prepare_cookies(source: Path, output: Path) -> Optional[Path]:
    if not source.exists():
        return None
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = source.read_text(encoding="utf-8", errors="ignore").splitlines()
    if any(line.startswith("# Netscape HTTP Cookie File") for line in lines[:5]):
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return output

    rows = []
    for row in csv.reader(lines, delimiter="\t"):
        if len(row) < 7 or row[0].lower() == "name":
            continue
        name, value, domain, path, expires, http_only, secure = row[:7]
        if not name or not domain:
            continue
        include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
        secure_flag = "TRUE" if secure.strip().lower() in {"true", "1", "yes"} else "FALSE"
        expires_value = _cookie_expiry_value(expires.strip())
        rows.append("\t".join([domain, include_subdomains, path or "/", secure_flag, expires_value, name, value]))

    if not rows:
        return None
    output.write_text("# Netscape HTTP Cookie File\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return output


def _cookie_expiry_value(value: str) -> str:
    if not value:
        return "0"
    if value.isdigit():
        return value
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return str(max(0, int(parsed.timestamp())))
    except ValueError:
        return "0"


def _load_jobs(
    manifest_path: Path,
    channel_url: str,
    yt_dlp: Path,
    cookies_path: Optional[Path],
    channel_limit: int,
) -> List[VodJob]:
    manifest = _read_manifest(manifest_path)
    jobs = [VodJob(**item) for item in manifest.get("vods", [])]
    if jobs:
        return jobs

    discovered = _discover_channel_vods(channel_url, yt_dlp, cookies_path, channel_limit)
    project_by_key = {
        "bb4": str(REPO_ROOT / "backtests" / "projects" / "aug5.tvh.json"),
        "bb5": str(REPO_ROOT / "backtests" / "projects" / "BB5.tvh.json"),
        "bb7": str(REPO_ROOT / "backtests" / "projects" / "sep19.tvh.json"),
        "bb8": str(REPO_ROOT / "backtests" / "projects" / "oct1.tvh.json"),
    }
    selected: List[VodJob] = []
    for item in discovered:
        title = item.get("title", "")
        if "full vod" not in title.lower():
            continue
        key = _vod_key(title)
        if not key:
            continue
        selected.append(
            VodJob(
                key=key,
                url=item.get("url", ""),
                title=title,
                project=project_by_key.get(key, ""),
            )
        )
    return selected


def _read_manifest(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _discover_channel_vods(channel_url: str, yt_dlp: Path, cookies_path: Optional[Path], limit: int) -> List[Dict[str, str]]:
    command = [
        str(yt_dlp),
        "--flat-playlist",
        "--dump-single-json",
        "--playlist-end",
        str(max(limit, 1)),
        channel_url,
    ]
    if cookies_path:
        command[1:1] = ["--cookies", str(cookies_path)]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    payload = json.loads(result.stdout)
    entries = []
    for entry in payload.get("entries", []) or []:
        video_id = entry.get("id", "")
        url = entry.get("url") or ("https://www.youtube.com/watch?v=" + video_id if video_id else "")
        if url and not url.startswith("http"):
            url = "https://www.youtube.com/watch?v=" + url
        entries.append({"title": entry.get("title", ""), "url": url})
    return entries


def _vod_key(title: str) -> str:
    compact = title.lower().replace("#", " ")
    for marker, key in [("basement brawl 4", "bb4"), ("basement brawl 5", "bb5"), ("basement brawl 7", "bb7"), ("basement brawl 8", "bb8")]:
        if marker in compact:
            return key
    return ""


def _download_vod(
    job: VodJob,
    yt_dlp: Path,
    cookies_path: Optional[Path],
    video_dir: Path,
    log_dir: Path,
    format_selector: str,
    player_client: str,
) -> Path:
    output_template = video_dir / (job.key + ".%(ext)s")
    command = [
        str(yt_dlp),
        "--no-playlist",
        "--newline",
        "--js-runtimes",
        "node",
        "--extractor-args",
        "youtube:player_client={}".format(player_client),
        "-f",
        format_selector,
        "-o",
        str(output_template),
        job.url,
    ]
    if cookies_path:
        command[1:1] = ["--cookies", str(cookies_path)]
    log_path = log_dir / (job.key + "-download.log")
    print("  downloading to scratch; log {}".format(log_path), flush=True)
    with log_path.open("w", encoding="utf-8") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, text=True, check=True)
    for candidate in video_dir.glob(job.key + ".*"):
        if candidate.suffix.lower() in {".mp4", ".webm", ".mkv"}:
            print("  downloaded {}".format(candidate), flush=True)
            return candidate
    raise RuntimeError("Download completed but no video file was found for {}".format(job.key))


def _run_backtest(job: VodJob, video_path: Path, output_dir: Path, args: argparse.Namespace) -> Dict[str, object]:
    script = REPO_ROOT / "scripts" / "backtest_youtube_boundaries.py"
    command = [
        sys.executable,
        str(script),
        "--video",
        str(video_path),
        "--start-reference",
        args.start_reference,
        "--end-reference",
        args.end_reference,
        "--output-dir",
        str(output_dir),
        "--progress-every",
        str(args.progress_every),
    ]
    if job.project and Path(job.project).exists():
        command.extend(["--project", job.project])
        if not args.no_save_candidate_frames:
            command.append("--save-candidate-frames")
        mode = "project"
    else:
        command.extend(["--timeline-scan", "--timeline-step", str(args.timeline_step)])
        if args.timeline_save_frames:
            command.append("--timeline-save-frames")
        mode = "timeline"

    print("  running {} backtest; output {}".format(mode, output_dir), flush=True)
    subprocess.run(command, check=True)
    report_path = output_dir / "boundary-backtest-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return {
        "key": job.key,
        "title": job.title,
        "url": job.url,
        "mode": mode,
        "project": job.project,
        "report": str(report_path),
        "match_count": len(report.get("matches", []) or report.get("candidate_matches", []) or []),
    }


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
        print("  removed scratch video {}".format(path), flush=True)
    except OSError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
