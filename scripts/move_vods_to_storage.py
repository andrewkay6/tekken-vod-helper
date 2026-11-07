"""Copy the streaming PC's Tekken VODs onto network storage with event-based names.

Streams each file through this box (Windows can reach neither share directly) and
verifies every transfer against a source-side SHA-256 before publishing the final
name. Nothing on the source is modified or deleted.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HOST = "kwtekken"
SRC_DIR = "C:\\Users\\KWTekken\\Videos"
SRC_PROJECTS = "C:\\Users\\KWTekken\\Downloads"

DEST_ROOT = Path("/var/home/andy/SharedStorage/Videos/Tekken")
DEST_VODS = DEST_ROOT / "VODs"
DEST_META = DEST_ROOT / "metadata"
DEST_LOG = DEST_META / "vod-move-log.jsonl"

# (source name, destination name, size on the PC as listed 2026-11-06)
PLAN = [
    ("2026-06-10 18-47-44 1440p.mp4", "2026-06-10 [1440p].mp4", 77900998962),
    ("2026-06-10 18-47-44.mkv", "2026-06-10 [raw].mkv", 34292904436),
    ("2026-06-10 18-47-44.mp4", "2026-06-10 [remux].mp4", 34307546424),
    ("2026-06-24 18-32-28.mkv", "2026-06-24 [raw 01].mkv", 12474708),
    ("2026-06-24 18-33-59.mkv", "2026-06-24 [raw 02].mkv", 10089339),
    ("2026-06-24 18-34-39.mkv", "2026-06-24 [raw 03].mkv", 39652527),
    ("2026-06-24 18-35-26.mkv", "2026-06-24 [raw 04].mkv", 53942647),
    ("2026-06-24 18-35-51.mkv", "2026-06-24 [raw 05].mkv", 140283190),
    ("2026-06-24 18-36-47.mkv", "2026-06-24 [raw 06].mkv", 39538307),
    ("2026-06-24 18-37-55.mkv", "2026-06-24 [raw 07].mkv", 24872783),
    ("2026-06-24 18-38-32.mkv", "2026-06-24 [raw 08].mkv", 31447319),
    ("2026-06-24 18-40-55.mkv", "2026-06-24 [raw 09].mkv", 13572149),
    ("2026-06-24 18-41-21.mkv", "2026-06-24 [raw 10].mkv", 15149541),
    ("2026-07-08 18-13-06.mkv", "2026-07-08 [raw 01].mkv", 626452651),
    ("2026-07-08 19-02-57.mkv", "2026-07-08 [raw 02].mkv", 41953098125),
    ("2026-07-22 18-30-04.mkv", "2026-07-22 [raw].mkv", 40212174527),
    ("jul 22 1440p.mp4", "2026-07-22 [1440p].mp4", 91487376924),
    ("2026-08-05 18-39-23.mkv", "2026-08-05 Basement Brawl 4 A Crescent Komeback [raw].mkv", 52679116840),
    ("aug 5 1440p.mp4", "2026-08-05 Basement Brawl 4 A Crescent Komeback [1440p].mp4", 122111003150),
    ("2026-08-19 19-08-53.mkv", "2026-08-19 Basement Brawl 5 Heihachis Homecoming [raw].mkv", 27769601933),
    ("aug 19 1440p.mp4", "2026-08-19 Basement Brawl 5 Heihachis Homecoming [1440p].mp4", 64382930496),
    ("2026-09-17 19-17-31.mkv", "2026-09-17 Basement Brawl 7 Cafe Edition [raw].mkv", 29529216723),
    ("sep 17 1440p.mp4", "2026-09-17 Basement Brawl 7 Cafe Edition [1440p].mp4", 71157059575),
    ("2026-10-01 18-46-03.mkv", "2026-10-01 Basement Brawl 8 Cafe Edition [raw 01].mkv", 3284841953),
    ("2026-10-01 19-20-20.mkv", "2026-10-01 Basement Brawl 8 Cafe Edition [raw 02].mkv", 27842246828),
    ("oct 01.mp4", "2026-10-01 Basement Brawl 8 Cafe Edition [1440p].mp4", 64452665328),
    ("oct 01 exhibition.mp4", "2026-10-01 Exhibition [1440p].mp4", 8887117119),
    ("2026-10-22 19-08-09.mkv", "2026-10-22 Basement Brawl 9 Cafe Edition Pre-Pre-Clash-Clash [raw].mkv", 23029101728),
    ("oct 22 1440p.mp4", "2026-10-22 Basement Brawl 9 Cafe Edition Pre-Pre-Clash-Clash [1440p].mp4", 56831736893),
    ("2026-11-05 19-35-07.mkv", "2026-11-05 Basement Brawl 10 Cafe Edition [raw].mkv", 31451794033),
]

PROJECT_JSONS = [
    "aug5.tvh.json",
    "BB5.tvh.json",
    "sep19.tvh.json",
    "oct1.tvh.json",
    "oct 22.tvh.json",
    "nov 4.tvh.tvh.json",
]

# A dead TCP path must fail the transfer, not hang the run silently.
SSH_BASE = [
    "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4", HOST,
]

_lock = threading.Lock()


def log(message: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}"
    with _lock:
        print(line, flush=True)
        with DEST_LOG.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def record(entry: dict) -> None:
    with _lock:
        with (DEST_META / "manifest.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")


def remote_shell(command: str, timeout: int = 120) -> str:
    result = subprocess.run(
        [*SSH_BASE, command],
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"remote command failed ({result.returncode}): {command}\n"
            f"{result.stderr.decode('utf-8', 'replace')[:400]}"
        )
    return result.stdout.decode("utf-8", "replace")


def source_hash(name: str, timeout: int) -> str:
    out = remote_shell(f'certutil -hashfile "{SRC_DIR}\\{name}" SHA256', timeout=timeout)
    for line in out.splitlines():
        candidate = line.strip().replace(" ", "")
        if re.fullmatch(r"[0-9a-fA-F]{64}", candidate):
            return candidate.lower()
    raise RuntimeError(f"no sha256 in certutil output for {name}: {out[:200]}")


def stream_to(name: str, target: Path, expected: int) -> tuple[int, str]:
    """Copy one remote file to target.part through a pipe, hashing as it lands."""
    part = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    total = 0
    proc = subprocess.Popen(
        [*SSH_BASE, f'type "{SRC_DIR}\\{name}"'],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        with part.open("wb") as handle:
            while True:
                chunk = proc.stdout.read(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                digest.update(chunk)
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        code = proc.wait()
        proc.stdout.close()
    if code != 0:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"ssh type exited {code} for {name} after {total} bytes")
    if total != expected:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"size mismatch for {name}: streamed {total}, expected {expected}")
    part.rename(target)
    return total, digest.hexdigest()


def transfer(item: tuple[str, str, int]) -> dict:
    name, dest_name, expected = item
    target = DEST_VODS / dest_name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size == expected:
        log(f"skip {dest_name} (already present at expected size)")
        return {"source": name, "dest": dest_name, "bytes": expected, "status": "skipped-existing"}

    # Hash before reading so a bad source read fails without burning bandwidth.
    src_timeout = max(600, expected // (30 << 20))
    before = source_hash(name, timeout=src_timeout)

    stream_timeout = max(600, expected // (5 << 20))
    start = time.time()
    total, streamed = _stream_with_retry(name, target, expected, stream_timeout)
    after = source_hash(name, timeout=src_timeout)
    elapsed = time.time() - start

    if not (before == streamed == after):
        status = "HASH-MISMATCH"
        log(f"FAILED {dest_name}: source-before={before[:12]} stream={streamed[:12]} source-after={after[:12]}")
    else:
        status = "verified"
        log(f"ok {dest_name} {total/1e9:.2f} GB in {elapsed/60:.1f} min at {total/1e6/max(elapsed,1):.1f} MB/s")

    entry = {
        "source": f"{SRC_DIR}\\{name}",
        "dest": str(target),
        "bytes": total,
        "expected_bytes": expected,
        "sha256_source_before": before,
        "sha256_streamed": streamed,
        "sha256_source_after": after,
        "status": status,
        "minutes": round(elapsed / 60, 2),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    record(entry)
    return entry


def _stream_with_retry(name: str, target: Path, expected: int, timeout: int) -> tuple[int, str]:
    deadline = time.time() + timeout
    last: Exception | None = None
    for attempt in (1, 2):
        try:
            remaining = int(deadline - time.time())
            if remaining < 30:
                raise RuntimeError(f"out of time budget for {name}")
            return stream_to(name, target, expected)
        except Exception as exc:  # noqa: BLE001 - retry once, then report
            last = exc
            log(f"attempt {attempt} failed for {name}: {exc}")
            time.sleep(5)
    raise RuntimeError(f"giving up on {name}: {last}")


def copy_text(remote_path: str, local_path: Path) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    data = subprocess.run(
        [*SSH_BASE, f'type "{remote_path}"'],
        capture_output=True,
        timeout=120,
        check=True,
    ).stdout
    local_path.write_bytes(data)


def do_meta() -> None:
    DEST_META.mkdir(parents=True, exist_ok=True)
    for name in PROJECT_JSONS:
        dest = DEST_META / "projects" / name
        copy_text(f"{SRC_PROJECTS}\\{name}", dest)
        log(f"project json -> {dest.name} ({dest.stat().st_size} bytes)")

    listing = remote_shell(
        'powershell -NoProfile -Command "Get-ChildItem C:\\Users\\KWTekken\\Videos -Recurse -Filter *.json -File | Select-Object -ExpandProperty FullName"'
    )
    paths = [line.strip() for line in listing.splitlines() if line.strip().endswith(".json")]
    copied = 0
    for remote_path in paths:
        relative = remote_path[len(SRC_DIR) + 1 :].replace("\\", os.sep)
        dest = DEST_META / "clips" / relative
        try:
            copy_text(remote_path, dest)
            copied += 1
        except Exception as exc:  # noqa: BLE001 - metadata copy must not stop the migration
            log(f"clip json skipped {relative}: {exc}")
    log(f"clip metadata copied: {copied}/{len(paths)}")


def run_one(item: tuple[str, str, int]) -> dict:
    """One file's transfer; a failure here must not cancel the rest of the batch."""
    name, dest_name, expected = item
    try:
        return transfer(item)
    except Exception as exc:  # noqa: BLE001 - keep the unattended run going, report at the end
        log(f"FAILED {dest_name}: {exc}")
        entry = {
            "source": f"{SRC_DIR}\\{name}",
            "dest": str(DEST_VODS / dest_name),
            "bytes": 0,
            "expected_bytes": expected,
            "status": f"error: {exc}"[:300],
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        record(entry)
        return entry


def original_plan() -> list[tuple[str, str, int]]:
    """Archive camera/recorder files by event, excluding every processed MP4."""
    items = []
    for source, dest, size in PLAN:
        if not source.lower().endswith(".mkv"):
            continue
        event = dest.split(" [raw", 1)[0]
        stamp = source.removesuffix(".mkv")
        items.append((source, f"{event}/recordings/{stamp} [original].mkv", size))
    return items


def do_vods(workers: int, only: str | None, originals: bool = False) -> int:
    DEST_VODS.mkdir(parents=True, exist_ok=True)
    DEST_META.mkdir(parents=True, exist_ok=True)
    plan = original_plan() if originals else PLAN
    items = [p for p in plan if only is None or only in p[0] or only in p[1]]
    (DEST_META / "move-status.txt").write_text(
        f"RUNNING since {time.strftime('%Y-%m-%d %H:%M:%S%z')}\n", encoding="utf-8"
    )
    log(f"transferring {len(items)} files, {sum(i[2] for i in items)/1e9:.1f} GB, {workers} workers")
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(run_one, items):
            results.append(result)
    bad = [r for r in results if not r["status"].startswith(("verified", "skipped"))]
    log(f"done: {len(results) - len(bad)} ok, {len(bad)} failed")
    for r in bad:
        log(f"  FAILED {r['dest']} ({r['status']})")
    summary = "COMPLETE" if not bad else f"INCOMPLETE: {len(bad)} of {len(results)} failed"
    (DEST_META / "move-status.txt").write_text(
        summary
        + f"\nfinished {time.strftime('%Y-%m-%d %H:%M:%S%z')}"
        + f"\nverified-or-skipped {len(results) - len(bad)}/{len(results)}"
        + "\n"
        + "".join(f"FAILED {r['dest']} ({r['status']})\n" for r in bad),
        encoding="utf-8",
    )
    return 1 if bad else 0


def do_manifest() -> None:
    rows = [
        json.loads(line)
        for line in (DEST_META / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    latest: dict[str, dict] = {}
    for row in rows:
        latest[row["dest"]] = row
    fields = [
        "dest", "source", "bytes", "sha256_streamed", "sha256_source_before",
        "sha256_source_after", "status", "minutes", "at",
    ]
    out = DEST_META / "manifest.csv"
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in sorted(latest.values(), key=lambda r: r["dest"]):
            writer.writerow(row)
    log(f"wrote {out} ({len(latest)} rows)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--meta", action="store_true", help="copy JSON metadata only")
    parser.add_argument("--vods", action="store_true", help="transfer the recordings")
    parser.add_argument("--manifest", action="store_true", help="render manifest.csv")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--only", help="restrict to names containing this substring")
    parser.add_argument("--originals", action="store_true", help="only original MKVs, organized into event folders")
    args = parser.parse_args()
    if args.meta:
        do_meta()
    if args.vods:
        return do_vods(args.workers, args.only, args.originals)
    if args.manifest:
        do_manifest()
    if not (args.meta or args.vods or args.manifest):
        parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
