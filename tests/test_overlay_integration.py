from tekken_vod_helper.app import TekkenVodHelperApp
from tekken_vod_helper.models import MatchSegment, OverlayState, ProjectState
from tekken_vod_helper import startgg_client
from tekken_vod_helper import ffmpeg_tools
from tekken_vod_helper.overlay_server import OverlayServer
from tekken_vod_helper.startgg_client import BracketSet, TournamentSummary, parse_tournament_sets, normalize_tournament_slug


def make_app(state):
    app = TekkenVodHelperApp.__new__(TekkenVodHelperApp)
    app.project_state = state
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
        "accent_color": "#f3135e",
        "opacity": 1.0,
    }


def test_overlay_server_starts_without_ffmpeg(tmp_path, monkeypatch):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<!doctype html><title>overlay</title>", encoding="utf-8")

    monkeypatch.setattr(ffmpeg_tools.shutil, "which", lambda _name: None)
    monkeypatch.setattr(ffmpeg_tools.sys, "executable", str(tmp_path / "TekkenVodHelper.exe"))

    server = OverlayServer(static_dir, port=0)
    try:
        server.start()
        server.write_state(OverlayState(description="Overlay Only"))

        assert (server.runtime_dir / "index.html").exists()
        assert '"description": "Overlay Only"' in (server.runtime_dir / "state.json").read_text(encoding="utf-8")
    finally:
        server.stop()


def test_overlay_static_runtime_includes_svg_and_obs_safe_script():
    static_dir = Path(__file__).resolve().parents[1] / "tekken_vod_helper" / "overlay_static"
    server = OverlayServer(static_dir, port=0)

    server._prepare_runtime_dir()

    assert (server.runtime_dir / "assets" / "scoreboard.svg").exists()
    script = (server.runtime_dir / "index.js").read_text(encoding="utf-8")
    assert "replaceAll" not in script
    assert "fallbackPlateSvg" in script


def test_project_state_round_trips_overlay_obs_and_startgg_settings():
    state = ProjectState()
    state.overlay = OverlayState(description="Event", p1name="Alice", p2name="Bob")
    state.obs.host = "localhost"
    state.obs.port = 4456
    state.obs.password = "secret"
    state.startgg.token = "token"
    state.startgg.tournament_slug = "tournament/test-event"
    state.player_characters = {"alice": "Jun"}

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
    assert loaded.player_characters == {"alice": "Jun"}


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
    state = ProjectState(player_characters={"alice": "Jin", "bob": "King"})
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
    assert combo.values == ["Tekken 8 - Winners Finals - Alice vs Bob"]


def test_loading_duplicate_bracket_set_labels_stays_selectable_without_preview_ids():
    app = make_app(ProjectState())
    combo = DummyCombo()
    app.bracket_set_var = DummyVar()
    app._bracket_set_combos = lambda: [combo]
    app._set_startgg_controls_visible = lambda _visible: None
    app.startgg_status_var = DummyVar()
    app.log = lambda _message: None
    app._refresh_combo_values = lambda: None

    app._load_bracket_sets(
        [
            BracketSet(
                id="preview_1",
                event_name="Tekken 8",
                round_name="Winners Round 1",
                player1="Alice",
                player2="Bob",
                state=1,
            ),
            BracketSet(
                id="preview_2",
                event_name="Tekken 8",
                round_name="Winners Round 1",
                player1="Alice",
                player2="Bob",
                state=1,
            ),
        ]
    )

    assert combo.values == [
        "Tekken 8 - Winners Round 1 - Alice vs Bob",
        "Tekken 8 - Winners Round 1 - Alice vs Bob (2)",
    ]
    assert app.bracket_set_by_label[combo.values[1]].id == "preview_2"


def test_loading_bracket_sets_populates_player_dropdown_values():
    state = ProjectState(players=["Existing"])
    app = make_app(state)
    combo = DummyCombo()
    app.bracket_set_var = DummyVar()
    app._bracket_set_combos = lambda: []
    app._set_startgg_controls_visible = lambda _visible: None
    app.startgg_status_var = DummyVar()
    app.log = lambda _message: None
    app.overlay_p1_combo = combo
    app.overlay_p2_combo = DummyCombo()
    app.overlay_character1_combo = DummyCombo()
    app.overlay_character2_combo = DummyCombo()

    app._load_bracket_sets(
        [
            BracketSet(
                id="1",
                event_name="Tekken 8",
                round_name="Winners Finals",
                player1="Alice",
                player2="Bob",
                state=1,
            ),
            BracketSet(
                id="2",
                event_name="Tekken 8",
                round_name="Grand Finals",
                player1="Alice",
                player2="Carol",
                state=1,
            ),
        ]
    )

    assert state.players == ["Alice", "Bob", "Carol", "Existing"]
    assert combo.values == ["Alice", "Bob", "Carol", "Existing"]


def test_picker_tournament_selection_clears_uncached_bracket_and_refreshes_board():
    app = make_app(ProjectState())
    app.startgg_slug_var = DummyVar()
    app.overlay_description_var = DummyVar()
    app.event_var = DummyVar()
    app.bracket_set_var = DummyVar("old")
    app.bracket_sets = [BracketSet("old", "Old", "Winners Final", "Alice", "Bob", 1)]
    app.bracket_set_by_label = {"old": app.bracket_sets[0]}
    app._sync_paths_to_state = lambda: None
    refreshes = []
    app._refresh_startgg_picker = lambda: refreshes.append(True)

    app._set_selected_tournament_without_fetch(TournamentSummary("Basement Brawl 4", "tournament/basement-brawl-4"))

    assert app.startgg_slug_var.get() == "tournament/basement-brawl-4"
    assert app.overlay_description_var.get() == "Basement Brawl 4"
    assert app.event_var.get() == "Basement Brawl 4"
    assert app.bracket_sets == []
    assert app.bracket_set_by_label == {}
    assert app.bracket_set_var.get() == ""
    assert refreshes == [True]


def test_picker_tournament_selection_uses_cached_bracket_without_fetching():
    app = make_app(ProjectState())
    app.startgg_slug_var = DummyVar()
    app.overlay_description_var = DummyVar()
    app.event_var = DummyVar()
    app.bracket_set_var = DummyVar()
    app._sync_paths_to_state = lambda: None
    app._bracket_set_combos = lambda: []
    app._set_startgg_controls_visible = lambda _visible: None
    app.startgg_status_var = DummyVar()
    app.log = lambda _message: None
    app._refresh_combo_values = lambda: None
    cached_set = BracketSet("1", "Tekken 8 Singles", "Winners Round 1", "Alice", "Bob", 1)
    app.startgg_sets_cache = {"tournament/basement-brawl-4": [cached_set]}

    app._set_selected_tournament_without_fetch(TournamentSummary("Basement Brawl 4", "tournament/basement-brawl-4"))

    assert app.bracket_sets == [cached_set]
    assert app.bracket_set_by_label == {"Tekken 8 Singles - Winners Round 1 - Alice vs Bob": cached_set}


def test_bracket_picker_groups_winners_above_losers_and_sorts_early_to_late():
    app = make_app(ProjectState())
    app.bracket_set_by_label = {
        "gf": BracketSet("gf", "Tekken 8 Singles", "Grand Final", "TBD", "TBD", 1),
        "lr2": BracketSet("lr2", "Tekken 8 Singles", "Losers Round 2", "TBD", "TBD", 1),
        "wqf": BracketSet("wqf", "Tekken 8 Singles", "Winners Quarter-Final", "TBD", "TBD", 1),
        "lr1": BracketSet("lr1", "Tekken 8 Singles", "Losers Round 1", "TBD", "TBD", 1),
        "wf": BracketSet("wf", "Tekken 8 Singles", "Winners Final", "TBD", "TBD", 1),
        "wr1": BracketSet("wr1", "Tekken 8 Singles", "Winners Round 1", "TBD", "TBD", 1),
    }

    grouped = app._bracket_sets_by_event_and_section()

    assert [section for section, _rounds in grouped[0][1]] == ["Winners", "Losers"]
    winners = grouped[0][1][0][1]
    losers = grouped[0][1][1][1]
    assert [round_name for round_name, _sets in winners] == ["Winners Round 1", "Winners Quarter-Final", "Winners Final", "Grand Final"]
    assert [round_name for round_name, _sets in losers] == ["Losers Round 1", "Losers Round 2"]


def test_bracket_picker_offsets_smaller_rounds_for_bracket_shape():
    app = make_app(ProjectState())

    assert app._bracket_round_top_offset(4, 4) == 0
    assert app._bracket_round_top_offset(4, 2) > 0
    assert app._bracket_card_gap(4, 2) > app._bracket_card_gap(4, 4)


def test_bracket_picker_connector_fallback_pairs_sources_by_round_size():
    app = make_app(ProjectState())

    assert app._fallback_connector_source_indexes(4, 2, 0) == [0, 1]
    assert app._fallback_connector_source_indexes(4, 2, 1) == [2, 3]
    assert app._fallback_connector_source_indexes(2, 4, 2) == [1]


def test_bracket_picker_centers_child_between_parent_cards():
    app = make_app(ProjectState())
    rounds = [
        (
            "Winners Round 1",
            [
                ("p1", BracketSet("p1", "Tekken 8 Singles", "Winners Round 1", "Alice", "Bob", 1)),
                ("p2", BracketSet("p2", "Tekken 8 Singles", "Winners Round 1", "Carol", "Drew", 1)),
            ],
        ),
        (
            "Winners Final",
            [
                (
                    "child",
                    BracketSet(
                        "child",
                        "Tekken 8 Singles",
                        "Winners Final",
                        "TBD",
                        "TBD",
                        1,
                        parent_set_ids=["p1", "p2"],
                    ),
                )
            ],
        ),
    ]

    centers = app._bracket_round_card_centers(rounds)

    assert centers[1][0] == sum(centers[0]) / 2


def test_player_character_memory_is_case_insensitive_and_overwritten():
    state = ProjectState()
    app = make_app(state)

    app._remember_player_character(" Alice  Smith ", "Jun")
    app._remember_player_character("alice smith", "Lili")

    assert app._remembered_player_character("ALICE SMITH") == "Lili"
    assert state.player_characters == {"alice smith": "Lili"}


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
                                "round": 1,
                                "state": 1,
                                "slots": [
                                    {"entrant": {"id": 1, "name": "Alice"}},
                                    {"entrant": {"id": 2, "name": "Bob"}, "prereqId": "99", "prereqType": "set"},
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
    assert sets[0].round == 1
    assert sets[0].parent_set_ids == ["99"]


def test_startgg_tournament_sets_strip_preview_suffixes_from_names():
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
                                    {"entrant": {"id": 1, "name": "Alice [preview_423234908]"}},
                                    {"entrant": {"id": 2, "name": "Bob [preview_987]"}},
                                ],
                            }
                        ]
                    },
                }
            ]
        }
    )

    assert sets[0].player1 == "Alice"
    assert sets[0].player2 == "Bob"
    assert sets[0].label == "Tekken 8 Singles - Winners Round 1 - Alice vs Bob"


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
from pathlib import Path
