"""Map overlay epochs to recording time using explicit, calibrated checkpoints.

OBS duration is an observation, not a calibrated media clock. Callers must supply
an uncertainty bound that includes capture/encoder latency; otherwise no bounded
mapping is returned. Never extrapolate or combine separate recording IDs.
"""
from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass


class RecordingClockError(ValueError):
    pass


def _number(value, name, *, nonnegative=False):
    if isinstance(value, bool):
        raise RecordingClockError(f"{name} must be a finite number.")
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise RecordingClockError(f"{name} must be a finite number.") from exc
    if not math.isfinite(value) or (nonnegative and value < 0):
        raise RecordingClockError(f"{name} must be finite" + (" and nonnegative." if nonnegative else "."))
    return value


@dataclass(frozen=True)
class Checkpoint:
    epoch: float
    duration_seconds: float
    active: bool = True
    paused: bool = False
    uncertainty_seconds: float | None = None

    def __post_init__(self):
        object.__setattr__(self, "epoch", _number(self.epoch, "epoch"))
        object.__setattr__(self, "duration_seconds", _number(self.duration_seconds, "duration", nonnegative=True))
        if type(self.active) is not bool or type(self.paused) is not bool:
            raise RecordingClockError("active and paused must be booleans.")
        if self.paused and not self.active:
            raise RecordingClockError("An inactive recording cannot be paused.")
        if self.uncertainty_seconds is not None:
            object.__setattr__(self, "uncertainty_seconds", _number(self.uncertainty_seconds, "uncertainty", nonnegative=True))


class RecordingClock:
    def __init__(self, recording_id, checkpoints):
        if not isinstance(recording_id, str) or not recording_id.strip():
            raise RecordingClockError("A unique recording ID is required for each recording start.")
        self.recording_id = recording_id
        self.checkpoints = tuple(checkpoints)
        if not self.checkpoints:
            raise RecordingClockError("At least one checkpoint is required.")
        seen_inactive = False
        for point in self.checkpoints:
            if not isinstance(point, Checkpoint):
                raise RecordingClockError("Expected Checkpoint objects.")
            if seen_inactive and point.active:
                raise RecordingClockError("A restart needs a new recording ID.")
            seen_inactive |= not point.active
        for a, b in zip(self.checkpoints, self.checkpoints[1:]):
            if b.epoch <= a.epoch:
                raise RecordingClockError("Epochs must increase; duplicates or wall-clock reversals need review.")
            if b.duration_seconds < a.duration_seconds:
                raise RecordingClockError("Recording duration decreased; a restart needs a new recording ID.")
            if a.uncertainty_seconds is not None and b.uncertainty_seconds is not None:
                if b.duration_seconds-a.duration_seconds > b.epoch-a.epoch+a.uncertainty_seconds+b.uncertainty_seconds+1e-8:
                    raise RecordingClockError("Duration advanced faster than wall time; check clock jumps or calibration.")
        self.epochs = tuple(p.epoch for p in self.checkpoints)

    def map_epoch(self, epoch):
        epoch = _number(epoch, "event epoch")
        result = {"recording_id": self.recording_id, "epoch": epoch,
                  "provenance": "estimated_from_checkpoints", "status": "unmapped"}
        index = bisect_left(self.epochs, epoch)
        if index < len(self.epochs) and self.epochs[index] == epoch:
            a = b = self.checkpoints[index]
        elif index == 0 or index == len(self.epochs):
            return dict(result, reason="outside_checkpoint_range")
        else:
            a, b = self.checkpoints[index-1:index+1]
        if not a.active or not b.active:
            return dict(result, reason="inactive_or_stop_transition")
        if a.uncertainty_seconds is None or b.uncertainty_seconds is None:
            return dict(result, status="unknown_uncertainty", reason="capture_clock_not_calibrated")
        # Recording time can advance between zero and one second per wall second.
        # Intersect constraints from both anchors; never guess where a pause occurred.
        low = max(a.duration_seconds-a.uncertainty_seconds,
                  b.duration_seconds-b.uncertainty_seconds-(b.epoch-epoch), 0.0)
        high = min(a.duration_seconds+a.uncertainty_seconds+(epoch-a.epoch),
                   b.duration_seconds+b.uncertainty_seconds)
        if low > high+1e-8:
            return dict(result, reason="inconsistent_checkpoints")
        high = max(low, high)
        paused = a.paused and b.paused and (a is b or (
            a.duration_seconds == b.duration_seconds and a.uncertainty_seconds == b.uncertainty_seconds == 0))
        history = [p for p in self.checkpoints if p.epoch <= b.epoch]
        pause_possible = any(p.paused for p in history) or any(
            y.duration_seconds-x.duration_seconds < y.epoch-x.epoch-1e-8
            for x,y in zip(history, history[1:]))
        return dict(result, status="paused" if paused else "mapped",
                    lower_seconds=low, upper_seconds=high, estimate_seconds=(low+high)/2,
                    uncertainty_seconds=(high-low)/2, pause_transition=a.paused != b.paused, pause_history_possible=pause_possible)


def map_source_time(mapping, offset_seconds, uncertainty_seconds=None, *, shares_main_pause_clock=False):
    """Positive offset means the source recording began after the main recording.

    Offset must be measured against media timestamps, not inferred from filenames
    or Source Record's divisor-affected duration. This models a constant offset;
    it does not correct or certify drift or pauses in independent source files.
    """
    offset = _number(offset_seconds, "source offset")
    error = None if uncertainty_seconds is None else _number(uncertainty_seconds, "source offset uncertainty", nonnegative=True)
    if type(shares_main_pause_clock) is not bool:
        raise RecordingClockError("shares_main_pause_clock must be a boolean.")
    result = dict(mapping, source_offset_seconds=offset,
                  source_offset_uncertainty_seconds=error)
    if "lower_seconds" not in mapping:
        return result
    # A main-clock bound cannot survive an unbounded source-clock offset.
    for key in ("lower_seconds", "upper_seconds", "estimate_seconds", "uncertainty_seconds"):
        result.pop(key, None)
    if mapping.get("pause_history_possible") and not shares_main_pause_clock:
        return dict(result, status="unmapped", reason="independent_source_pause_behavior_unverified")
    if error is None:
        return dict(result, status="unknown_uncertainty", reason="source_offset_not_calibrated")
    low = mapping["lower_seconds"]-offset-error
    high = mapping["upper_seconds"]-offset+error
    if low < 0:
        return dict(result, status="unmapped", reason="before_source_start" if high < 0 else "straddles_source_start")
    return dict(result, lower_seconds=low, upper_seconds=high,
                estimate_seconds=(low+high)/2, uncertainty_seconds=(high-low)/2)
