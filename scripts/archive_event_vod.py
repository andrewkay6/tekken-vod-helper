"""Archive one file from the streaming PC to shared storage, verified end to end.

Per-event successor to move_vods_to_storage.py for the devbox:

  1. SHA-256 the source on the PC (certutil) and read its size.
  2. Stream it over `ssh kwtekken type` into a local staging file, hashing as it lands.
  3. SFTP it to mac-studio (alias `tvh-storage`, SFTP-only account) as `<dest>.part`, then rename.
  4. Re-hash the landed file through the read-only storage mount on the devbox.
  5. Re-hash the source, and require all four hashes to agree.
  6. Upload a JSON receipt to metadata/receipts/ and delete the staging copy.

Nothing on the PC is modified or deleted. An existing destination is never overwritten.

    .venv/bin/python scripts/archive_event_vod.py \
        --source "2026-11-19 19-26-51.mkv" \
        --dest "VODs/2026-11-19 Basement Brawl 11 Cafe Edition/recordings/2026-11-19 19-26-51 [original].mkv"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath

PC = "kwtekken"
STORAGE = "tvh-storage"
SRC_DIR = "C:\\Users\\KWTekken\\Videos"
RO_MOUNT = Path("/var/lib/ai-sandbox-media")  # read-only view of Tekken/VODs
DEFAULT_STAGING = Path(__file__).resolve().parents[1] / "build/work/archive-staging"
LOG = DEFAULT_STAGING / "archive-log.txt"
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=4"]


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def pc(command: str, timeout: int) -> str:
    r = subprocess.run(["ssh", *SSH_OPTS, PC, command], capture_output=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"PC command failed ({r.returncode}): {command}\n{r.stderr.decode('utf-8', 'replace')[:400]}")
    return r.stdout.decode("utf-8", "replace")


def pc_size(name: str) -> int:
    out = pc(f'powershell -NoProfile -Command "(Get-Item -LiteralPath \'{SRC_DIR}\\{name}\').Length"', 120)
    return int(out.strip())


def pc_hash(name: str, timeout: int) -> str:
    out = pc(f'certutil -hashfile "{SRC_DIR}\\{name}" SHA256', timeout)
    for line in out.splitlines():
        cand = line.strip().replace(" ", "")
        if re.fullmatch(r"[0-9a-fA-F]{64}", cand):
            return cand.lower()
    raise RuntimeError(f"no sha256 in certutil output: {out[:200]}")


def sftp(commands: list[str], timeout: int, check: bool = True) -> subprocess.CompletedProcess:
    with tempfile.NamedTemporaryFile("w", suffix=".sftp", delete=False) as fh:
        fh.write("\n".join(commands) + "\n")
        batch = fh.name
    try:
        r = subprocess.run(["sftp", *SSH_OPTS, "-b", batch, STORAGE], capture_output=True, timeout=timeout)
    finally:
        Path(batch).unlink(missing_ok=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"sftp failed ({r.returncode}): {r.stderr.decode('utf-8', 'replace')[:400]}")
    return r


def q(path: str) -> str:
    return '"' + path.replace('"', '\\"') + '"'


def remote_exists(rel: str) -> bool:
    return sftp([f"ls {q(rel)}"], 60, check=False).returncode == 0


def mkdirs(rel_dir: PurePosixPath) -> None:
    parts = rel_dir.parts
    # "-" prefix: ignore "already exists".
    sftp([f"-mkdir {q(str(PurePosixPath(*parts[:i])))}" for i in range(2, len(parts) + 1)], 60)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(8 << 20):
            h.update(chunk)
    return h.hexdigest()


def stream_to(name: str, target: Path, expected: int, timeout: int) -> str:
    h = hashlib.sha256()
    total = 0
    proc = subprocess.Popen(["ssh", *SSH_OPTS, PC, f'type "{SRC_DIR}\\{name}"'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    deadline = time.time() + timeout
    last_report = time.time()
    try:
        with target.open("wb") as fh:
            while chunk := proc.stdout.read(8 << 20):
                h.update(chunk)
                fh.write(chunk)
                total += len(chunk)
                if time.time() > deadline:
                    proc.kill()
                    raise RuntimeError("stream timed out")
                if time.time() - last_report > 60:
                    log(f"  pulled {total / 1e9:.1f} / {expected / 1e9:.1f} GB")
                    last_report = time.time()
    finally:
        code = proc.wait()
    if code != 0 or total != expected:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"stream failed: exit {code}, {total} of {expected} bytes")
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="file name in C:\\Users\\KWTekken\\Videos")
    ap.add_argument("--dest", required=True, help="path relative to Videos/Tekken, must start with VODs/")
    ap.add_argument("--staging", type=Path, default=DEFAULT_STAGING)
    args = ap.parse_args()

    dest = PurePosixPath(args.dest)
    if dest.parts[0] != "VODs" or ".." in dest.parts:
        ap.error("--dest must be under VODs/")
    mounted = RO_MOUNT.joinpath(*dest.parts[1:])

    if remote_exists(str(dest)):
        log(f"skip: {dest} already exists on storage (not overwriting)")
        return 0

    size = pc_size(args.source)
    hash_timeout = max(600, size // (30 << 20))
    move_timeout = max(1800, size // (5 << 20))
    log(f"archive {args.source} ({size / 1e9:.2f} GB) -> {dest}")

    before = pc_hash(args.source, hash_timeout)
    log(f"  source sha256 before {before[:16]}")

    args.staging.mkdir(parents=True, exist_ok=True)
    staged = args.staging / dest.name
    t0 = time.time()
    streamed = stream_to(args.source, staged, size, move_timeout)
    log(f"  pulled in {(time.time() - t0) / 60:.1f} min, sha256 {streamed[:16]}")

    mkdirs(dest.parent)
    part = str(dest) + ".part"
    t1 = time.time()
    sftp([f"-rm {q(part)}", f"put {q(str(staged))} {q(part)}", f"rename {q(part)} {q(str(dest))}"], move_timeout)
    log(f"  uploaded in {(time.time() - t1) / 60:.1f} min")

    landed = sha256_file(mounted)
    after = pc_hash(args.source, hash_timeout)
    ok = before == streamed == landed == after and mounted.stat().st_size == size
    receipt = {
        "source": f"{SRC_DIR}\\{args.source}",
        "dest": str(dest),
        "bytes": size,
        "sha256_source_before": before,
        "sha256_streamed": streamed,
        "sha256_landed": landed,
        "sha256_source_after": after,
        "status": "verified" if ok else "HASH-MISMATCH",
        "minutes": round((time.time() - t0) / 60, 2),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "by": "devbox scripts/archive_event_vod.py",
    }
    rec = args.staging / (dest.name + ".receipt.json")
    rec.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    sftp(["-mkdir metadata/receipts", f"put {q(str(rec))} {q('metadata/receipts/' + rec.name)}"], 120)

    if not ok:
        log(f"FAILED verification: {json.dumps(receipt)}")
        return 1
    staged.unlink()
    log(f"ok {dest} verified (sha256 {before})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
