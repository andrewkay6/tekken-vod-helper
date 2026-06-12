from tekken_vod_helper.app import TekkenVodHelperApp
from tekken_vod_helper.models import ExportJob, MatchSegment, ProjectState


def make_app(state):
    app = TekkenVodHelperApp.__new__(TekkenVodHelperApp)
    app.state = state
    app.log_messages = []
    app.log = app.log_messages.append
    app._thread_log = app.log_messages.append
    return app


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


def test_new_video_match_reset_is_empty():
    state = ProjectState(
        video_path="old.mkv",
        matches=[MatchSegment(start=10.0)],
    )
    app = make_app(state)

    app._reset_matches_for_new_video("old.mkv", "new.mkv")

    assert state.matches == []


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
