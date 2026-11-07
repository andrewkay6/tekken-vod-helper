"""Resilience of set recovery against inconsistent live markings.

Every test here treats markings as untrusted input: the same timeline must recover the same sets when
marks are shifted, duplicated, deleted, or dropped mid-play, and no confident gameplay may vanish
without a record saying where it went.
"""
import pytest

from tekken_vod_helper import boundary_marks as bm


def row(t, state, p1="", p2="", s1=None, s2=None, c=0.95, char1="", char2=""):
    return {"timestamp": float(t), "state": state, "confidence": c, "player1": p1, "player2": p2,
            "character1": char1, "character2": char2, "score1": s1, "score2": s2}


def played_match(start, pair, games=3, game_rows=3):
    """A grid, `games` games whose score climbs to the set winner, and the closing controller prompt."""
    rows = [row(start, "stage_select", *pair), row(start + 5, "vs_screen", *pair)]
    t = start + 10
    for game in range(games):
        for score in range(game + 1):
            rows.append(row(t, "gameplay", pair[0], pair[1], score, 0))
            t += 20
        rows.append(row(t, "character_select", pair[0], pair[1], game + 1, 0))
        t += 60
    rows.append(row(t, "controller_prompt", pair[0], pair[1], games, 0))
    return rows, t + 30


def bracket(pairings=(("Ann", "Zoe"), ("Bo", "Cy"), ("Di", "Ed")), games=3, break_seconds=400):
    rows, first_starts = [], []
    t = 40.0
    for pair in pairings:
        chunk, t = played_match(t + break_seconds, pair, games)
        rows.extend(chunk)
        first_starts.append(chunk[0]["timestamp"])
    return rows, first_starts


def cut_between_matches():
    """BB9's hardest shape: the stream cuts from live play to live play, so neither set has an edge.

    The cut is drawn the way a cut actually appears on tape -- the fight on screen changes with no
    transition between it. A broadcast header changing over continuous play is the other case, and it
    lives in `test_a_header_change_over_continuous_play_is_reported_not_cut`.
    """
    rows, t = played_match(40.0, ("Mattiniero", "Spitfire2929"), games=2)
    t += 60
    rows.append(row(t, "gameplay", "PWDDA | Ariz", "Buk", 0, 2, char1="Nina", char2="Reina"))
    rows.append(row(t + 4, "gameplay", "PWDDA | Ariz", "Buk", 0, 2, char1="Nina", char2="Reina"))
    first = t + 6
    rows.append(row(first, "gameplay", "Ann", "Zoe", 0, 0, char1="Reina", char2="Azucena"))
    rows.append(row(first + 30, "gameplay", "Ann", "Zoe", 1, 0, char1="Reina", char2="Azucena"))
    rows.append(row(first + 70, "controller_prompt", "Ann", "Zoe", 3, 0,
                    char1="Reina", char2="Azucena"))
    return rows, t, first


# ---------- markings cannot hide or invent a set ----------

def test_unmarked_sets_are_recovered_from_play_alone():
    rows, starts = bracket()
    out = bm.recover_sets(rows, marks=[], duration=rows[-1]["timestamp"] + 60)
    assert len(out["sets"]) == 3
    assert [round(s["start"]) for s in out["sets"]] == [round(s) for s in starts]


def test_missing_marks_cost_no_coverage():
    rows, starts = bracket()
    complete = bm.recover_sets(rows, marks=starts, duration=rows[-1]["timestamp"] + 60)
    partial = bm.recover_sets(rows, marks=starts[:1], duration=rows[-1]["timestamp"] + 60)
    assert [s["start"] for s in partial["sets"]] == [s["start"] for s in complete["sets"]]
    assert partial["coverage"]["rows_outside_sets"] == 0


def test_duplicated_marks_do_not_split_a_set():
    rows, starts = bracket()
    once = bm.recover_sets(rows, marks=starts, duration=rows[-1]["timestamp"] + 60)
    copies = [t for s in starts for t in (s, s + 3, s - 3)]
    thrice = bm.recover_sets(rows, marks=copies, duration=rows[-1]["timestamp"] + 60)
    assert [s["start"] for s in thrice["sets"]] == [s["start"] for s in once["sets"]]
    assert sorted(len(s["marks_inside"]) for s in thrice["sets"]) == [3, 3, 3], \
        "every copy belongs to the one match it lands in; none may open a second"
    assert all(len(s["marks_inside"]) >= 1 for s in once["sets"])


@pytest.mark.parametrize("shift", [-240.0, -30.0, 30.0, 240.0, 600.0])
def test_shifted_marks_move_no_boundary(shift):
    rows, starts = bracket()
    clean = bm.recover_sets(rows, marks=starts, duration=rows[-1]["timestamp"] + 60)
    moved = bm.recover_sets(rows, marks=[s + shift for s in starts],
                            duration=rows[-1]["timestamp"] + 60)
    assert [(s["start"], s["end"]) for s in moved["sets"]] == \
        [(s["start"], s["end"]) for s in clean["sets"]], \
        "a marking error must never move a clip boundary"
    assert moved["coverage"]["rows_outside_sets"] == 0


def test_marks_far_from_any_play_are_reported_not_invented_with():
    rows, starts = bracket()
    out = bm.recover_sets(rows, marks=[starts[0], 10.0 ** 6], duration=rows[-1]["timestamp"] + 60)
    status = [m.get("status") for m in out["marks"]]
    assert "unanchored" in status
    assert len(out["sets"]) == 3, "a wild mark must not conjure a fourth set"


def test_a_mark_inside_play_is_recorded_as_an_offset_not_an_edge():
    rows, starts = bracket()
    middle = starts[1] + 120.0
    out = bm.recover_sets(rows, marks=[middle], duration=rows[-1]["timestamp"] + 60)
    target = [s for s in out["sets"] if s["marks_inside"]][0]
    assert target["marks_inside"][0]["position"] == "inside"
    assert target["start"] < middle < target["end"]


def test_unusable_marks_are_kept_visible():
    rows, starts = bracket()
    out = bm.recover_sets(rows, marks=[{"start": None}, {"start": float("nan")}, starts[0]],
                          duration=rows[-1]["timestamp"] + 60)
    assert [m["usable"] for m in out["marks"][:2]] == [False, False]
    assert out["coverage"]["rows_outside_sets"] == 0


# ---------- evidence, not convention, decides boundaries ----------

def test_grid_and_prompt_are_preferred_but_not_mandatory():
    rows, _ = bracket()
    out = bm.recover_sets(rows, duration=rows[-1]["timestamp"] + 60)
    assert all(s["start_evidence"] == "stage_select" for s in out["sets"])
    assert all(s["end_evidence"] == "controller_prompt" for s in out["sets"])
    assert all(s["partial"] == [] for s in out["sets"])


def test_a_partial_set_is_preserved_rather_than_dropped():
    """A set the recording joined mid-game still gets a span, flagged partial and truncated."""
    rows, cut_at, resumed = cut_between_matches()
    out = bm.recover_sets(rows, duration=rows[-1]["timestamp"] + 60)
    truncated = [s for s in out["sets"] if s["truncated"]["start"]]
    assert truncated, "the set that starts with live play must be recovered, not skipped"
    assert any(s["start_evidence"] == "play_observed" and "start" in s["partial"] for s in truncated)


def test_no_boundary_evidence_is_not_answered_with_silence():
    """The failure mode this whole module exists for: play nothing proposed, and status complete."""
    rows = [row(t, "gameplay", "Ann", "Zoe", 0, 0) for t in (0, 5, 10, 15, 20)]
    out = bm.recover_sets(rows, duration=60.0)
    assert out["coverage"]["rows_outside_sets"] == 0
    covered = [(s["play_start"], s["play_end"]) for s in out["sets"]]
    residual = [(r["start"], r["end"]) for r in out["residuals"]]
    assert any(not (b < 0 or a > 20) for a, b in covered + residual)
    unexplained = [r for r in out["residuals"] if r["reason"] not in
                   ("truncated_recording", "recording_edge", "no_boundary_evidence",
                    "pairing_changed_without_boundary_evidence")]
    assert unexplained == []


def test_a_truncated_recording_is_distinct_from_a_late_mark():
    """A cut mid-match leaves one set with no ending and the next with no beginning."""
    rows, cut_at, resumed = cut_between_matches()
    out = bm.recover_sets(rows, marks=[resumed + 400], duration=rows[-1]["timestamp"] + 60)
    lost_ending = [s for s in out["sets"] if s["truncated"]["end"]]
    late_start = [s for s in out["sets"] if s["truncated"]["start"]]
    assert len(lost_ending) == 1 and len(late_start) == 1
    assert lost_ending[0]["end_evidence"] == "play_observed", "no ending screen was ever broadcast"
    assert late_start[0]["start_evidence"] == "play_observed", "no grid was ever broadcast"
    assert late_start[0]["split_basis"] == "hard_cut"
    assert "end" in lost_ending[0]["partial"] and "start" in late_start[0]["partial"]
    # A mark far from any edge is stored as an offset; it may not become a boundary.
    mark = out["marks"][0]
    assert mark["status"] in ("anchored", "nearest_set", "unanchored")
    assert all(mark["timestamp"] not in (s["start"], s["end"]) for s in out["sets"])


def test_a_controller_prompt_before_the_set_is_decided_is_not_an_end():
    """Ending on any prompt is what cut BB9's sets ~200 s short; a 0-2 prompt has not decided a set."""
    rows = [row(0, "stage_select", "Ann", "Zoe")]
    rows += [row(10 + 20 * i, "gameplay", "Ann", "Zoe", 0, min(i, 2)) for i in range(6)]
    rows.append(row(140, "controller_prompt", "Ann", "Zoe", 0, 2))
    rows += [row(160 + 20 * i, "gameplay", "Ann", "Zoe", min(i, 3), 2) for i in range(4)]
    rows.append(row(250, "controller_prompt", "Ann", "Zoe", 3, 2))
    out = bm.recover_sets(rows, duration=400.0)
    assert len(out["sets"]) == 1
    assert out["sets"][0]["end"] == 250, "the set concludes when the score says so, not at the first menu"
    assert out["sets"][0]["partial"] == []


# ---------- names and scores are estimates, so they cannot drive boundaries ----------

def _two_blocks_of_play(gap_between=None, prompt_between=False):
    """One stretch of play, then more play by a differently-named pair, with optional evidence between."""
    rows = [row(0, "stage_select", "Ann", "Zoe")]
    rows += [row(10 + 10 * i, "gameplay", "Ann", "Zoe", 0, 0) for i in range(6)]
    t = 80
    if prompt_between:
        rows.append(row(t, "controller_prompt", "Ann", "Zoe", 1, 0))
        t += 10
    if gap_between:
        t += gap_between
    rows += [row(t + 10 * i, "gameplay", "Bo", "Cy", 0, 0) for i in range(6)]
    rows.append(row(t + 80, "controller_prompt", "Bo", "Cy", 3, 0))
    return rows


def test_a_name_change_alone_cannot_open_a_match():
    """A pairing reading the surrounding play does not support is a warning, not a boundary."""
    rows = [row(0, "stage_select", "Ann", "Zoe")]
    rows += [row(10 + 20 * i, "gameplay", "Ann", "Zoe", 0, 0) for i in range(6)]
    # Two readings of a "different" pairing, spaced too widely to be a stream cut and too few to
    # outvote the play on either side: a misreading, not an opponent.
    rows += [row(200, "gameplay", "Bo", "Cy", 0, 0), row(260, "gameplay", "Bo", "Cy", 0, 0)]
    rows += [row(320 + 20 * i, "gameplay", "Ann", "Zoe", 1, 0) for i in range(6)]
    rows.append(row(460, "controller_prompt", "Ann", "Zoe", 3, 0))
    out = bm.recover_sets(rows, duration=600.0)
    assert len(out["sets"]) == 1, "an isolated pairing reading must not invent a match"
    assert out["sets"][0]["unconfirmed_pairing_change"], "but it must be reported, not swallowed"
    assert out["coverage"]["rows_outside_sets"] == 0


def test_a_persistent_name_change_with_a_screen_between_does_open_a_match():
    rows = _two_blocks_of_play(gap_between=30, prompt_between=True)
    out = bm.recover_sets(rows, duration=400.0)
    assert len(out["sets"]) == 2
    assert out["sets"][1]["split_basis"] in ("new_pairing_after_screen", "pairing_change_persisted",
                                            "controller_assignment", "hard_cut")
    assert not out["sets"][1]["pairing_uncertain"]


def test_a_header_change_over_continuous_play_is_reported_not_cut():
    """The BB9 case, in one fixture: names and score box change while the same fight continues.

    Nothing structural happened, so the recovery must not invent a boundary -- and it must not swallow
    the contradiction either. The change is reported against the set it landed in, which is the only
    honest answer the footage supports at this sampling density.
    """
    rows = [row(0, "stage_select", "Buk", "Ariz", char1="Reina", char2="Azucena")]
    for t in (10, 50, 90, 130, 170, 210, 250, 290):
        late = t >= 170
        rows.append(row(t, "gameplay",
                        "Mattiniero" if late else "Buk",
                        "Spitfire2929" if late else "Ariz",
                        0 if late else 0, 0 if late else 2,
                        char1="Reina", char2="Azucena"))
    rows.append(row(330, "controller_prompt", "Mattiniero", "Spitfire2929", 0, 0,
                    char1="Reina", char2="Azucena"))
    out = bm.recover_sets(rows, duration=400.0)
    assert len(out["sets"]) == 1, "a mid-round header correction is not a new set"
    assert out["overlay_changes"], "but the contradiction has to reach the output"
    assert out["sets"][0]["unconfirmed_pairing_change"]


def test_a_long_break_opens_a_match_without_any_name_evidence():
    rows = _two_blocks_of_play(gap_between=5000)
    out = bm.recover_sets(rows, duration=9000.0)
    assert [s["split_basis"] for s in out["sets"]] == ["first_play", "play_gap"]


def test_trusting_single_name_readings_opens_more_matches_not_better_ones():
    rows = [row(0, "stage_select", "Ann", "Zoe")]
    rows += [row(10 + 20 * i, "gameplay", "Ann", "Zoe", 0, 0) for i in range(6)]
    rows += [row(200, "gameplay", "Bo", "Cy", 0, 0), row(260, "gameplay", "Bo", "Cy", 0, 0)]
    rows += [row(320 + 20 * i, "gameplay", "Ann", "Zoe", 1, 0) for i in range(6)]
    rows.append(row(460, "controller_prompt", "Ann", "Zoe", 3, 0))
    corroborated = bm.recover_sets(rows, duration=600.0)
    trusting = bm.recover_sets(rows, duration=600.0, split_on_names="always")
    ignored = bm.recover_sets(rows, duration=600.0, split_on_names="never")
    assert len(corroborated["sets"]) == 1 < len(trusting["sets"])
    assert len(ignored["sets"]) == 1, "names are not the only way to find a boundary"


def test_mangled_names_degrade_counting_but_never_lose_play():
    """Names are an estimate, so their damage must degrade loudly rather than silently.

    Blurring who is playing cannot be forgiven -- counting matches is exactly what that signal is for
    -- but it may never cost coverage: every confident gameplay row stays inside a set or a named
    residual, and no two sets may overlap however bad the readings get.
    """
    rows, starts = bracket()
    clean = bm.recover_sets(rows, marks=starts, duration=rows[-1]["timestamp"] + 60)

    def mangle(name, kind):
        if kind == "prefix":
            return name.split("|")[-1].strip()
        if kind == "letters":
            return name[:max(2, len(name) - 3)]
        return ""

    for kind in ("prefix", "letters", "blank"):
        broken = []
        for source in rows:
            copy = dict(source)
            for key in ("player1", "player2"):
                if copy.get(key):
                    copy[key] = mangle(copy[key], kind)
            broken.append(copy)
        got = bm.recover_sets(broken, marks=starts, duration=rows[-1]["timestamp"] + 60)
        assert got["coverage"]["fraction_accounted"] == 1.0, kind
        assert got["coverage"]["rows_outside_sets"] == 0, kind
        assert got["overlaps"] == [], kind
        assert got["sets"], kind


def test_sets_never_overlap_even_when_evidence_conflicts():
    rows = _two_blocks_of_play(gap_between=30, prompt_between=True)
    out = bm.recover_sets(rows, duration=400.0)
    assert out["overlaps"] == []
    for a, b in zip(out["sets"], out["sets"][1:]):
        assert a["end"] <= b["start"]


def test_scrambled_scores_move_no_boundary():
    """The score field is a guess about what the overlay meant; boundaries may not depend on it."""
    rows, starts = bracket()
    clean = bm.recover_sets(rows, marks=starts, duration=rows[-1]["timestamp"] + 60)
    expected = [(s["start"], s["end"]) for s in clean["sets"]]

    for variant in (lambda a, b: (None, None), lambda a, b: (7, 7), lambda a, b: (b, a)):
        scrambled = []
        for source in rows:
            copy = dict(source)
            copy["score1"], copy["score2"] = variant(source.get("score1"), source.get("score2"))
            scrambled.append(copy)
        got = bm.recover_sets(scrambled, marks=starts, duration=rows[-1]["timestamp"] + 60)
        assert [(s["start"], s["end"]) for s in got["sets"]] == expected
        assert all(s["evidence_is_estimate"] == {"names": True, "scores": True} for s in got["sets"])


# ---------- clip ends come from evidence plus padding ----------


def test_padding_extends_clips_without_moving_evidence():
    rows, starts = bracket()
    tight = bm.recover_sets(rows, pad_before=0, pad_after=0, duration=rows[-1]["timestamp"] + 60)
    loose = bm.recover_sets(rows, pad_before=15, pad_after=25, duration=rows[-1]["timestamp"] + 60)
    assert [(s["start"], s["end"]) for s in tight["sets"]] == \
        [(s["start"], s["end"]) for s in loose["sets"]]
    assert loose["sets"][0]["clip_start"] == tight["sets"][0]["clip_start"] - 15
    assert loose["sets"][0]["clip_end"] == tight["sets"][0]["clip_end"] + 25


def test_padding_never_eats_the_previous_set():
    rows, starts = bracket()
    out = bm.recover_sets(rows, pad_before=10_000, pad_after=10_000,
                          duration=rows[-1]["timestamp"] + 60)
    for a, b in zip(out["sets"], out["sets"][1:]):
        assert a["clip_end"] <= b["clip_start"]


# ---------- the invariants that make 'complete' mean something ----------

def test_every_play_row_is_accounted_for_under_any_marking():
    rows, starts = bracket(pairings=(("Ann", "Zoe"), ("Bo", "Cy"), ("Di", "Ed"), ("Fi", "Gil")))
    for marks in ([], starts, [s + 900 for s in starts], starts * 3, [0.0], [10.0 ** 7]):
        out = bm.recover_sets(rows, marks=marks, duration=rows[-1]["timestamp"] + 60)
        assert out["coverage"]["fraction_accounted"] == 1.0, marks
        assert out["coverage"]["rows_outside_sets"] == sum(r["gameplay_rows"] for r in out["residuals"])


def test_sets_never_overlap_and_stay_in_time_order():
    rows, _ = bracket()
    out = bm.recover_sets(rows, duration=rows[-1]["timestamp"] + 60)
    spans = [(s["start"], s["end"]) for s in out["sets"]]
    assert spans == sorted(spans)
    for (a_start, a_end), (b_start, _) in zip(spans, spans[1:]):
        assert a_end <= b_start


def test_an_empty_or_broken_timeline_fails_loudly():
    assert bm.recover_sets([], marks=[])["sets"] == []
    with pytest.raises(ValueError):
        bm.recover_sets([{"timestamp": float("nan"), "state": "gameplay", "confidence": 1}])
