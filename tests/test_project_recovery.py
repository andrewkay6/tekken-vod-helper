import json

from tekken_vod_helper import app as app_module
from tekken_vod_helper.app import TekkenVodHelperApp
from tekken_vod_helper.models import ProjectState


class DummyVar:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def make_recovery_app(state, tmp_path):
    app = TekkenVodHelperApp.__new__(TekkenVodHelperApp)
    app.project_state = state
    app.project_path = None
    app.log_messages = []
    app.log = app.log_messages.append
    app.temporary_project_path = tmp_path / "recovery.tvh.json"
    app.temporary_project_dismissed_path = tmp_path / "recovery.tvh.json.deleted"
    app.temporary_project_dismissed = False
    app.temporary_project_after_id = None
    app.apply_match_details = lambda show_errors=False: None
    app._saved_portrait_setting = lambda configured: configured
    app.event_var = DummyVar(state.event_name)
    app.output_var = DummyVar(state.output_dir)
    app.portrait_var = DummyVar(state.portrait_dir)
    app.thumbnail_background_var = DummyVar(state.thumbnail_background_path)
    app.ffmpeg_var = DummyVar(state.ffmpeg_path)
    app.ffprobe_var = DummyVar(state.ffprobe_path)
    app.reencode_var = DummyVar(state.reencode)
    app.overlay_description_var = DummyVar(state.overlay.description)
    app.overlay_subtitle_var = DummyVar(state.overlay.subtitle)
    app.overlay_p1_var = DummyVar(state.overlay.p1name)
    app.overlay_p2_var = DummyVar(state.overlay.p2name)
    app.overlay_p1score_var = DummyVar(state.overlay.p1score)
    app.overlay_p2score_var = DummyVar(state.overlay.p2score)
    app.overlay_font_var = DummyVar(state.overlay.font)
    app.overlay_accent_color_var = DummyVar(state.overlay.accent_color)
    app.overlay_opacity_var = DummyVar("{:.0f}".format(state.overlay.opacity * 100))
    app.obs_host_var = DummyVar(state.obs.host)
    app.obs_port_var = DummyVar(str(state.obs.port))
    app.obs_password_var = DummyVar(state.obs.password)
    app.startgg_token_var = DummyVar("")
    app.startgg_slug_var = DummyVar(state.startgg.tournament_slug)
    return app


def test_project_json_writer_round_trips_recovery_copy(tmp_path):
    app = make_recovery_app(ProjectState(event_name="Basement Brawl"), tmp_path)

    app._write_project_json(app.temporary_project_path)

    payload = json.loads(app.temporary_project_path.read_text(encoding="utf-8"))
    assert payload["event_name"] == "Basement Brawl"
    assert ProjectState.from_dict(payload).event_name == "Basement Brawl"


def test_temporary_project_on_exit_syncs_controls_to_recovery_file(tmp_path):
    app = make_recovery_app(ProjectState(), tmp_path)
    app.event_var.set("Friday Night Tekken")
    app.output_var.set(str(tmp_path / "clips"))
    app.overlay_p1_var.set("Alice")
    app.overlay_p2_var.set("Bob")

    app._save_temporary_project_on_exit()

    payload = json.loads(app.temporary_project_path.read_text(encoding="utf-8"))
    assert payload["event_name"] == "Friday Night Tekken"
    assert payload["output_dir"] == str(tmp_path / "clips")
    assert payload["overlay"]["p1name"] == "Alice"
    assert payload["overlay"]["p2name"] == "Bob"


def test_temporary_project_on_exit_removes_recovery_when_saved_project_matches(tmp_path):
    app = make_recovery_app(ProjectState(event_name="Saved Event"), tmp_path)
    app.project_path = tmp_path / "saved.tvh.json"
    app.temporary_project_path.write_text("stale", encoding="utf-8")
    app._write_project_json(app.project_path)

    app._save_temporary_project_on_exit()

    assert not app.temporary_project_path.exists()


def test_recovery_prompt_delete_choice_marks_recovery_deleted(tmp_path):
    app = make_recovery_app(ProjectState(event_name="Saved Event"), tmp_path)
    app.temporary_project_path.write_text("stale", encoding="utf-8")
    app._temporary_project_recovery_choice = lambda: "delete"

    app._offer_temporary_project_recovery()

    assert not app.temporary_project_path.exists()
    assert app.temporary_project_dismissed_path.read_text(encoding="utf-8") == "stale"
    assert app.temporary_project_dismissed
    assert "Marked recovery copy deleted" in app.log_messages[-1]


def test_clear_temporary_project_backups_removes_active_and_deleted(tmp_path, monkeypatch):
    app = make_recovery_app(ProjectState(event_name="Saved Event"), tmp_path)
    app.temporary_project_path.write_text("active", encoding="utf-8")
    app.temporary_project_dismissed_path.write_text("deleted", encoding="utf-8")
    app.temporary_project_dismissed = True
    messages = []
    monkeypatch.setattr(app_module.messagebox, "showinfo", lambda title, message: messages.append((title, message)))

    app.clear_temporary_project_backups()

    assert not app.temporary_project_path.exists()
    assert not app.temporary_project_dismissed_path.exists()
    assert not app.temporary_project_dismissed
    assert messages[-1][0] == "Recovery backups"
