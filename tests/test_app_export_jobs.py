from pathlib import Path

from tekken_vod_helper import app as app_module
from tekken_vod_helper.app import TekkenVodHelperApp
from tekken_vod_helper.models import ExportJob, MatchSegment, ProjectState


def make_app(state):
    app = TekkenVodHelperApp.__new__(TekkenVodHelperApp)
    app.project_state = state
    app.loading_form = False
    app.log_messages = []
    app.log = app.log_messages.append
    app._thread_log = app.log_messages.append
    return app


class DummyVar:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def test_export_jobs_use_next_start_when_end_is_unmarked(tmp_path):
    state = ProjectState(
        video_path=str(tmp_path / "tournament.mkv"),
        duration=600.0,
        matches=[
            MatchSegment(start=10.0, player1="Alice", player2="Bob"),
            MatchSegment(start=120.0, player1="Carol", player2="Dave"),
        ],
    )
    app = make_app(state)

    jobs = app._build_export_jobs(str(tmp_path / "out"))

    assert [(job.start, job.end) for job in jobs] == [(10.0, 120.0), (120.0, 600.0)]
    assert all(job.clip_path.endswith(".mp4") for job in jobs)


def test_export_jobs_prefer_explicit_end(tmp_path):
    state = ProjectState(
        video_path=str(tmp_path / "tournament.mkv"),
        duration=600.0,
        matches=[
            MatchSegment(start=10.0, end=90.0),
            MatchSegment(start=120.0, end=240.0),
        ],
    )
    app = make_app(state)

    jobs = app._build_export_jobs(str(tmp_path / "out"))

    assert [(job.start, job.end) for job in jobs] == [(10.0, 90.0), (120.0, 240.0)]


def test_previous_match_for_start_finds_active_segment():
    state = ProjectState(
        duration=600.0,
        matches=[
            MatchSegment(start=10.0),
            MatchSegment(start=120.0),
        ],
    )
    app = make_app(state)

    previous = app._previous_match_for_start(state.sorted_matches(), 180.0)

    assert previous is state.matches[1]


def test_previous_match_for_start_uses_match_before_insert_point():
    state = ProjectState(
        duration=600.0,
        matches=[
            MatchSegment(start=10.0),
            MatchSegment(start=120.0),
            MatchSegment(start=300.0),
        ],
    )
    app = make_app(state)

    previous = app._previous_match_for_start(state.sorted_matches(), 200.0)

    assert previous is state.matches[1]


def test_new_video_keeps_existing_match_timestamps():
    state = ProjectState(
        video_path="old.mkv",
        matches=[MatchSegment(start=10.0)],
    )
    app = make_app(state)

    app._reset_matches_for_new_video("old.mkv", "new.mkv")

    assert [match.start for match in state.matches] == [10.0]


def test_start_now_updates_selected_match_without_inserting():
    state = ProjectState(
        video_path="vod.mkv",
        duration=600.0,
        matches=[MatchSegment(start=10.0), MatchSegment(start=120.0)],
    )
    app = make_app(state)
    app.current_time = 33.3334
    app._selected_tree_index = lambda: 0
    app._refresh_tree = lambda select_start=None: None
    app.player1_var = DummyVar()
    app.player2_var = DummyVar()
    app.character1_var = DummyVar()
    app.character2_var = DummyVar()
    app.round_var = DummyVar()
    app.notes_var = DummyVar()
    app.start_var = DummyVar()
    app.end_var = DummyVar()

    app.set_selected_start_to_current_time()

    assert [match.start for match in state.sorted_matches()] == [33.333, 120.0]


def test_end_now_updates_selected_match_from_current_time():
    state = ProjectState(
        video_path="vod.mkv",
        duration=600.0,
        matches=[MatchSegment(start=10.0), MatchSegment(start=120.0)],
    )
    app = make_app(state)
    app.current_time = 88.1254
    app._selected_tree_index = lambda: 0
    app._refresh_tree = lambda select_start=None: None
    app.player1_var = DummyVar()
    app.player2_var = DummyVar()
    app.character1_var = DummyVar()
    app.character2_var = DummyVar()
    app.round_var = DummyVar()
    app.notes_var = DummyVar()
    app.start_var = DummyVar()
    app.end_var = DummyVar()

    app.set_selected_end_to_current_time()

    assert state.sorted_matches()[0].end == 88.125


def test_go_to_selected_match_uses_seek_helper():
    state = ProjectState(
        video_path="vod.mkv",
        duration=600.0,
        matches=[MatchSegment(start=10.0), MatchSegment(start=120.0)],
    )
    app = make_app(state)
    calls = []
    app._selected_tree_index = lambda: 1
    app._ensure_vlc_player_for_seek = lambda: True
    app._seek_to_time = calls.append
    app.scrub_var = DummyVar()

    app.go_to_selected_match()

    assert app.scrub_var.get() == 120.0
    assert calls == [120.0]


def test_project_state_round_trips_thumbnail_background():
    state = ProjectState(thumbnail_background_path="C:/thumbs/bg.jpg")

    loaded = ProjectState.from_dict(state.to_dict())

    assert loaded.thumbnail_background_path == "C:/thumbs/bg.jpg"


def test_upload_sidecars_include_event_and_boilerplate(tmp_path):
    state = ProjectState(
        event_name="Friday Night Tekken",
        description_boilerplate="Follow us: https://example.test",
    )
    match = MatchSegment(
        start=10.0,
        end=90.0,
        player1="Alice",
        player2="Bob",
        character1="Jin",
        character2="King",
        round_name="Winners Finals",
    )
    app = make_app(state)
    job = ExportJob(
        match=match,
        index=1,
        start=match.start,
        end=match.end,
        folder_name="match",
        clip_path=str(tmp_path / "clip.mp4"),
        thumbnail_path=str(tmp_path / "thumbnail.jpg"),
        metadata_path=str(tmp_path / "match.json"),
        title_path=str(tmp_path / "title.txt"),
        description_path=str(tmp_path / "description.txt"),
    )

    app._write_upload_sidecars(job)

    assert (tmp_path / "title.txt").read_text(encoding="utf-8").strip() == (
        "Alice (Jin) vs Bob (King) - Winners Finals - Friday Night Tekken"
    )
    description = (tmp_path / "description.txt").read_text(encoding="utf-8")
    assert "Event: Friday Night Tekken" in description
    assert "Follow us: https://example.test" in description


def test_export_artifacts_use_resolved_portrait_dir(tmp_path, monkeypatch):
    state = ProjectState(event_name="Event", thumbnail_background_path="")
    match = MatchSegment(start=10.0, end=90.0, player1="Alice", player2="Bob", character1="Jin")
    app = make_app(state)
    app._resolved_portrait_dir = lambda: str(tmp_path / "bundled_portraits")
    calls = []

    def fake_make_thumbnail(*args, **kwargs):
        calls.append((args, kwargs))
        Path(args[4]).write_text("thumbnail", encoding="utf-8")

    monkeypatch.setattr(app_module, "make_thumbnail", fake_make_thumbnail)
    job = ExportJob(
        match=match,
        index=1,
        start=match.start,
        end=match.end,
        folder_name="match",
        clip_path=str(tmp_path / "clip.mp4"),
        thumbnail_path=str(tmp_path / "thumbnail.jpg"),
        metadata_path=str(tmp_path / "match.json"),
        title_path=str(tmp_path / "title.txt"),
        description_path=str(tmp_path / "description.txt"),
    )

    app._write_export_artifacts(job)

    assert calls[0][0][5] == str(tmp_path / "bundled_portraits")
    assert (tmp_path / "match.json").exists()
    assert (tmp_path / "title.txt").exists()
    assert (tmp_path / "description.txt").exists()


def test_metadata_only_worker_writes_artifacts_without_video_clip(tmp_path, monkeypatch):
    state = ProjectState(event_name="Event")
    match = MatchSegment(start=10.0, end=90.0, player1="Alice", player2="Bob")
    app = make_app(state)
    app._resolved_portrait_dir = lambda: str(tmp_path / "portraits")
    app.log_queue = type("DummyQueue", (), {"items": [], "put": lambda self, item: self.items.append(item)})()

    def fake_make_thumbnail(*args, **_kwargs):
        Path(args[4]).write_text("thumbnail", encoding="utf-8")

    monkeypatch.setattr(app_module, "make_thumbnail", fake_make_thumbnail)
    job = ExportJob(
        match=match,
        index=1,
        start=match.start,
        end=match.end,
        folder_name="match",
        clip_path=str(tmp_path / "match" / "clip.mp4"),
        thumbnail_path=str(tmp_path / "match" / "thumbnail.jpg"),
        metadata_path=str(tmp_path / "match" / "match.json"),
        title_path=str(tmp_path / "match" / "title.txt"),
        description_path=str(tmp_path / "match" / "description.txt"),
    )

    app._metadata_only_worker([job], str(tmp_path))

    assert not (tmp_path / "match" / "clip.mp4").exists()
    assert (tmp_path / "match" / "thumbnail.jpg").read_text(encoding="utf-8") == "thumbnail"
    assert (tmp_path / "match" / "match.json").exists()
    assert app.log_queue.items == [("done", "Metadata generation complete.")]
