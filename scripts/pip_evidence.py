#!/usr/bin/env python3
"""One evidence base for the read-only tools, so two reports cannot disagree about the recording.

This name used to hold the implementation. It now holds a pointer.

The reason it existed is a specific contradiction found on 2026-10-30: the set-trial planner merged the
saved pip answers and reported four games with validated winners in ``[1200, 2200)``, while the
integration report read the state timeline alone for the same interval and reported zero winners. Each was
faithful to the rows it fed the tracker, and a reader comparing the two could not tell which question each
had answered. One entry point fixed that: the clock/state facts joined with the crop readings through the
canonical merge, with the framing guard (a second framing never joins one pip series) and the window guard
(a reading outside the window is dropped, not appended -- appending one once put game 1's frames inside
game 2's replay and invented a game) travelling with it.

Moving the implementation into :mod:`tekken_vod_helper.boundary_evidence` is the same fix applied one
level up. The public recovery command (``investigate-games``) needs that evidence base, a library cannot
import a script, and a package-level *copy* of the join would be two joins again -- which is the failure
this file was written to stop. Every tool, script or command now calls the one function.

Nothing here sends a request or writes a file: it reads answers that already exist.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tekken_vod_helper import boundary_evidence as be  # noqa: E402,F401

#: Kept as importable names for callers and saved reports. They are the package's values, not a second
#: list that can drift: reading these is fine, reimplementing what they point at is not.
FACTS = be.DEFAULT_FACTS
DEFAULT_FIXTURE = be.DEFAULT_FIXTURE
PLAN_SNAPSHOT_CROP_RUNS = be.PLAN_SNAPSHOT_CROP_RUNS
CROP_RUNS = be.CROP_RUNS
STATE_ONLY = be.STATE_ONLY

evidence_root = be.evidence_root
crop_run_dirs = be.crop_run_dirs
crop_runs = be.crop_runs
framings = be.framings
evidence = be.evidence
