"""Which character a field gets when a player is attached to a match or a start.gg bracket set.

The rule belongs to the project model, not to the Tkinter layer, for a practical reason: the picker that
uses it cannot be imported on a machine without tkinter, so while the decision lived inside ``app.py`` no
test on this box could reach it. What it used to do was read the remembered default and nothing else, which
silently destroyed any character the recording itself had supplied -- and blanked the field outright when
nothing was remembered.

These tests pin the replacement: observed wins, remembered is a fallback, and unknown stays empty rather
than becoming a guess.
"""

from tekken_vod_helper.models import MatchSegment, ProjectState


def _state(remembered=None, segment=None):
    return ProjectState(player_characters=dict(remembered or {}),
                        matches=[segment] if segment else [])


SEGMENT = MatchSegment(start=0.0, end=100.0, player1="Buk", player2="Matt C",
                       character1="Jin", character2="Noctis")


def test_an_observed_character_beats_the_remembered_default():
    """The case the old code got wrong in every direction: the tape says Jin, habit says something else."""
    state = _state({"buk": "Hwoarang"}, SEGMENT)
    assert state.character_for_player("Buk", observed=[("Buk", "Jin")]) == "Jin"
    assert state.remembered_character("Buk") == "Hwoarang", "the default is still known, just not first"


def test_a_segment_s_observed_pair_follows_the_player_not_the_slot():
    """A swapped bracket must not hand Jin to the player the recording never saw on Jin.

    Observed pairs come in as (player, character) from the segment, so the answer depends on who the
    bracket set names, not on which field number is being filled.
    """
    observed = [(SEGMENT.player1, SEGMENT.character1), (SEGMENT.player2, SEGMENT.character2)]
    state = _state({}, SEGMENT)
    assert state.character_for_player("Matt C", observed=observed) == "Noctis"
    assert state.character_for_player("Buk", observed=observed) == "Jin"


def test_remembered_still_answers_when_the_recording_said_nothing():
    state = _state({"lap sap": "Paul"}, MatchSegment(start=0.0, end=1.0, player1="Lap Sap", player2=""))
    assert state.character_for_player("Lap Sap", observed=[("Lap Sap", "")]) == "Paul"


def test_no_source_means_empty_rather_than_an_invented_character():
    state = _state({})
    assert state.character_for_player("Nobody") == ""
    assert state.character_for_player("Nobody", observed=[("Nobody", " ")]) == ""


def test_names_are_matched_the_way_the_app_normalises_them():
    """Case and repeated spaces are operator typing; a letter split by spaces is a different string.

    The second half matters as much as the first: normalising "  JAN  " to "jan" is what lets a bracket
    spelling match a tape spelling, while "J a n" staying distinct means an observed value cannot be
    silently borrowed by a name that only looks similar once the spaces are removed.
    """
    state = _state({"jan": "Lei"}, SEGMENT)
    assert state.character_for_player("  JAN  ") == "Lei"
    assert state.character_for_player("JAN", observed=[("jan", "Feng")]) == "Feng"
    assert state.character_for_player("jan", observed=[("J a n", "Feng")]) == "Lei"


def test_an_empty_observation_does_not_shadow_a_remembered_value():
    state = _state({"crispy bacon": "Eddy"}, SEGMENT)
    assert state.character_for_player("Crispy Bacon",
                                      observed=[("Crispy Bacon", ""), ("Buk", "Jin")]) == "Eddy"


def test_remembered_defaults_survive_a_save_and_reload():
    """The fallback is only useful if the project file keeps it."""
    state = _state({"buk": "Hwoarang"})
    reloaded = ProjectState.from_dict(state.to_dict())
    assert reloaded.player_characters == {"buk": "Hwoarang"}
    assert reloaded.character_for_player("Buk") == "Hwoarang"


def test_a_remembered_default_fills_a_gap_but_never_overwrites_a_value():
    """The ``<FocusOut>`` rule, stated where it can actually be tested.

    The handler that fires when the operator tabs out of a player-name field used to push the remembered
    character into the box unconditionally, so a character already there -- possibly the one the tape
    showed for this game -- was replaced by habit. It now asks the same precedence question with the
    current value as the observation, which means: non-empty current value wins, empty current value takes
    the default, and a player nobody recorded leaves the field empty.
    """
    state = _state({"buk": "Hwoarang"})
    current = "Jin"
    assert state.character_for_player("Buk", observed=[("Buk", current)]) == "Jin"
    assert state.character_for_player("Buk", observed=[("Buk", "")]) == "Hwoarang"
    assert state.character_for_player("Buk", observed=[("Buk", "   ")]) == "Hwoarang"
    assert state.character_for_player("Someone New", observed=[("Someone New", "")]) == ""


def test_a_segment_can_carry_no_more_than_one_character_pair_per_side():
    """What the review path cannot yet represent, stated as a test so the gap cannot be forgotten.

    Recovery observes a character per game; the project model stores one pair per segment. Importing a
    recovered set therefore has to keep the first observation and leave the rest to the reviewer rather
    than pretending a set has one character per player.
    """
    assert set(MatchSegment.__dataclass_fields__) >= {"character1", "character2"}
    assert not any(name.startswith("game") for name in MatchSegment.__dataclass_fields__)
