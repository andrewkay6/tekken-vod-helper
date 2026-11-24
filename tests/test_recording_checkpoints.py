"""Tests for the persistent OBS status connection and the offline checkpoint collector.

No real OBS, socket or network is used: the WebSocket adapter is injected as a scripted fake and both
clocks are injected functions. Synthetic FFmpeg media is used for the one test that ties a collected
checkpoint document to a measured media timeline.
"""
import hashlib
import importlib
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tekken_vod_helper import recording_checkpoints as ck
from tekken_vod_helper.models import ObsSettings
from tekken_vod_helper.obs_client import ObsRecordStatus, _auth_response
from tekken_vod_helper.recording_clock import Checkpoint, RecordingClock

ROOT = Path(__file__).resolve().parents[1]
MAPPER = ROOT / 'scripts' / 'map_overlay_history.py'
COLLECTOR = ROOT / 'scripts' / 'collect_recording_checkpoints.py'
SALT = 'abcdefghijklmnop'
CHALLENGE = 'zyxwvutsrqponmlk'


class Closed(Exception):
    pass


class Empty(Exception):
    pass


def status_reply(request_id, *, active=True, paused=False, duration_ms=1000, timecode='00:00:01.000'):
    return {'op': ck.RESPONSE_OP, 'd': {'requestId': request_id,
                                        'requestStatus': {'result': True, 'code': 1},
                                        'responseData': {'outputActive': active, 'outputPaused': paused,
                                                         'outputDuration': duration_ms,
                                                         'outputTimecode': timecode}}}


def rejected(request_id, *, code=500, comment='the main output is not available'):
    return {'op': ck.RESPONSE_OP, 'd': {'requestId': request_id,
                                        'requestStatus': {'result': False, 'code': code,
                                                          'comment': comment}}}


def event(name='ChannelName'):
    return {'op': 5, 'd': {'eventType': name, 'eventData': {'channelName': 'Main'}}}


class FakeSocket:
    def __init__(self, obs):
        self.obs = obs
        self.sent = []
        self.timeouts = []
        self.closed = False
        self.inbox = [{'op': 0, 'd': dict(obs.hello)}]

    def send(self, text):
        self.obs.tick()
        message = json.loads(text)
        self.sent.append(message)
        if self.obs.send_failures and self.obs.requests_count in self.obs.send_failures:
            raise self.obs.send_failures[self.obs.requests_count]
        op = message.get('op')
        if op == ck.IDENTIFY_OP:
            self.obs.identifies.append(message['d'])
            self.inbox.append(obs_identify_reply(self.obs))
        elif op == ck.REQUEST_OP:
            payload = message['d']
            self.obs.requests.append(payload)
            self.obs.requests_count += 1
            for item in self.obs.reply_for(len(self.obs.requests) - 1, payload['requestId']):
                self.inbox.append(item)

    def recv(self):
        self.obs.tick()
        if self.obs.malformed_recv:
            return '{not json'
        if self.closed:
            raise Closed('recv on a closed socket')
        if not self.inbox:
            raise Empty('OBS queued nothing')
        item = self.inbox.pop(0)
        if isinstance(item, BaseException):
            raise item
        return json.dumps(item)

    def settimeout(self, value):
        self.obs.tick()
        self.timeouts.append(value)

    def close(self):
        self.obs.tick()
        self.closed = True


def obs_identify_reply(obs):
    return {'op': ck.IDENTIFIED_OP, 'd': {'rpcVersion': 1}} if obs.identify_message is None \
        else obs.identify_message


class FakeObs:
    """Stands in for the websocket-client module: scripted replies, synthetic clocks, no sockets."""

    def __init__(self, script=(), hello=None, step=0.01, connect_failure=None):
        self.now = 0.0
        self.epoch_base = 1_000_000_000.0
        self.step = step
        self.hello = {'rpcVersion': 1} if hello is None else hello
        self.script = list(script)
        self.sockets = []
        self.requests = []
        self.identifies = []
        self.urls = []
        self.connect_attempts = 0
        self.connect_failure = connect_failure
        self.send_failures = {}
        self.identify_message = None
        self.malformed_recv = False
        self.requests_count = 0
        self.wall_drift = 0.0
        self.monotonic_drift = 0.0
        self.wall_drift_at = {}
        self.monotonic_drift_at = {}

    def tick(self, amount=None):
        self.now += self.step if amount is None else amount

    def clock(self):
        return self.now + self.monotonic_drift

    def wall(self):
        return self.epoch_base + self.now + self.wall_drift

    def sleep(self, seconds):
        self.tick(seconds)

    def create_connection(self, url, timeout=None, subprotocols=None):
        self.connect_attempts += 1
        self.tick()
        self.urls.append((url, timeout, subprotocols))
        if self.connect_failure is not None:
            raise self.connect_failure
        socket = FakeSocket(self)
        self.sockets.append(socket)
        return socket

    def reply_for(self, index, request_id):
        self.wall_drift += self.wall_drift_at.get(index, 0.0)
        self.monotonic_drift += self.monotonic_drift_at.get(index, 0.0)
        item = self.script[index] if index < len(self.script) else None
        if item is None:
            return [status_reply(request_id, duration_ms=1000 + 1000 * index)]
        if isinstance(item, BaseException):
            return [item]
        if callable(item):
            return item(request_id)
        if isinstance(item, dict) and 'op' in item:
            return [item]
        if isinstance(item, dict):
            return [status_reply(request_id, **item)]
        return list(item)


def obs_connection(obs, *, password='', timeout=ck.DEFAULT_REQUEST_TIMEOUT_SECONDS):
    settings = ObsSettings(host='unused.invalid', port=34455, password=password)
    return ck.RecordingStatusConnection(settings, timeout=timeout, websocket_module=obs,
                                        monotonic=obs.clock, wall=obs.wall)


def collect_with_obs(obs, *, calibration=None, max_samples=None, interval=1.0, close=True):
    connection = obs_connection(obs)
    try:
        return ck.collect_recording_checkpoints(connection, recording_id='take-01',
                                                interval_seconds=interval,
                                                max_samples=len(obs.script) if max_samples is None
                                                else max_samples,
                                                calibration_error_seconds=calibration,
                                                sleep=obs.sleep)
    finally:
        if close:
            connection.close()


def poll(epoch, duration, *, active=True, paused=False, request=0.02, monotonic=None, timecode=''):
    start = epoch * 0.001 if monotonic is None else monotonic
    return ck.PollResult(status=ObsRecordStatus(active=active, paused=paused, duration_seconds=duration,
                                                timecode=timecode),
                         timing=ck.QueryTiming(wall_start=epoch - request / 2, wall_midpoint=epoch,
                                               wall_end=epoch + request / 2, monotonic_start=start,
                                               monotonic_end=start + request, request_seconds=request,
                                               events_skipped=0))


def sample_from(result, index=0, calibration=None):
    """Wrap a scripted PollResult the way the collector would, to test the save-time backstop alone."""
    status, query = result.status, result.timing
    return ck.Sample(index=index, epoch=query.wall_midpoint, duration_seconds=status.duration_seconds,
                     active=status.active, paused=status.paused, timecode=status.timecode,
                     capture_clock_error_seconds=calibration,
                     uncertainty_seconds=None if calibration is None
                     else calibration + query.request_seconds,
                     query=query)


class FakeConnection:
    """For collector tests that do not need the wire: only observe() is exercised."""

    def __init__(self, polls, timeout=ck.DEFAULT_REQUEST_TIMEOUT_SECONDS):
        self.polls = list(polls)
        self.timeout = timeout
        self.attempts = 0
        self.closed = False

    def observe(self):
        self.attempts += 1
        if self.attempts > len(self.polls):
            raise ck.RecordingStatusTransportError('no more scripted OBS samples')
        item = self.polls[self.attempts - 1]
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closed = True


def collect(polls, *, recording_id='take-01', max_samples=None, calibration=None, sleep=lambda _s: None):
    connection = FakeConnection(polls)
    run = ck.collect_recording_checkpoints(connection, recording_id=recording_id, interval_seconds=0.5,
                                           max_samples=len(polls) if max_samples is None else max_samples,
                                           calibration_error_seconds=calibration, sleep=sleep)
    return run, connection


# -- the persistent connection -----------------------------------------------------------------

def test_one_socket_is_reused_for_every_poll_and_closed_once():
    obs = FakeObs()
    connection = obs_connection(obs)
    try:
        first = connection.observe()
        second = connection.observe()
        assert connection.connections == 1 and len(obs.sockets) == 1
        assert len(obs.requests) == 2 and len(obs.sockets[0].sent) == 3  # identify + two requests
        assert first.status.duration_seconds == 1.0 and second.status.duration_seconds == 2.0
        assert obs.sockets[0].closed is False
        assert connection.polls == 2 and connection.last_error is None
    finally:
        connection.close()
    assert obs.sockets[0].closed is True
    assert connection.connected is False


def test_a_context_manager_closes_the_socket_after_a_failing_poll():
    obs = FakeObs(script=[{'duration_ms': 1000}, RuntimeError('connection reset by peer')])
    connection = obs_connection(obs)
    with pytest.raises(ck.RecordingStatusTransportError):
        with connection:
            connection.observe()
            connection.observe()
    assert all(socket.closed for socket in obs.sockets)
    assert connection.connected is False
    assert connection.last_error == 'RecordingStatusTransportError'


def test_a_closed_connection_refuses_further_polls_without_reopening():
    obs = FakeObs()
    connection = obs_connection(obs)
    connection.observe()
    connection.close()
    with pytest.raises(ck.RecordingStatusError, match='closed'):
        connection.observe()
    assert obs.connect_attempts == 1


def test_authentication_reuses_the_existing_obs_digest():
    obs = FakeObs(hello={'rpcVersion': 1, 'authentication': {'salt': SALT, 'challenge': CHALLENGE}})
    obs_connection(obs, password='hunter2').observe()
    assert obs.identifies[0]['authentication'] == _auth_response('hunter2', SALT, CHALLENGE)
    assert obs.identifies[0]['rpcVersion'] == 1


def test_no_authentication_field_is_sent_when_the_server_does_not_ask():
    obs = FakeObs()
    obs_connection(obs).observe()
    assert 'authentication' not in obs.identifies[0]


def test_a_password_protected_server_without_a_password_is_reported():
    obs = FakeObs(hello={'rpcVersion': 1, 'authentication': {'salt': SALT, 'challenge': CHALLENGE}})
    connection = obs_connection(obs)
    with pytest.raises(ck.RecordingStatusAuthError):
        connection.observe()
    assert connection.connected is False and obs.sockets[0].closed is True
    assert not obs.identifies and not obs.requests  # never identified, so nothing was ever requested


def test_events_and_mismatched_request_ids_are_skipped():
    def reply(request_id):
        return [event(),
                {'op': ck.RESPONSE_OP, 'd': {'requestId': 'someone-elses-request',
                                             'requestStatus': {'result': True, 'code': 1},
                                             'responseData': {'outputActive': True, 'outputPaused': False,
                                                              'outputDuration': 999}}},
                event('SceneCreated'),
                status_reply(request_id, duration_ms=2500)]

    obs = FakeObs(script=[reply])
    result = obs_connection(obs).observe()
    assert result.status.duration_seconds == 2.5  # the mismatched id's 999ms was not used
    assert result.timing.events_skipped == 3


def test_the_whole_request_deadline_bounds_a_stream_of_unrelated_events():
    obs = FakeObs(script=[lambda request_id: [event()] * 200])
    connection = obs_connection(obs, timeout=0.05)
    with pytest.raises(ck.RecordingStatusTimeout):
        connection.observe()
    budgets = obs.sockets[0].timeouts
    assert budgets and max(budgets) <= 0.05
    assert all(second <= first for first, second in zip(budgets, budgets[1:]))
    assert connection.connected is False and obs.sockets[0].closed is True


def test_a_negative_obs_response_is_reported_instead_of_read_as_status():
    obs = FakeObs(script=[lambda request_id: [rejected(request_id, code=604)]])
    connection = obs_connection(obs)
    with pytest.raises(ck.ObsRequestRejected) as failure:
        connection.observe()
    assert failure.value.code == 604
    assert 'not available' not in str(failure.value)  # the server comment is never carried along
    assert connection.polls == 0 and connection.last_error == 'ObsRequestRejected'


def test_a_negative_response_is_not_replayed_and_leaves_the_connection_usable():
    obs = FakeObs(script=[lambda request_id: [rejected(request_id)], {'duration_ms': 4000}])
    connection = obs_connection(obs)
    with pytest.raises(ck.ObsRequestRejected):
        connection.observe()
    assert len(obs.requests) == 1  # the rejected request was never resent
    assert connection.observe().status.duration_seconds == 4.0
    assert connection.connections == 1 and len(obs.sockets) == 1


def test_a_transport_failure_invalidates_the_socket_without_resending_the_request():
    obs = FakeObs(script=[lambda request_id: [event(), RuntimeError('socket is already closed')],
                          {'duration_ms': 7000}])
    connection = obs_connection(obs)
    with pytest.raises(ck.RecordingStatusTransportError):
        connection.observe()
    assert len(obs.requests) == 1 and obs.sockets[0].closed is True
    assert connection.connected is False
    assert connection.observe().status.duration_seconds == 7.0  # an explicit new poll, on a new socket
    assert connection.connections == 2 and len(obs.sockets) == 2
    assert len(obs.requests) == 2


def test_a_failed_identification_is_a_protocol_failure():
    obs = FakeObs()
    obs.identify_message = {'op': 99, 'd': {}}
    connection = obs_connection(obs)
    with pytest.raises(ck.RecordingStatusProtocolError):
        connection.observe()
    assert not obs.requests
    assert connection.connected is False and obs.sockets[0].closed is True


def test_a_malformed_message_is_a_protocol_failure():
    obs = FakeObs()
    obs.malformed_recv = True
    connection = obs_connection(obs)
    with pytest.raises(ck.RecordingStatusProtocolError):
        connection.observe()
    assert connection.connected is False


def test_a_refused_connection_names_no_host_or_port():
    obs = FakeObs(connect_failure=OSError('Connection refused for ws://unused.invalid:34455'))
    connection = obs_connection(obs)
    with pytest.raises(ck.RecordingStatusTransportError) as failure:
        connection.observe()
    assert 'unused.invalid' not in str(failure.value) and '34455' not in str(failure.value)
    assert connection.connected is False


def test_a_missing_duration_is_never_read_as_zero():
    def reply(request_id):
        return [{'op': ck.RESPONSE_OP, 'd': {'requestId': request_id,
                                             'requestStatus': {'result': True, 'code': 1},
                                             'responseData': {'outputActive': True,
                                                              'outputPaused': False}}}]

    connection = obs_connection(FakeObs(script=[reply]))
    with pytest.raises(ck.RecordingStatusProtocolError, match='outputDuration'):
        connection.observe()
    assert connection.polls == 0


@pytest.mark.parametrize('duration', ['soon', -1000, float('nan'), True])
def test_an_unusable_duration_is_a_protocol_failure(duration):
    connection = obs_connection(FakeObs(script=[{'duration_ms': duration}]))
    with pytest.raises(ck.RecordingStatusProtocolError):
        connection.observe()
    assert connection.connected is False


def test_duration_is_obs_reported_milliseconds_without_any_source_divisor():
    obs = FakeObs(script=[{'duration_ms': 12345}])
    result = obs_connection(obs).observe()
    assert result.status.duration_seconds == 12.345
    assert result.status.timecode == '00:00:01.000'
    assert obs.requests[0]['requestType'] == ck.GET_RECORD_STATUS
    assert obs.requests[0]['requestData'] == {}  # the main recording output; no source output is polled


def test_the_package_imports_without_the_websocket_adapter_installed():
    # Only a real poll may need websocket-client, and every test here injects a fake module instead.
    result = subprocess.run([sys.executable, '-c', 'import tekken_vod_helper.recording_checkpoints'],
                            cwd=str(ROOT), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


# -- the collector -----------------------------------------------------------------------------

def test_a_healthy_run_stops_on_the_sample_limit():
    run, connection = collect([poll(100 + index, 10 + index) for index in range(3)])
    assert (run.status, run.reason) == ('completed', ck.TERMINAL_COMPLETED)
    assert [row['epoch'] for row in run.rows] == [100, 101, 102]
    assert connection.attempts == 3


def test_capture_clock_uncertainty_stays_none_without_calibration():
    run, _ = collect([poll(100, 1), poll(101, 2)])
    assert [row['uncertainty_seconds'] for row in run.rows] == [None, None]
    document = run.document()
    assert document['provenance']['calibration']['capture_clock_error_seconds'] is None
    assert any('round-trip' in text for text in document['limitations'])


def test_a_supplied_zero_calibration_is_not_the_same_as_unknown():
    run, _ = collect([poll(100, 1, request=0.04)], calibration=0.0)
    assert run.rows[0]['uncertainty_seconds'] == pytest.approx(0.04)
    observation = run.document()['observations'][0]
    assert observation['capture_clock_error_seconds'] == 0.0
    assert observation['query']['request_seconds'] == pytest.approx(0.04)


def test_calibration_and_query_time_are_combined_conservatively_and_kept_apart():
    run, _ = collect([poll(100, 1, request=0.25)], calibration=0.5)
    assert run.rows[0]['uncertainty_seconds'] == pytest.approx(0.75)
    observation = run.document()['observations'][0]
    assert observation['capture_clock_error_seconds'] == 0.5
    assert observation['query']['request_seconds'] == 0.25


def test_the_epoch_is_the_query_midpoint_and_the_request_time_is_monotonic():
    run, _ = collect([poll(100.0, 5, request=0.2, monotonic=40.0)])
    query = run.document()['observations'][0]['query']
    assert query['wall_midpoint_epoch'] == 100.0
    assert query['wall_start_epoch'] == pytest.approx(99.9)
    assert query['wall_end_epoch'] == pytest.approx(100.1)
    assert query['monotonic_start'] == 40.0 and query['monotonic_end'] == pytest.approx(40.2)
    assert query['request_seconds'] == pytest.approx(0.2)
    assert run.rows[0]['epoch'] == 100.0  # neither the wall instant the request started nor ended


def test_paused_samples_are_retained_and_a_resume_widens_uncertainty():
    run, _ = collect([poll(100, 5), poll(101, 5, paused=True), poll(102, 5, paused=True), poll(103, 5)],
                     calibration=0.0)
    assert [row['paused'] for row in run.rows] == [False, True, True, False]
    assert [row['duration_seconds'] for row in run.rows] == [5, 5, 5, 5]
    pause = run.document()['observations'][3]['pause_transition']
    assert pause['source'] == 'pause_anchor' and pause['paused_at_epoch'] == 102
    # The pause ended somewhere in an interval that was never sampled, so the bound widens instead of
    # pretending the resume instant was observed.
    assert run.rows[3]['uncertainty_seconds'] == pytest.approx(1.02)


def test_an_inactive_recording_stops_the_run_and_is_recorded():
    run, _ = collect([poll(100, 10), poll(101, 11), poll(102, 11, active=False)])
    assert (run.status, run.reason) == ('stopped', ck.TERMINAL_INACTIVE)
    assert [(row['epoch'], row['active']) for row in run.rows] == [(100, True), (101, True), (102, False)]
    assert run.rows[-1]['paused'] is False


def test_a_paused_and_inactive_observation_stays_out_of_the_rows():
    run, _ = collect([poll(100, 10), poll(101, 10, active=False, paused=True)])
    assert run.reason == ck.TERMINAL_INACTIVE
    assert [row['epoch'] for row in run.rows] == [100]
    assert run.document()['provenance']['skipped_samples'] == [{'index': 1,
                                                               'reason': ck.SKIP_PAUSED_WHILE_INACTIVE}]


def test_a_decreasing_duration_stops_as_a_restart_and_is_never_bridged():
    run, _ = collect([poll(100, 40, monotonic=0), poll(101, 41, monotonic=1), poll(102, 1, monotonic=2)])
    assert (run.status, run.reason) == ('stopped', ck.TERMINAL_DURATION_DECREASED)
    assert [row['duration_seconds'] for row in run.rows] == [40, 41]
    assert run.document()['provenance']['skipped_samples'] == [
        {'index': 2, 'reason': 'stop:' + ck.TERMINAL_DURATION_DECREASED}]
    assert any('new recording ID' in text for text in run.document()['limitations'])


def test_a_backward_wall_clock_stops_the_run():
    run, _ = collect([poll(100, 10, monotonic=0), poll(99, 11, monotonic=1)])
    assert (run.status, run.reason) == ('stopped', ck.TERMINAL_WALL_REVERSAL)
    assert [row['epoch'] for row in run.rows] == [100]


def test_wall_time_and_monotonic_time_that_disagree_stop_the_run():
    run, _ = collect([poll(100, 10, monotonic=0), poll(4_600, 11, monotonic=1)])
    assert (run.status, run.reason) == ('stopped', ck.TERMINAL_DISCONTINUITY)
    assert len(run.rows) == 1


def test_a_monotonic_clock_that_rewinds_stops_the_run():
    run, _ = collect([poll(100, 10, monotonic=50), poll(101, 11, monotonic=0)])
    assert (run.status, run.reason) == ('stopped', ck.TERMINAL_DISCONTINUITY)


def test_duration_advancing_faster_than_wall_time_stops_the_run():
    run, _ = collect([poll(100, 10, monotonic=0), poll(101, 600, monotonic=1)])
    assert (run.status, run.reason) == ('stopped', ck.TERMINAL_DURATION_AHEAD)
    assert len(run.rows) == 1


def test_a_connection_failure_keeps_the_checkpoints_actually_observed():
    run, _ = collect([poll(100, 10), poll(101, 11), ck.RecordingStatusTimeout('deadline passed')])
    assert (run.status, run.reason) == ('failed', ck.TERMINAL_CONNECTION)
    assert [row['epoch'] for row in run.rows] == [100, 101]
    terminal = run.document()['terminal']
    assert terminal['checkpoint_rows'] == 2 and terminal['failed_polls'] == 1
    assert terminal['poll_failures'] == [{'attempt': 2, 'error': 'RecordingStatusTimeout'}]
    assert 'deadline passed' not in json.dumps(run.document())  # no wire text is persisted


def test_an_interrupted_run_keeps_the_checkpoints_actually_observed():
    run, _ = collect([poll(100, 10), KeyboardInterrupt()])
    assert (run.status, run.reason) == ('interrupted', ck.TERMINAL_INTERRUPTED)
    assert [row['epoch'] for row in run.rows] == [100]


def test_a_run_with_no_sample_says_there_is_no_anchor():
    run, _ = collect([ck.RecordingStatusTransportError('nothing reached OBS')])
    document = run.document()
    assert document['recordings'] == {'take-01': []}
    assert document['terminal']['checkpoint_rows'] == 0
    assert document['terminal']['samples_observed'] == 0
    assert document['observations'] == []
    assert any('no epoch anchor' in text for text in document['limitations'])


def test_two_polls_inside_one_clock_tick_yield_one_row_and_two_observations():
    run, _ = collect([poll(100, 10, monotonic=0), poll(100, 10, monotonic=0.01)])
    assert len(run.rows) == 1 and len(run.samples) == 2
    assert run.status == 'completed'
    assert run.document()['provenance']['skipped_samples'] == [{'index': 1,
                                                               'reason': ck.SKIP_DUPLICATE_EPOCH}]


def test_a_failed_poll_is_never_an_anchor():
    run, _ = collect([poll(100, 10), ck.ObsRequestRejected(500), poll(101, 11)])
    assert [row['epoch'] for row in run.rows] == [100]
    assert run.reason == ck.TERMINAL_CONNECTION


def test_a_recording_id_is_required():
    with pytest.raises(ck.RecordingCheckpointError, match='recording ID'):
        ck.collect_recording_checkpoints(FakeConnection([]), recording_id='   ')
    with pytest.raises(ck.RecordingCheckpointError):
        ck.collect_recording_checkpoints(FakeConnection([]), recording_id=None)


def test_the_connection_type_is_checked():
    with pytest.raises(ck.RecordingCheckpointError):
        ck.RecordingStatusConnection('localhost')


@pytest.mark.parametrize('kwargs', [
    {'interval_seconds': 0}, {'interval_seconds': -1}, {'interval_seconds': math.nan},
    {'interval_seconds': math.inf}, {'interval_seconds': '1'}, {'max_samples': 0},
    {'max_samples': -3}, {'max_samples': math.inf}, {'max_samples': True}, {'max_samples': 3.5},
    {'max_samples': ck.MAX_SAMPLES_LIMIT + 1}, {'calibration_error_seconds': -0.5},
    {'calibration_error_seconds': math.nan}, {'calibration_error_seconds': math.inf},
    {'calibration_error_seconds': True}, {'calibration_error_seconds': '0.5'},
    {'timeout_seconds': 0}, {'timeout_seconds': math.nan}])
def test_only_finite_positive_arguments_are_accepted(kwargs):
    with pytest.raises(ck.RecordingCheckpointError):
        ck.validate_collection_arguments(**kwargs)


def test_rows_carry_exactly_the_checkpoint_signature_and_build_a_real_clock():
    run, _ = collect([poll(100, 10), poll(101, 10, paused=True), poll(102, 11)], calibration=0.1)
    rows = run.rows
    assert all(set(row) == set(ck.CHECKPOINT_ROW_KEYS) for row in rows)
    clock = RecordingClock('take-01', [Checkpoint(**row) for row in rows])
    mapping = clock.map_epoch(101.5)
    assert mapping['status'] == 'mapped' and mapping['pause_transition'] is True
    # Half a second of wall time past a frozen paused anchor cannot have produced a full second of
    # recording time, so the interval stays below 11 rather than guessing where the resume was.
    assert mapping['lower_seconds'] <= 10.5 <= mapping['upper_seconds']


def test_a_calibrated_duration_jump_stops_instead_of_losing_every_checkpoint(tmp_path):
    # The review's reproduction. With calibration 0, a duration that gains 2.5s over a 1s poll is a pair
    # RecordingClock refuses, so the collector has to stop here: accepting it produced a report that save
    # then rejected, which threw away every checkpoint the run had actually observed.
    run, _ = collect([poll(100, 10.0, request=0.01, monotonic=10.0), poll(101, 11.0, request=0.01,
                                                                          monotonic=11.0),
                      poll(102, 13.5, request=0.01, monotonic=12.0),
                      poll(103, 14.5, request=0.01, monotonic=13.0)], calibration=0.0)
    assert (run.status, run.reason) == ('stopped', ck.TERMINAL_DURATION_AHEAD)
    assert [row['duration_seconds'] for row in run.rows] == [10.0, 11.0]
    path = tmp_path / 'checkpoints.json'
    ck.save_checkpoint_report(run.document(), path)  # must not raise, and must not write nothing
    saved = json.loads(path.read_text())
    assert saved['terminal']['status'] == 'stopped' and saved['terminal']['checkpoint_rows'] == 2
    assert [observation['recorded_as_checkpoint'] for observation in saved['observations']] == [
        True, True, False]  # the contradicting sample stays an observation
    RecordingClock('take-01', [Checkpoint(**row) for row in saved['recordings']['take-01']])


def test_the_clock_is_followed_even_if_the_collector_ever_disagrees_with_it(tmp_path):
    # Defence in depth, forced by hand: rows the collector would have kept but RecordingClock cannot read.
    run = ck.CheckpointRun(recording_id='take-01', calibration_error_seconds=0.0, interval_seconds=1.0,
                           max_samples=3, timeout_seconds=5.0, clock_discontinuity_tolerance_seconds=2.0)
    run.samples = [sample_from(poll(100, 10.0, request=0.01, monotonic=0.0), 0, 0.0),
                   sample_from(poll(101, 11.0, request=0.01, monotonic=1.0), 1, 0.0),
                   sample_from(poll(102, 99.0, request=0.01, monotonic=2.0), 2, 0.0)]
    run.finish(ck.TERMINAL_COMPLETED)
    document = run.document()
    assert len(document['recordings']['take-01']) == 2
    note = document['provenance']['mapper_compatibility']
    assert note['accepted_rows'] == 2 and note['dropped_rows'] == 1
    assert 'faster than wall time' in note['reason']
    assert [o['recorded_as_checkpoint'] for o in document['observations']] == [True, True, False]
    assert document['observations'][2]['skip_reason'].startswith(ck.SKIP_CLOCK_PREFIX)
    assert any('RecordingClock refused' in text for text in document['limitations'])
    path = tmp_path / 'backstop.json'
    ck.save_checkpoint_report(document, path)  # the run is published, not discarded
    assert len(json.loads(path.read_text())['recordings']['take-01']) == 2


def test_a_slower_request_does_not_burn_the_clock_disagreement_tolerance():
    # Wall and monotonic midpoints are compared, so a request four times slower than its predecessor
    # cancels out instead of eating the allowance for a real clock step.
    connection = FakeConnection([poll(100.0, 10.0, request=0.02, monotonic=0.0),
                                 poll(102.0, 12.0, request=2.0, monotonic=1.0)])
    run = ck.collect_recording_checkpoints(connection, recording_id='take-01', interval_seconds=1.0,
                                           max_samples=2, clock_discontinuity_tolerance_seconds=0.5,
                                           sleep=lambda _s: None)
    assert run.reason == ck.TERMINAL_COMPLETED and len(run.rows) == 2
    # A genuine step still shows up once the two clocks are read at the same point of the request.
    connection = FakeConnection([poll(100.0, 10.0, request=0.02, monotonic=0.0),
                                 poll(102.0, 12.0, request=0.02, monotonic=5.0)])
    stepped = ck.collect_recording_checkpoints(connection, recording_id='take-01', interval_seconds=1.0,
                                               max_samples=2, clock_discontinuity_tolerance_seconds=0.5,
                                               sleep=lambda _s: None)
    assert stepped.reason == ck.TERMINAL_DISCONTINUITY


@pytest.mark.parametrize('scenario,polls,calibration', [
    ('steady', [poll(100, 1, request=0.01, monotonic=0), poll(101, 2, request=0.01, monotonic=1)], 0.0),
    ('uncalibrated', [poll(100, 1, request=0.01, monotonic=0), poll(101, 2, request=0.01, monotonic=1)],
     None),
    ('pause', [poll(100, 5, request=0.01, monotonic=0), poll(101, 5, paused=True, request=0.01,
                                                             monotonic=1),
               poll(102, 5, request=0.01, monotonic=2)], 0.0),
    ('jump', [poll(100, 10, request=0.01, monotonic=0), poll(101, 11, request=0.01, monotonic=1),
              poll(102, 13.5, request=0.01, monotonic=2)], 0.0),
    ('restart', [poll(100, 40, request=0.01, monotonic=0), poll(101, 1, request=0.01, monotonic=1)], 0.5),
    ('reversal', [poll(100, 10, request=0.01, monotonic=0), poll(99, 11, request=0.01, monotonic=1)], 0.5),
    ('inactive', [poll(100, 10, request=0.01, monotonic=0),
                  poll(101, 10, active=False, request=0.01, monotonic=1)], 0.25),
    ('duplicate', [poll(100, 10, request=0.01, monotonic=0), poll(100, 10, request=0.01, monotonic=0.5)],
     0.25)])
def test_whatever_a_run_records_is_always_loadable_by_the_clock(tmp_path, scenario, polls, calibration):
    # The contract the mapper depends on, stated for every stop condition rather than one at a time.
    run, _ = collect(polls, calibration=calibration)
    path = tmp_path / (scenario + '.json')
    ck.save_checkpoint_report(run.document(), path)
    rows = json.loads(path.read_text())['recordings']['take-01']
    assert rows == run.rows
    if rows:
        RecordingClock('take-01', [Checkpoint(**row) for row in rows])


def test_the_collected_report_keeps_the_mapper_sections_apart():
    run, _ = collect([poll(100, 10), poll(101, 11)], calibration=0)
    document = run.document()
    assert document['kind'] == ck.REPORT_KIND and document['schema_version'] == 1
    assert set(document) == {'kind', 'schema_version', 'recording_id', 'recordings', 'terminal',
                             'provenance', 'observations', 'limitations'}
    assert 'source_offsets' not in document  # nothing here invents a camera offset
    for row in document['recordings']['take-01']:
        assert set(row) == set(ck.CHECKPOINT_ROW_KEYS)


# -- saving and the CLI --------------------------------------------------------------------------

def test_an_existing_report_is_never_overwritten(tmp_path):
    existing = tmp_path / 'checkpoints.json'
    existing.write_text('original bytes')
    run, _ = collect([poll(100, 10)])
    with pytest.raises(ck.RecordingCheckpointError, match='Refusing to overwrite'):
        ck.save_checkpoint_report(run.document(), existing)
    assert existing.read_text() == 'original bytes'


def test_a_publication_race_keeps_both_reports(tmp_path, monkeypatch):
    run, _ = collect([poll(100, 10), poll(101, 11)])
    destination = tmp_path / 'checkpoints.json'
    original = ck.os.link

    def race(source, target):
        if not destination.exists():
            destination.write_text('another writer')
        return original(source, target)

    monkeypatch.setattr(ck.os, 'link', race)
    with pytest.raises(ck.CheckpointPublicationConflict) as failure:
        ck.save_checkpoint_report(run.document(), destination)
    # The other writer's bytes are untouched, and this run's checkpoints still exist somewhere readable.
    assert destination.read_text() == 'another writer'
    rescued = failure.value.rescued_to
    assert rescued == Path(rescued) and rescued.parent == tmp_path and rescued != destination
    document = json.loads(rescued.read_text())
    assert [row['duration_seconds'] for row in document['recordings']['take-01']] == [10.0, 11.0]
    assert document['terminal']['reason'] == ck.TERMINAL_COMPLETED
    # Nothing is left behind under the staging prefix: the rescue is a real, named report.
    assert not list(tmp_path.glob(ck.TEMP_PREFIX + '*'))


def test_a_rescued_report_is_accepted_by_the_overlay_mapper(tmp_path, monkeypatch):
    run, _ = collect([poll(100, 10), poll(101, 11)], calibration=0.1)
    destination = tmp_path / 'checkpoints.json'
    original = ck.os.link
    monkeypatch.setattr(ck.os, 'link', lambda source, target: (destination.write_text('someone else'),
                                                               original(source, target))[1])
    with pytest.raises(ck.CheckpointPublicationConflict) as failure:
        ck.save_checkpoint_report(run.document(), destination)
    rescued = failure.value.rescued_to
    history = tmp_path / 'history.jsonl'
    history.write_text(json.dumps({'epoch': 100.5}) + '\n')
    result = subprocess.run([sys.executable, str(MAPPER), str(rescued), str(history),
                             str(tmp_path / 'mapped.json'), '--recording-id', 'take-01'],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads((tmp_path / 'mapped.json').read_text())['events'][0]['main']['status'] == 'mapped'


def test_a_cli_publication_conflict_keeps_its_checkpoints_and_fails_loudly(tmp_path, monkeypatch, capsys):
    cli, _ = drive_cli(monkeypatch, FakeObs(script=[{'duration_ms': 1000}, {'duration_ms': 2000}]))
    output = tmp_path / 'take-01.json'
    original = ck.os.link

    def race(source, target):
        if not output.exists():
            output.write_text('another writer')
        return original(source, target)

    monkeypatch.setattr(ck.os, 'link', race)
    code = cli.main(['--output', str(output), '--recording-id', 'take-01', '--max-samples', '2',
                     '--interval', '1'], environ={})
    assert code == 2  # never claim success when the requested report was not written
    rescued = list(tmp_path.glob('take-01.*.partial.json'))
    assert len(rescued) == 1 and output.read_text() == 'another writer'
    assert json.loads(rescued[0].read_text())['terminal']['checkpoint_rows'] == 2
    captured = capsys.readouterr()
    assert 'checkpoints were kept at' in captured.err and rescued[0].name in captured.err
    assert not list(tmp_path.glob(ck.TEMP_PREFIX + '*'))


def test_saving_creates_only_the_requested_file(tmp_path):
    run, _ = collect([poll(100, 10), poll(101, 11)])
    path = tmp_path / 'checkpoints.json'
    ck.save_checkpoint_report(run.document(), path)
    assert json.loads(path.read_text())['recordings']['take-01'][0]['epoch'] == 100
    assert [item.name for item in path.parent.iterdir()] == ['checkpoints.json']


def test_saving_refuses_rows_the_mapper_could_not_read(tmp_path):
    run, _ = collect([poll(100, 10)])
    document = run.document()
    document['recordings']['take-01'][0]['camera_model'] = 'invented'
    with pytest.raises(ck.RecordingCheckpointError, match='Checkpoint'):
        ck.save_checkpoint_report(document, tmp_path / 'out.json')
    assert not (tmp_path / 'out.json').exists()


def load_cli(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    return importlib.import_module('collect_recording_checkpoints')


def drive_cli(monkeypatch, obs):
    """Point the CLI's connection and sleep at the fake OBS so a run is instant and offline."""
    cli = load_cli(monkeypatch)
    built = []

    def build(settings, **kwargs):
        connection = ck.RecordingStatusConnection(settings,
                                                  timeout=kwargs.get('timeout'),
                                                  websocket_module=obs, monotonic=obs.clock,
                                                  wall=obs.wall)
        built.append(connection)
        return connection

    monkeypatch.setattr(cli, 'RecordingStatusConnection', build)
    monkeypatch.setattr(cli, '_pause', obs.sleep)
    return cli, built


def test_the_cli_refuses_an_existing_output_without_touching_obs(tmp_path, monkeypatch):
    cli = load_cli(monkeypatch)
    output = tmp_path / 'checkpoints.json'
    output.write_text('keep me')
    calls = []
    monkeypatch.setattr(cli, 'RecordingStatusConnection', lambda *a, **k: calls.append(a))
    assert cli.main(['--output', str(output), '--recording-id', 'take-01'], environ={}) == 2
    assert not calls
    assert output.read_text() == 'keep me'


@pytest.mark.parametrize('argv', [
    ['--interval', '0'], ['--interval', 'nan'], ['--interval', '-1'], ['--interval', 'inf'],
    ['--max-samples', '0'], ['--max-samples', str(ck.MAX_SAMPLES_LIMIT + 1)],
    ['--calibration-error-seconds', '-1'], ['--calibration-error-seconds', 'nan'],
    ['--calibration-error-seconds', 'inf'], ['--port', '0'], ['--port', '70000'],
    ['--recording-id', '   '], ['--host', '   ']])
def test_the_cli_validates_arguments_before_connecting(tmp_path, monkeypatch, argv):
    cli = load_cli(monkeypatch)
    monkeypatch.setattr(cli, 'RecordingStatusConnection',
                        lambda *a, **k: pytest.fail('the CLI must not connect for bad arguments'))
    assert cli.main(['--output', str(tmp_path / 'out.json'), '--recording-id', 'take-01', *argv],
                    environ={}) == 2
    assert not (tmp_path / 'out.json').exists()


def test_the_password_comes_only_from_the_named_environment_variable(tmp_path, monkeypatch):
    cli = load_cli(monkeypatch)
    obs = FakeObs(script=[{'duration_ms': 1000}])
    cli, built = drive_cli(monkeypatch, obs)
    missing = cli.main(['--output', str(tmp_path / 'a.json'), '--recording-id', 'take-01',
                        '--password-env', 'OBS_PW', '--max-samples', '1', '--interval', '1'],
                       environ={})
    assert missing == 2 and not built and not (tmp_path / 'a.json').exists()
    # An explicitly empty value is the operator's own statement that no password is used.
    empty = cli.main(['--output', str(tmp_path / 'b.json'), '--recording-id', 'take-01',
                      '--password-env', 'OBS_PW', '--max-samples', '1', '--interval', '1'],
                     environ={'OBS_PW': ''})
    assert empty == 0 and built[0].settings.password == ''


def test_the_cli_has_no_password_flag(tmp_path):
    help_text = subprocess.run([sys.executable, str(COLLECTOR), '--help'], capture_output=True, text=True)
    assert help_text.returncode == 0
    assert set(re.findall(r'--password[-\w]*', help_text.stdout)) == {'--password-env'}
    run = subprocess.run([sys.executable, str(COLLECTOR), '--output', str(tmp_path / 'x.json'),
                          '--recording-id', 'take-01', '--password', 'hunter2'],
                         capture_output=True, text=True)
    assert run.returncode == 2 and 'unrecognized' in run.stderr
    assert not (tmp_path / 'x.json').exists()


def test_a_report_holds_no_secrets_or_connection_settings(tmp_path, monkeypatch):
    cli, _ = drive_cli(monkeypatch, FakeObs(
        hello={'rpcVersion': 1, 'authentication': {'salt': SALT, 'challenge': CHALLENGE}},
        script=[{'duration_ms': 1000}, {'duration_ms': 2000}]))
    output = tmp_path / 'take-01.json'
    code = cli.main(['--output', str(output), '--recording-id', 'take-01', '--password-env', 'OBS_PW',
                     '--host', 'unused.invalid', '--port', '34455', '--max-samples', '2',
                     '--interval', '1'], environ={'OBS_PW': 'hunter2'})
    assert code == 0, code
    report = output.read_text()
    for secret in ('hunter2', 'OBS_PW', _auth_response('hunter2', SALT, CHALLENGE), 'unused.invalid',
                   '34455', 'ws://', SALT, CHALLENGE, 'outputActive', 'outputDuration', 'requestStatus',
                   'responseData', 'rpcVersion'):
        assert secret not in report
    assert json.loads(report)['recordings']['take-01'][-1]['duration_seconds'] == 2.0


def test_a_connection_failure_still_publishes_partial_checkpoints(tmp_path, monkeypatch, capsys):
    obs = FakeObs(script=[{'duration_ms': 1000}, {'duration_ms': 2000}, RuntimeError('reset by peer')])
    cli, built = drive_cli(monkeypatch, obs)
    output = tmp_path / 'partial.json'
    code = cli.main(['--output', str(output), '--recording-id', 'take-01', '--max-samples', '50',
                     '--interval', '1', '--calibration-error-seconds', '0'], environ={})
    assert code == 3  # a failed capture is never reported as a clean run, even with usable partials
    terminal = json.loads(output.read_text())['terminal']
    assert terminal['status'] == 'failed' and terminal['reason'] == 'connection_failed'
    assert terminal['checkpoint_rows'] == 2 and terminal['samples_observed'] == 2
    assert terminal['failed_polls'] == 1
    rows = json.loads(output.read_text())['recordings']['take-01']
    assert [row['duration_seconds'] for row in rows] == [1.0, 2.0]
    assert all(row['uncertainty_seconds'] > 0 for row in rows)  # calibrated, so bounded rather than null
    assert all(socket.closed for socket in obs.sockets)
    captured = capsys.readouterr()
    assert 'reset by peer' not in captured.out + captured.err
    assert built[0].polls == 2


def test_an_inactive_recording_leaves_a_usable_partial(tmp_path, monkeypatch):
    cli, _ = drive_cli(monkeypatch, FakeObs(script=[{'duration_ms': 1000},
                                                    {'duration_ms': 1001, 'active': False}]))
    output = tmp_path / 'stopped.json'
    assert cli.main(['--output', str(output), '--recording-id', 'take-01', '--max-samples', '20',
                     '--interval', '1'], environ={}) == 1
    document = json.loads(output.read_text())
    assert document['terminal']['status'] == 'stopped' and document['terminal']['reason'] == 'recording_inactive'
    assert document['recordings']['take-01'][-1] == {'epoch': document['recordings']['take-01'][-1]['epoch'],
                                                     'duration_seconds': 1.001, 'active': False,
                                                     'paused': False, 'uncertainty_seconds': None}


def test_a_calibrated_duration_jump_still_publishes_and_maps(tmp_path, monkeypatch):
    # End to end over the real connection code: a run that has to stop on a duration jump still leaves its
    # observed checkpoints on disk, and that file still feeds the unmodified mapper.
    cli, _ = drive_cli(monkeypatch, FakeObs(script=[{'duration_ms': 10000}, {'duration_ms': 11000},
                                                    {'duration_ms': 13500}, {'duration_ms': 14500}]))
    output = tmp_path / 'jump.json'
    code = cli.main(['--output', str(output), '--recording-id', 'take-01', '--max-samples', '4',
                     '--interval', '1', '--calibration-error-seconds', '0'], environ={})
    assert code == 1  # usable checkpoints written, run stopped early: neither success nor nothing
    document = json.loads(output.read_text())
    assert document['terminal']['status'] == 'stopped'
    assert document['terminal']['reason'] == ck.TERMINAL_DURATION_AHEAD
    rows = document['recordings']['take-01']
    assert [row['duration_seconds'] for row in rows] == [10.0, 11.0]
    assert len(document['observations']) == 3 and document['observations'][2]['recorded_as_checkpoint'] \
        is False
    history = tmp_path / 'history.jsonl'
    history.write_text(json.dumps({'epoch': (rows[0]['epoch'] + rows[1]['epoch']) / 2,
                                   'p1_name': 'Alice'}) + '\n')
    report = tmp_path / 'mapped.json'
    result = subprocess.run([sys.executable, str(MAPPER), str(output), str(history), str(report),
                             '--recording-id', 'take-01'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    mapped = json.loads(report.read_text())['events'][0]['main']
    assert mapped['status'] == 'mapped' and mapped['lower_seconds'] <= 10.5 <= mapped['upper_seconds']


def test_an_interrupt_before_the_first_sample_still_records_its_reason(tmp_path, monkeypatch, capsys):
    cli, _ = drive_cli(monkeypatch, FakeObs())
    calls = []

    def interrupted(*args, **kwargs):
        calls.append(1)
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, 'collect_recording_checkpoints', interrupted)
    output = tmp_path / 'early.json'
    assert cli.main(['--output', str(output), '--recording-id', 'take-01', '--max-samples', '4',
                     '--interval', '1'], environ={}) == 2
    assert calls == [1]
    document = json.loads(output.read_text())  # an outcome is recorded instead of no file at all
    assert document['recordings'] == {'take-01': []}
    assert document['terminal']['status'] == 'interrupted'
    assert document['terminal']['reason'] == ck.TERMINAL_INTERRUPTED
    assert 'No checkpoint row was recorded' in capsys.readouterr().err


def test_an_interrupted_collection_reports_the_checkpoints_it_reached(tmp_path, monkeypatch):
    cli, _ = drive_cli(monkeypatch, FakeObs(script=[{'duration_ms': 1000}, KeyboardInterrupt()]))
    output = tmp_path / 'interrupted.json'
    assert cli.main(['--output', str(output), '--recording-id', 'take-01', '--max-samples', '10',
                     '--interval', '1'], environ={}) == 1
    document = json.loads(output.read_text())
    assert document['terminal']['reason'] == 'interrupted'
    assert document['terminal']['checkpoint_rows'] == 1


def test_a_first_poll_failure_invents_no_anchor(tmp_path, monkeypatch, capsys):
    cli, _ = drive_cli(monkeypatch, FakeObs(script=[RuntimeError('unreachable')]))
    output = tmp_path / 'empty.json'
    assert cli.main(['--output', str(output), '--recording-id', 'take-01', '--max-samples', '5',
                     '--interval', '1'], environ={}) == 2
    document = json.loads(output.read_text())
    assert document['recordings'] == {'take-01': []}
    assert 'no epoch anchor' in json.dumps(document).lower()
    assert 'No checkpoint row was recorded' in capsys.readouterr().err


def test_a_collected_report_is_accepted_by_the_overlay_mapper(tmp_path, monkeypatch):
    cli, built = drive_cli(monkeypatch, FakeObs(script=[{'duration_ms': 1000},
                                                        {'duration_ms': 2000, 'paused': True},
                                                        {'duration_ms': 2000},
                                                        {'duration_ms': 3000}]))
    output = tmp_path / 'mapped-input.json'
    code = cli.main(['--output', str(output), '--recording-id', 'take-01', '--max-samples', '4',
                     '--interval', '1', '--calibration-error-seconds', '0'], environ={})
    assert code == 0, code
    assert built[0].connected is False  # the context manager closed the persistent socket
    rows = json.loads(output.read_text())['recordings']['take-01']
    assert [row['paused'] for row in rows] == [False, True, False, False]
    history = tmp_path / 'history.jsonl'
    events = [{'epoch': rows[1]['epoch'], 'p1_name': 'Alice'}, {'epoch': rows[-1]['epoch'] + 5}]
    history.write_text('\n'.join(json.dumps(event) for event in events) + '\n')
    report = tmp_path / 'mapped.json'
    result = subprocess.run([sys.executable, str(MAPPER), str(output), str(history), str(report),
                             '--recording-id', 'take-01'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    mapped = json.loads(report.read_text())['events']
    assert mapped[0]['main']['status'] == 'paused' and mapped[0]['event'] == events[0]
    assert mapped[0]['main']['estimate_seconds'] == pytest.approx(2.0)
    assert mapped[1]['main']['reason'] == 'outside_checkpoint_range'  # never extrapolated


def test_the_mapper_refuses_a_collection_with_no_checkpoints(tmp_path, monkeypatch):
    cli, _ = drive_cli(monkeypatch, FakeObs(script=[RuntimeError('unreachable')]))
    output = tmp_path / 'empty.json'
    assert cli.main(['--output', str(output), '--recording-id', 'take-01', '--max-samples', '1',
                     '--interval', '1'], environ={}) == 2
    history = tmp_path / 'history.jsonl'
    history.write_text('{"epoch": 1.0}\n')
    result = subprocess.run([sys.executable, str(MAPPER), str(output), str(history),
                             str(tmp_path / 'mapped.json'), '--recording-id', 'take-01'],
                            capture_output=True, text=True)
    assert result.returncode == 1 and 'checkpoint' in result.stderr.lower()
    assert not (tmp_path / 'mapped.json').exists()


def test_running_the_collector_as_a_script_reports_its_usage():
    result = subprocess.run([sys.executable, str(COLLECTOR)], capture_output=True, text=True)
    assert result.returncode == 2 and '--recording-id' in result.stderr


# -- synthetic media ------------------------------------------------------------------------------

def synthetic_clip(directory):
    ffmpeg = shutil.which('ffmpeg')
    ffprobe = shutil.which('ffprobe')
    assert ffmpeg and ffprobe, 'the guest must provide FFmpeg and ffprobe for the media check'
    clip = directory / 'synthetic.mkv'
    encode = subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-y', '-f', 'lavfi',
                             '-i', 'testsrc2=s=128x72:r=30000/1001', '-t', '2', '-c:v', 'libx265',
                             '-x265-params', 'pools=none:frame-threads=1:bframes=0:log-level=error',
                             str(clip)], capture_output=True, text=True)
    assert encode.returncode == 0, encode.stderr
    probe = subprocess.run([ffprobe, '-v', 'error', '-show_entries', 'format=duration',
                            '-show_entries', 'stream=codec_name,r_frame_rate', '-of', 'json', str(clip)],
                           capture_output=True, text=True)
    assert probe.returncode == 0, probe.stderr
    packets = subprocess.run([ffprobe, '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                              'packet=pts', '-of', 'csv=p=0', str(clip)], capture_output=True, text=True)
    assert packets.returncode == 0, packets.stderr
    document = json.loads(probe.stdout)
    document['video_packets'] = len([line for line in packets.stdout.splitlines() if line.strip()])
    return clip, document


def test_a_collected_report_maps_the_middle_of_a_real_synthetic_clip(tmp_path):
    """Ties a collected checkpoint document to a measured media timeline.

    OBS stays faked here, as everywhere in this file. What is real is the encoded clip and its probed
    duration: the check is that rows an operator collects survive the mapper intact and place a mid-clip
    overlay near the middle of the footage, bounded rather than precise.
    """
    clip, probed = synthetic_clip(tmp_path)
    assert probed['streams'][0]['codec_name'] == 'hevc'
    assert probed['streams'][0]['r_frame_rate'] == '30000/1001'
    assert probed['video_packets'] >= 30, 'the synthetic clip has to carry real encoded frames'
    media_seconds = float(probed['format']['duration'])
    digest_before = hashlib.sha256(clip.read_bytes()).hexdigest()

    # The fake OBS session advances its recording duration in lockstep with the clip's measured length.
    step = media_seconds / 3
    obs = FakeObs(script=[{'duration_ms': round(index * step * 1000)} for index in range(4)])
    run = collect_with_obs(obs, calibration=0.25, interval=step)
    assert run.status == 'completed' and len(run.rows) == 4

    report = tmp_path / 'checkpoints.json'
    ck.save_checkpoint_report(run.document(), report)
    rows = run.rows
    assert rows[0]['uncertainty_seconds'] > 0.25  # calibration plus the measured query width

    middle = tmp_path / 'history.jsonl'
    middle.write_text(json.dumps({'epoch': (rows[0]['epoch'] + rows[-1]['epoch']) / 2,
                                 'note': 'mid-clip overlay'}) + '\n')
    mapped = tmp_path / 'mapped.json'
    result = subprocess.run([sys.executable, str(MAPPER), str(report), str(middle), str(mapped),
                             '--recording-id', 'take-01'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    event = json.loads(mapped.read_text())['events'][0]['main']
    assert event['status'] == 'mapped' and event['provenance'] == 'estimated_from_checkpoints'
    assert event['lower_seconds'] <= media_seconds / 2 <= event['upper_seconds']
    assert event['estimate_seconds'] == pytest.approx(media_seconds / 2, abs=0.1)
    assert event['uncertainty_seconds'] > 0
    # The collector never opened or rewrote the footage: a checkpoint is an OBS observation, not a media edit.
    assert hashlib.sha256(clip.read_bytes()).hexdigest() == digest_before
