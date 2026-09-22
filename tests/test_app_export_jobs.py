from pathlib import Path

import json

import pytest

from tekken_vod_helper import app as app_module
from tekken_vod_helper.app import TekkenVodHelperApp
from tekken_vod_helper.models import DEFAULT_DESCRIPTION_BOILERPLATE, ExportJob, MatchSegment, ProjectState


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


def test_export_jobs_place_clips_in_drag_drop_upload_directory(tmp_path):
    state = ProjectState(
        video_path=str(tmp_path / "tournament.mkv"),
        duration=600.0,
        matches=[
            MatchSegment(start=10.0, end=90.0, player1="Alice", player2="Bob"),
            MatchSegment(start=120.0, end=240.0, player1="Carol", player2="Dave"),
        ],
    )
    app = make_app(state)

    jobs = app._build_export_jobs(str(tmp_path / "out"))

    assert len(jobs) == 2
    assert Path(jobs[0].clip_path).parent == tmp_path / "out" / "_youtube_uploads"
    assert Path(jobs[1].clip_path).parent == tmp_path / "out" / "_youtube_uploads"
    assert Path(jobs[0].clip_path).name.startswith("tvh-001-")
    assert Path(jobs[1].clip_path).name.startswith("tvh-002-")
    assert jobs[0].youtube_metadata_path.endswith("youtube.json")


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
        upload_id="tvh-001-test01",
        start=match.start,
        end=match.end,
        folder_name="match",
        clip_path=str(tmp_path / "clip.mp4"),
        thumbnail_path=str(tmp_path / "thumbnail.jpg"),
        metadata_path=str(tmp_path / "match.json"),
        youtube_metadata_path=str(tmp_path / "youtube.json"),
        title_path=str(tmp_path / "title.txt"),
        description_path=str(tmp_path / "description.txt"),
    )

    app._write_upload_sidecars(job)

    assert (tmp_path / "title.txt").read_text(encoding="utf-8").strip() == (
        "Alice (Jin) vs Bob (King) - Winners Finals - Friday Night Tekken"
    )
    description = (tmp_path / "description.txt").read_text(encoding="utf-8")
    assert not description.startswith("Alice (Jin) vs Bob (King)")
    assert "Event: Friday Night Tekken" not in description
    assert "Players:" not in description
    assert "Clip time:" not in description
    assert "Follow us: https://example.test" in description
    assert description.strip() == "Follow us: https://example.test"


def test_upload_description_uses_default_boilerplate_when_project_is_blank(tmp_path):
    state = ProjectState(
        event_name="Friday Night Tekken",
        description_boilerplate="",
    )
    match = MatchSegment(start=10.0, end=90.0, player1="Alice", player2="Bob")
    app = make_app(state)
    job = ExportJob(
        match=match,
        index=1,
        upload_id="tvh-001-test01",
        start=match.start,
        end=match.end,
        folder_name="match",
        clip_path=str(tmp_path / "match.mp4"),
        thumbnail_path=str(tmp_path / "thumbnail.jpg"),
        metadata_path=str(tmp_path / "match.json"),
        youtube_metadata_path=str(tmp_path / "youtube.json"),
        title_path=str(tmp_path / "title.txt"),
        description_path=str(tmp_path / "description.txt"),
    )

    description = app._upload_description(job)

    assert description == DEFAULT_DESCRIPTION_BOILERPLATE
    assert "Event:" not in description
    assert "Players:" not in description
    assert "https://kwtekken.ca" in description
    assert "https://discord.gg/mCwGVgjXED" in description


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
        upload_id="tvh-001-test01",
        start=match.start,
        end=match.end,
        folder_name="match",
        clip_path=str(tmp_path / "clip.mp4"),
        thumbnail_path=str(tmp_path / "thumbnail.jpg"),
        metadata_path=str(tmp_path / "match.json"),
        youtube_metadata_path=str(tmp_path / "youtube.json"),
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
        upload_id="tvh-001-test01",
        start=match.start,
        end=match.end,
        folder_name="match",
        clip_path=str(tmp_path / "match" / "clip.mp4"),
        thumbnail_path=str(tmp_path / "match" / "thumbnail.jpg"),
        metadata_path=str(tmp_path / "match" / "match.json"),
        youtube_metadata_path=str(tmp_path / "match" / "youtube.json"),
        title_path=str(tmp_path / "match" / "title.txt"),
        description_path=str(tmp_path / "match" / "description.txt"),
    )

    app._metadata_only_worker([job], str(tmp_path))

    assert not (tmp_path / "match" / "clip.mp4").exists()
    assert (tmp_path / "match" / "thumbnail.jpg").read_text(encoding="utf-8") == "thumbnail"
    assert (tmp_path / "match" / "match.json").exists()
    assert app.log_queue.items == [("done", "Metadata generation complete.")]


def test_sample_project_generates_upload_metadata_and_dry_run_payloads(tmp_path):
    pillow_image = pytest.importorskip("PIL.Image")
    fixture_path = Path(__file__).parent / "fixtures" / "sep19_template.tvh.json"
    state = ProjectState.from_dict(json.loads(fixture_path.read_text(encoding="utf-8")))
    state.video_path = str(tmp_path / "sample-source.mkv")
    state.output_dir = str(tmp_path / "exports")
    state.thumbnail_background_path = str(tmp_path / "solid-background.jpg")
    pillow_image.new("RGB", (1280, 720), (24, 36, 52)).save(state.thumbnail_background_path)

    app = make_app(state)
    app._resolved_portrait_dir = lambda: str(tmp_path / "portraits")
    app.log_queue = type("DummyQueue", (), {"items": [], "put": lambda self, item: self.items.append(item)})()

    jobs = app._build_export_jobs(state.output_dir)
    app._metadata_only_worker(jobs, state.output_dir)

    upload_dir = Path(state.output_dir) / "_youtube_uploads"
    manifest_path = upload_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(jobs) == 9
    assert len(manifest["entries"]) == 9
    assert app.log_queue.items[-1] == ("done", "Metadata generation complete.")
    assert all(Path(job.clip_path).parent == upload_dir for job in jobs)
    assert all(Path(job.clip_path).suffix == ".mp4" for job in jobs)
    assert not any(Path(job.clip_path).exists() for job in jobs)

    first_job = jobs[0]
    first_metadata = json.loads(Path(first_job.youtube_metadata_path).read_text(encoding="utf-8"))
    first_match = json.loads(Path(first_job.metadata_path).read_text(encoding="utf-8"))
    assert Path(first_job.thumbnail_path).exists()
    assert Path(first_job.thumbnail_path).stat().st_size > 0
    assert Path(first_job.title_path).read_text(encoding="utf-8").strip() == (
        "Ag (Claudio) vs CrispyBacon (Alisa) - Winners Round 1 - Basement Brawl #7 (CAF\u00c9 EDITION)"
    )
    assert first_metadata["upload_id"] == first_job.upload_id
    assert first_metadata["video_file"].startswith(first_job.upload_id)
    assert first_metadata["thumbnail_path"] == first_job.thumbnail_path
    assert first_metadata["start_timestamp"] == "00:03:39.669"
    assert first_metadata["end_timestamp"] == "00:16:02.377"
    assert first_metadata["privacy_status"] == "private"
    assert first_metadata["self_declared_made_for_kids"] is False
    assert DEFAULT_DESCRIPTION_BOILERPLATE in first_metadata["description"]
    assert first_match["clip"] == Path(first_job.clip_path).name
    assert first_match["duration"] == pytest.approx(742.708)

    entries = app._read_upload_queue_entries(Path(state.output_dir))
    app._set_upload_metadata_entries(entries)
    app.upload_youtube_rows = [
        {
            "video_id": "yt-{:03d}".format(job.index),
            "title": "{} raw upload title".format(job.upload_id),
            "description": "",
            "privacy_status": "private",
            "upload_status": "uploaded",
            "processing_status": "succeeded",
            "thumbnail_url": "",
        }
        for job in jobs
    ]
    review_rows = app._upload_review_rows()
    dry_run = app._dry_run_upload_payload(review_rows[0])

    assert len(review_rows) == 9
    assert all(row["metadata"] and row["youtube"] for row in review_rows)
    assert dry_run["dry_run"] is True
    assert dry_run["video_id"] == "yt-001"
    assert dry_run["upload_id"] == first_job.upload_id
    assert dry_run["update"]["title"] == first_metadata["title"]
    assert dry_run["update"]["description"] == first_metadata["description"]
    assert dry_run["update"]["thumbnail_path"] == first_job.thumbnail_path
    assert dry_run["update"]["privacy_status"] == "private"
    assert dry_run["update"]["self_declared_made_for_kids"] is False


def test_upload_detail_selection_does_not_fetch_remote_thumbnail(monkeypatch):
    app = make_app(ProjectState())
    app.upload_thumbnail_cache = {}
    monkeypatch.setattr(app_module.urllib.request, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("remote fetch")))

    image = app._upload_detail_thumbnail(
        {
            "youtube_id": "yt-001",
            "metadata": {},
            "youtube": {"thumbnail_url": "https://example.test/thumb.jpg"},
        }
    )

    assert image is None


def test_upload_editor_prefills_from_youtube_row_without_export_metadata():
    app = make_app(ProjectState())
    row = {
        "metadata": {},
        "youtube": {
            "title": "Existing Full VOD",
            "description": "Already uploaded description",
            "tags": ["KW Tekken", "Tekken"],
            "privacy_status": "public",
            "local_thumbnail_path": "C:/thumb.jpg",
        },
    }

    metadata = app._upload_editor_metadata_for_row(row)

    assert metadata["title"] == "Existing Full VOD"
    assert metadata["description"] == "Already uploaded description"
    assert metadata["tags"] == ["KW Tekken", "Tekken"]
    assert metadata["thumbnail_path"] == "C:/thumb.jpg"
    assert metadata["privacy_status"] == "public"
    assert metadata["self_declared_made_for_kids"] is False
    assert row["metadata"] == {}


def test_upload_review_row_requires_saved_changes_before_saved_marker():
    app = make_app(ProjectState())
    loaded_row = {"metadata": {"title": "New title"}, "youtube": {"video_id": "yt-001"}}
    saved_row = {**loaded_row, "saved_changes": True}
    unsaved_row = {**saved_row, "editor_dirty": True}

    assert app._upload_review_tree_values(loaded_row)[0] == "Loaded"
    assert app._upload_review_tree_values(saved_row)[0] == "Saved"
    assert app._upload_review_tree_values(unsaved_row)[0] == "Unsaved"


def test_upload_change_detail_text_groups_fields_by_video():
    app = make_app(ProjectState())
    row = {
        "youtube_id": "yt-001",
        "current_title": "Old title",
        "youtube": {
            "title": "Old title",
            "description": "old",
            "tags": ["old"],
            "privacy_status": "private",
        },
        "metadata": {
            "title": "New title",
            "description": "new description",
            "tags": ["KW Tekken", "Tekken"],
            "privacy_status": "public",
            "thumbnail_path": "C:/thumb.jpg",
            "self_declared_made_for_kids": False,
        },
    }

    details = app._upload_change_detail_text(row)

    assert details.startswith("Old title (yt-001)")
    assert "  Title: Old title -> New title" in details
    assert "  Visibility: private -> public" in details
    assert "  Thumbnail: C:/thumb.jpg" in details
    assert "  Made for kids: No" in details
