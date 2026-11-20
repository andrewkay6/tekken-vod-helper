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


def test_an_answer_that_cannot_name_its_attempt_is_never_credited(tmp_path):
    """A cached reply from an earlier run proves the request was answered, not which attempt did it.

    This guard used to be a timestamp comparison -- "the reply on disk predates this attempt" -- and it was
    the mechanism that broke the migration: on this project's ext4 storage an inode mtime is quantised to a
    couple of milliseconds and trails ``time.time()`` by about as much, so a reply written *after* a charge
    can carry an mtime *before* it, and every legitimately resumed attempt was declared unanswerable. The
    proof is now the receipt the transport stores with the answer; a store that has none (an older answer
    file, or one written by hand) is treated as exactly what it is -- no evidence about the attempt -- and
    is left uncertain rather than guessed either way.
    """
    ledger = ledger_at(tmp_path)
    entry = ledger.reserve(request="frame-0160")
    report = ledger.reconcile_in_flight({"frame-0160": {"receipt": None, "mtime": 1.0}})
    assert report["closed"] == [] and entry["state"] == "uncertain"
    assert entry["outcome_reason"] == "answer_without_attempt_receipt"
    assert entry["request_satisfied"] is True, "the request really is answered, and that is separate"
    assert ledger.used == 1, "and the attempt is still paid for"


def test_a_receipt_naming_the_attempt_closes_it_even_when_the_clock_disagrees(tmp_path):
    """The devbox regression: mtime a few milliseconds older than charged_at, reply genuinely from us."""
    ledger = ledger_at(tmp_path)
    entry = ledger.reserve(request="frame-0165")
    receipt = {"attempt_id": entry["attempt_id"], "request": "frame-0165", "n": entry["n"],
               "run_id": ledger.run_id, "ledger": str(ledger.path)}
    stale_mtime = entry["charged_at"] - 0.0035          # exactly the sign the migration produced
    report = ledger.reconcile_in_flight({"frame-0165": {"receipt": receipt, "mtime": stale_mtime}})
    assert report["closed"] == ["frame-0165"]
    assert entry["state"] == "answered" and entry["answer_attributed_by"] == "attempt_receipt"
    assert ledger.used == 1 and not ledger.in_flight


def test_a_receipt_naming_another_attempt_still_refuses_the_credit(tmp_path):
    ledger = ledger_at(tmp_path)
    hung = ledger.reserve(request="frame-0166")
    retried = ledger.reserve(request="frame-0166")
    receipt = {"attempt_id": retried["attempt_id"], "request": "frame-0166", "n": retried["n"],
               "run_id": ledger.run_id, "ledger": str(ledger.path)}
    report = ledger.reconcile_in_flight({"frame-0166": {"receipt": receipt, "mtime": None}})
    # The receipt closes exactly the attempt it names, and no other. The earlier spend of the same request
    # stays unknown, which is the pair of facts a reader needs: the request was answered once, two attempts
    # paid for it, and nothing was refunded or re-sent.
    assert report["closed"] == ["frame-0166"] and report["unattributable"] == ["frame-0166"]
    assert retried["state"] == "answered" and hung["state"] == "uncertain"
    assert hung["outcome_reason"] == "reply_belongs_to_another_attempt"
    assert retried["attempt_id"] in hung["reason"]
    assert ledger.used == 2, "the unattributed spend is charged, never refunded"


def test_two_runs_of_one_request_never_answer_each_other(tmp_path):
    """``frame-0100#1`` is a per-ledger name, so it cannot be the whole of an attempt's identity.

    Two runs over the same recording charge the same request, and each calls its first attempt ``#1``. The
    answer one of them got is that run's spend explained and the other's is not; before the run id existed
    the second ledger read the first one's receipt as proof its own hanging attempt had been answered, and
    reported a reply that never came back to it.
    """
    clock = FakeClock()
    a = budget.AttemptLedger.open(tmp_path / "run-a.json", cap=4, minutes=5, clock=clock)
    b = budget.AttemptLedger.open(tmp_path / "run-b.json", cap=4, minutes=5, clock=clock)
    entry_a = a.reserve(request="frame-0100")
    entry_b = b.reserve(request="frame-0100")
    assert entry_a["attempt_id"] == entry_b["attempt_id"], "the collision is in the naming, not a mistake"
    assert entry_a["run_id"] != entry_b["run_id"], "and the run id is what tells them apart"

    receipt_a = {"attempt_id": entry_a["attempt_id"], "request": "frame-0100", "n": entry_a["n"],
                 "run_id": a.run_id, "ledger": str(a.path)}
    closed = a.reconcile_in_flight({"frame-0100": {"receipt": receipt_a, "mtime": None}})
    assert closed["closed"] == ["frame-0100"], "a run recognises the reply it paid for"
    refused = b.reconcile_in_flight({"frame-0100": {"receipt": receipt_a, "mtime": None}})
    assert refused["closed"] == [], "another run's reply cannot close this one's attempt"
    assert entry_b["state"] == "uncertain"
    assert entry_b["outcome_reason"] == "reply_belongs_to_another_run"
    assert entry_b["request_satisfied"] is True, "the request was answered once, by the other run"
    assert b.used == 1 and b.remaining == 3, "refusing the credit does not refund the spend"


def test_a_relocated_ledger_still_recognises_its_own_answers(tmp_path):
    """Identity has to travel with the file, or a migration turns every answered attempt into a lie.

    This project has moved machines once and reads its own history through legacy symlinks, so the path a
    receipt was written under is information, not identity. The run id is persisted in the ledger, and a
    copy of a run's directory reconciles exactly as the original did.
    """
    ledger = ledger_at(tmp_path)
    entry = ledger.reserve(request="frame-0120")
    receipt = {"attempt_id": entry["attempt_id"], "request": "frame-0120", "n": entry["n"],
               "run_id": ledger.run_id, "ledger": str(ledger.path)}
    moved = tmp_path / "moved-attempts.json"
    moved.write_text((tmp_path / "attempts.json").read_text(), encoding="utf-8")

    reopened = budget.AttemptLedger.open(moved, cap=25, minutes=25, clock=FakeClock())
    assert reopened.run_id == ledger.run_id, "the run is named in the file, not by where it sits"
    hanging = reopened.entries[0]
    assert hanging["state"] == "in_flight"
    report = reopened.reconcile_in_flight({"frame-0120": {"receipt": receipt, "mtime": None}})
    assert report["closed"] == ["frame-0120"]
    assert hanging["answer_attributed_by"] == "attempt_receipt"


def test_a_ledger_written_before_runs_were_named_still_closes_its_own_attempt(tmp_path):
    """The conservative legacy rule, kept on purpose.

    Old answer files carry an attempt id and no run, and there is no identity to check them against -- a
    ledger written before this fix has never claimed one. Refusing them would re-create the migration
    failure the receipt was invented to end, so an id naming this ledger's own hanging attempt still
    closes it, while a receipt that *does* claim a run and names the wrong one never does.
    """
    ledger = ledger_at(tmp_path)
    entry = ledger.reserve(request="frame-0130")
    stored = json.loads((tmp_path / "attempts.json").read_text())
    stored.pop("run_id")
    stored["entries"] = [{key: value for key, value in row.items() if key != "run_id"}
                         for row in stored["entries"]]
    (tmp_path / "attempts.json").write_text(json.dumps(stored), encoding="utf-8")

    reopened = ledger_at(tmp_path)
    assert reopened.run_id == budget.LEGACY_RUN_ID
    legacy_entry = reopened.entries[0]
    assert legacy_entry["attempt_id"] == entry["attempt_id"]
    legacy_receipt = {"attempt_id": entry["attempt_id"], "request": "frame-0130", "n": entry["n"],
                      "ledger": str(reopened.path)}
    report = reopened.reconcile_in_flight({"frame-0130": {"receipt": legacy_receipt, "mtime": None}})
    assert report["closed"] == ["frame-0130"], "a run-less receipt is judged as well as it can be"

    forged = dict(legacy_receipt, run_id="0123456789ab")
    second = reopened.reserve(request="frame-0131")
    refused = reopened.reconcile_in_flight({"frame-0131": {"receipt": dict(forged, request="frame-0131",
                                                                          attempt_id=second["attempt_id"]),
                                                           "mtime": None}})
    assert refused["closed"] == [], "but a receipt that claims another run is refused outright"
    assert second["outcome_reason"] == "reply_belongs_to_another_run"


def test_the_charged_attempt_carries_the_receipt_the_answer_file_stores(tmp_path):
    """The two halves of the receipt are written by one code path, so a resume can join them by name.

    An answer file that names no attempt is worthless for attribution, and a ledger entry that names no
    receipt cannot be checked against one; this asserts both are produced by the same ``attach`` call,
    which is what makes the reconciliation above a lookup rather than a guess.
    """
    ledger = ledger_at(tmp_path)

    class AnsweringClient:
        def __init__(self):
            self.sent = 0

        def _post(self, body):
            self.sent += 1
            return {"choices": [{"message": {"content": "{}"}}], "usage": {"completion_tokens": 7}}

    client = AnsweringClient()
    budget.attach(client, ledger)
    client.ledger_request_tag = "frame-0160"
    client._post({"frames": ["frame-0160.jpg"]})
    entry = ledger.entries[-1]
    assert client.attempt_receipt == {"attempt_id": entry["attempt_id"], "request": "frame-0160",
                                      "n": entry["n"], "run_id": ledger.run_id,
                                      "ledger": str(ledger.path)}
    assert entry["run_id"] == ledger.run_id, "the charge names its run before any reply exists"
    assert entry["attempt_receipt"] == client.attempt_receipt
    assert entry["state"] == "answered"
    # A failed send leaves no receipt behind, so the next answer file cannot be credited to this attempt.
    client._post = lambda body: (_ for _ in ()).throw(ValueError("endpoint died"))
    with pytest.raises(ValueError):
        client._post({"frames": ["frame-0161.jpg"]})


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


# ------------------------------------------------- anonymous receipts: an ordinal is not an owner


def test_an_unlabelled_receipt_from_elsewhere_does_not_answer_a_new_ledger(tmp_path):
    """The loophole the first cut of the run rule left open, in its sharpest shape.

    An old-style answer file carries an attempt id and no run at all. Read as "the ids match", it closed a
    freshly minted ledger's hanging spend -- a *missing* claim treated as a *matching* one, which is the
    same coincidence the run id was introduced to refuse, only harder to notice because nothing in the
    receipt was wrong. A local ordinal two runs reach by counting from one proves nothing about who was
    answered, so the attempt stays uncertain and the spend stays charged, while the request is still
    recorded satisfied: those remain two different facts.
    """
    a = budget.AttemptLedger.open(tmp_path / "A.json", cap=4, minutes=5)
    b = budget.AttemptLedger.open(tmp_path / "B.json", cap=4, minutes=5)
    entry_a = a.reserve(request="same-request")
    entry_b = b.reserve(request="same-request")
    assert entry_a["attempt_id"] == entry_b["attempt_id"], "the collision is in the naming, not a mistake"

    anonymous = {"attempt_id": entry_a["attempt_id"], "request": "same-request", "n": entry_a["n"],
                 "ledger": str(a.path)}                     # every pre-run receipt looked like this
    closed = a.reconcile_in_flight({"same-request": {"receipt": anonymous, "mtime": None}})
    refused = b.reconcile_in_flight({"same-request": {"receipt": anonymous, "mtime": None}})

    # Refused by the ledger that appears to hold the answer too. A run started under this code labels every
    # receipt it issues, so an unlabelled one did not come from it however well the id fits -- and the
    # alternative is to let the absence of a claim read as a matching one.
    assert closed["closed"] == [] and refused["closed"] == []
    for ledger, entry in ((a, entry_a), (b, entry_b)):
        assert entry["state"] == "uncertain" and entry["outcome_reason"] == "reply_without_run_identity"
        assert entry["request_satisfied"] is True, "the request was answered by someone; that is separate"
        assert entry.get("answer_attributed_by") is None, "nothing is stamped as attributed"
        assert ledger.used == 1 and ledger.remaining == 3, "refusing the credit never refunds the spend"

    # A labelled receipt closes the same shape of attempt. (Not *this* attempt: an uncertain outcome is a
    # settled one, and a later reconciliation does not go back and rewrite what it already judged. The
    # refusal above is the record, and it is why the transport labels everything it writes.)
    c = budget.AttemptLedger.open(tmp_path / "C.json", cap=4, minutes=5)
    entry_c = c.reserve(request="same-request")
    assert c.reconcile_in_flight({"same-request": {"receipt": dict(anonymous, run_id=c.run_id),
                                                   "mtime": None}})["closed"] == ["same-request"]
    assert entry_c["state"] == "answered" and entry_c["answer_attributed_by"] == "attempt_receipt"


def test_an_old_ledger_still_recovers_its_own_answers_and_no_ones(tmp_path):
    """Conservative attribution for a store that never had an identity: its own file, and nothing wider.

    A legacy ledger cannot check a run id, because it has never issued one. What it can check is whether the
    receipt claims to come from this very file -- corroboration, not identity, which is why it is asked only
    of a ledger with no identity of its own to compare against. A second legacy ledger, whose own answers
    name their own file, gets nothing credit from the first one's.
    """
    def legacy(path, request):
        path.write_text(json.dumps({"kind": "attempt-ledger", "label": "old", "cap": 4, "minutes": 5.0,
                                    "created_at": 1000.0, "deadline_at": 1100.0, "dry_run": False,
                                    "entries": [{"n": 1, "request": request, "purpose": "an old spend",
                                                 "charged_at": 1000.5, "state": "in_flight",
                                                 "attempt_id": "%s#1" % request,
                                                 "deadline_at": 1100.0}]}) + "\n", encoding="utf-8")
        return budget.AttemptLedger.open(path, cap=4, minutes=5)

    first = legacy(tmp_path / "old-a.json", "frame-0100")
    second = legacy(tmp_path / "old-b.json", "frame-0100")
    assert first.run_id == second.run_id == budget.LEGACY_RUN_ID
    assert first.entries[0]["attempt_id"] == second.entries[0]["attempt_id"]

    own = {"attempt_id": "frame-0100#1", "request": "frame-0100", "n": 1, "ledger": str(first.path)}
    report = first.reconcile_in_flight({"frame-0100": {"receipt": own, "mtime": None}})
    assert report["closed"] == ["frame-0100"], "a legacy store recognises its own answer"
    assert first.entries[0]["answer_attributed_by"] == "attempt_id_unscoped", \
        "and says out loud that the credit rested on the weaker basis"

    stranger = {"attempt_id": "frame-0100#1", "request": "frame-0100", "n": 1, "ledger": str(first.path)}
    refused = second.reconcile_in_flight({"frame-0100": {"receipt": stranger, "mtime": None}})
    assert refused["closed"] == [] and second.entries[0]["state"] == "uncertain"
    assert second.entries[0]["outcome_reason"] == "reply_without_run_identity"

    third = legacy(tmp_path / "old-c.json", "frame-0100")
    unfiled = {"attempt_id": "frame-0100#1", "request": "frame-0100", "n": 1}
    blank = third.reconcile_in_flight({"frame-0100": {"receipt": unfiled, "mtime": None}})
    assert blank["closed"] == [], "a receipt that names no ledger and no run claims nothing it can be given"
    assert third.entries[0]["outcome_reason"] == "reply_without_run_identity"


def test_relocation_still_reconciles_on_identity_alone(tmp_path):
    """A path is never asked of a ledger that has an identity -- so a moved run loses nothing.

    This project reads its own history through legacy ``/var/home`` symlinks and has changed machines once,
    which is exactly why the run id lives inside the file: the answer a run paid for stays its answer under
    a new directory, a different symlink spelling, or a copy of the whole run tree.
    """
    ledger = budget.AttemptLedger.open(tmp_path / "run.json", cap=4, minutes=5)
    entry = ledger.reserve(request="frame-0120")
    receipt = {"attempt_id": entry["attempt_id"], "request": "frame-0120", "n": entry["n"],
               "run_id": ledger.run_id, "ledger": str(ledger.path)}
    moved = tmp_path / "moved" / "run.json"
    moved.parent.mkdir()
    moved.write_text((tmp_path / "run.json").read_text(encoding="utf-8"), encoding="utf-8")

    reopened = budget.AttemptLedger.open(moved, cap=4, minutes=5)
    assert reopened.run_id == ledger.run_id
    # The receipt still names the *old* path. Identity, not location, is what closes the attempt.
    report = reopened.reconcile_in_flight({"frame-0120": {"receipt": receipt, "mtime": None}})
    assert report["closed"] == ["frame-0120"]
    assert reopened.entries[0]["answer_attributed_by"] == "attempt_receipt"
