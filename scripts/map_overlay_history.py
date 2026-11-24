"""Map overlay JSONL epochs using saved recording checkpoints, without editing inputs.

Checkpoint JSON: {"recordings": {"unique-start-id": [{"epoch": 1,
"duration_seconds": 0, "active": true, "paused": false,
"uncertainty_seconds": null}]}, "source_offsets": {"camera": {
"seconds": 0.2, "uncertainty_seconds": null}}}.
Unknown calibration stays unknown. This is an offline report, not a compositor.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tekken_vod_helper.recording_clock import Checkpoint, RecordingClock, RecordingClockError, map_source_time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoints", type=Path)
    parser.add_argument("history", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--recording-id", required=True)
    args = parser.parse_args()
    try:
        document = json.loads(args.checkpoints.read_text())
        clock = RecordingClock(args.recording_id, [Checkpoint(**p) for p in document["recordings"][args.recording_id]])
        offsets = document.get("source_offsets", {})
        # Parse and validate every event before creating a report.
        events = []
        for line_number, line in enumerate(args.history.read_text().splitlines(), 1):
            if not line.strip():
                continue
            event = json.loads(line)
            mapped = clock.map_epoch(event["epoch"])
            sources = {name: map_source_time(mapped, value["seconds"], value.get("uncertainty_seconds"),
                                                       shares_main_pause_clock=value.get("shares_main_pause_clock", False))
                       for name, value in offsets.items()}
            events.append({"line": line_number, "event": event, "main": mapped, "sources": sources})
        report = {"kind": "tvh-overlay-time-report", "schema_version": 1,
                  "recording_id": args.recording_id,
                  "notes": ["Checkpoint and offset bounds require independent calibration.",
                            "A constant offset does not certify drift or independent pause behavior."], "events": events}
        encoded = json.dumps(report, indent=2, allow_nan=False) + "\n"
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(encoded)
    except (RecordingClockError, OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
