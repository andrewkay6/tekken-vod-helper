"""The review tab's callbacks, exercised without a display.

``app.py`` cannot be run headless on this box -- Tk has no display and the operator's desktop is off-limits
to an unattended test -- so driving the real tab was never going to be the check. What can be checked is the
part the callbacks are made of: a selected stretch of tape in, an action string out, and the guidance a
reviewer gets when they pointed at the wrong thing. The controller each action names is tested in
``test_review_flow.py`` against real projects; this file is the seam between the widget and that controller.
"""
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tekken_vod_helper import app as app_module
from tekken_vod_helper.app import TekkenVodHelperApp
from tekken_vod_helper.models import ProjectState


class FakeTree:
    """Just enough of a ttk.Treeview selection for a callback to read."""

    def __init__(self, selected=()):
        self._selected = tuple(selected)

    def selection(self):
        return self._selected


class FakeVar:
    """A ``tk.StringVar`` is out of the question with no display, and a callback only ever calls get()."""

    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value


def make_app(iid=None, dispose=""):
    """The tab's callbacks against a bare app object: a selection in, an action string out.

    ``review_dispose_choices`` is filled from the controller here exactly as the build step fills it in the
    running app, so a label offered to this test is a label the journal would accept.
    """
    app = TekkenVodHelperApp.__new__(TekkenVodHelperApp)
    app.project_state = ProjectState()
    app.log_messages = []
    app.log = app.log_messages.append
    app.press_calls = []
    app._review_press = app.press_calls.append
    app.review_unresolved_tree = FakeTree(() if iid is None else (iid,))
    app.review_dispose_choices = sorted(app_module.boundary_review.FOOTAGE_DISPOSITIONS.items())
    app.review_dispose_var = FakeVar(dict(app.review_dispose_choices).get(dispose, ""))
    return app


def test_no_row_selected_sends_the_reviewer_to_the_list_instead_of_guessing():
    app = make_app()
    assert app._review_selected_interval() is None
    assert app.press_calls == []
    assert "Select a row" in app.log_messages[-1]


def test_an_unclaimed_stretch_reaches_the_controller_as_the_interval_it_covers():
    app = make_app("gap|1787.305|2000.0")
    assert app._review_selected_interval() == "1787.305_2000.0"
    app._review_claim_unresolved()
    assert app.press_calls == ["claim-interval:1787.305_2000.0"]


def test_a_stretch_with_no_end_of_its_own_is_claimed_from_where_it_starts():
    app = make_app("gap|6870.861|None")
    assert app._review_selected_interval() == "6870.861_6870.861"
    app._review_play_unresolved()
    assert app.press_calls == ["play-interval:6870.861_6870.861"]


def test_a_held_clip_is_not_an_unclaimed_stretch_and_says_so():
    """Two different things on one list, and claiming a clip twice would be the worse way to mix them up."""
    app = make_app("held|100.0|202.0")
    assert app._review_selected_interval() is None
    assert app.press_calls == []
    assert "already claims" in app.log_messages[-1]
    app._review_claim_unresolved()
    assert app.press_calls == [], "a refusal must not reach the controller at all"


def test_a_disposition_names_the_footage_and_carries_the_interval_it_was_about():
    app = make_app("gap|0.0|53.053", dispose="not_play")
    app._review_dispose_unresolved()
    assert app.press_calls == ["dispose:not_play:0.0_53.053"]


def test_a_disposition_the_controller_does_not_offer_is_never_sent():
    app = make_app("gap|0.0|53.053")
    app.review_dispose_var = FakeVar("the footage was nice")
    app._review_dispose_unresolved()
    assert app.press_calls == []
    assert "Choose what the footage was" in app.log_messages[-1]


def test_the_labels_the_tab_offers_are_the_controllers_own_dispositions():
    """A button that offers a word the journal would refuse is a button that ships a failure."""
    app = make_app("gap|0.0|1.0")
    assert dict(app.review_dispose_choices) == app_module.boundary_review.FOOTAGE_DISPOSITIONS


def test_the_claim_button_sends_the_gap_at_its_real_instants_not_a_rounded_abbreviation():
    """The action string is how the list tells the controller where the footage was.

    A tab that sends "574.572" has moved the boundary before the controller is told it exists, and a
    boundary moved by a third of a millisecond can cross the clip after it. The row id is built from the
    plan's own floats, so the instants survive the trip to the callback and back.
    """
    app = make_app("gap|574.57197|790.789958")
    assert app._review_selected_interval() == "574.57197_790.789958"
    app._review_claim_unresolved()
    assert app.press_calls == ["claim-interval:574.57197_790.789958"]


def test_a_disposition_records_the_stretch_it_was_offered_at_full_precision():
    app = make_app("gap|574.57197|790.789958", dispose="not_play")
    app._review_dispose_unresolved()
    assert app.press_calls == ["dispose:not_play:574.57197_790.789958"]


def test_a_disputed_opening_gets_its_button_from_the_controller_without_any_app_wiring():
    """The tab draws whatever controls the panel offers, so a new one cannot be forgotten here.

    That is the whole reason the panel owns the list: a control added for one kind of evidence shows up
    with its label and its enabled state, and the only app-side failure left is a verb the controller does
    not implement -- which the panel's own tests catch.
    """
    from tekken_vod_helper.boundary_review_flow import ReviewPanel
    from tekken_vod_helper.models import ProjectState

    segment = {
        "start": 1588.586916, "end": 2137.132888,
        "recovery": {
            "clip_end_at": 2137.132888,
            "clip_end_anchor": {"winner_confirmed_at": 2136.0},
            "edges": {"start": {
                "at": 1588.586916,
                "clock_at_boundary": 60,
                "settled_by": {"at": 1538.536919, "kind": "cue", "state": "controller_prompt"},
                # A walked frame only disputes a settled cue if it shows the round being fought; this
                # one does, which is what earns the button.
                "walked_past": [{"at": 1587.585917, "state": "gameplay", "clock": 58,
                                 "because": "clock unread",
                                 "evidence": "Active combat with timer 58 and a Counter popup"}],
            }},
        },
    }
    state = ProjectState.from_dict({"duration": 6928.421136, "video_path": "/recordings/bb9.mkv",
                                    "matches": [segment]})
    question = ReviewPanel(state).state_view()["question"]
    dispute = next(control for control in question["controls"]
                   if control["action"] == "play:dispute")
    assert dispute["enabled"] is True
    assert dispute["label"].startswith("Play the frames in dispute")
    assert question["boundary"]["readings_conflict"] is True
    assert "does not agree with itself" in question["why"] or \
        "does not agree with itself" in question["boundary"].get("needs_review_why", "")


def explain(question):
    """The tab's own explanation builder, called without building a window."""
    return TekkenVodHelperApp._review_explain_text(object(), question)


def proposal_question(**override):
    proposal = dict({"kind": "opening", "at": 785.784959, "current": 790.789958,
                     "distance_seconds": -5.005, "uncertainty_text": "+-5.005 s",
                     "why": "the saved sequence recovers this round's own opening",
                     "evidence": {"frame": "frame-0786.jpg", "preserves": "play"}}, **override)
    return {"why": "Where does this match start?", "state": "open",
            "boundary": {"at": 790.789958, "late_by_at_least_seconds": None,
                         "earliest_supported_opening": None, "evidence_describes_boundary": True},
            "context": {"play_from": 780.0, "play_to": 800.0}, "proposal": proposal}


def test_the_card_shows_the_number_the_apply_button_would_use():
    """A button labelled "Apply this proposal" is not safe to press without the instant behind it.

    Found by driving the real tab headlessly: every controller test asserted the ``proposal`` dict the
    panel is handed, and nothing asserted the reviewer could see it. The card drew the current bound and
    the buttons, and no proposed number at all.
    """
    text = explain(proposal_question())
    assert "790.790" in text and "785.785" in text, \
        "current and proposed both have to be on screen, or the edit is invisible"
    assert "-5.005" in text, "the direction and size of the move is stated, not implied"
    assert "+-5.005 s" in text, "and the width the sampling can defend with it"
    assert "frame-0786.jpg" in text and "machine" in text.lower()
    assert "recovers this round's own opening" in text


def test_an_unknown_width_is_written_as_unknown_on_the_card():
    text = explain(proposal_question(uncertainty_text="unknown", distance_seconds=None))
    assert "unknown" in text
    assert "+-0" not in text and "+0.000" not in text, "an unknown never renders as a measurement"


def test_the_summary_line_counts_proposals_apart_from_answers():
    """`30 open` says nothing about machine suggestions waiting; they are neither open nor answered work."""
    state = ProjectState.from_dict({
        "duration": 600.0, "video_path": "/rec.mkv",
        "matches": [{"start": 100.0, "end": 200.0, "recovery": {
            "clip_end_at": 202.0,
            "edges": {"start": {"at": 100.0, "clock_at_boundary": 60}}}}],
        "review": {"version": 1, "decisions": [], "log": []}})
    from tekken_vod_helper.boundary_review_flow import ReviewFlow
    flow = ReviewFlow(state)
    summary = flow.summary()
    assert summary["machine_proposals_pending"] == 0
    flow.propose_end(100.0, 205.0, why="the closing pose", purpose="presentation")
    assert flow.summary()["machine_proposals_pending"] == 1, \
        "the count moves with the proposals, not with the answers"
    assert flow.summary()["open"] == 2, "and the owner's questions are still counted as open"
    # The line the tab formats is built from this dict; a missing key is a KeyError at draw time.
    assert set(("open deferred answered exportable held machine_supported_starts unknown_starts "
                "machine_contradicted_starts start_readings_conflict openings_needing_review "
                "operator_accepted operator_approved_partials machine_proposals_pending "
                "independently_validated unplaced_seconds").split()) <= set(summary)


def test_the_review_helpers_stay_on_the_class_that_uses_them():
    """A helper added at column zero inside the class body silently ends the class.

    That is not hypothetical: it happened while wiring the proposal buttons. Everything after the stray
    ``def`` became a module-level function, ``TekkenVodHelperApp`` lost ``_review_press``, the fake app in
    these tests started resolving attributes through ``tkinter.Misc.__getattr__`` and recursed, and 62 tests
    in three files failed for reasons that pointed nowhere near the edit. ``py_compile`` passed and the
    review-controller suites passed, because none of them instantiates the class. This is the two-line check
    that would have caught it at the file where the change was made.
    """
    for name in ("_review_press", "_review_refresh", "_upload_review_rows", "_offer_temporary_project_recovery"):
        assert callable(getattr(TekkenVodHelperApp, name, None)), \
            "%s has to be a method of the app, not a module function" % name
    assert not any(callable(getattr(app_module, name, None))
                   for name in ("_review_press", "_review_refresh", "_upload_review_rows")), \
        "a method that leaked to module scope means the class body was cut short"


def test_applying_a_proposal_rebuilds_the_clip_tree_and_rejecting_one_does_not():
    """The one place the tab has to know that a proposal is not a boundary change.

    ``_review_press`` refreshes the card for every action and the tree only for actions that can move a
    boundary. A proposal's two answers sit on opposite sides of that line, so a single
    ``startswith("proposal")`` shortcut would either leave a moved clip displayed at its old bounds or redraw
    the tree for a refusal that changed nothing.
    """
    assert app_module._is_structural_review_action("proposal:apply") is True
    assert app_module._is_structural_review_action("proposal:reject") is False
    for verb in ("answer:new_match", "nudge:1.0", "end:now", "start:now", "orientation:as-listed",
                 "claim-here", "undo", "undo-last"):
        assert app_module._is_structural_review_action(verb) is True, verb
    for verb in ("view:open", "select:abc", "play:candidate", "bracket:pick"):
        assert app_module._is_structural_review_action(verb) is False, verb
