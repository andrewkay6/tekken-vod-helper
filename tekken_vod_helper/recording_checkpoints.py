"""Poll OBS recording status on one persistent read-only connection and save clock checkpoints.

The connection owned here is separate from the short-lived
:class:`tekken_vod_helper.obs_client.ObsClient`: it is opened once, reused for every poll, speaks only
``GetRecordStatus``, and is dropped rather than replayed when a poll fails. Nothing in this module starts,
stops or pauses a recording, and no media file is opened.

Two clocks are kept on purpose. A checkpoint epoch is the wall instant at the *midpoint* of a request, while
the length of that request is measured on a monotonic clock, so a network stall inflates the recorded query
duration instead of quietly moving an epoch.

Limits this module cannot get around, and writes into every report:

* ``uncertainty_seconds`` stays ``None`` unless the operator supplies a calibrated capture-clock error
  bound. How long a query took says nothing about capture or encoder latency, so round-trip time is never
  presented as a bound on when a frame was captured.
* Polling samples. A recording that stops and restarts, or pauses and resumes, entirely between two polls
  is invisible, and the interval between them is unknown; it is never bridged and a recording ID is never
  reused across a restart.
* Duration is OBS's own reported duration for the main recording output. It is never derived from a source
  output (with or without a divisor), a filename, wall time, or a frame-rate header.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .models import ObsSettings
from .obs_client import ObsRecordStatus, _auth_response
from .recording_clock import Checkpoint, RecordingClock, RecordingClockError

GET_RECORD_STATUS = "GetRecordStatus"
IDENTIFY_OP = 1
IDENTIFIED_OP = 2
REQUEST_OP = 6
RESPONSE_OP = 7

DEFAULT_REQUEST_TIMEOUT_SECONDS = 5.0
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_MAX_SAMPLES = 3600
MAX_SAMPLES_LIMIT = 100000
MAX_POLL_INTERVAL_SECONDS = 3600.0
MAX_CALIBRATION_ERROR_SECONDS = 3600.0
DEFAULT_CLOCK_DISCONTINUITY_TOLERANCE_SECONDS = 2.0
DURATION_EPSILON_SECONDS = 1e-9

REPORT_KIND = "tvh-recording-checkpoints"
REPORT_SCHEMA_VERSION = 1
TEMP_PREFIX = ".tvh-checkpoints-"
CHECKPOINT_ROW_KEYS = ("epoch", "duration_seconds", "active", "paused", "uncertainty_seconds")

TERMINAL_COMPLETED = "sample_limit_reached"
TERMINAL_INACTIVE = "recording_inactive"
TERMINAL_DURATION_DECREASED = "recording_duration_decreased"
TERMINAL_WALL_REVERSAL = "wall_clock_reversal"
TERMINAL_DISCONTINUITY = "wall_monotonic_discontinuity"
TERMINAL_DURATION_AHEAD = "duration_ahead_of_wall_clock"
TERMINAL_CONNECTION = "connection_failed"
TERMINAL_INTERRUPTED = "interrupted"

TERMINAL_STATUS_BY_REASON = {
    TERMINAL_COMPLETED: "completed",
    TERMINAL_INACTIVE: "stopped",
    TERMINAL_DURATION_DECREASED: "stopped",
    TERMINAL_WALL_REVERSAL: "stopped",
    TERMINAL_DISCONTINUITY: "stopped",
    TERMINAL_DURATION_AHEAD: "stopped",
    TERMINAL_CONNECTION: "failed",
    TERMINAL_INTERRUPTED: "interrupted",
}

TERMINAL_DESCRIPTIONS = {
    TERMINAL_COMPLETED: "Collected the requested number of samples and stopped on the sample limit.",
    TERMINAL_INACTIVE: "OBS reported the recording inactive. Nothing after this point is covered and this "
                       "recording ID must not be reused: a restart needs a new collector run and ID.",
    TERMINAL_DURATION_DECREASED: "OBS reported a recording duration lower than the previous sample, which a "
                                 "continuing recording cannot do. This is read as a stop/restart inside the "
                                 "unknown interval; nothing was bridged and this ID must not be reused.",
    TERMINAL_WALL_REVERSAL: "The wall clock moved backwards between two samples, so the later epoch is not a "
                            "usable anchor. Checkpoints were cut at the last trustworthy epoch.",
    TERMINAL_DISCONTINUITY: "Wall time and monotonic time disagreed by more than the tolerance between two "
                            "samples (a clock step, or a sleep the monotonic clock did not count).",
    TERMINAL_DURATION_AHEAD: "OBS recording duration advanced further than wall time plus the measured query "
                             "uncertainty, so at least one of the two anchors is wrong.",
    TERMINAL_CONNECTION: "A poll failed on the OBS connection. Polls that never answered are not "
                         "observations and no checkpoint was invented for them.",
    TERMINAL_INTERRUPTED: "Collection was interrupted by the operator. The checkpoints recorded so far are kept.",
}

SKIP_DUPLICATE_EPOCH = "duplicate_wall_epoch"
SKIP_PAUSED_WHILE_INACTIVE = "paused_while_inactive"
SKIP_CLOCK_PREFIX = "rejected_by_recording_clock"

BASE_LIMITATIONS = (
    "Polling samples OBS: a stop/restart or pause/resume hidden entirely between two polls is invisible and "
    "the gap between them is unknown. Nothing here bridges it.",
    "A known OBS stop or restart needs a new collector run with a new recording ID; these rows cover only "
    "this run and must never be merged with another ID.",
    "uncertainty_seconds is None unless a calibrated capture-clock error bound was supplied. Query "
    "round-trip time does not bound capture or encoder latency and is never presented as such.",
    "Duration is OBS's reported main-recording duration, converted from the milliseconds OBS documents for "
    "GetRecordStatus. No source output, divisor, filename, wall time or frame-rate header was used.",
    "Paused samples are retained exactly as observed, including a frozen duration, and are never "
    "interpolated across.",
    "Read-only: no recording was started, stopped or paused, and no media file was opened or verified.",
    "The password is read only from the environment variable named on the command line. It, the host and "
    "port, the authentication handshake and raw WebSocket payloads are never written to this report.",
    "A checkpoint is an observation of OBS state, not a frame-accurate media timestamp; full-run camera "
    "sync and compositing are separate, uncertified work.",
)


class RecordingCheckpointError(ValueError):
    """A supplied value could not be used to collect or save checkpoints."""


class CheckpointPublicationConflict(RecordingCheckpointError):
    """The destination was taken while the report was being written, so it was kept elsewhere.

    The collected checkpoints are not lost: they are hard-linked to a unique sibling name and
    :attr:`rescued_to` says where. Overwriting the other writer would be wrong, and so would throwing
    away a finished report because of a naming collision.
    """

    def __init__(self, destination: Path, rescued_to: Path) -> None:
        super().__init__(
            "Another report already occupies %s, so this run's checkpoints were kept at %s."
            % (destination.name, rescued_to.name))
        self.destination = destination
        self.rescued_to = rescued_to


class RecordingStatusError(RuntimeError):
    """A poll could not be completed. The connection owning it is no longer trusted."""


class RecordingStatusTransportError(RecordingStatusError):
    """The WebSocket transport failed, or the adapter itself was unavailable."""


class RecordingStatusTimeout(RecordingStatusError):
    """No matching response arrived before the whole-request deadline."""


class RecordingStatusProtocolError(RecordingStatusError):
    """The reply could not be interpreted, so nothing about this poll is known."""


class RecordingStatusAuthError(RecordingStatusError):
    """OBS demanded authentication that the caller did not supply."""


class ObsRequestRejected(RecordingStatusError):
    """OBS answered negatively. The comment is never carried, only the numeric status code.

    A refusal is a well-formed protocol exchange, so unlike the other failures it does not mean the socket
    is broken; it does mean the caller learned no status, and the request is never resent on its own.
    """

    def __init__(self, code: Optional[int] = None) -> None:
        super().__init__("OBS rejected the GetRecordStatus request (status code %s)." % code)
        self.code = code


def _number(value: Any, name: str, *, minimum: Optional[float] = None,
            maximum: Optional[float] = None, exclusive_minimum: bool = False,
            allow_none: bool = False) -> Optional[float]:
    if value is None:
        if allow_none:
            return None
        raise RecordingCheckpointError("%s must be a finite number." % name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RecordingCheckpointError("%s must be a finite number." % name)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RecordingCheckpointError("%s must be a finite number." % name) from exc
    if not math.isfinite(number):
        raise RecordingCheckpointError("%s must be finite." % name)
    if minimum is not None and ((number <= minimum) if exclusive_minimum else (number < minimum)):
        raise RecordingCheckpointError("%s must be %s%s." % (name, "more than " if exclusive_minimum else "at least ", minimum))
    if maximum is not None and number > maximum:
        raise RecordingCheckpointError("%s must be at most %s." % (name, maximum))
    return number


def _whole_number(value: Any, name: str, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RecordingCheckpointError("%s must be a whole number." % name)
    if value < 1:
        raise RecordingCheckpointError("%s must be at least 1." % name)
    if value > maximum:
        raise RecordingCheckpointError("%s must be at most %s." % (name, maximum))
    return int(value)


def validate_collection_arguments(*, interval_seconds: Any = DEFAULT_POLL_INTERVAL_SECONDS,
                                  max_samples: Any = DEFAULT_MAX_SAMPLES,
                                  calibration_error_seconds: Any = None,
                                  timeout_seconds: Any = DEFAULT_REQUEST_TIMEOUT_SECONDS) -> Dict[str, Any]:
    """Check the knobs an operator controls before anything is opened or written."""
    return {
        "interval_seconds": _number(interval_seconds, "interval", minimum=0.001, exclusive_minimum=True,
                                    maximum=MAX_POLL_INTERVAL_SECONDS),
        "max_samples": _whole_number(max_samples, "max samples", maximum=MAX_SAMPLES_LIMIT),
        "calibration_error_seconds": _number(calibration_error_seconds, "calibration error bound",
                                            minimum=0.0, maximum=MAX_CALIBRATION_ERROR_SECONDS,
                                            allow_none=True),
        "timeout_seconds": _number(timeout_seconds, "request timeout", minimum=0.001, exclusive_minimum=True,
                                   maximum=MAX_POLL_INTERVAL_SECONDS),
    }


def _record_status(payload: Any) -> ObsRecordStatus:
    """Read one GetRecordStatus reply, refusing to turn a missing or broken field into an observation."""
    if not isinstance(payload, dict):
        raise RecordingStatusProtocolError("OBS returned no GetRecordStatus data.")
    for key in ("outputActive", "outputPaused", "outputDuration"):
        if payload.get(key) is None:
            raise RecordingStatusProtocolError("OBS did not report %s." % key)
    raw = payload["outputDuration"]
    if isinstance(raw, str):
        try:
            raw = int(raw)
        except ValueError as exc:
            raise RecordingStatusProtocolError("OBS reported a non-numeric outputDuration.") from exc
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw) or raw < 0:
        raise RecordingStatusProtocolError("OBS reported an unusable outputDuration.")
    # OBS documents outputDuration in milliseconds for the main recording output. This unit conversion is
    # not a duration heuristic: no divisor, filename, wall time or frame-rate header is involved.
    return ObsRecordStatus(active=bool(payload["outputActive"]),
                           paused=bool(payload["outputPaused"]),
                           duration_seconds=float(raw) / 1000.0,
                           timecode=str(payload.get("outputTimecode") or ""))


def _timeout_like(value: BaseException) -> bool:
    return isinstance(value, TimeoutError) or "timeout" in type(value).__name__.lower()


@dataclass(frozen=True)
class QueryTiming:
    """When a query straddled, measured on both clocks, and how long it actually took."""

    wall_start: float
    wall_midpoint: float
    wall_end: float
    monotonic_start: float
    monotonic_end: float
    request_seconds: float
    events_skipped: int

    def as_dict(self) -> Dict[str, Any]:
        return {"wall_start_epoch": self.wall_start, "wall_midpoint_epoch": self.wall_midpoint,
                "wall_end_epoch": self.wall_end, "monotonic_start": self.monotonic_start,
                "monotonic_end": self.monotonic_end, "request_seconds": self.request_seconds,
                "events_skipped": self.events_skipped}

    @property
    def monotonic_midpoint(self) -> float:
        """The monotonic counterpart of :attr:`wall_midpoint`.

        Two clocks have to be compared at the same point of a request. Measured from the starts instead, a
        query slower than its predecessor would consume part of the disagreement allowance for a reason
        that has nothing to do with either clock.
        """
        return self.monotonic_start + self.request_seconds / 2.0


@dataclass(frozen=True)
class PollResult:
    status: ObsRecordStatus
    timing: QueryTiming


class RecordingStatusConnection:
    """A persistent, single-threaded, read-only OBS WebSocket connection for ``GetRecordStatus``.

    The socket is opened on the first poll and reused after that. Any transport or protocol failure
    discards it and is raised to the caller, which decides what to do next; the request that failed is
    never resent. Use it as a context manager, or call :meth:`close`, when finished.
    """

    def __init__(self, settings: ObsSettings, *,
                 timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
                 websocket_module: Any = None,
                 monotonic: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time) -> None:
        if not isinstance(settings, ObsSettings):
            raise RecordingCheckpointError("An ObsSettings value is required to reach OBS.")
        self.settings = settings
        self.timeout = _number(timeout, "request timeout", minimum=0.001, exclusive_minimum=True,
                               maximum=MAX_POLL_INTERVAL_SECONDS) or DEFAULT_REQUEST_TIMEOUT_SECONDS
        self._websocket = websocket_module
        self._monotonic = monotonic
        self._wall = wall
        self._socket: Any = None
        self._closed = False
        self.connections = 0
        self.polls = 0
        self.events_skipped = 0
        self.last_error: Optional[str] = None

    # -- lifecycle ---------------------------------------------------------------

    def __enter__(self) -> "RecordingStatusConnection":
        return self

    def __exit__(self, *exc_info) -> bool:
        self.close()
        return False

    @property
    def connected(self) -> bool:
        return self._socket is not None

    def close(self) -> None:
        """Close the socket explicitly. Further polls on this object are refused, not reopened."""
        self._closed = True
        self._discard()

    def _discard(self) -> None:
        socket, self._socket = self._socket, None
        if socket is None:
            return
        try:
            socket.close()
        except Exception:  # a socket we are throwing away cannot improve by failing here
            pass

    def _invalidate(self) -> None:
        # A failed request is never replayed on a socket we no longer trust. The next explicit poll opens
        # a new connection; nothing is resent behind the caller's back.
        self._discard()

    # -- requests ----------------------------------------------------------------

    def observe(self) -> PollResult:
        """Poll OBS once, reusing the connection, and report exactly what that exchange measured."""
        if self._closed:
            raise RecordingStatusError("This OBS status connection is closed.")
        wall_start = float(self._wall())
        monotonic_start = float(self._monotonic())
        deadline = monotonic_start + self.timeout
        try:
            try:
                websocket = self._connect(deadline)
                request_id = str(uuid.uuid4())
                self._send(websocket, {"op": REQUEST_OP, "d": {"requestType": GET_RECORD_STATUS,
                                                               "requestId": request_id,
                                                               "requestData": {}}})
                payload, skipped = self._await_response(websocket, request_id, deadline)
                status = _record_status(payload.get("responseData"))
            except ObsRequestRejected:
                # A refusal is a complete, well-formed exchange: keep the connection, report the failure.
                raise
            except BaseException:
                self._invalidate()
                raise
        except RecordingStatusError as exc:
            self.last_error = type(exc).__name__
            raise
        wall_end = float(self._wall())
        monotonic_end = float(self._monotonic())
        timing = QueryTiming(wall_start=wall_start,
                             wall_midpoint=wall_start + (wall_end - wall_start) / 2.0,
                             wall_end=wall_end,
                             monotonic_start=monotonic_start,
                             monotonic_end=monotonic_end,
                             request_seconds=monotonic_end - monotonic_start,
                             events_skipped=skipped)
        self.polls += 1
        self.last_error = None
        return PollResult(status=status, timing=timing)

    def _module(self) -> Any:
        if self._websocket is not None:
            return self._websocket
        try:  # the same optional adapter ObsClient uses, imported only when a real poll is attempted
            import websocket
        except ImportError as exc:
            raise RecordingStatusTransportError(
                "Install websocket-client to poll OBS recording status.") from exc
        return websocket

    def _connect(self, deadline: float) -> Any:
        if self._socket is not None:
            return self._socket
        remaining = deadline - float(self._monotonic())
        if remaining <= 0:
            raise RecordingStatusTimeout("No request time was left to open the OBS status connection.")
        try:
            websocket = self._module().create_connection(
                "ws://{}:{}".format(self.settings.host, self.settings.port),
                timeout=remaining, subprotocols=["obswebsocket.json"])
        except RecordingStatusError:
            raise
        except Exception as exc:  # the URL stays out of the message: only the failure kind is reportable
            raise RecordingStatusTransportError(
                "The OBS status connection could not be opened (%s)." % type(exc).__name__) from exc
        self.connections += 1
        self._socket = websocket
        hello = self._receive_json(websocket, deadline)
        self._identify(websocket, hello, deadline)
        return websocket

    def _identify(self, websocket: Any, hello: Any, deadline: float) -> None:
        if not isinstance(hello, dict):
            raise RecordingStatusProtocolError("OBS did not send a hello message.")
        data = hello.get("d") or {}
        if not isinstance(data, dict):
            raise RecordingStatusProtocolError("OBS sent a hello message that is not an object.")
        version = data.get("rpcVersion", 1)
        if isinstance(version, bool) or not isinstance(version, int):
            try:
                version = int(str(version).strip())
            except (TypeError, ValueError) as exc:
                raise RecordingStatusProtocolError("OBS reported an unusable rpcVersion.") from exc
        identify = {"rpcVersion": min(version or 1, 1)}
        authentication = data.get("authentication")
        if authentication:
            if not self.settings.password:
                raise RecordingStatusAuthError("OBS WebSocket requires a password.")
            # The existing authentication helper is reused verbatim rather than reimplemented here.
            identify["authentication"] = _auth_response(
                self.settings.password,
                str(authentication.get("salt", "")),
                str(authentication.get("challenge", "")),
            )
        self._send(websocket, {"op": IDENTIFY_OP, "d": identify})
        response = self._receive_json(websocket, deadline)
        if not isinstance(response, dict) or response.get("op") != IDENTIFIED_OP:
            raise RecordingStatusProtocolError("OBS WebSocket identification failed.")

    def _send(self, websocket: Any, payload: Dict[str, Any]) -> None:
        try:
            websocket.send(json.dumps(payload))
        except Exception as exc:
            raise RecordingStatusTransportError(
                "The OBS status request could not be sent (%s)." % type(exc).__name__) from exc

    def _receive(self, websocket: Any, deadline: float) -> Any:
        remaining = deadline - float(self._monotonic())
        if remaining <= 0:
            raise RecordingStatusTimeout("OBS did not answer before the request deadline.")
        setter = getattr(websocket, "settimeout", None)
        if callable(setter):
            # Each receive gets the *remaining* whole-request budget, never a fresh timeout, so an endless
            # stream of unrelated events cannot keep one request alive forever.
            setter(remaining)
        try:
            return websocket.recv()
        except Exception as exc:
            if _timeout_like(exc):
                raise RecordingStatusTimeout("OBS did not answer before the request deadline.") from exc
            raise RecordingStatusTransportError(
                "The OBS status response could not be read (%s)." % type(exc).__name__) from exc

    def _receive_json(self, websocket: Any, deadline: float) -> Any:
        try:
            return json.loads(self._receive(websocket, deadline))
        except ValueError as exc:  # a truncated or non-JSON frame is a protocol failure, never a status
            raise RecordingStatusProtocolError("OBS sent a WebSocket message that is not JSON.") from exc

    def _await_response(self, websocket: Any, request_id: str,
                        deadline: float) -> tuple[Dict[str, Any], int]:
        skipped = 0
        while True:
            message = self._receive_json(websocket, deadline)
            if not isinstance(message, dict):
                raise RecordingStatusProtocolError("OBS sent a WebSocket message that is not an object.")
            if message.get("op") != RESPONSE_OP:
                skipped += 1
                self.events_skipped += 1
                continue
            payload = message.get("d")
            if not isinstance(payload, dict):
                raise RecordingStatusProtocolError("OBS request response carried no payload.")
            if payload.get("requestId") != request_id:
                skipped += 1
                self.events_skipped += 1
                continue
            status = payload.get("requestStatus")
            if not isinstance(status, dict):
                raise RecordingStatusProtocolError("OBS request response carried no requestStatus.")
            if not status.get("result"):
                code = status.get("code")
                raise ObsRequestRejected(code if isinstance(code, int) and not isinstance(code, bool) else None)
            return payload, skipped


@dataclass(frozen=True)
class PauseTransition:
    """A detected pause/resume pair, kept so the resume does not look like an unexplained jump."""

    paused_at_epoch: float
    resumed_at_epoch: float
    duration_at_pause_seconds: float
    source: str

    def as_dict(self) -> Dict[str, Any]:
        return {"paused_at_epoch": self.paused_at_epoch, "resumed_at_epoch": self.resumed_at_epoch,
                "duration_at_pause_seconds": self.duration_at_pause_seconds, "source": self.source}


@dataclass(frozen=True)
class Sample:
    """One poll the collector accepted as an observation, and whether it became a checkpoint row."""

    index: int
    epoch: float
    duration_seconds: float
    active: bool
    paused: bool
    timecode: str
    uncertainty_seconds: Optional[float]
    capture_clock_error_seconds: Optional[float]
    query: QueryTiming
    pause: Optional[PauseTransition] = None
    recorded: bool = True
    skip_reason: Optional[str] = None

    def checkpoint_row(self) -> Dict[str, Any]:
        # Exactly the keys Checkpoint accepts; nothing extra is ever added to a row.
        return {"epoch": self.epoch, "duration_seconds": self.duration_seconds, "active": self.active,
                "paused": self.paused, "uncertainty_seconds": self.uncertainty_seconds}

    def as_dict(self) -> Dict[str, Any]:
        return {"index": self.index, "epoch": self.epoch, "duration_seconds": self.duration_seconds,
                "active": self.active, "paused": self.paused, "obs_timecode": self.timecode,
                "uncertainty_seconds": self.uncertainty_seconds,
                "capture_clock_error_seconds": self.capture_clock_error_seconds,
                "recorded_as_checkpoint": self.recorded, "skip_reason": self.skip_reason,
                "pause_transition": self.pause.as_dict() if self.pause else None,
                "query": self.query.as_dict()}


@dataclass
class CheckpointRun:
    """What one collector run observed, and why it stopped."""

    recording_id: str
    calibration_error_seconds: Optional[float]
    interval_seconds: float
    max_samples: int
    timeout_seconds: float
    clock_discontinuity_tolerance_seconds: float
    samples: List[Sample] = field(default_factory=list)
    poll_failures: List[Dict[str, Any]] = field(default_factory=list)
    clock_compatibility: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None
    status: str = "running"

    def _reconcile_rows(self) -> List[Dict[str, Any]]:
        """The rows to publish: everything recorded, cut back if ``RecordingClock`` would refuse them.

        The collector stops at the first sample that breaks one of RecordingClock's own rules, so nothing is
        normally cut here. The backstop stays because a report nobody can load is worse than a report with
        one fewer anchor: if the two ever disagree, keep the longest prefix the mapper can read, mark the
        rest observations-only, and say so, rather than losing every checkpoint that was observed.
        """
        rows = self.rows
        kept, refusal = _longest_valid_prefix(self.recording_id, rows)
        if kept == len(rows):
            return rows
        seen = 0
        rebuilt: List[Sample] = []
        dropped: List[float] = []
        for sample in self.samples:
            if sample.recorded:
                seen += 1
                if seen > kept:
                    dropped.append(sample.epoch)
                    rebuilt.append(replace(sample, recorded=False,
                                           skip_reason=SKIP_CLOCK_PREFIX + ": " + (refusal or "rejected")))
                    continue
            rebuilt.append(sample)
        self.samples = rebuilt
        self.clock_compatibility = {"accepted_rows": kept, "dropped_rows": len(dropped),
                                    "dropped_epochs": dropped, "reason": refusal,
                                    "meaning": "the rows past this point were not loadable by RecordingClock "
                                               "and are kept as observations only; nothing was bridged"}
        return self.rows

    @property
    def rows(self) -> List[Dict[str, Any]]:
        return [sample.checkpoint_row() for sample in self.samples if sample.recorded]

    @property
    def observations(self) -> List[Dict[str, Any]]:
        return [sample.as_dict() for sample in self.samples]

    def finish(self, reason: str) -> "CheckpointRun":
        self.reason = reason
        self.status = TERMINAL_STATUS_BY_REASON[reason]
        return self

    def document(self) -> Dict[str, Any]:
        rows = self._reconcile_rows()
        limitations = list(BASE_LIMITATIONS)
        if self.clock_compatibility:
            limitations.append("RecordingClock refused the rows past epoch %s of this run, so they were kept "
                               "as observations only and nothing was bridged across them; the collector and "
                               "the clock disagreed and the clock was followed."
                               % (self.clock_compatibility["dropped_epochs"] or ["?"])[0])
        if self.reason == TERMINAL_CONNECTION:
            limitations.append("The run ended on a connection failure, so the last checkpoint is the end of "
                               "what OBS was able to confirm; the remainder of the recording is unobserved.")
        if not rows:
            limitations.append("Zero checkpoint rows: this file records no epoch anchor, so nothing may be "
                               "mapped from it and no anchor may be inferred.")
        return {
            "kind": REPORT_KIND,
            "schema_version": REPORT_SCHEMA_VERSION,
            "recording_id": self.recording_id,
            "recordings": {self.recording_id: rows},
            "terminal": {
                "status": self.status,
                "reason": self.reason,
                "description": TERMINAL_DESCRIPTIONS.get(self.reason, "Collection has not finished."),
                "samples_observed": len(self.samples),
                "checkpoint_rows": len(rows),
                "failed_polls": len(self.poll_failures),
                "poll_failures": [dict(failure) for failure in self.poll_failures],
            },
            "provenance": {
                "generated_by": "scripts/collect_recording_checkpoints.py",
                "checkpoint_rows_are_actual_observations_only": True,
                "clocks": {
                    "epoch": "wall clock sampled at the request midpoint",
                    "request_duration": "monotonic clock",
                    "request_timeout_seconds": self.timeout_seconds,
                    "interval_seconds": self.interval_seconds,
                    "max_samples": self.max_samples,
                    "clock_discontinuity_tolerance_seconds": self.clock_discontinuity_tolerance_seconds,
                },
                "calibration": {
                    "capture_clock_error_seconds": self.calibration_error_seconds,
                    "source": "operator-supplied calibration" if self.calibration_error_seconds is not None
                              else "uncalibrated: default capture-clock uncertainty is None",
                    "rule": "uncertainty_seconds = capture_clock_error_seconds + request_seconds "
                            "(plus the wall time a retained pause could have hidden a resume in)",
                    "pieces_kept": ["capture_clock_error_seconds", "query.request_seconds"],
                },
                "obs": {
                    "request_type": GET_RECORD_STATUS,
                    "record_output": "the main recording output (recordOutputName was omitted)",
                    "duration_unit_as_reported_by_obs": "milliseconds",
                },
                "pause_transitions": [sample.pause.as_dict() for sample in self.samples if sample.pause],
                "skipped_samples": [{"index": sample.index, "reason": sample.skip_reason}
                                    for sample in self.samples if not sample.recorded],
                # Normally null: the collector stops before writing a row RecordingClock would refuse. When
                # it ever disagrees with the clock, the clock wins and this says which rows were held back.
                "mapper_compatibility": self.clock_compatibility,
            },
            "observations": self.observations,
            "limitations": limitations,
        }


def abandoned_run(recording_id: str, *, reason: str = TERMINAL_INTERRUPTED,
                  calibration_error_seconds: Optional[float] = None,
                  interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
                  max_samples: int = DEFAULT_MAX_SAMPLES,
                  timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS) -> CheckpointRun:
    """A finished run that observed nothing, so an early interrupt still leaves a reason on disk.

    Zero rows and an explicit terminal reason: this records that collection did not happen, which is a
    different thing from an absent file, and it must never be mistaken for a usable checkpoint set.
    """
    run = CheckpointRun(recording_id=recording_id, calibration_error_seconds=calibration_error_seconds,
                        interval_seconds=interval_seconds, max_samples=max_samples,
                        timeout_seconds=timeout_seconds,
                        clock_discontinuity_tolerance_seconds=DEFAULT_CLOCK_DISCONTINUITY_TOLERANCE_SECONDS)
    return run.finish(reason)


def collect_recording_checkpoints(connection: RecordingStatusConnection, *,
                                  recording_id: str,
                                  interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
                                  max_samples: int = DEFAULT_MAX_SAMPLES,
                                  calibration_error_seconds: Optional[float] = None,
                                  clock_discontinuity_tolerance_seconds: float =
                                  DEFAULT_CLOCK_DISCONTINUITY_TOLERANCE_SECONDS,
                                  sleep: Callable[[float], None] = time.sleep,
                                  on_sample: Optional[Callable[[CheckpointRun], None]] = None) -> CheckpointRun:
    """Poll OBS until a stop condition, returning every actual observation and the reason it stopped.

    The collector never bridges a gap it did not observe: the first sample that contradicts its neighbour
    ends the run with that contradiction as the recorded reason, and a run that cannot produce a single
    row says so instead of inventing an anchor.
    """
    settings = validate_collection_arguments(interval_seconds=interval_seconds, max_samples=max_samples,
                                             calibration_error_seconds=calibration_error_seconds)
    tolerance = _number(clock_discontinuity_tolerance_seconds, "clock discontinuity tolerance",
                        minimum=0.0, maximum=MAX_POLL_INTERVAL_SECONDS)
    interval = settings["interval_seconds"]
    max_samples = settings["max_samples"]
    calibration = settings["calibration_error_seconds"]
    recording_id = (recording_id or "") if isinstance(recording_id, str) else ""
    if not recording_id.strip():
        raise RecordingCheckpointError("A non-empty recording ID is required for this recording run.")
    run = CheckpointRun(recording_id=recording_id, calibration_error_seconds=calibration,
                        interval_seconds=interval, max_samples=max_samples,
                        timeout_seconds=getattr(connection, "timeout", DEFAULT_REQUEST_TIMEOUT_SECONDS),
                        clock_discontinuity_tolerance_seconds=tolerance)
    previous: Optional[Sample] = None
    recorded: Optional[Sample] = None
    pause_anchor: Optional[Sample] = None

    for index in range(max_samples):
        try:
            poll = connection.observe()
        except KeyboardInterrupt:
            return run.finish(TERMINAL_INTERRUPTED)
        except RecordingStatusError as exc:
            run.poll_failures.append({"attempt": index, "error": type(exc).__name__})
            return run.finish(TERMINAL_CONNECTION)

        status, query = poll.status, poll.timing
        pause = None
        pause_hold_seconds = 0.0
        if not status.paused and pause_anchor is not None:
            # The resume happened somewhere inside an interval we never sampled, so the duration belongs to
            # the anchor at the end of the pause, not to this instant. Widen instead of guessing.
            pause_hold_seconds = max(0.0, query.wall_midpoint - pause_anchor.epoch)
            pause = PauseTransition(pause_anchor.epoch, query.wall_midpoint, pause_anchor.duration_seconds,
                                    "pause_anchor" if pause_hold_seconds > 0 else "previous_sample")

        sample = Sample(
            index=index,
            epoch=query.wall_midpoint,
            duration_seconds=status.duration_seconds,
            active=status.active,
            paused=status.paused and status.active,
            timecode=status.timecode,
            capture_clock_error_seconds=calibration,
            uncertainty_seconds=None if calibration is None
            else calibration + query.request_seconds + pause_hold_seconds,
            query=query,
            pause=pause)

        skip_reason = None
        if recorded is not None and sample.epoch == recorded.epoch:
            # RecordingClock needs strictly increasing epochs; two polls landing inside one clock tick
            # cannot both be anchors, and inventing a microsecond of difference would be fabrication.
            skip_reason = SKIP_DUPLICATE_EPOCH
        elif status.paused and not status.active:
            # A Checkpoint cannot be paused while inactive. Keep the observation, drop the row, stop below.
            skip_reason = SKIP_PAUSED_WHILE_INACTIVE
        if skip_reason is not None:
            sample = replace(sample, recorded=False, skip_reason=skip_reason)

        stop = _contradiction(previous, recorded, sample, tolerance)
        if stop is not None:
            # This sample cannot be trusted alongside the rows already written, so it stays an observation
            # only: a row list the mapper would reject is worse than a run that ended one sample early.
            sample = replace(sample, recorded=False, skip_reason="stop:" + stop)
        run.samples.append(sample)
        previous = sample
        if sample.recorded:
            recorded = sample
        if status.paused:
            if sample.recorded:
                pause_anchor = sample
        else:
            pause_anchor = None
        if on_sample is not None:
            on_sample(run)
        if stop is not None:
            return run.finish(stop)
        if not status.active:
            return run.finish(TERMINAL_INACTIVE)
        if index + 1 < max_samples:
            sleep(interval)

    return run.finish(TERMINAL_COMPLETED)


def _contradiction(previous: Optional[Sample], recorded: Optional[Sample], sample: Sample,
                   tolerance: float) -> Optional[str]:
    """The reason ``sample`` cannot safely continue this run, or None when it can.

    Two baselines are used deliberately. Clock health is judged against the immediately preceding
    observation, because that is the shortest interval anyone actually measured. Row validity is judged
    against the last row that would be written, because that is the sequence ``RecordingClock`` is handed:
    a pair this function accepts must never be one the mapper then refuses to load. When both rows carry a
    calibration the rule below is ``RecordingClock``'s own, copied rather than approximated.
    """
    if previous is not None:
        wall_delta = sample.epoch - previous.epoch
        monotonic_delta = sample.query.monotonic_midpoint - previous.query.monotonic_midpoint
        if monotonic_delta < 0:
            return TERMINAL_DISCONTINUITY
        if wall_delta < 0:
            return TERMINAL_WALL_REVERSAL
        if abs(wall_delta - monotonic_delta) > tolerance:
            return TERMINAL_DISCONTINUITY
    if recorded is None:
        return None
    duration_delta = sample.duration_seconds - recorded.duration_seconds
    if duration_delta < -DURATION_EPSILON_SECONDS:
        return TERMINAL_DURATION_DECREASED
    epoch_delta = sample.epoch - recorded.epoch
    if recorded.uncertainty_seconds is not None and sample.uncertainty_seconds is not None:
        allowance = (epoch_delta + recorded.uncertainty_seconds + sample.uncertainty_seconds + 1e-8)
    else:
        # Uncalibrated rows carry no bound for RecordingClock to weigh, so fall back to the operator
        # tolerance plus the measured width of both queries.
        allowance = (epoch_delta + tolerance + recorded.query.request_seconds
                     + sample.query.request_seconds)
    if duration_delta > allowance:
        return TERMINAL_DURATION_AHEAD
    return None


def _row_keys(row: Dict[str, Any]) -> None:
    """Guard the one contract the mapper cannot forgive: a row is exactly the Checkpoint signature."""
    if set(row) != set(CHECKPOINT_ROW_KEYS):
        raise RecordingCheckpointError("A checkpoint row carried keys other than Checkpoint's own.")


def _clock_accepts(recording_id: str, rows: List[Dict[str, Any]]) -> bool:
    if not rows:
        return False  # RecordingClock refuses an empty clock, so an empty prefix is not "accepted"
    try:
        RecordingClock(recording_id, [Checkpoint(**row) for row in rows])
    except (RecordingClockError, TypeError):
        return False
    return True


def _clock_refusal(recording_id: str, rows: List[Dict[str, Any]]) -> Optional[str]:
    try:
        RecordingClock(recording_id, [Checkpoint(**row) for row in rows])
    except (RecordingClockError, TypeError) as exc:
        return str(exc)
    return None


def _longest_valid_prefix(recording_id: str, rows: List[Dict[str, Any]]) -> tuple[int, Optional[str]]:
    """How many leading rows ``RecordingClock`` accepts, and why it refused if it refused any.

    Every RecordingClock rule concerns a single row or a neighbouring pair, so appending can only ever make
    things worse: the longest accepted prefix is a monotone property and is safe to bisect.
    """
    if not rows or _clock_accepts(recording_id, rows):
        return len(rows), None
    failure = _clock_refusal(recording_id, rows)
    if not _clock_accepts(recording_id, rows[:1]):
        return 0, failure or _clock_refusal(recording_id, rows[:1])
    low, high = 1, len(rows) - 1
    while low < high:
        middle = (low + high + 1) // 2
        if _clock_accepts(recording_id, rows[:middle]):
            low = middle
        else:
            high = middle - 1
    return low, failure


def _publish_aside(staged: Path, destination: Path, attempts: int = 8) -> Path:
    """Hard-link a finished report to a unique sibling name, so a collision destroys neither file."""
    suffix = destination.suffix or ".json"
    last: Optional[BaseException] = None
    for _ in range(attempts):
        candidate = destination.with_name("%s.%s.partial%s"
                                          % (destination.stem, uuid.uuid4().hex[:12], suffix))
        try:
            os.link(staged, candidate)
            return candidate
        except OSError as exc:
            last = exc
    raise RecordingCheckpointError(
        "Could not name a place to keep this run's checkpoints; the finished report is still at %s."
        % staged) from last


def save_checkpoint_report(document: Dict[str, Any], output: Path | str) -> Path:
    """Publish a checkpoint report as a new file, never by truncating one that exists.

    The report is written to a temporary file in the destination directory, fsynced, and then linked into
    place. ``os.link`` refuses an existing destination, so a report created by another process while this
    one was writing loses nothing, which ``os.replace`` would have silently overwritten.

    Once the report is complete on disk, a failure to link it under the requested name must not throw the
    observations away either: it is published under a unique sibling name and reported through
    :class:`CheckpointPublicationConflict`.
    """
    destination = Path(output)
    if destination.exists():
        raise RecordingCheckpointError(
            "Refusing to overwrite an existing checkpoint report: %s" % destination.name)
    rows = document.get("recordings", {}).get(document.get("recording_id"), [])
    for row in rows:
        _row_keys(row)
    if rows:
        # The report has to be readable by scripts/map_overlay_history.py: a row is a Checkpoint signature
        # and the rows together have to be a RecordingClock. Checking here keeps an unusable file unwritten.
        try:
            RecordingClock(document["recording_id"], [Checkpoint(**row) for row in rows])
        except RecordingClockError as exc:
            raise RecordingCheckpointError(
                "The checkpoints in this report are not a usable recording clock: %s" % exc) from exc
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(document, indent=2, allow_nan=False) + "\n"
    staged: Optional[Path] = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=TEMP_PREFIX, suffix=".json", dir=parent)
        staged = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        if staged is not None:
            staged.unlink(missing_ok=True)  # a half-written report is not data worth keeping
        raise
    try:
        os.link(staged, destination)
    except OSError as exc:
        rescued = _publish_aside(staged, destination)
        staged.unlink(missing_ok=True)
        raise CheckpointPublicationConflict(destination, rescued) from exc
    staged.unlink(missing_ok=True)
    return destination
