"""Bounded retries when the vision model returns something unusable.

Motivated by the BB9 run: on the 11th batch Flash Next emitted a duplicated '{' inside the
array, json.loads failed, and the whole detection run died 15 minutes in with nothing
retrying it. Roughly 1 call in 11 cannot survive a 174-call pass without retries.
"""
import json
import re
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest

from tekken_vod_helper.boundary_media import VisionClient

GOOD_ROW = ('{"id": 1, "state": "gameplay", "confidence": 0.9, "player1": "", "player2": "", '
            '"character1": "", "character2": "", "evidence": ""}')
GOOD = "[" + GOOD_ROW + "]"
# The real BB9 shape: a valid array with one duplicated '{' inside a later element, so the
# first json.loads fails and _parse_output's "[...]" fallback re-raises the same raw error.
BAD = ('[\n  {\n    "id": 1, "state": "gameplay", "confidence": 0.9, "player1": "", "player2": "", '
       '"character1": "", "character2": "", "evidence": ""\n  },\n  {\n    {\n    "id": 2\n  }\n]')
NOT_JSON = "I cannot classify these frames."


def _payload(content):
    return json.dumps({"choices": [{"message": {"content": content}}], "usage": {}}).encode()


def _responder(contents):
    """Stand in for urllib.request.urlopen, replying with each content in turn."""
    state = {"n": 0}

    def _open(request, timeout=None):
        i = min(state["n"], len(contents) - 1)
        state["n"] += 1
        if isinstance(contents[i], Exception):
            raise contents[i]
        resp = mock.Mock()
        resp.read.return_value = _payload(contents[i])
        cm = mock.MagicMock()
        cm.__enter__ = mock.Mock(return_value=resp)
        cm.__exit__ = mock.Mock(return_value=False)
        return cm

    _open.state = state
    return _open


def _frames(n=1):
    handle = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    handle.write(b"\xff\xd8\xff\xd9")
    handle.close()
    return [{"id": i + 1, "timestamp": float(i * 5), "path": handle.name} for i in range(n)]


def _client(tmp_path, **kw):
    opts = {"endpoint": "http://model.invalid", "model": "test-model", "max_requests": 10,
            "output_dir": str(tmp_path)}
    opts.update(kw)
    return VisionClient(**opts)


def _batches(tmp_path):
    return sorted((Path(tmp_path) / "batches").glob("*.json"))


def test_a_resume_cannot_overwrite_an_earlier_attempts_provenance(tmp_path):
    """Two processes, one output directory, both starting at call 1: every record has to survive.

    The filename used to be just the process's call counter, so resuming a run destroyed the first
    attempt recorded by the original process -- an attempt that was sent, answered and charged, and then
    left no trace. Names carry the request fingerprint now, so only a genuine re-ask of the identical
    request can collide, and that is the case the reuse rules already refuse to re-send.
    """
    def frame(index):
        handle = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        handle.write(b"\xff\xd8\xff\xd9")
        handle.close()
        # id 1 every time, because that is what the canned reply answers: what differs is the frame, and
        # therefore the request body, which is exactly what the old filename ignored.
        return [{"id": 1, "timestamp": float(index) * 5.0, "path": handle.name}]

    for index in (1, 2):
        with mock.patch("urllib.request.urlopen", side_effect=_responder([GOOD])):
            _client(tmp_path).classify(frame(index))          # first process: calls 1 and 2
    with mock.patch("urllib.request.urlopen", side_effect=_responder([GOOD])):
        _client(tmp_path).classify(frame(3))                  # "resume": its call 1 again
    files = _batches(tmp_path)
    assert len(files) == 3, [p.name for p in files]
    bodies = [json.dumps(json.loads(p.read_text(encoding="utf-8"))["request"], sort_keys=True,
                         default=str) for p in files]
    assert len(set(bodies)) == 3, "two different requests shared one provenance record"


def test_malformed_output_is_retried_then_accepted(tmp_path):
    responder = _responder([BAD, GOOD])
    client = _client(tmp_path, max_attempts=3)
    with mock.patch("urllib.request.urlopen", side_effect=responder):
        results = client.classify(_frames())
    assert len(results) == 1 and results[0]["state"] == "gameplay"
    assert client.calls == 2 and responder.state["n"] == 2

    files = _batches(tmp_path)
    assert len(files) == 2
    first = json.loads(files[0].read_text(encoding="utf-8"))
    second = json.loads(files[1].read_text(encoding="utf-8"))
    assert first["results"] == [] and "error" in first and first["attempt"] == 1
    # The failed reply's raw text survives, which is how this got diagnosed.
    assert first["response"]["choices"][0]["message"]["content"] == BAD
    assert second["results"] and "error" not in second and second["attempt"] == 2


def test_exhausted_attempts_raise_without_fabricating_results(tmp_path):
    responder = _responder([BAD])
    client = _client(tmp_path, max_attempts=3)
    with mock.patch("urllib.request.urlopen", side_effect=responder):
        with pytest.raises(ValueError):
            client.classify(_frames())
    assert client.calls == 3 and responder.state["n"] == 3
    files = _batches(tmp_path)
    assert len(files) == 3
    for path in files:
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["results"] == [] and record["error"]


def test_single_attempt_keeps_the_old_bail_out_behaviour(tmp_path):
    responder = _responder([BAD])
    client = _client(tmp_path, max_attempts=1)
    with mock.patch("urllib.request.urlopen", side_effect=responder):
        with pytest.raises(ValueError):
            client.classify(_frames())
    assert client.calls == 1 and responder.state["n"] == 1


def test_retries_are_still_bounded_by_max_requests(tmp_path):
    responder = _responder([BAD])
    client = _client(tmp_path, max_attempts=5, max_requests=2)
    with mock.patch("urllib.request.urlopen", side_effect=responder):
        with pytest.raises(ValueError) as exc:
            client.classify(_frames())
    assert "Budget exhausted" in str(exc.value)
    assert client.calls == 2 and responder.state["n"] == 2


def test_budget_exhaustion_does_not_lose_the_model_error(tmp_path):
    responder = _responder([BAD])
    client = _client(tmp_path, max_attempts=5, max_requests=1)
    with mock.patch("urllib.request.urlopen", side_effect=responder):
        with pytest.raises(ValueError) as exc:
            client.classify(_frames())
    message = str(exc.value)
    assert "Budget exhausted" in message
    # The parse failure is the actionable half; swallowing it sends the next reader hunting
    # for a budget problem that was only the symptom.
    assert "Expecting property name" in message


def test_output_with_no_json_array_at_all_reports_that_clearly(tmp_path):
    responder = _responder([NOT_JSON])
    client = _client(tmp_path, max_attempts=2)
    with mock.patch("urllib.request.urlopen", side_effect=responder):
        with pytest.raises(ValueError) as exc:
            client.classify(_frames())
    assert "Model output is not valid JSON" in str(exc.value)
    assert client.calls == 2


def test_transient_request_failure_is_retried(tmp_path):
    responder = _responder([OSError("connection reset"), GOOD])
    client = _client(tmp_path, max_attempts=3)
    with mock.patch("urllib.request.urlopen", side_effect=responder):
        results = client.classify(_frames())
    assert len(results) == 1
    assert client.calls == 2


def test_invalid_max_attempts_rejected(tmp_path):
    client = _client(tmp_path, max_attempts=0)
    with pytest.raises(ValueError):
        client.classify(_frames())


def test_empty_batch_needs_no_request(tmp_path):
    responder = _responder([GOOD])
    client = _client(tmp_path)
    with mock.patch("urllib.request.urlopen", side_effect=responder):
        assert client.classify([]) == []
    assert client.calls == 0 and responder.state["n"] == 0


def _raiser(factory):
    """Stand in for urlopen, failing before any answer arrives."""
    state = {"n": 0}

    def _open(request, timeout=None):
        state["n"] += 1
        raise factory()

    _open.state = state
    return _open


def test_a_socket_timeout_is_never_retried(tmp_path):
    """The request left and the answer is unknown, so a second one queues behind the first.

    On a single-purpose inference box the outstanding generation owns the machine, so retrying a
    timeout is not cheap insurance -- it doubles the wait for nobody's benefit.
    """
    import socket
    opener = _raiser(lambda: socket.timeout("timed out"))
    with mock.patch("urllib.request.urlopen", opener):
        with pytest.raises(ValueError) as caught:
            _client(tmp_path, max_attempts=3).classify(_frames(2))
    assert opener.state["n"] == 1, "a timeout must cost exactly one request"
    assert "timed out" in str(caught.value)


def test_a_deadline_exceeded_after_sending_is_never_retried(tmp_path):
    opener = _raiser(TimeoutError)
    with mock.patch("urllib.request.urlopen", opener):
        with pytest.raises(ValueError):
            _client(tmp_path, max_attempts=3).classify(_frames(2))
    assert opener.state["n"] == 1


def test_a_connection_refused_is_retried_because_nothing_could_be_running(tmp_path):
    """Distinct from a timeout: nothing was ever listening, so no work can be outstanding."""
    import urllib.error
    opener = _raiser(lambda: urllib.error.URLError(ConnectionRefusedError("refused")))
    with mock.patch("urllib.request.urlopen", opener):
        with pytest.raises(ValueError):
            _client(tmp_path, max_attempts=3).classify(_frames(2))
    assert opener.state["n"] == 3


def test_a_transport_uncertain_failure_is_persisted_before_it_raises(tmp_path):
    """The stopped run still has to leave a record of the request it could not resolve."""
    import socket
    client = _client(tmp_path, max_attempts=2)
    with mock.patch("urllib.request.urlopen", _raiser(socket.timeout)):
        with pytest.raises(ValueError):
            client.classify(_frames(1))
    saved = list(Path(tmp_path, "batches").glob("batch-*.json"))
    assert saved, "an unresolved request must not vanish from the provenance"
    record = json.loads(saved[0].read_text(encoding="utf-8"))
    assert record["error"] and "timed out" in record["error"].lower()


def test_the_two_failure_classes_are_distinguishable_to_callers(tmp_path):
    """Retrying is a decision the caller has to be able to make on evidence, not on string matching."""
    import socket
    from tekken_vod_helper.boundary_media import TransportUncertain
    with mock.patch("urllib.request.urlopen", _raiser(socket.timeout)):
        with pytest.raises(TransportUncertain):
            _client(tmp_path).classify(_frames(1))
    with mock.patch("urllib.request.urlopen", _raiser(ValueError("bad body"))):
        with pytest.raises(ValueError) as caught:
            _client(tmp_path).classify(_frames(1))
    assert not isinstance(caught.value, TransportUncertain)


# --------------------------------------------------- provenance across repeated failed-request resumes

def _replay(tmp_path, contents, frames=None):
    """One process's whole life: a fresh client, one request, ``len(contents)`` attempts at it.

    ``frames`` is built once by the caller and passed every time, because that is what a resume is: the
    same prepared images, the same request body, a brand-new call counter and attempt counter.
    """
    with mock.patch("urllib.request.urlopen", side_effect=_responder(contents)):
        client = _client(tmp_path, max_attempts=len(contents))
        try:
            client.classify(frames if frames is not None else _frames())
            return "answered"
        except Exception:
            return "failed"


def test_a_request_that_fails_again_after_a_resume_keeps_both_failures(tmp_path):
    """The request, not the process, owns a provenance record -- so a resume cannot overwrite one.

    Naming a record by the request *and* the attempt number fixed the first clobbering bug, and left a
    second one behind: a resume restarts the attempt counter, so a request that failed in the old process
    and fails again in the new one writes to exactly the name the old failure already used. Four charged
    attempts produced two records, and the two earlier errors -- the evidence for why the run stopped
    once -- were gone. The stage-3 review asked for this case specifically, because it is the one that
    only happens when a run is already going badly.
    """
    one_request = _frames()
    assert _replay(tmp_path, [BAD, BAD], one_request) == "failed"     # process 1: two unusable replies
    assert _replay(tmp_path, [BAD], one_request) == "failed"          # resume: unusable again
    assert _replay(tmp_path, [GOOD], one_request) == "answered"       # second resume: finally answered
    files = _batches(tmp_path)
    assert len(files) == 4, "4 charged attempts left %d record(s): %s" % (
        len(files), [p.name for p in files])
    records = [json.loads(p.read_text(encoding="utf-8")) for p in files]
    assert sum(1 for r in records if "error" in r) == 3, "every failed attempt has to stay readable"
    assert sum(1 for r in records if "error" not in r) == 1
    # the slot counts resumes of *one* attempt number: two processes both failing at attempt 1 leave
    # ``...-a1.json`` and ``...-a1-r2.json``, while the retry inside the first process keeps its own
    # ``...-a2.json``. Strip the resume suffix and exactly two request-attempt names remain.
    def stem(path):
        return re.sub(r"-r\d+$", "", path.name[:-len(".json")])

    assert len({stem(p) for p in files}) == 2, "three resumes of one attempt share one request name"
    assert {stem(p).rsplit("-", 1)[1] for p in files} == {"a1", "a2"}
    assert sorted(r["provenance_slot"] for r in records) == [1, 1, 2, 3]
    assert len({(r["attempt"], r["provenance_slot"]) for r in records}) == 4
    # the attempt number alone cannot tell two processes apart, so the records keep both fields
    assert [r["attempt"] for r in records].count(1) == 3


def test_a_resume_that_answers_still_keeps_the_unanswered_attempt_of_the_run_it_resumes(tmp_path):
    """Success must not launder the failure that came before it in the same request's history."""
    one_request = _frames()
    assert _replay(tmp_path, [BAD, GOOD], one_request) == "answered"
    files = _batches(tmp_path)
    assert len(files) == 2
    records = [json.loads(p.read_text(encoding="utf-8")) for p in files]
    assert "error" in records[0] and "error" not in records[1]
    assert records[1]["results"], "the answered attempt has to carry the answer that was kept"
    # and a third process asking the identical request cannot be stopped by the first two names existing
    assert _replay(tmp_path, [GOOD], one_request) == "answered"
    assert len(_batches(tmp_path)) == 3


def test_writers_are_told_apart_so_a_shared_call_number_is_ambiguous_no_longer(tmp_path):
    """``call`` restarts at 1 in every process; the pid and slot together say which write this was."""
    one_request = _frames()
    _replay(tmp_path, [BAD], one_request)
    _replay(tmp_path, [GOOD], one_request)
    records = [json.loads(p.read_text(encoding="utf-8")) for p in _batches(tmp_path)]
    assert all(r["call"] == 1 for r in records), "the test replays two first calls on purpose"
    assert len({(r["writer_pid"], r["provenance_slot"]) for r in records}) == 2
