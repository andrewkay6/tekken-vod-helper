"""Collect OBS recording-status checkpoints into a mapper-ready JSON report.

Example:
  .venv/bin/python scripts/collect_recording_checkpoints.py --output take-01.json \
      --recording-id 2026-11-21-kw-main --interval 1 --max-samples 3600 --password-env OBS_PASSWORD

Read-only: this opens one persistent OBS WebSocket connection that issues GetRecordStatus, and writes one
new JSON file. It never starts, stops or pauses a recording, never reads a media file, and refuses an
existing --output instead of truncating it. The password is taken only from the environment variable named
by --password-env and is never written to the report or the log lines.

Exit codes: 0 collected the requested samples, 1 stopped early with usable checkpoints (the recording went
inactive, a stop condition was detected, or an operator interrupted it), 2 nothing usable was recorded at
the requested path (bad arguments, an existing output, no checkpoint at all, or the path was taken by
another writer mid-run, in which case this run's report is kept under a sibling <name>.<token>.partial.json
and its path is printed), 3 a poll failed part-way, with the checkpoints actually observed still written.
No run that failed, stopped early or was rescued ever exits 0. The report's own terminal status and reason
stay authoritative.
"""
import argparse
import os
import signal
import sys
import time
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tekken_vod_helper.models import ObsSettings
from tekken_vod_helper.recording_checkpoints import (
    DEFAULT_MAX_SAMPLES,
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    TERMINAL_INTERRUPTED,
    CheckpointPublicationConflict,
    RecordingCheckpointError,
    RecordingStatusConnection,
    abandoned_run,
    collect_recording_checkpoints,
    save_checkpoint_report,
    validate_collection_arguments,
)

MIN_PORT = 1
MAX_PORT = 65535


def _parser() -> argparse.ArgumentParser:
    # allow_abbrev stays off: with abbreviations on, "--password hunter2" silently means
    # "--password-env hunter2", which is exactly how a credential ends up in a shell history and an argv
    # listing. The only supported route to a password is the name of an environment variable.
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", required=True, type=Path,
                        help="path of the checkpoint report to create; existing files are never overwritten")
    parser.add_argument("--recording-id", required=True,
                        help="unique ID for this recording run; a restart needs a new run and a new ID")
    parser.add_argument("--host", default="127.0.0.1",
                        help="OBS WebSocket host (default: 127.0.0.1)")
    parser.add_argument("--port", default=4455, type=int,
                        help="OBS WebSocket port (default: 4455)")
    parser.add_argument("--password-env", metavar="ENV_NAME", default="",
                        help="name of the environment variable holding the OBS WebSocket password; "
                             "without it no password is used")
    parser.add_argument("--interval", dest="interval", default=DEFAULT_POLL_INTERVAL_SECONDS, type=float,
                        help="seconds between polls (default: %(default)s, must be positive)")
    parser.add_argument("--max-samples", default=DEFAULT_MAX_SAMPLES, type=int,
                        help="stop after this many polls (default: %(default)s)")
    parser.add_argument("--calibration-error-seconds", dest="calibration_error_seconds", default=None,
                        type=float, metavar="NONNEGATIVE_NUMBER",
                        help="operator-calibrated capture-clock error bound; without it capture-clock "
                             "uncertainty stays unknown and is recorded as null")
    return parser


def _port(value: int) -> int:
    if isinstance(value, bool) or not MIN_PORT <= int(value) <= MAX_PORT:
        raise RecordingCheckpointError("OBS port must be between %d and %d." % (MIN_PORT, MAX_PORT))
    return int(value)


def _password(environment_name: str, environ: Dict[str, str]) -> str:
    """Read the password from the one place the operator named, and nowhere else.

    A missing variable is an error rather than a silent anonymous connection: an operator who typed the
    wrong name deserves a refusal, and the variable name itself is the only credential detail ever
    mentioned, never its value.
    """
    if not environment_name:
        return ""
    if environment_name not in environ:
        raise RecordingCheckpointError(
            "Environment variable %s is not set. Unset --password-env to connect without a password."
            % environment_name)
    return environ[environment_name]


def _pause(seconds: float) -> None:
    """Sleep between polls. One indirection so a test can drive the loop on a synthetic clock."""
    time.sleep(seconds)


def _interrupt(signum, frame) -> None:  # noqa: ARG001 - the signal handler signature is fixed
    raise KeyboardInterrupt


def _trap_termination():
    """Let a service-style stop interrupt the poll loop so the partial report is still written."""
    if not hasattr(signal, "SIGTERM"):
        return None
    try:
        return signal.signal(signal.SIGTERM, _interrupt)
    except (OSError, ValueError, RuntimeError):
        return None  # not the main thread: keep whatever the host process already had


def _release_termination(previous) -> None:
    if previous is not None:
        signal.signal(signal.SIGTERM, previous)


def _report(run, output: Path) -> int:
    document = run.document()
    rows = len(run.rows)
    print("checkpoints: %d row(s) from %d sample(s) written to %s" % (rows, len(run.samples), output.name))
    print("terminal: status=%s reason=%s" % (run.status, run.reason))
    print(document["terminal"]["description"])
    if not rows:
        print("No checkpoint row was recorded: this report contains no epoch anchor and nothing may be "
              "mapped from it.", file=sys.stderr)
    if run.calibration_error_seconds is None:
        print("Capture-clock uncertainty was recorded as unknown (null) because no calibrated error bound "
              "was supplied.", file=sys.stderr)
    print("Limits: a stop/restart hidden between two polls cannot be detected; the interval between "
          "checkpoints stays unknown.", file=sys.stderr)
    if not rows:
        return 2
    if run.status == "failed":
        return 3
    return 0 if run.status == "completed" else 1


def main(argv: Optional[list] = None, environ: Optional[Dict[str, str]] = None) -> int:
    args = _parser().parse_args(argv)
    environ = os.environ if environ is None else environ
    live: Dict[str, object] = {}
    try:
        if not str(args.recording_id).strip():
            raise RecordingCheckpointError("--recording-id must not be empty; it names this recording run.")
        knobs = validate_collection_arguments(interval_seconds=args.interval, max_samples=args.max_samples,
                                             calibration_error_seconds=args.calibration_error_seconds,
                                             timeout_seconds=DEFAULT_REQUEST_TIMEOUT_SECONDS)
        settings = ObsSettings(host=str(args.host).strip(), port=_port(args.port),
                               password=_password(str(args.password_env).strip(), environ))
        if not settings.host:
            raise RecordingCheckpointError("--host must not be empty.")
        if args.output.exists():
            raise RecordingCheckpointError(
                "Refusing to overwrite an existing report: %s. Choose a new path." % args.output.name)
    except (RecordingCheckpointError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    connection = RecordingStatusConnection(settings, timeout=knobs["timeout_seconds"])
    previous_handler = _trap_termination()
    try:
        try:
            with connection:
                run = collect_recording_checkpoints(
                    connection, recording_id=str(args.recording_id),
                    interval_seconds=knobs["interval_seconds"], max_samples=knobs["max_samples"],
                    calibration_error_seconds=knobs["calibration_error_seconds"],
                    sleep=_pause,
                    on_sample=lambda current: live.__setitem__("run", current))
        except KeyboardInterrupt:
            run = live.get("run")
        except RecordingCheckpointError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        if run is None:
            # Interrupted before the collector loop could report anything. Publish the outcome rather than
            # leaving no trace: zero rows, an explicit interrupted reason, and a non-zero exit.
            run = abandoned_run(str(args.recording_id), reason=TERMINAL_INTERRUPTED,
                                calibration_error_seconds=knobs["calibration_error_seconds"],
                                interval_seconds=knobs["interval_seconds"],
                                max_samples=knobs["max_samples"],
                                timeout_seconds=knobs["timeout_seconds"])
        if run.status == "running":
            run.finish(TERMINAL_INTERRUPTED)
        try:
            saved = save_checkpoint_report(run.document(), args.output)
        except CheckpointPublicationConflict as exc:
            # The other writer keeps its file and this run keeps its checkpoints; neither is destroyed.
            print(str(exc), file=sys.stderr)
            print("checkpoints were kept at: %s" % exc.rescued_to, file=sys.stderr)
            return 2
        except (RecordingCheckpointError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
    finally:
        _release_termination(previous_handler)
    return _report(run, saved)


if __name__ == "__main__":
    raise SystemExit(main())
