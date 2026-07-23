from tekken_vod_helper.app import TekkenVodHelperApp
from tekken_vod_helper.models import MatchSegment, OverlayState, ProjectState
from tekken_vod_helper import startgg_client
from tekken_vod_helper.startgg_client import BracketSet, parse_tournament_sets, normalize_tournament_slug


def make_app(state):
    app = TekkenVodHelperApp.__new__(TekkenVodHelperApp)
    app.state = state
    return app


class DummyVar:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class DummyCombo:
    def __init__(self):
        self.values = []

    def configure(self, **kwargs):
        if "values" in kwargs:
            self.values = kwargs["values"]


def test_overlay_state_matches_browser_json_shape():
    state = OverlayState(description="Basement Brawl", p1name="Alice", p1score=2, p2name="Bob", p2score=1)

    assert state.to_dict() == {
        "description": "Basement Brawl",
        "subtitle": "",
        "p1name": "Alice",
        "p1country": "",
        "p1score": 2,
        "p1team": "",
        "p2name": "Bob",
        "p2country": "",
        "p2score": 1,
        "p2team": "",
        "font": "Bahnschrift",
    }


def test_project_state_round_trips_overlay_obs_and_startgg_settings():
    state = ProjectState()
    state.overlay = OverlayState(description="Event", p1name="Alice", p2name="Bob")
    state.obs.host = "localhost"
    state.obs.port = 4456
    state.obs.password = "secret"
    state.startgg.token = "token"
    state.startgg.tournament_slug = "tournament/test-event"

    payload = state.to_dict()
    loaded = ProjectState.from_dict(payload)

    assert loaded.overlay.p1name == "Alice"
    assert loaded.overlay.p2name == "Bob"
    assert loaded.obs.host == "localhost"
    assert loaded.obs.port == 4456
    assert loaded.obs.password == "secret"
    assert "token" not in payload["startgg"]
    assert loaded.startgg.token == ""
    assert loaded.startgg.tournament_slug == "tournament/test-event"


def test_project_state_ignores_startgg_token_from_project_json():
    loaded = ProjectState.from_dict({"startgg": {"token": "legacy", "tournament_slug": "tournament/test-event"}})

    assert loaded.startgg.token == ""
    assert loaded.startgg.tournament_slug == "tournament/test-event"


def test_capture_match_from_overlay_populates_players_and_closes_previous():
    state = ProjectState(
        matches=[MatchSegment(start=10.0)],
        overlay=OverlayState(p1name="Alice", p2name="Bob", subtitle="Winners Finals"),
    )
    app = make_app(state)
    app.overlay_character1_var = DummyVar("Jin")
    app.overlay_character2_var = DummyVar("King")

    match, message = app._capture_match_from_overlay(120.0)

    assert message == ""
    assert match is not None
    assert state.matches[0].end == 120.0
    assert match.player1 == "Alice"
    assert match.player2 == "Bob"
    assert match.character1 == "Jin"
    assert match.character2 == "King"
    assert match.round_name == "Winners Finals"
    assert state.players == ["Alice", "Bob"]
    assert state.characters == ["Jin", "King"]


def test_capture_match_from_overlay_rejects_near_duplicate_start():
    state = ProjectState(
        matches=[MatchSegment(start=10.0)],
        overlay=OverlayState(p1name="Alice", p2name="Bob"),
    )
    app = make_app(state)

    match, message = app._capture_match_from_overlay(10.1)

    assert match is None
    assert "already close" in message


def test_capture_match_from_overlay_allows_zero_zero_drafts():
    state = ProjectState(
        matches=[],
        overlay=OverlayState(p1name="Alice", p2name="Bob", subtitle="Pools"),
    )
    app = make_app(state)
    app.overlay_character1_var = DummyVar("Asuka")
    app.overlay_character2_var = DummyVar("Lili")

    first, first_message = app._capture_match_from_overlay(0.0, end=0.0, allow_duplicate=True)
    second, second_message = app._capture_match_from_overlay(0.0, end=0.0, allow_duplicate=True)

    assert first_message == ""
    assert second_message == ""
    assert first is not None
    assert second is not None
    assert [(match.start, match.end) for match in state.matches] == [(0.0, 0.0), (0.0, 0.0)]
    assert state.matches[0].character1 == "Asuka"
    assert state.matches[0].character2 == "Lili"


def test_startgg_set_populates_vod_fields_without_selected_match():
    state = ProjectState()
    app = make_app(state)
    app.bracket_set_var = DummyVar("set")
    app.bracket_set_by_label = {
        "set": BracketSet(
            id="1",
            event_name="Tekken 8",
            round_name="Winners Finals",
            player1="Alice",
            player2="Bob",
            state=1,
            character1="Jin",
            character2="King",
        )
    }
    app.player1_var = DummyVar()
    app.player2_var = DummyVar()
    app.character1_var = DummyVar()
    app.character2_var = DummyVar()
    app.round_var = DummyVar()
    app.event_var = DummyVar()
    app._selected_tree_index = lambda: None
    app._refresh_combo_values = lambda: None
    app.log = lambda _message: None

    app.apply_selected_bracket_set_to_match()

    assert app.player1_var.get() == "Alice"
    assert app.player2_var.get() == "Bob"
    assert app.character1_var.get() == "Jin"
    assert app.character2_var.get() == "King"
    assert app.round_var.get() == "Winners Finals"
    assert app.event_var.get() == "Tekken 8"
    assert state.players == ["Alice", "Bob"]
    assert state.characters == ["Jin", "King"]


def test_loading_bracket_sets_does_not_preselect_without_applying():
    app = make_app(ProjectState())
    combo = DummyCombo()
    app.bracket_set_var = DummyVar("stale")
    app._bracket_set_combos = lambda: [combo]
    app._set_startgg_controls_visible = lambda _visible: None
    app.startgg_status_var = DummyVar()
    app.log = lambda _message: None

    app._load_bracket_sets(
        [
            BracketSet(
                id="1",
                event_name="Tekken 8",
                round_name="Winners Finals",
                player1="Alice",
                player2="Bob",
                state=1,
            )
        ]
    )

    assert app.bracket_set_var.get() == ""
    assert combo.values == ["Tekken 8 - Winners Finals - Alice vs Bob [1]"]


def test_matches_json_text_can_be_edited_and_applied():
    state = ProjectState(
        matches=[
            MatchSegment(start=10.0, player1="Alice", player2="Bob"),
            MatchSegment(start=10.1, player1="Alice", player2="Bob"),
        ],
    )
    app = make_app(state)
    edited = """
[
  {
    "start": 10.0,
    "end": 120.0,
    "player1": "Alice",
    "player2": "Bob",
    "character1": "Jin",
    "character2": "King",
    "round_name": "Winners Finals",
    "notes": ""
  }
]
"""

    app._load_matches_json_text(edited)

    assert len(state.matches) == 1
    assert state.matches[0].end == 120.0
    assert state.matches[0].character1 == "Jin"
    assert state.players == ["Alice", "Bob"]
    assert "Winners Finals" in app._matches_json_text()


def test_bundled_portrait_path_is_saved_as_portable_default(tmp_path):
    bundled = tmp_path / "portraits"
    bundled.mkdir()
    custom = tmp_path / "custom"
    custom.mkdir()
    app = make_app(ProjectState())
    app._default_portrait_dir = lambda: bundled

    assert app._portable_portrait_setting("") == ""
    assert app._portable_portrait_setting(str(bundled)) == ""
    assert app._portable_portrait_setting(str(custom)) == str(custom)
    assert app._portrait_dir_or_default("") == str(bundled)


def test_character_autocomplete_prefers_prefix_matches():
    app = make_app(ProjectState(characters=["King", "Armor King", "Kazuya", "Devil Jin"]))

    assert app._best_character_prefix_match("ki") == "King"
    assert app._best_character_prefix_match("kaz") == "Kazuya"
    assert app._best_character_prefix_match("jin") == ""
    assert app._matching_characters("jin", limit=1) == ["Devil Jin"]


def test_startgg_tournament_slug_normalization_accepts_urls_and_slugs():
    assert normalize_tournament_slug("https://www.start.gg/tournament/basement-brawl-2-1/details") == "tournament/basement-brawl-2-1"
    assert normalize_tournament_slug("tournament/basement-brawl-2-1") == "tournament/basement-brawl-2-1"
    assert normalize_tournament_slug("basement-brawl-2-1") == "tournament/basement-brawl-2-1"


def test_startgg_tournament_sets_parse_events_as_a_list():
    sets = parse_tournament_sets(
        {
            "events": [
                {
                    "name": "Tekken 8 Singles",
                    "sets": {
                        "nodes": [
                            {
                                "id": "123",
                                "fullRoundText": "Winners Round 1",
                                "state": 1,
                                "slots": [
                                    {"entrant": {"id": 1, "name": "Alice"}},
                                    {"entrant": {"id": 2, "name": "Bob"}},
                                ],
                                "games": [
                                    {
                                        "selections": [
                                            {"entrant": {"id": 1}, "character": {"name": "Jin"}},
                                            {"entrant": {"id": 2}, "character": {"name": "King"}},
                                        ]
                                    },
                                    {
                                        "selections": [
                                            {"entrant": {"id": 1}, "character": {"name": "Jun"}},
                                        ]
                                    },
                                ],
                            }
                        ]
                    },
                }
            ]
        }
    )

    assert len(sets) == 1
    assert sets[0].event_name == "Tekken 8 Singles"
    assert sets[0].round_name == "Winners Round 1"
    assert sets[0].player1 == "Alice"
    assert sets[0].player2 == "Bob"
    assert sets[0].character1 == "Jun"
    assert sets[0].character2 == "King"


def test_startgg_owned_tournaments_reads_all_pages(monkeypatch):
    def fake_graphql(_token, _query, variables):
        if "ownerId" not in variables:
            return {"data": {"currentUser": {"id": "42"}}}
        page = variables["page"]
        if page == 1:
            return {
                "data": {
                    "tournaments": {
                        "pageInfo": {"totalPages": 2},
                        "nodes": [
                            {"name": "Recent 1", "slug": "tournament/recent-1", "startAt": 30},
                            {"name": "Recent 2", "slug": "tournament/recent-2", "startAt": 20},
                        ],
                    }
                }
            }
        return {
            "data": {
                "tournaments": {
                    "pageInfo": {"totalPages": 2},
                    "nodes": [
                        {"name": "Older", "slug": "tournament/older", "startAt": 10},
                    ],
                }
            }
        }

    monkeypatch.setattr(startgg_client, "_graphql", fake_graphql)

    tournaments = startgg_client.fetch_owned_tournaments("token", per_page=2)

    assert [item.slug for item in tournaments] == [
        "tournament/recent-1",
        "tournament/recent-2",
        "tournament/older",
    ]
