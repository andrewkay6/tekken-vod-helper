"""The shared attempt ledger: charged before sending, one deadline, never reset by a resume.

The trial's budget is only real if it survives a crash and survives being spread over several commands,
so these tests attack it from both directions: kill the process mid-request and check the attempt is
already paid for; open a second ledger against the same file and check it cannot start a fresh clock or
restore a spent allowance. No socket is opened -- the transport under every client here is a stub.
"""
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tekken_vod_helper import boundary_budget as budget      # noqa: E402
from tekken_vod_helper import boundary_pips as bp            # noqa: E402

MODEL = "qwen3.8-flash-next"


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class GarbageTransport:
    """Replies with something unusable, so the client's own retry loop spends several attempts."""

    def __init__(self):
        self.sent = 0

    def __call__(self, body):
        self.sent += 1
        return {"choices": [{"message": {"content": "not json at all"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def ledger_at(tmp_path, cap=25, minutes=25, clock=None, **kwargs):
    return budget.AttemptLedger.open(tmp_path / "attempts.json", cap=cap, minutes=minutes,
                                     clock=clock or FakeClock(), **kwargs)


def one_frame_run(tmp_path):
    """A prepared single-frame crops run: a frame at the geometry the crops were validated on."""
    frame = tmp_path / "frames" / "frame-0100.jpg"
    frame.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", bp.FRAME_SIZE, "black").save(frame)
    records = bp.build_records([{"frame": str(frame), "timestamp": 100.0, "row_drawn": None,
                                 "pips": {"LEFT": None, "RIGHT": None}}], tmp_path / "crops")
    assert records[0]["crops"]["LEFT"]["path"].endswith("frame-0100-LEFT.png")
    return records


def attached_client(tmp_path, ledger):
    client = bp.PipReadClient(arm="crops", endpoint="http://model.test/v1/chat/completions",
                              model=MODEL, max_requests=20, output_dir=str(tmp_path / "provenance"))
    client._post = GarbageTransport()          # the real transport, and only it, is replaced
    budget.attach(client, ledger)              # the ledger wraps it, charging every attempt
    return client


# ---------------------------------------------------------------- the cap

def test_attempts_are_charged_before_the_request_leaves(tmp_path):
    clock = FakeClock()
    ledger = ledger_at(tmp_path, cap=4, clock=clock)
    assert ledger.used == 0 and ledger.deadline_at is None, "no send yet, so no clock started"
    entry = ledger.reserve(request="frame-0100")
    assert ledger.used == 1 and ledger.remaining == 3
    assert entry["state"] == "in_flight"
    assert ledger.deadline_at == 1000.0 + 25 * 60, "the deadline is stamped on the first send"
    written = json.loads((tmp_path / "attempts.json").read_text())
    assert written["attempts_charged"] == 1, "the charge must be durable before the socket opens"


def test_the_cap_stops_asking_and_names_the_cap_that_did_it(tmp_path):
    ledger = ledger_at(tmp_path, cap=2)
    ledger.reserve(request="frame-0100")
    ledger.reserve(request="frame-0110")
    with pytest.raises(budget.BudgetStopped) as stopped:
        ledger.reserve(request="frame-0120")
    assert stopped.value.reason == "attempt_cap_reached"
    assert ledger.used == 2, "a refusal must not itself look like spending"


def test_every_retry_is_charged_not_just_the_group(tmp_path):
    """The cap counts attempts, because attempts are what the box spends.

    A cap that counted groups would let three unusable replies per frame bill as one, which is how a
    fourteen-request plan quietly becomes forty-two.
    """
    ledger = ledger_at(tmp_path, cap=9)
    client = attached_client(tmp_path, ledger)
    records = one_frame_run(tmp_path)
    with pytest.raises(ValueError):
        client.classify(records)
    assert ledger.used == client.max_attempts == 3, [e["state"] for e in ledger.entries]
    # A reply arrived each time, so the ledger says answered: that it was unusable is the client's
    # validation record in the group file, and the attempt is spent either way.
    assert [e["state"] for e in ledger.entries] == ["answered"] * 3
    assert [e["purpose"] for e in ledger.entries] == ["primary"] * 3


def test_the_ledger_refuses_before_the_transport_is_reached(tmp_path):
    """A retry the ledger will not pay for never reaches the socket."""
    ledger = ledger_at(tmp_path, cap=1)
    client = attached_client(tmp_path, ledger)
    records = one_frame_run(tmp_path)
    with pytest.raises(budget.BudgetStopped):
        client.classify(records)
    assert ledger.used == 1, "the refusal is not a second attempt, and not a refund either"
    assert ledger.entries[0]["state"] == "answered", "the reply arrived; that it was unusable is the " \
                                                     "client's record, not the ledger's"


# ---------------------------------------------------------------- the deadline

def test_one_deadline_covers_every_command(tmp_path):
    clock = FakeClock()
    first = budget.AttemptLedger.open(tmp_path / "attempts.json", cap=10, minutes=5, clock=clock)
    first.reserve(request="frame-0100")
    clock.advance(5 * 60 + 1)
    later = budget.AttemptLedger.open(tmp_path / "attempts.json", cap=10, minutes=5, clock=clock)
    assert later.deadline_at == first.deadline_at, "a second command cannot restart the clock"
    assert later.used == 1, "and it cannot restore what was already spent"
    with pytest.raises(budget.BudgetStopped) as stopped:
        later.reserve(request="frame-0110")
    assert stopped.value.reason == "wall_time_deadline_reached"


def test_a_ledger_written_under_another_budget_is_refused(tmp_path):
    ledger_at(tmp_path, cap=25, minutes=25)
    with pytest.raises(ValueError, match="refusing to run at 14"):
        budget.AttemptLedger.open(tmp_path / "attempts.json", cap=14, minutes=25)
    with pytest.raises(ValueError, match="one clock across every command"):
        budget.AttemptLedger.open(tmp_path / "attempts.json", cap=25, minutes=40)


# ---------------------------------------------------------------- in flight and recovery

def test_an_uncertain_attempt_stays_charged_and_marked(tmp_path):
    ledger = ledger_at(tmp_path)
    entry = ledger.reserve(request="frame-0100")
    ledger.settle(entry, "uncertain", error="connection dropped after the request left")
    assert ledger.used == 1, "an unknown answer is still a spent attempt"
    assert ledger.in_flight == []
    stored = json.loads((tmp_path / "attempts.json").read_text())["entries"][0]
    assert stored["state"] == "uncertain" and "connection dropped" in stored["error"]


def test_a_killed_run_leaves_the_attempt_in_flight_until_the_answer_appears(tmp_path):
    ledger = ledger_at(tmp_path)
    ledger.reserve(request="frame-0100")
    ledger.reserve(request="frame-0110")
    assert len(ledger.in_flight) == 2
    report = ledger.reconcile_in_flight(["frame-0100"])
    assert report["closed"] == ["frame-0100"] and report["unattributable"] == []
    assert [e["request"] for e in ledger.in_flight] == ["frame-0110"]
    assert ledger.used == 2, "reconciling is not refunding"
    assert ledger.remaining == 23


def test_a_retry_satisfied_is_not_credited_to_the_attempt_it_replaced(tmp_path):
    """Request satisfaction and attempt outcome are two facts, and only one of them is provable here.

    The crash case leaves an attempt whose reply never arrived; the resume re-sends the same frame and
    gets an answer. That answer is linked, by the transport wrapper, to the *resume's* attempt -- the
    earlier spend's outcome stays unknown, and it stays charged. Closing it as answered would report a
    run that paid once where the ledger says it paid twice.
    """
    clock = FakeClock()
    ledger = ledger_at(tmp_path, clock=clock)
    hung = ledger.reserve(request="frame-0150")            # the process that died mid-send
    clock.advance(30.0)
    retried = ledger.reserve(request="frame-0150")         # the resume, which is what actually answered
    ledger.settle(retried, "answered", tokens={"completion_tokens": 100})
    report = ledger.reconcile_in_flight({"frame-0150": clock.now})

    assert report["closed"] == [], "an attempt was closed by a reply it never received"
    assert report["unattributable"] == ["frame-0150"]
    assert hung["state"] == "uncertain", "a spend of unknown outcome was written off as answered"
    assert hung["request_satisfied"] is True, "the request itself is answered, and that is a separate fact"
    assert "attempt {}".format(retried["n"]) in hung["reason"]
    assert ledger.used == 2 and ledger.states() == {"answered": 1, "in_flight": 0, "uncertain": 1,
                                                    "failed": 0}
    saved = json.loads((tmp_path / "attempts.json").read_text())
    assert saved["attempts_charged"] == 2 and saved["in_flight"] == 0
    assert saved["uncertain_attempts"] == 1
    assert saved["uncertain_requests"] == [{"n": 1, "reason": "reply_belongs_to_another_attempt",
                                           "request": "frame-0150", "request_satisfied": True}]
    assert saved["requests_satisfied_by_an_answer_file"] == ["frame-0150"]


def test_an_answer_written_before_an_attempt_was_charged_cannot_close_it(tmp_path):
    clock = FakeClock()
    ledger = ledger_at(tmp_path, clock=clock)
    entry = ledger.reserve(request="frame-0160")
    report = ledger.reconcile_in_flight({"frame-0160": clock.now - 60.0})   # older than the charge
    assert report["closed"] == [] and entry["state"] == "uncertain"
    assert entry["outcome_reason"] == "reply_predates_attempt"
    assert ledger.used == 1, "and the attempt is still paid for"


def test_one_answer_file_is_not_shared_between_two_hanging_attempts(tmp_path):
    ledger = ledger_at(tmp_path)
    first = ledger.reserve(request="frame-0170")
    second = ledger.reserve(request="frame-0170")
    report = ledger.reconcile_in_flight({"frame-0170": 1_000.0 + 5})
    assert report["closed"] == []
    assert [e["state"] for e in (first, second)] == ["uncertain", "uncertain"]
    assert [e["outcome_reason"] for e in (first, second)] == ["reply_not_attributable"] * 2
    assert ledger.used == 2


def test_an_interrupted_send_settles_its_own_attempt_as_uncertain(tmp_path):
    """Ctrl-C is a send that stopped, which is a fact this process knows and no later one can rebuild.

    Recorded at the socket rather than reconstructed from an answer file: an entry left ``in_flight`` for
    a dead process is an invitation for whichever run arrives next to close it with somebody else's
    reply.
    """
    ledger = ledger_at(tmp_path)
    client = attached_client(tmp_path, ledger)

    def cutting(body):
        raise KeyboardInterrupt("operator stopped the run")

    client._post = cutting
    with pytest.raises(KeyboardInterrupt):
        budget.attach(client, ledger).classify(one_frame_run(tmp_path))
    entry = ledger.entries[0]
    assert entry["state"] == "uncertain" and entry["reason"] == "interrupted_before_reply"
    assert ledger.used == 1, "the interrupt does not refund the attempt"
    assert ledger.in_flight == []


def test_a_dead_run_ledger_refuses_everything(tmp_path):
    ledger = ledger_at(tmp_path, dry_run=True)
    with pytest.raises(budget.BudgetStopped) as stopped:
        ledger.reserve(request="frame-0100")
    assert stopped.value.reason == "dry_run"


def test_requests_are_named_by_the_frame_they_send():
    assert budget.request_tag({"messages": [{"content": [
        {"type": "text", "text": 'LEFT "x/frame-0286-LEFT.png"'}]}]}) == "frame-0286"
    assert budget.request_tag({"crop": ["x/frame-0100-LEFT.png", "x/frame-0110-LEFT.png"]}) \
        == "frame-0100+frame-0110"
    assert budget.request_tag({"no": "frames"}).startswith("body:")
