from tekken_vod_helper.models import MatchSegment, ProjectState


def test_match_segment_loads_generated_boundary_aliases():
    segment = MatchSegment.from_dict(
        {
            "round": "Grand Final Reset",
            "player1": "andy",
            "player2": "Spitfire2929",
            "char1": "Heihachi",
            "char2": "Azucena",
            "start_time": 6760.0,
            "end_time": 7420.0,
            "notes": "Generated boundary",
        }
    )

    assert segment.round_name == "Grand Final Reset"
    assert segment.character1 == "Heihachi"
    assert segment.character2 == "Azucena"
    assert segment.start == 6760.0
    assert segment.end == 7420.0


def test_project_state_loads_generated_match_timestamps():
    state = ProjectState.from_dict(
        {
            "matches": [
                {
                    "round": "Winners Round 1",
                    "player1": "Pillaibro",
                    "player2": "gandhi",
                    "char1": "Dragunov",
                    "char2": "Lee",
                    "start_time": 105.0,
                    "end_time": 1360.56,
                }
            ]
        }
    )

    assert len(state.matches) == 1
    assert state.matches[0].start == 105.0
    assert state.matches[0].end == 1360.56
