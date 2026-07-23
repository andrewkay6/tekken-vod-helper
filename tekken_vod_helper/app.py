import json
import queue
import sys
import threading
import tempfile
import tkinter as tk
import ctypes
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple

from .ffmpeg_tools import FfmpegError, extract_frame, probe_duration, require_tool, slice_clip
from .models import ExportJob, MatchSegment, OverlayState, ProjectState
from .obs_client import ObsClient, ObsError
from .overlay_server import OverlayServer
from .startgg_client import BracketSet, TournamentSummary, fetch_bracket_sets, fetch_owned_tournaments, normalize_tournament_slug
from .thumbnails import Image, ImageTk, find_portrait, make_thumbnail
from .util import parse_timestamp, safe_relative_name, seconds_to_timestamp, slugify, unique_sorted
from .windows_credentials import CredentialError, delete_startgg_token, read_startgg_token, write_startgg_token

try:
    import vlc
except ImportError:  # pragma: no cover - exercised by app runtime messaging
    vlc = None


def _set_windows_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("KWTekken.TekkenVodHelper")
    except Exception:
        pass


class TekkenVodHelperApp(tk.Tk):
    def __init__(self) -> None:
        _set_windows_app_user_model_id()
        super().__init__()
        self.title("KWTekken VOD Helper")
        self.geometry("1280x820")
        self.minsize(1040, 680)

        self.state = ProjectState(
            characters=self._load_default_characters(),
            portrait_dir="",
        )
        self.project_path: Optional[Path] = None
        self.current_time = 0.0
        self.selected_index: Optional[int] = None
        self.preview_after_id: Optional[str] = None
        self.playback_after_id: Optional[str] = None
        self.preview_photo = None
        self.vlc_instance = None
        self.vlc_player = None
        self.vlc_media_path = ""
        self.playback_requested = False
        self.updating_playback_time = False
        self.loading_form = False
        self.log_queue: "queue.Queue[Tuple[str, object]]" = queue.Queue()
        self.export_thread: Optional[threading.Thread] = None
        self.bracket_sets: List[BracketSet] = []
        self.bracket_set_by_label: Dict[str, BracketSet] = {}
        self.tournaments: List[TournamentSummary] = []
        self.tournament_by_label: Dict[str, TournamentSummary] = {}
        self.fetching_startgg_events = False
        self.fetching_startgg_sets = False
        self.overlay_server = OverlayServer(self._overlay_static_dir())
        self.output_var = tk.StringVar(value=self.state.output_dir)
        self.event_var = tk.StringVar(value=self.state.event_name)
        self.portrait_var = tk.StringVar(value=self.state.portrait_dir)
        self.thumbnail_background_var = tk.StringVar(value=self.state.thumbnail_background_path)
        self.ffmpeg_var = tk.StringVar(value=self.state.ffmpeg_path)
        self.ffprobe_var = tk.StringVar(value=self.state.ffprobe_path)
        self.reencode_var = tk.BooleanVar(value=self.state.reencode)
        self.overlay_description_var = tk.StringVar(value=self.state.overlay.description)
        self.overlay_subtitle_var = tk.StringVar(value=self.state.overlay.subtitle)
        self.overlay_p1_var = tk.StringVar(value=self.state.overlay.p1name)
        self.overlay_p2_var = tk.StringVar(value=self.state.overlay.p2name)
        self.overlay_character1_var = tk.StringVar()
        self.overlay_character2_var = tk.StringVar()
        self.overlay_p1score_var = tk.IntVar(value=self.state.overlay.p1score)
        self.overlay_p2score_var = tk.IntVar(value=self.state.overlay.p2score)
        self.overlay_font_var = tk.StringVar(value=self.state.overlay.font)
        self.overlay_url_var = tk.StringVar(value=self.overlay_server.url)
        self.obs_host_var = tk.StringVar(value=self.state.obs.host)
        self.obs_port_var = tk.StringVar(value=str(self.state.obs.port))
        self.obs_password_var = tk.StringVar(value=self.state.obs.password)
        self.obs_status_var = tk.StringVar(value="OBS not checked")
        self.startgg_token_var = tk.StringVar(value=self._initial_startgg_token())
        self.startgg_slug_var = tk.StringVar(value=self.state.startgg.tournament_slug)
        self.startgg_tournament_var = tk.StringVar()
        self.bracket_set_var = tk.StringVar()
        self.startgg_status_var = tk.StringVar(value="Load start.gg events to choose bracket matches.")
        self.startgg_events_button = None
        self.vod_startgg_events_button = None
        self.show_advanced_var = tk.BooleanVar(value=False)
        self.advanced_section = None
        self.log_frame = None
        self.overlay_scroll_canvas = None
        self.menu_items = {}
        self.portrait_photo_cache = {}
        self.app_icon_photos = []

        self._apply_app_icon()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._configure_styles()
        self.current_mode = "overlay"
        self._build_menu()
        self._build_ui()
        self._start_overlay_server()
        self.apply_overlay(log_message=False)
        self._refresh_combo_values()
        self._refresh_tree()
        self.after(150, self._poll_log_queue)

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=False)
        self._add_menu_command(file_menu, "Load Project...", self.load_project)
        self._add_menu_command(file_menu, "Save Project", self.save_project, accelerator="Ctrl+S")
        self._add_menu_command(file_menu, "Save Project As...", self.save_project_as)
        if self.current_mode == "vod":
            file_menu.add_separator()
            self._add_menu_command(file_menu, "Open Video...", self.open_video, accelerator="Ctrl+O")
        file_menu.add_separator()
        self._add_menu_command(file_menu, "Exit", self.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

        mode_menu = tk.Menu(menubar, tearoff=False)
        mode_menu.add_command(label="Overlay Control", command=lambda: self.set_workspace_mode("overlay"))
        mode_menu.add_command(label="VOD Editor", command=lambda: self.set_workspace_mode("vod"))
        menubar.add_cascade(label="Mode", menu=mode_menu)

        view_menu = tk.Menu(menubar, tearoff=False)
        view_menu.add_checkbutton(
            label="Show Advanced Data",
            variable=self.show_advanced_var,
            command=self.toggle_advanced_data,
        )
        menubar.add_cascade(label="View", menu=view_menu)

        settings_menu = tk.Menu(menubar, tearoff=False)
        self._add_menu_command(settings_menu, "Settings...", self.open_settings, accelerator="Ctrl+,")
        menubar.add_cascade(label="Settings", menu=settings_menu)

        self.config(menu=menubar)
        self.bind_all("<Control-o>", lambda _event: self._invoke_menu_command(self.open_video))
        self.bind_all("<Control-s>", lambda _event: self._invoke_menu_command(self.save_project))
        self.bind_all("<Control-e>", lambda _event: self._invoke_menu_command(self.export_clips))
        self.bind_all("<Control-comma>", lambda _event: self._invoke_menu_command(self.open_settings))

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.configure("Export.TButton", font=("TkDefaultFont", 12, "bold"), padding=(18, 12))

    def _add_menu_command(
        self,
        menu: tk.Menu,
        label: str,
        command,
        state: str = "normal",
        accelerator: str = "",
    ) -> None:
        menu.add_command(label=label, command=command, state=state, accelerator=accelerator)
        self.menu_items[label] = (menu, menu.index("end"))

    def _set_menu_item_state(self, label: str, state: str) -> None:
        item = self.menu_items.get(label)
        if item is None:
            return
        menu, index = item
        menu.entryconfigure(index, state=state)

    def _set_export_state(self, state: str) -> None:
        if hasattr(self, "export_button"):
            self.export_button.configure(state=state)
        if hasattr(self, "metadata_button"):
            self.metadata_button.configure(state=state)

    def _invoke_menu_command(self, command):
        command()
        return "break"

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.rowconfigure(1, weight=0)

        self.workspace = ttk.Frame(self)
        self.workspace.grid(row=0, column=0, sticky="nsew")
        self.workspace.columnconfigure(0, weight=1)
        self.workspace.rowconfigure(0, weight=1)

        self.overlay_workspace_container, self.overlay_workspace = self._scrollable_frame(self.workspace, padding=(10, 10, 10, 0))
        self.overlay_workspace.columnconfigure(0, weight=1)
        self._build_overlay_panel(self.overlay_workspace)

        self.vod_workspace = ttk.Frame(self.workspace)
        self.vod_workspace.columnconfigure(0, weight=3)
        self.vod_workspace.columnconfigure(1, weight=2)
        self.vod_workspace.rowconfigure(0, weight=1)

        left = ttk.Frame(self.vod_workspace, padding=(10, 10, 5, 0))
        right = ttk.Frame(self.vod_workspace, padding=(5, 10, 10, 0))
        left.grid(row=0, column=0, sticky="nsew")
        right.grid(row=0, column=1, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(right)
        notebook.grid(row=0, column=0, sticky="nsew")
        match_tab = ttk.Frame(notebook)
        notebook.add(match_tab, text="Matches")

        self._build_video_panel(left)
        self._build_match_panel(match_tab)
        self.set_workspace_mode(self.current_mode, rebuild_menu=False)

    def set_workspace_mode(self, mode: str, rebuild_menu: bool = True) -> None:
        self.current_mode = "vod" if mode == "vod" else "overlay"
        self.overlay_workspace_container.grid_remove()
        self.vod_workspace.grid_remove()
        if self.current_mode == "vod":
            self.unbind_all("<MouseWheel>")
            self.vod_workspace.grid(row=0, column=0, sticky="nsew")
            self.title("KWTekken VOD Helper")
            self.geometry("1280x820")
        else:
            self.overlay_workspace_container.grid(row=0, column=0, sticky="nsew")
            self.title("KWTekken Overlay Control")
            self.geometry("720x760")
            self.bind_all("<MouseWheel>", self._on_overlay_mousewheel)
        if rebuild_menu:
            self.menu_items = {}
            self._build_menu()

    def _scrollable_frame(self, parent: ttk.Frame, padding=(0, 0, 0, 0)) -> Tuple[ttk.Frame, ttk.Frame]:
        container = ttk.Frame(parent)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        canvas = tk.Canvas(container, borderwidth=0, highlightthickness=0)
        self.overlay_scroll_canvas = canvas
        scroll = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        content = ttk.Frame(canvas, padding=padding)
        window_id = canvas.create_window((0, 0), window=content, anchor="nw")

        canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scroll.set)

        def update_scrollbar() -> None:
            bbox = canvas.bbox("all")
            if bbox and (bbox[3] - bbox[1]) > canvas.winfo_height():
                scroll.grid()
            else:
                scroll.grid_remove()

        def configure_content(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            update_scrollbar()

        def configure_canvas(event):
            canvas.itemconfigure(window_id, width=event.width)
            update_scrollbar()

        content.bind("<Configure>", configure_content)
        canvas.bind("<Configure>", configure_canvas)
        return container, content

    def _on_overlay_mousewheel(self, event) -> None:
        if self.overlay_scroll_canvas is None or not self._overlay_can_scroll():
            return
        if self._mousewheel_belongs_to_combobox(event):
            return
        self.overlay_scroll_canvas.yview_scroll(-1 * int(event.delta / 120), "units")

    def _overlay_can_scroll(self) -> bool:
        if self.overlay_scroll_canvas is None:
            return False
        bbox = self.overlay_scroll_canvas.bbox("all")
        if not bbox:
            return False
        return (bbox[3] - bbox[1]) > self.overlay_scroll_canvas.winfo_height()

    def _mousewheel_belongs_to_combobox(self, event) -> bool:
        widget = getattr(event, "widget", None)
        try:
            if widget is not None and widget.winfo_class() == "TCombobox":
                return True
            if widget is not None and widget.winfo_class() == "Listbox":
                return True
            focus = self.focus_get()
            return focus is not None and focus.winfo_class() == "TCombobox"
        except tk.TclError:
            return False

    def _build_overlay_panel(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(2, weight=1)

        fields = ttk.LabelFrame(parent, text="Overlay", padding=8)
        fields.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 8))
        fields.columnconfigure(1, weight=1)
        fields.columnconfigure(3, weight=1)
        fields.columnconfigure(5, weight=1)
        ttk.Label(fields, text="Title").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(fields, textvariable=self.overlay_description_var).grid(row=0, column=1, columnspan=5, sticky="ew", pady=3)
        ttk.Label(fields, text="Round").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(fields, textvariable=self.overlay_subtitle_var).grid(row=1, column=1, columnspan=5, sticky="ew", pady=3)
        ttk.Label(fields, text="P1").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=3)
        self.overlay_p1_combo = ttk.Combobox(fields, textvariable=self.overlay_p1_var)
        self.overlay_p1_combo.grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Label(fields, text="Char").grid(row=2, column=2, sticky="e", padx=(8, 6), pady=3)
        self.overlay_character1_combo = ttk.Combobox(fields, textvariable=self.overlay_character1_var)
        self.overlay_character1_combo.grid(row=2, column=3, sticky="ew", pady=3)
        self._bind_overlay_character_search(self.overlay_character1_combo, self.overlay_character1_var)
        ttk.Spinbox(fields, textvariable=self.overlay_p1score_var, from_=0, to=999, width=5).grid(row=2, column=4, sticky="w", padx=(8, 4), pady=3)
        ttk.Button(fields, text="Win", width=6, command=lambda: self.increment_overlay_score(1)).grid(row=2, column=5, sticky="w", pady=3)
        ttk.Label(fields, text="P2").grid(row=3, column=0, sticky="w", padx=(0, 6), pady=3)
        self.overlay_p2_combo = ttk.Combobox(fields, textvariable=self.overlay_p2_var)
        self.overlay_p2_combo.grid(row=3, column=1, sticky="ew", pady=3)
        ttk.Label(fields, text="Char").grid(row=3, column=2, sticky="e", padx=(8, 6), pady=3)
        self.overlay_character2_combo = ttk.Combobox(fields, textvariable=self.overlay_character2_var)
        self.overlay_character2_combo.grid(row=3, column=3, sticky="ew", pady=3)
        self._bind_overlay_character_search(self.overlay_character2_combo, self.overlay_character2_var)
        ttk.Spinbox(fields, textvariable=self.overlay_p2score_var, from_=0, to=999, width=5).grid(row=3, column=4, sticky="w", padx=(8, 4), pady=3)
        ttk.Button(fields, text="Win", width=6, command=lambda: self.increment_overlay_score(2)).grid(row=3, column=5, sticky="w", pady=3)
        buttons = ttk.Frame(fields)
        buttons.grid(row=4, column=0, columnspan=6, sticky="ew", pady=(8, 0))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        ttk.Button(buttons, text="Swap", command=self.swap_overlay_players).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(buttons, text="Reset Scores", command=self.reset_overlay_scores).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(buttons, text="Apply Overlay", command=self.apply_overlay).grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=(6, 0))
        ttk.Button(buttons, text="Apply + Capture Match", command=self.apply_overlay_and_capture_match).grid(row=1, column=1, sticky="ew", padx=4, pady=(6, 0))
        ttk.Button(buttons, text="Match JSON", command=self.open_match_json_window).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        bracket = ttk.LabelFrame(parent, text="start.gg Bracket", padding=8)
        bracket.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 8))
        bracket.columnconfigure(1, weight=1)
        ttk.Label(bracket, textvariable=self.startgg_status_var, anchor="w").grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        self.startgg_controls = ttk.Frame(bracket)
        self.startgg_controls.grid(row=1, column=0, columnspan=3, sticky="ew")
        self.startgg_controls.columnconfigure(1, weight=1)
        action_row = ttk.Frame(bracket)
        action_row.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        action_row.columnconfigure(0, weight=1)
        action_row.columnconfigure(1, weight=1)
        self.startgg_events_button = ttk.Button(action_row, text="Load start.gg Events", command=self.fetch_startgg_tournaments)
        self.startgg_events_button.grid(row=0, column=0, columnspan=2, sticky="ew")

        self.startgg_tournament_combo = ttk.Combobox(self.startgg_controls, textvariable=self.startgg_tournament_var, state="readonly")
        self.startgg_tournament_combo.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 3))
        self.startgg_tournament_combo.bind("<<ComboboxSelected>>", lambda _event: self.use_selected_tournament())
        ttk.Label(self.startgg_controls, text="Tournament").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(self.startgg_controls, textvariable=self.startgg_slug_var).grid(row=1, column=1, sticky="ew", pady=3)
        self.bracket_set_combo = ttk.Combobox(self.startgg_controls, textvariable=self.bracket_set_var, state="readonly")
        self.bracket_set_combo.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(6, 3))
        self.bracket_set_combo.bind("<<ComboboxSelected>>", lambda _event: self.use_selected_bracket_set())
        self._set_startgg_controls_visible(False)

        obs = ttk.LabelFrame(parent, text="OBS Recording Time", padding=8)
        obs.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 8))
        obs.columnconfigure(1, weight=1)
        ttk.Label(obs, text="Host").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(obs, textvariable=self.obs_host_var).grid(row=0, column=1, sticky="ew", pady=3)
        ttk.Label(obs, text="Port").grid(row=0, column=2, sticky="w", padx=(8, 6), pady=3)
        ttk.Entry(obs, textvariable=self.obs_port_var, width=7).grid(row=0, column=3, sticky="w", pady=3)
        ttk.Label(obs, text="Password").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(obs, textvariable=self.obs_password_var, show="*").grid(row=1, column=1, columnspan=3, sticky="ew", pady=3)
        ttk.Button(obs, text="Test OBS", command=self.test_obs_connection).grid(row=2, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(obs, textvariable=self.obs_status_var, anchor="w").grid(row=2, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=(6, 0))

        advanced = ttk.LabelFrame(parent, text="Technical Settings", padding=8)
        advanced.grid(row=3, column=0, sticky="ew", padx=4, pady=(0, 4))
        advanced.columnconfigure(1, weight=1)
        self.advanced_section = advanced
        ttk.Label(advanced, text="Browser source").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(advanced, textvariable=self.overlay_url_var, state="readonly").grid(row=0, column=1, sticky="ew", pady=3)
        self._build_log_panel(advanced, row=1, padx=0, pady=(8, 0))
        self.toggle_advanced_data()

    def toggle_advanced_data(self) -> None:
        if self.advanced_section is None:
            return
        if self.show_advanced_var.get():
            self.advanced_section.grid()
        else:
            self.advanced_section.grid_remove()

    def _build_video_panel(self, parent: ttk.Frame) -> None:
        self.preview_frame = ttk.Frame(parent, relief="solid", borderwidth=1)
        self.preview_frame.grid(row=0, column=0, sticky="nsew")
        self.preview_frame.columnconfigure(0, weight=1)
        self.preview_frame.rowconfigure(0, weight=1)
        self.preview_frame.bind("<Button-1>", self.on_preview_click)
        self.video_surface = tk.Frame(self.preview_frame, background="black")
        self.video_surface.grid(row=0, column=0, sticky="nsew")
        self.video_surface.bind("<Button-1>", self.on_preview_click)
        self.preview_label = ttk.Label(
            self.preview_frame,
            text="Open a video to preview frames and mark match starts.",
            anchor="center",
        )
        self.preview_label.grid(row=0, column=0, sticky="nsew")
        self.preview_label.bind("<Button-1>", self.on_preview_click)
        self.preview_label.tkraise()

        scrub_frame = ttk.Frame(parent)
        scrub_frame.grid(row=1, column=0, sticky="ew", pady=(8, 4))
        scrub_frame.columnconfigure(1, weight=1)
        ttk.Button(scrub_frame, text="-5s", width=6, command=lambda: self.seek_relative(-5)).grid(row=0, column=0, padx=(0, 6))
        self.scrub_var = tk.DoubleVar(value=0.0)
        self.scrub = ttk.Scale(scrub_frame, from_=0.0, to=1.0, orient="horizontal", variable=self.scrub_var, command=self.on_scrub)
        self.scrub.grid(row=0, column=1, sticky="ew")
        self.scrub.bind("<Button-1>", self.on_scrub_click)
        ttk.Button(scrub_frame, text="+5s", width=6, command=lambda: self.seek_relative(5)).grid(row=0, column=2, padx=(6, 0))
        self.play_button = ttk.Button(scrub_frame, text="Play", width=7, command=self.toggle_playback)
        self.play_button.grid(row=0, column=3, padx=(8, 0))

        mark_frame = ttk.Frame(parent)
        mark_frame.grid(row=2, column=0, sticky="ew", pady=(4, 8))
        mark_frame.columnconfigure(4, weight=1)
        mark_frame.columnconfigure(7, weight=1)
        self.time_label_var = tk.StringVar(value="00:00:00.000 / 00:00:00.000")
        ttk.Label(mark_frame, textvariable=self.time_label_var, width=29).grid(row=0, column=0, columnspan=6, sticky="w", padx=(0, 8))

        ttk.Button(mark_frame, text="Mark Match Start", command=self.mark_match_start).grid(row=1, column=0, sticky="w", padx=(0, 3), pady=(6, 0))
        ttk.Button(mark_frame, text="Mark Match End", command=self.mark_match_end).grid(row=1, column=1, sticky="w", padx=3, pady=(6, 0))
        ttk.Button(mark_frame, text="Go To Match", command=self.go_to_selected_match).grid(row=1, column=2, sticky="w", padx=3, pady=(6, 0))
        ttk.Button(mark_frame, text="Delete Match", command=self.delete_selected_match).grid(row=1, column=3, sticky="w", padx=3, pady=(6, 0))

        ttk.Label(mark_frame, text="Start").grid(row=2, column=0, sticky="e", padx=(0, 3), pady=(6, 0))
        self.start_var = tk.StringVar(value="00:00:00.000")
        self.start_entry = ttk.Entry(mark_frame, textvariable=self.start_var, width=14)
        self.start_entry.grid(row=2, column=1, sticky="ew", pady=(6, 0))
        ttk.Button(mark_frame, text="Set", width=5, command=self.set_selected_start).grid(row=2, column=2, sticky="w", padx=(4, 16), pady=(6, 0))

        ttk.Label(mark_frame, text="End").grid(row=2, column=3, sticky="e", padx=(0, 3), pady=(6, 0))
        self.end_var = tk.StringVar(value="")
        self.end_entry = ttk.Entry(mark_frame, textvariable=self.end_var, width=14)
        self.end_entry.grid(row=2, column=4, sticky="ew", pady=(6, 0))
        ttk.Button(mark_frame, text="Set", width=5, command=self.set_selected_end).grid(row=2, column=5, sticky="w", padx=(4, 0), pady=(6, 0))

    def _build_match_panel(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        summary = ttk.Frame(parent)
        summary.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        summary.columnconfigure(1, weight=1)
        self.video_var = tk.StringVar(value="No video loaded")
        ttk.Label(summary, text="Event").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(summary, textvariable=self.event_var).grid(row=0, column=1, sticky="ew")
        ttk.Label(summary, textvariable=self.video_var, anchor="w").grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        tree_frame = ttk.Frame(parent)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        columns = ("number", "start", "end", "players", "characters")
        self.match_tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
        self.match_tree.heading("number", text="#")
        self.match_tree.heading("start", text="Start")
        self.match_tree.heading("end", text="End")
        self.match_tree.heading("players", text="Players")
        self.match_tree.heading("characters", text="Characters")
        self.match_tree.column("number", width=40, stretch=False, anchor="center")
        self.match_tree.column("start", width=98, stretch=False)
        self.match_tree.column("end", width=98, stretch=False)
        self.match_tree.column("players", width=180)
        self.match_tree.column("characters", width=170)
        self.match_tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.match_tree.yview)
        tree_scroll.grid(row=0, column=1, sticky="ns")
        self.match_tree.configure(yscrollcommand=tree_scroll.set)
        self.match_tree.bind("<<TreeviewSelect>>", self.on_match_selected)

        editor = ttk.LabelFrame(parent, text="Selected Match", padding=8)
        editor.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        editor.columnconfigure(0, weight=1)

        self.player1_var = tk.StringVar()
        self.player2_var = tk.StringVar()
        self.character1_var = tk.StringVar()
        self.character2_var = tk.StringVar()
        self.round_var = tk.StringVar()
        self.notes_var = tk.StringVar()
        self.character_picker_window: Optional[tk.Toplevel] = None
        self.character_picker_slot: Optional[int] = None
        self.character_picker_var = tk.StringVar()
        self.character_picker_status_var = tk.StringVar()
        self.character_picker_grid: Optional[ttk.Frame] = None
        self.character_picker_after_id: Optional[str] = None

        p1_row = ttk.Frame(editor)
        p1_row.grid(row=0, column=0, sticky="ew", pady=3)
        p1_row.columnconfigure(1, weight=1)
        p1_row.columnconfigure(3, weight=1)
        ttk.Label(p1_row, text="P1", width=7, anchor="e").grid(row=0, column=0, sticky="e", padx=(0, 6))
        self.player1_combo = ttk.Combobox(p1_row, textvariable=self.player1_var)
        self.player1_combo.grid(row=0, column=1, sticky="ew")
        ttk.Label(p1_row, text="Char", width=6, anchor="e").grid(row=0, column=2, sticky="e", padx=(12, 6))
        self.character1_combo = ttk.Combobox(p1_row, textvariable=self.character1_var)
        self.character1_combo.grid(row=0, column=3, sticky="ew")
        ttk.Button(p1_row, text="Portraits", command=lambda: self._open_character_picker(1)).grid(row=0, column=4, padx=(6, 0))
        self._bind_character_search(self.character1_combo, 1)

        p2_row = ttk.Frame(editor)
        p2_row.grid(row=1, column=0, sticky="ew", pady=3)
        p2_row.columnconfigure(1, weight=1)
        p2_row.columnconfigure(3, weight=1)
        ttk.Label(p2_row, text="P2", width=7, anchor="e").grid(row=0, column=0, sticky="e", padx=(0, 6))
        self.player2_combo = ttk.Combobox(p2_row, textvariable=self.player2_var)
        self.player2_combo.grid(row=0, column=1, sticky="ew")
        ttk.Label(p2_row, text="Char", width=6, anchor="e").grid(row=0, column=2, sticky="e", padx=(12, 6))
        self.character2_combo = ttk.Combobox(p2_row, textvariable=self.character2_var)
        self.character2_combo.grid(row=0, column=3, sticky="ew")
        ttk.Button(p2_row, text="Portraits", command=lambda: self._open_character_picker(2)).grid(row=0, column=4, padx=(6, 0))
        self._bind_character_search(self.character2_combo, 2)

        meta_row = ttk.Frame(editor)
        meta_row.grid(row=2, column=0, sticky="ew", pady=3)
        meta_row.columnconfigure(1, weight=1)
        meta_row.columnconfigure(3, weight=1)
        ttk.Label(meta_row, text="Round", width=7, anchor="e").grid(row=0, column=0, sticky="e", padx=(0, 6))
        ttk.Entry(meta_row, textvariable=self.round_var).grid(row=0, column=1, sticky="ew")
        ttk.Label(meta_row, text="Notes", width=6, anchor="e").grid(row=0, column=2, sticky="e", padx=(12, 6))
        ttk.Entry(meta_row, textvariable=self.notes_var).grid(row=0, column=3, columnspan=2, sticky="ew")

        bracket_row = ttk.Frame(editor)
        bracket_row.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        bracket_row.columnconfigure(0, weight=1)
        self.vod_startgg_events_button = ttk.Button(bracket_row, text="Load start.gg Events", command=self.fetch_startgg_tournaments)
        self.vod_startgg_events_button.grid(row=0, column=0, sticky="ew")

        self.vod_startgg_controls = ttk.Frame(editor)
        self.vod_startgg_controls.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self.vod_startgg_controls.columnconfigure(0, weight=1)
        self.vod_startgg_tournament_combo = ttk.Combobox(self.vod_startgg_controls, textvariable=self.startgg_tournament_var, state="readonly")
        self.vod_startgg_tournament_combo.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.vod_startgg_tournament_combo.bind("<<ComboboxSelected>>", lambda _event: self.use_selected_tournament())
        self.vod_bracket_set_combo = ttk.Combobox(self.vod_startgg_controls, textvariable=self.bracket_set_var, state="readonly")
        self.vod_bracket_set_combo.grid(row=1, column=0, sticky="ew")
        self.vod_bracket_set_combo.bind("<<ComboboxSelected>>", lambda _event: self.apply_selected_bracket_set_to_match())
        self._set_startgg_controls_visible(False)

        detail_buttons = ttk.Frame(editor)
        detail_buttons.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        detail_buttons.columnconfigure(0, weight=1)
        detail_buttons.columnconfigure(1, weight=1)
        ttk.Button(detail_buttons, text="Swap Players", command=self.swap_selected_match_players).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(detail_buttons, text="Apply Details", command=self.apply_match_details).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        export_buttons = ttk.Frame(parent)
        export_buttons.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        export_buttons.columnconfigure(0, weight=1)
        export_buttons.columnconfigure(1, weight=1)
        self.export_button = ttk.Button(
            export_buttons,
            text="Export Clips",
            command=self.export_clips,
            style="Export.TButton",
        )
        self.export_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.metadata_button = ttk.Button(
            export_buttons,
            text="Generate Metadata Only",
            command=self.generate_metadata_only,
        )
        self.metadata_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))

    def _build_log_panel(self, parent: ttk.Frame, row: int = 1, padx: int = 10, pady=(8, 10)) -> None:
        log_frame = ttk.LabelFrame(parent, text="Log", padding=6)
        log_frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=padx, pady=pady)
        self.log_frame = log_frame
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, height=7, wrap="word")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)

    def show_copyable_error(self, title: str, message: object) -> None:
        text = str(message)
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.transient(self)
        dialog.grab_set()
        dialog.minsize(440, 220)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)

        body = ttk.Frame(dialog, padding=10)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        message_text = tk.Text(body, height=8, width=72, wrap="word")
        message_text.grid(row=0, column=0, sticky="nsew")
        message_text.insert("1.0", text)
        message_text.configure(state="disabled")
        scroll = ttk.Scrollbar(body, orient="vertical", command=message_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        message_text.configure(yscrollcommand=scroll.set)

        buttons = ttk.Frame(body)
        buttons.grid(row=1, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Copy", command=lambda: self._copy_text(text)).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(buttons, text="Close", command=dialog.destroy).grid(row=0, column=1)

        dialog.bind("<Control-c>", lambda _event: self._copy_text(text))
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        message_text.focus_set()

    def _copy_text(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)

    def _initial_startgg_token(self) -> str:
        try:
            return read_startgg_token()
        except CredentialError as exc:
            self.after(0, lambda: self.log("Windows credential error: {}".format(exc)))
            return ""

    def _current_startgg_token(self) -> str:
        return self.startgg_token_var.get().strip()

    def _save_startgg_token_value(self, token: str) -> None:
        token = token.strip()
        if not token:
            messagebox.showinfo("No token", "Enter a start.gg token first.")
            return
        try:
            write_startgg_token(token)
        except CredentialError as exc:
            self.show_copyable_error("Could not save start.gg token", exc)
            return
        self.state.startgg.token = token
        self.log("Saved start.gg token to Windows Credential Manager.")

    def _clear_startgg_token_value(self, variable: Optional[tk.StringVar] = None) -> None:
        try:
            delete_startgg_token()
        except CredentialError as exc:
            self.show_copyable_error("Could not clear start.gg token", exc)
            return
        if variable is not None:
            variable.set("")
        else:
            self.startgg_token_var.set("")
        self.state.startgg.token = ""
        self.log("Cleared start.gg token from Windows Credential Manager.")

    def open_match_json_window(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Match JSON")
        dialog.transient(self)
        dialog.geometry("880x620")
        dialog.minsize(720, 460)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)

        body = ttk.Frame(dialog, padding=10)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        status_var = tk.StringVar()
        ttk.Label(body, textvariable=status_var, anchor="w").grid(row=0, column=0, sticky="ew", pady=(0, 6))

        columns = ("number", "start", "end", "players", "characters")
        tree_frame = ttk.Frame(body)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
        tree.heading("number", text="#")
        tree.heading("start", text="Start")
        tree.heading("end", text="End")
        tree.heading("players", text="Players")
        tree.heading("characters", text="Characters")
        tree.column("number", width=44, stretch=False, anchor="center")
        tree.column("start", width=98, stretch=False)
        tree.column("end", width=98, stretch=False)
        tree.column("players", width=240)
        tree.column("characters", width=220)
        tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree_scroll.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=tree_scroll.set)

        editor = ttk.LabelFrame(body, text="Selected Match", padding=8)
        editor.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        editor.columnconfigure(1, weight=1)
        editor.columnconfigure(3, weight=1)
        start_var = tk.StringVar()
        end_var = tk.StringVar()
        player1_var = tk.StringVar()
        player2_var = tk.StringVar()
        character1_var = tk.StringVar()
        character2_var = tk.StringVar()
        round_var = tk.StringVar()
        notes_var = tk.StringVar()

        ttk.Label(editor, text="Start").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(editor, textvariable=start_var, width=13).grid(row=0, column=1, sticky="w", pady=3)
        ttk.Label(editor, text="End").grid(row=0, column=2, sticky="e", padx=(12, 6), pady=3)
        ttk.Entry(editor, textvariable=end_var, width=13).grid(row=0, column=3, sticky="w", pady=3)
        ttk.Label(editor, text="P1").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(editor, textvariable=player1_var).grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Label(editor, text="Char").grid(row=1, column=2, sticky="e", padx=(12, 6), pady=3)
        ttk.Combobox(editor, textvariable=character1_var, values=self.state.characters).grid(row=1, column=3, sticky="ew", pady=3)
        ttk.Label(editor, text="P2").grid(row=2, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(editor, textvariable=player2_var).grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Label(editor, text="Char").grid(row=2, column=2, sticky="e", padx=(12, 6), pady=3)
        ttk.Combobox(editor, textvariable=character2_var, values=self.state.characters).grid(row=2, column=3, sticky="ew", pady=3)
        ttk.Label(editor, text="Round").grid(row=3, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(editor, textvariable=round_var).grid(row=3, column=1, sticky="ew", pady=3)
        ttk.Label(editor, text="Notes").grid(row=3, column=2, sticky="e", padx=(12, 6), pady=3)
        ttk.Entry(editor, textvariable=notes_var).grid(row=3, column=3, sticky="ew", pady=3)

        def selected_index() -> Optional[int]:
            selection = tree.selection()
            if not selection:
                return None
            try:
                return int(selection[0])
            except ValueError:
                return None

        def refresh() -> None:
            previous = selected_index()
            for item in tree.get_children():
                tree.delete(item)
            for index, match in enumerate(self.state.sorted_matches()):
                tree.insert(
                    "",
                    "end",
                    iid=str(index),
                    values=(
                        index + 1,
                        seconds_to_timestamp(match.start),
                        seconds_to_timestamp(match.end) if match.end is not None else "",
                        "{} vs {}".format(match.player1 or "Player 1", match.player2 or "Player 2"),
                        "{} vs {}".format(match.character1 or "Character", match.character2 or "Character"),
                    ),
                )
            matches = self.state.sorted_matches()
            if matches:
                index = previous if previous is not None and previous < len(matches) else 0
                tree.selection_set(str(index))
                tree.focus(str(index))
                load_form()
            else:
                for variable in (start_var, end_var, player1_var, player2_var, character1_var, character2_var, round_var, notes_var):
                    variable.set("")
            status_var.set("{} captured match{}.".format(len(self.state.matches), "" if len(self.state.matches) == 1 else "es"))

        def copy() -> None:
            self._copy_text(self._matches_json_text())
            status_var.set("Copied match JSON.")

        def load_form(_event=None) -> None:
            index = selected_index()
            matches = self.state.sorted_matches()
            if index is None or index >= len(matches):
                return
            match = matches[index]
            start_var.set(seconds_to_timestamp(match.start))
            end_var.set(seconds_to_timestamp(match.end) if match.end is not None else "")
            player1_var.set(match.player1)
            player2_var.set(match.player2)
            character1_var.set(match.character1)
            character2_var.set(match.character2)
            round_var.set(match.round_name)
            notes_var.set(match.notes)

        def apply_details() -> None:
            index = selected_index()
            matches = self.state.sorted_matches()
            if index is None or index >= len(matches):
                return
            try:
                start = parse_timestamp(start_var.get())
                end = parse_timestamp(end_var.get()) if end_var.get().strip() else None
            except Exception as exc:
                self.show_copyable_error("Invalid match time", exc)
                return
            match = matches[index]
            match.start = start
            match.end = end
            match.player1 = player1_var.get().strip()
            match.player2 = player2_var.get().strip()
            match.character1 = character1_var.get().strip()
            match.character2 = character2_var.get().strip()
            match.round_name = round_var.get().strip()
            match.notes = notes_var.get().strip()
            self.state.matches = sorted(matches, key=lambda segment: segment.start)
            self.state.players = unique_sorted(self.state.players + [match.player1, match.player2])
            self.state.characters = unique_sorted(self.state.characters + [match.character1, match.character2])
            self._refresh_combo_values()
            if hasattr(self, "match_tree"):
                self._refresh_tree(select_start=match.start)
            refresh()
            status_var.set("Applied match details.")

        def delete_selected() -> None:
            index = selected_index()
            matches = self.state.sorted_matches()
            if index is None or index >= len(matches):
                return
            del matches[index]
            self.state.matches = matches
            if hasattr(self, "match_tree"):
                self._refresh_tree()
            refresh()
            status_var.set("Deleted match.")

        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Apply Details", command=apply_details).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(buttons, text="Delete", command=delete_selected).grid(row=0, column=1, padx=6)
        ttk.Button(buttons, text="Refresh", command=refresh).grid(row=0, column=2, padx=6)
        ttk.Button(buttons, text="Copy JSON", command=copy).grid(row=0, column=3, padx=6)
        ttk.Button(buttons, text="Raw JSON...", command=lambda: self.open_raw_match_json_window(dialog)).grid(row=0, column=4, padx=6)
        ttk.Button(buttons, text="Close", command=dialog.destroy).grid(row=0, column=5, padx=(6, 0))

        tree.bind("<<TreeviewSelect>>", load_form)
        dialog.bind("<Control-s>", lambda _event: apply_details())
        dialog.bind("<Control-r>", lambda _event: refresh())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        refresh()
        tree.focus_set()

    def open_raw_match_json_window(self, parent=None) -> None:
        dialog = tk.Toplevel(parent or self)
        dialog.title("Raw Match JSON")
        dialog.transient(parent or self)
        dialog.geometry("760x520")
        dialog.minsize(520, 320)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)

        body = ttk.Frame(dialog, padding=10)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        status_var = tk.StringVar(value="Edit captured matches, then Apply JSON.")
        ttk.Label(body, textvariable=status_var, anchor="w").grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))

        text = tk.Text(body, wrap="none", undo=True)
        text.grid(row=1, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        yscroll.grid(row=1, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(body, orient="horizontal", command=text.xview)
        xscroll.grid(row=2, column=0, sticky="ew")
        text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        def refresh() -> None:
            text.delete("1.0", "end")
            text.insert("1.0", self._matches_json_text())
            status_var.set("{} captured match{}.".format(len(self.state.matches), "" if len(self.state.matches) == 1 else "es"))

        def copy() -> None:
            self._copy_text(text.get("1.0", "end").strip())
            status_var.set("Copied match JSON.")

        def apply() -> None:
            try:
                self._load_matches_json_text(text.get("1.0", "end"))
            except Exception as exc:
                status_var.set("Invalid JSON.")
                self.show_copyable_error("Invalid match JSON", exc)
                return
            if hasattr(self, "match_tree"):
                self._refresh_tree()
            self._refresh_combo_values()
            status_var.set("Applied {} captured match{}.".format(len(self.state.matches), "" if len(self.state.matches) == 1 else "es"))

        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Refresh", command=refresh).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(buttons, text="Copy", command=copy).grid(row=0, column=1, padx=6)
        ttk.Button(buttons, text="Apply JSON", command=apply).grid(row=0, column=2, padx=6)
        ttk.Button(buttons, text="Close", command=dialog.destroy).grid(row=0, column=3, padx=(6, 0))

        dialog.bind("<Control-s>", lambda _event: apply())
        dialog.bind("<Control-r>", lambda _event: refresh())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        refresh()
        text.focus_set()

    def _matches_json_text(self) -> str:
        return json.dumps([match.to_dict() for match in self.state.sorted_matches()], indent=2)

    def _load_matches_json_text(self, text: str) -> None:
        payload = json.loads(text)
        if not isinstance(payload, list):
            raise ValueError("Match JSON must be a list.")
        matches = []
        for index, item in enumerate(payload):
            if not isinstance(item, dict):
                raise ValueError("Match {} must be an object.".format(index + 1))
            matches.append(MatchSegment.from_dict(item))
        self.state.matches = sorted(matches, key=lambda segment: segment.start)
        players = []
        characters = []
        for match in self.state.matches:
            players.extend([match.player1, match.player2])
            characters.extend([match.character1, match.character2])
        self.state.players = unique_sorted(self.state.players + players)
        self.state.characters = unique_sorted(self.state.characters + characters)

    def _start_overlay_server(self) -> None:
        try:
            self.overlay_server.start()
            self.overlay_url_var.set(self.overlay_server.url)
            self.log("Overlay server: {}".format(self.overlay_server.url))
        except Exception as exc:
            self.log("Could not start overlay server: {}".format(exc))

    def _overlay_state_from_controls(self) -> OverlayState:
        return OverlayState(
            description=self.overlay_description_var.get().strip(),
            subtitle=self.overlay_subtitle_var.get().strip(),
            p1name=self.overlay_p1_var.get().strip(),
            p1score=self._int_var(self.overlay_p1score_var),
            p2name=self.overlay_p2_var.get().strip(),
            p2score=self._int_var(self.overlay_p2score_var),
            font=self.overlay_font_var.get().strip() or "Bahnschrift",
        )

    def apply_overlay(self, log_message: bool = True) -> None:
        self.state.overlay = self._overlay_state_from_controls()
        self.overlay_server.write_state(self.state.overlay)
        self.state.players = unique_sorted(self.state.players + [self.state.overlay.p1name, self.state.overlay.p2name])
        self.state.characters = unique_sorted(
            self.state.characters + [self._overlay_character_text(1), self._overlay_character_text(2)]
        )
        self._refresh_combo_values()
        if log_message:
            self.log("Overlay applied.")

    def apply_overlay_and_capture_match(self) -> None:
        self.apply_overlay(log_message=False)
        start, source = self._overlay_capture_time()
        end = 0.0 if source == "draft" else None
        match, message = self._capture_match_from_overlay(start, end=end, allow_duplicate=source == "draft")
        if match is None:
            messagebox.showinfo("Match already exists", message)
            return
        self._refresh_tree(select_start=match.start)
        self.log("Captured overlay match at {} from {}.".format(seconds_to_timestamp(match.start), "manual draft" if source == "draft" else source))

    def _overlay_capture_time(self) -> Tuple[float, str]:
        try:
            status = ObsClient(self._obs_settings_from_controls()).get_record_status()
            if status.active and not status.paused:
                self.obs_status_var.set("Recording {}".format(status.timecode or seconds_to_timestamp(status.duration_seconds)))
                return round(status.duration_seconds, 3), "OBS"
            self.obs_status_var.set("OBS recording is not active")
        except ObsError as exc:
            self.obs_status_var.set(str(exc))
        except Exception as exc:
            self.obs_status_var.set("OBS unavailable: {}".format(exc))
        if self.state.video_path:
            return round(self.current_time, 3), "loaded VOD"
        self.obs_status_var.set("No OBS or VOD timing source; capturing a 0:00 draft")
        return 0.0, "draft"

    def _capture_match_from_overlay(
        self,
        start: float,
        end: Optional[float] = None,
        allow_duplicate: bool = False,
    ) -> Tuple[Optional[MatchSegment], str]:
        for match in self.state.matches:
            if not allow_duplicate and abs(match.start - start) < 0.25:
                return None, "A match start is already close to this time."
        matches = self.state.sorted_matches()
        previous_match = self._previous_match_for_start(matches, start)
        if previous_match is not None and previous_match.end is None:
            previous_match.end = start
        match = MatchSegment(
            start=start,
            end=end,
            player1=self.state.overlay.p1name,
            player2=self.state.overlay.p2name,
            character1=self._overlay_character_text(1),
            character2=self._overlay_character_text(2),
            round_name=self.state.overlay.subtitle,
        )
        matches.append(match)
        self.state.matches = sorted(matches, key=lambda segment: segment.start)
        self.state.players = unique_sorted(self.state.players + [match.player1, match.player2])
        self.state.characters = unique_sorted(self.state.characters + [match.character1, match.character2])
        return match, ""

    def _overlay_character_text(self, slot: int) -> str:
        variable = getattr(self, "overlay_character1_var" if slot == 1 else "overlay_character2_var", None)
        if variable is None:
            return ""
        return variable.get().strip()

    def increment_overlay_score(self, player: int) -> None:
        if player == 1:
            self.overlay_p1score_var.set(self._int_var(self.overlay_p1score_var) + 1)
        else:
            self.overlay_p2score_var.set(self._int_var(self.overlay_p2score_var) + 1)

    def reset_overlay_scores(self) -> None:
        self.overlay_p1score_var.set(0)
        self.overlay_p2score_var.set(0)

    def swap_overlay_players(self) -> None:
        p1_name = self.overlay_p1_var.get()
        p1_score = self._int_var(self.overlay_p1score_var)
        p1_character = self.overlay_character1_var.get()
        self.overlay_p1_var.set(self.overlay_p2_var.get())
        self.overlay_p1score_var.set(self._int_var(self.overlay_p2score_var))
        self.overlay_character1_var.set(self.overlay_character2_var.get())
        self.overlay_p2_var.set(p1_name)
        self.overlay_p2score_var.set(p1_score)
        self.overlay_character2_var.set(p1_character)

    def fetch_startgg_sets(self) -> None:
        if self.fetching_startgg_sets:
            return
        self._sync_paths_to_state()
        for combo in self._bracket_set_combos():
            combo.configure(values=[])
        self.bracket_set_var.set("")
        self.startgg_status_var.set("Fetching start.gg bracket sets...")
        self.log("Fetching start.gg bracket sets...")
        token = self._current_startgg_token()
        self.fetching_startgg_sets = True
        self._set_startgg_events_button_state("disabled")
        thread = threading.Thread(
            target=self._fetch_startgg_sets_worker,
            args=(token, self.state.startgg.tournament_slug),
            daemon=True,
        )
        thread.start()

    def _fetch_startgg_sets_worker(self, token: str, slug: str) -> None:
        try:
            sets = fetch_bracket_sets(token, slug)
            self.log_queue.put(("sets", sets))
        except Exception as exc:
            self.log_queue.put(("sets_error", exc))

    def fetch_startgg_tournaments(self) -> None:
        if self.fetching_startgg_events:
            return
        self._sync_paths_to_state()
        for combo in self._startgg_tournament_combos():
            combo.configure(values=[])
        self.startgg_tournament_var.set("")
        self._set_startgg_controls_visible(False)
        self.startgg_status_var.set("Fetching start.gg events from your account...")
        self.log("Fetching start.gg events from your account...")
        token = self._current_startgg_token()
        self.fetching_startgg_events = True
        self._set_startgg_events_button_state("disabled")
        thread = threading.Thread(
            target=self._fetch_startgg_tournaments_worker,
            args=(token,),
            daemon=True,
        )
        thread.start()

    def _fetch_startgg_tournaments_worker(self, token: str) -> None:
        try:
            tournaments = fetch_owned_tournaments(token)
            self.log_queue.put(("tournaments", tournaments))
        except Exception as exc:
            self.log_queue.put(("tournaments_error", exc))

    def _load_tournaments(self, tournaments: List[TournamentSummary]) -> None:
        self.tournaments = tournaments
        self.tournament_by_label = {}
        labels = []
        for item in tournaments:
            label = item.label
            labels.append(label)
            self.tournament_by_label[label] = item
        for combo in self._startgg_tournament_combos():
            combo.configure(values=labels)
        if labels:
            self._set_startgg_controls_visible(True)
            self.startgg_status_var.set("Loaded start.gg events. Choose an event.")
        else:
            self._set_startgg_controls_visible(False)
            self.startgg_status_var.set("No start.gg events found for this token.")
        self.log("Fetched {} start.gg events.".format(len(tournaments)))

    def use_selected_tournament(self) -> None:
        tournament = self.tournament_by_label.get(self.startgg_tournament_var.get())
        if tournament is None:
            messagebox.showinfo("Select an event", "Fetch and select a start.gg event first.")
            return
        self.startgg_slug_var.set(tournament.slug)
        if not self.overlay_description_var.get().strip():
            self.overlay_description_var.set(tournament.name)
        if not self.event_var.get().strip():
            self.event_var.set(tournament.name)
        self.log("Selected start.gg event: {}".format(tournament.label))
        self.fetch_startgg_sets()

    def _load_bracket_sets(self, sets: List[BracketSet]) -> None:
        self.bracket_sets = sets
        self.bracket_set_by_label = {}
        labels = []
        for item in sets:
            label = "{} [{}]".format(item.label, item.id)
            labels.append(label)
            self.bracket_set_by_label[label] = item
        for combo in self._bracket_set_combos():
            combo.configure(values=labels)
        if labels:
            self.bracket_set_var.set("")
            self._set_startgg_controls_visible(True)
            self.startgg_status_var.set("Loaded bracket sets. Choose a set to populate the current mode.")
        else:
            self.startgg_status_var.set("No start.gg sets found for this tournament.")
        self.log("Fetched {} start.gg sets.".format(len(sets)))

    def _set_startgg_controls_visible(self, visible: bool) -> None:
        for controls in (getattr(self, "startgg_controls", None), getattr(self, "vod_startgg_controls", None)):
            if controls is None:
                continue
            if visible:
                controls.grid()
            else:
                controls.grid_remove()

    def _set_startgg_events_button_state(self, state: str) -> None:
        for button in (self.startgg_events_button, self.vod_startgg_events_button):
            if button is not None:
                button.configure(state=state)

    def _startgg_tournament_combos(self) -> List[ttk.Combobox]:
        return [
            combo
            for combo in (
                getattr(self, "startgg_tournament_combo", None),
                getattr(self, "vod_startgg_tournament_combo", None),
            )
            if combo is not None
        ]

    def _bracket_set_combos(self) -> List[ttk.Combobox]:
        return [
            combo
            for combo in (
                getattr(self, "bracket_set_combo", None),
                getattr(self, "vod_bracket_set_combo", None),
            )
            if combo is not None
        ]

    def use_selected_bracket_set(self) -> None:
        bracket_set = self.bracket_set_by_label.get(self.bracket_set_var.get())
        if bracket_set is None:
            messagebox.showinfo("Select a set", "Fetch and select a start.gg set first.")
            return
        if not self.overlay_description_var.get().strip():
            self.overlay_description_var.set(self.event_var.get().strip() or bracket_set.event_name)
        self.overlay_subtitle_var.set(bracket_set.round_name)
        self.overlay_p1_var.set(bracket_set.player1)
        self.overlay_p2_var.set(bracket_set.player2)
        self.overlay_character1_var.set(bracket_set.character1)
        self.overlay_character2_var.set(bracket_set.character2)
        self.reset_overlay_scores()
        self.state.players = unique_sorted(self.state.players + [bracket_set.player1, bracket_set.player2])
        self.state.characters = unique_sorted(self.state.characters + [bracket_set.character1, bracket_set.character2])
        self._refresh_combo_values()
        self.log("Loaded bracket set into overlay: {}".format(bracket_set.label))

    def apply_selected_bracket_set_to_match(self) -> None:
        bracket_set = self.bracket_set_by_label.get(self.bracket_set_var.get())
        if bracket_set is None:
            messagebox.showinfo("Select a set", "Load and select a start.gg set first.")
            return
        self.player1_var.set(bracket_set.player1)
        self.player2_var.set(bracket_set.player2)
        self.character1_var.set(bracket_set.character1)
        self.character2_var.set(bracket_set.character2)
        self.round_var.set(bracket_set.round_name)
        if not self.event_var.get().strip():
            self.event_var.set(bracket_set.event_name)
        self.state.players = unique_sorted(self.state.players + [bracket_set.player1, bracket_set.player2])
        self.state.characters = unique_sorted(self.state.characters + [bracket_set.character1, bracket_set.character2])
        self._refresh_combo_values()
        if self._selected_tree_index() is not None:
            self.apply_match_details()
            self.log("Applied start.gg set to selected match: {}".format(bracket_set.label))
        else:
            self.log("Loaded start.gg set into match fields: {}".format(bracket_set.label))

    def swap_selected_match_players(self) -> None:
        p1_name = self.player1_var.get()
        p1_character = self.character1_var.get()
        self.player1_var.set(self.player2_var.get())
        self.character1_var.set(self.character2_var.get())
        self.player2_var.set(p1_name)
        self.character2_var.set(p1_character)
        self.apply_match_details()

    def test_obs_connection(self) -> None:
        self._sync_paths_to_state()
        try:
            status = ObsClient(self.state.obs).get_record_status()
        except Exception as exc:
            self.obs_status_var.set("OBS error: {}".format(exc))
            return
        if status.active:
            label = status.timecode or seconds_to_timestamp(status.duration_seconds)
            self.obs_status_var.set("Recording {}".format(label))
        else:
            self.obs_status_var.set("Connected, not recording")

    def _obs_settings_from_controls(self):
        settings = self.state.obs.__class__()
        settings.host = self.obs_host_var.get().strip() or "127.0.0.1"
        try:
            settings.port = int(self.obs_port_var.get().strip() or "4455")
        except ValueError:
            settings.port = 4455
        settings.password = self.obs_password_var.get()
        return settings

    def _int_var(self, variable: tk.IntVar) -> int:
        try:
            return int(variable.get())
        except (tk.TclError, ValueError):
            return 0

    def open_video(self) -> None:
        path = filedialog.askopenfilename(
            title="Open tournament recording",
            filetypes=[
                ("Video files", "*.mkv *.mp4 *.mov *.avi *.webm"),
                ("MKV files", "*.mkv"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        self._sync_paths_to_state()
        previous_video = self.state.video_path
        self.stop_playback()
        try:
            duration = probe_duration(path, self.state.ffprobe_path)
        except Exception as exc:
            self.show_copyable_error("Could not read video", exc)
            return

        self.state.video_path = path
        self.state.duration = duration
        self._reset_matches_for_new_video(previous_video, path)
        self.current_time = 0.0
        self.scrub.configure(to=max(duration, 1.0))
        self.scrub_var.set(0.0)
        self.video_var.set("{} ({})".format(Path(path).name, seconds_to_timestamp(duration)))
        self._refresh_tree()
        self._update_time_label()
        self._schedule_preview()
        self.log("Loaded video: {}".format(path))

    def _reset_matches_for_new_video(self, previous_video: str, new_video: str) -> None:
        if previous_video and previous_video != new_video:
            self.state.matches = []

    def open_settings(self) -> None:
        self._sync_paths_to_state()
        dialog = tk.Toplevel(self)
        dialog.title("Settings")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)

        output_var = tk.StringVar(value=self.state.output_dir)
        portrait_var = tk.StringVar(value=self._portable_portrait_setting(self.state.portrait_dir))
        thumbnail_background_var = tk.StringVar(value=self.state.thumbnail_background_path)
        ffmpeg_var = tk.StringVar(value=self.state.ffmpeg_path)
        ffprobe_var = tk.StringVar(value=self.state.ffprobe_path)
        startgg_token_var = tk.StringVar(value=self.startgg_token_var.get())
        overlay_font_var = tk.StringVar(value=self.overlay_font_var.get())
        reencode_var = tk.BooleanVar(value=self.state.reencode)

        body = ttk.Frame(dialog, padding=12)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="Output override").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(body, textvariable=output_var, width=56).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(body, text="Browse", command=lambda: self._choose_directory(output_var, "Choose output folder")).grid(row=0, column=2, padx=(6, 0), pady=4)
        ttk.Label(body, text="Blank uses <video name>_matches.").grid(row=1, column=1, sticky="w", pady=(0, 8))

        ttk.Label(body, text="Portrait folder").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(body, textvariable=portrait_var, width=56).grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Button(body, text="Browse", command=lambda: self._choose_directory(portrait_var, "Choose portrait folder")).grid(row=2, column=2, padx=(6, 0), pady=4)
        ttk.Label(body, text="Blank uses bundled portraits.").grid(row=3, column=1, sticky="w", pady=(0, 8))

        ttk.Label(body, text="Thumbnail background").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(body, textvariable=thumbnail_background_var, width=56).grid(row=4, column=1, sticky="ew", pady=4)
        ttk.Button(body, text="Browse", command=lambda: self._choose_image(thumbnail_background_var)).grid(row=4, column=2, padx=(6, 0), pady=4)
        ttk.Label(body, text="Blank uses a black background.").grid(row=5, column=1, sticky="w", pady=(0, 8))

        ttk.Label(body, text="ffmpeg").grid(row=6, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(body, textvariable=ffmpeg_var, width=56).grid(row=6, column=1, sticky="ew", pady=4)
        ttk.Button(body, text="Browse", command=lambda: self._choose_executable(ffmpeg_var)).grid(row=6, column=2, padx=(6, 0), pady=4)

        ttk.Label(body, text="ffprobe").grid(row=7, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(body, textvariable=ffprobe_var, width=56).grid(row=7, column=1, sticky="ew", pady=4)
        ttk.Button(body, text="Browse", command=lambda: self._choose_executable(ffprobe_var)).grid(row=7, column=2, padx=(6, 0), pady=4)

        ttk.Label(body, text="start.gg token").grid(row=8, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(body, textvariable=startgg_token_var, show="*", width=56).grid(row=8, column=1, sticky="ew", pady=4)
        token_buttons = ttk.Frame(body)
        token_buttons.grid(row=8, column=2, sticky="ew", padx=(6, 0), pady=4)
        ttk.Button(token_buttons, text="Save", command=lambda: self._save_startgg_token_value(startgg_token_var.get())).grid(row=0, column=0, sticky="ew")
        ttk.Button(token_buttons, text="Clear", command=lambda: self._clear_startgg_token_value(startgg_token_var)).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        ttk.Label(body, text="Overlay font").grid(row=9, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Combobox(
            body,
            textvariable=overlay_font_var,
            values=["Bahnschrift", "Segoe UI", "Arial", "Trebuchet MS", "Tahoma", "Verdana"],
            state="readonly",
            width=53,
        ).grid(row=9, column=1, sticky="ew", pady=4)

        ttk.Label(body, text="Description boilerplate").grid(row=10, column=0, sticky="nw", padx=(0, 8), pady=4)
        boilerplate_text = tk.Text(body, width=56, height=5, wrap="word")
        boilerplate_text.grid(row=10, column=1, columnspan=2, sticky="ew", pady=4)
        boilerplate_text.insert("1.0", self.state.description_boilerplate)

        ttk.Checkbutton(body, text="Re-encode for more exact cuts", variable=reencode_var).grid(row=11, column=1, sticky="w", pady=(6, 10))

        buttons = ttk.Frame(body)
        buttons.grid(row=12, column=0, columnspan=3, sticky="e")
        ttk.Button(buttons, text="Use Defaults", command=lambda: self._reset_settings_dialog(output_var, portrait_var, thumbnail_background_var, ffmpeg_var, ffprobe_var, startgg_token_var, overlay_font_var, reencode_var, boilerplate_text)).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).grid(row=0, column=1, padx=6)
        ttk.Button(buttons, text="Save", command=lambda: self._save_settings_dialog(dialog, output_var, portrait_var, thumbnail_background_var, ffmpeg_var, ffprobe_var, startgg_token_var, overlay_font_var, reencode_var, boilerplate_text)).grid(row=0, column=2, padx=(6, 0))

        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.wait_window()

    def load_project(self) -> None:
        path = filedialog.askopenfilename(
            title="Load Tekken VOD Helper project",
            filetypes=[("Tekken VOD Helper project", "*.tvh.json"), ("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        self.stop_playback()
        try:
            with open(path, "r", encoding="utf-8") as handle:
                self.state = ProjectState.from_dict(json.load(handle))
        except Exception as exc:
            self.show_copyable_error("Could not load project", exc)
            return
        if not self.state.characters:
            self.state.characters = self._load_default_characters()
        self.project_path = Path(path)
        self._state_to_controls()
        self._refresh_combo_values()
        self._refresh_tree()
        self._update_video_summary()
        self._schedule_preview()
        self.log("Loaded project: {}".format(path))

    def save_project(self) -> None:
        if self.project_path is None:
            self.save_project_as()
            return
        self._save_project_to(self.project_path)

    def save_project_as(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save Tekken VOD Helper project",
            defaultextension=".tvh.json",
            filetypes=[("Tekken VOD Helper project", "*.tvh.json"), ("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        self.project_path = Path(path)
        self._save_project_to(self.project_path)

    def _save_project_to(self, path: Path) -> None:
        self.apply_match_details(show_errors=False)
        self._sync_paths_to_state()
        try:
            with path.open("w", encoding="utf-8") as handle:
                json.dump(self.state.to_dict(), handle, indent=2)
        except Exception as exc:
            self.show_copyable_error("Could not save project", exc)
            return
        self.log("Saved project: {}".format(path))

    def on_scrub(self, value: str) -> None:
        try:
            self._seek_to_time(float(value))
        except ValueError:
            pass

    def on_scrub_click(self, event) -> str:
        width = max(self.scrub.winfo_width(), 1)
        fraction = min(max(event.x / width, 0.0), 1.0)
        target = fraction * max(self.state.duration, 0.0)
        self.scrub_var.set(target)
        self._seek_to_time(target)
        return ""

    def seek_relative(self, offset: float) -> None:
        target = min(max(self.current_time + offset, 0.0), max(self.state.duration, 0.0))
        self.scrub_var.set(target)
        self._seek_to_time(target)

    def _seek_to_time(self, target: float) -> None:
        self.current_time = min(max(target, 0.0), max(self.state.duration, 0.0))
        self._update_time_label()
        if self.updating_playback_time:
            return
        if self._playback_is_running():
            self._seek_vlc(self.current_time)
        elif not self._vlc_available():
            self._schedule_preview()

    def toggle_playback(self) -> None:
        if self._playback_is_running():
            self.pause_playback()
        else:
            self.start_playback()

    def on_preview_click(self, _event=None) -> str:
        self.toggle_playback()
        return "break"

    def start_playback(self) -> None:
        if not self.state.video_path:
            messagebox.showinfo("Open a video first", "Load a VOD before starting playback.")
            return
        if not self._ensure_vlc_player():
            return
        self.video_surface.tkraise()
        self._seek_vlc(self.current_time)
        self.playback_requested = True
        self.vlc_player.play()
        self._update_play_button()
        self._schedule_playback_tick()

    def pause_playback(self) -> None:
        if self.playback_after_id is not None:
            self.after_cancel(self.playback_after_id)
            self.playback_after_id = None
        self.playback_requested = False
        if self.vlc_player is not None:
            self.vlc_player.pause()
        self._update_play_button()

    def stop_playback(self) -> None:
        if self.playback_after_id is not None:
            self.after_cancel(self.playback_after_id)
            self.playback_after_id = None
        self.playback_requested = False
        if self.vlc_player is not None:
            self.vlc_player.stop()
            self.vlc_player.release()
            self.vlc_player = None
            self.vlc_media_path = ""
        self._update_play_button()

    def _ensure_vlc_player(self) -> bool:
        if vlc is None:
            self.show_copyable_error("VLC required", "Install python-vlc and VLC media player to use embedded playback.")
            return False
        if self.vlc_player is not None and self.vlc_media_path == self.state.video_path:
            return True
        try:
            self._load_vlc_video()
        except Exception as exc:
            self.show_copyable_error("Could not start VLC playback", exc)
            return False
        return self.vlc_player is not None

    def _load_vlc_video(self) -> None:
        if vlc is None or not self.state.video_path:
            return
        if self.vlc_instance is None:
            self.vlc_instance = vlc.Instance(
                "--quiet",
                "--no-video-title-show",
                "--avcodec-hw=none",
            )
        if self.vlc_player is not None:
            self.vlc_player.stop()
            self.vlc_player.release()
        self.update_idletasks()
        player = self.vlc_instance.media_player_new()
        player.video_set_mouse_input(False)
        player.video_set_key_input(False)
        media = self.vlc_instance.media_new(self.state.video_path)
        player.set_media(media)
        handle = self.video_surface.winfo_id()
        if sys.platform.startswith("win"):
            player.set_hwnd(handle)
        elif sys.platform == "darwin":
            player.set_nsobject(handle)
        else:
            player.set_xwindow(handle)
        self.vlc_player = player
        self.vlc_media_path = self.state.video_path

    def _seek_vlc(self, timestamp: float) -> None:
        if self.vlc_player is None:
            return
        self.vlc_player.set_time(int(max(timestamp, 0.0) * 1000))

    def _schedule_playback_tick(self) -> None:
        if self.playback_after_id is not None:
            self.after_cancel(self.playback_after_id)
        self.playback_after_id = self.after(250, self._playback_tick)

    def _playback_tick(self) -> None:
        self.playback_after_id = None
        if not self._playback_is_running():
            if self.playback_requested and self._vlc_is_starting():
                self._schedule_playback_tick()
                return
            self.playback_requested = False
            self._update_play_button()
            return
        current_ms = self.vlc_player.get_time()
        if current_ms < 0:
            self._schedule_playback_tick()
            return
        self.current_time = min(current_ms / 1000.0, max(self.state.duration, 0.0))
        self.updating_playback_time = True
        try:
            self.scrub_var.set(self.current_time)
        finally:
            self.updating_playback_time = False
        self._update_time_label()
        self._schedule_playback_tick()

    def _playback_is_running(self) -> bool:
        return self.vlc_player is not None and bool(self.vlc_player.is_playing())

    def _vlc_available(self) -> bool:
        return vlc is not None and self.vlc_player is not None

    def _vlc_is_starting(self) -> bool:
        if vlc is None or self.vlc_player is None:
            return False
        return self.vlc_player.get_state() in (vlc.State.Opening, vlc.State.Buffering)

    def _update_play_button(self) -> None:
        if hasattr(self, "play_button"):
            self.play_button.configure(text="Pause" if self.playback_requested or self._playback_is_running() else "Play")

    def mark_match_start(self) -> None:
        if not self.state.video_path:
            messagebox.showinfo("Open a video first", "Load a VOD before marking match starts.")
            return
        self.apply_match_details(show_errors=False)
        start = round(self.current_time, 3)
        for match in self.state.matches:
            if abs(match.start - start) < 0.25:
                messagebox.showinfo("Match already exists", "A match start is already close to this time.")
                return
        matches = self.state.sorted_matches()
        previous_match = self._previous_match_for_start(matches, start)
        if previous_match is not None and previous_match.end is None:
            previous_match.end = start
        new_match = MatchSegment(start=start)
        matches.append(new_match)
        self.state.matches = matches
        self.state.matches = self.state.sorted_matches()
        self._refresh_tree(select_start=start)
        self.log("Marked match start at {}".format(seconds_to_timestamp(start)))

    def _previous_match_for_start(self, matches: List[MatchSegment], start: float) -> Optional[MatchSegment]:
        previous = None
        for match in matches:
            if match.start < start:
                previous = match
            else:
                break
        return previous

    def mark_match_end(self) -> None:
        if not self.state.video_path:
            messagebox.showinfo("Open a video first", "Load a VOD before marking match ends.")
            return

        index = self._selected_tree_index()
        if index is None:
            messagebox.showinfo("Select a match first", "Select the match you want to end.")
            return

        self.apply_match_details(show_errors=False, index=index, refresh=False)

        matches = self.state.sorted_matches()
        if index >= len(matches):
            return

        end = round(self.current_time, 3)
        match = matches[index]

        if end <= match.start:
            self.show_copyable_error("Invalid end time", "Match end must be after the match start.")
            return

        match.end = end
        self.state.matches = matches
        self._refresh_tree(select_start=match.start)
        self.log("Marked match end at {}".format(seconds_to_timestamp(end)))

    def delete_selected_match(self) -> None:
        index = self._selected_tree_index()
        if index is None:
            return
        matches = self.state.sorted_matches()
        if len(matches) <= 1:
            messagebox.showinfo("Keep one match", "A project needs at least one match segment.")
            return
        removed = matches.pop(index)
        self.state.matches = matches
        self.selected_index = None
        self._refresh_tree()
        self.log("Deleted match starting at {}".format(seconds_to_timestamp(removed.start)))

    def go_to_selected_match(self) -> None:
        index = self._selected_tree_index()
        if index is None:
            return
        matches = self.state.sorted_matches()
        if index >= len(matches):
            return
        self.scrub_var.set(matches[index].start)
        self.current_time = matches[index].start
        self._update_time_label()
        self._schedule_preview()

    def set_selected_start(self) -> None:
        index = self._selected_tree_index()
        if index is None:
            return
        self.apply_match_details(show_errors=False, index=index, refresh=False)
        try:
            start = parse_timestamp(self.start_var.get())
        except ValueError as exc:
            self.show_copyable_error("Invalid time", exc)
            return
        start = min(max(start, 0.0), max(self.state.duration, 0.0))
        matches = self.state.sorted_matches()
        matches[index].start = round(start, 3)
        self.state.matches = matches
        self._refresh_tree(select_start=start)
        self.log("Updated selected match start to {}".format(seconds_to_timestamp(start)))
    
    def set_selected_end(self) -> None:
        index = self._selected_tree_index()
        if index is None:
            return

        self.apply_match_details(show_errors=False, index=index, refresh=False)

        text = self.end_var.get().strip()
        if not text:
            end = None
        else:
            try:
                end = parse_timestamp(text)
            except ValueError as exc:
                self.show_copyable_error("Invalid time", exc)
                return
            end = min(max(end, 0.0), max(self.state.duration, 0.0))

        matches = self.state.sorted_matches()
        if index >= len(matches):
            return

        match = matches[index]
        if end is not None and end <= match.start:
            self.show_copyable_error("Invalid end time", "Match end must be after the match start.")
            return

        match.end = round(end, 3) if end is not None else None
        self.state.matches = matches
        self._refresh_tree(select_start=match.start)

        if end is None:
            self.log("Cleared selected match end.")
        else:
            self.log("Updated selected match end to {}".format(seconds_to_timestamp(end)))

    def on_match_selected(self, _event=None) -> None:
        previous_index = self.selected_index
        current_index = self._selected_tree_index()
        if previous_index is not None and previous_index != current_index:
            self.apply_match_details(show_errors=False, index=previous_index, refresh=False)
        self.selected_index = current_index
        self._load_selected_form()

    def apply_match_details(self, show_errors: bool = True, index: Optional[int] = None, refresh: bool = True) -> None:
        if self.loading_form:
            return
        if index is None:
            index = self._selected_tree_index()
        if index is None:
            return
        matches = self.state.sorted_matches()
        if index >= len(matches):
            return
        match = matches[index]
        match.player1 = self.player1_var.get().strip()
        match.player2 = self.player2_var.get().strip()
        match.character1 = self.character1_var.get().strip()
        match.character2 = self.character2_var.get().strip()
        match.round_name = self.round_var.get().strip()
        match.notes = self.notes_var.get().strip()
        self.state.players = unique_sorted(self.state.players + [match.player1, match.player2])
        self.state.characters = unique_sorted(self.state.characters + [match.character1, match.character2])
        self.state.matches = matches
        self._refresh_combo_values()
        if refresh:
            self._refresh_tree(select_start=match.start)
        if show_errors:
            self.log("Applied details for match {}.".format(index + 1))

    def export_clips(self) -> None:
        if self.export_thread and self.export_thread.is_alive():
            messagebox.showinfo("Export running", "The current export is still running.")
            return
        self.apply_match_details(show_errors=False)
        self._sync_paths_to_state()
        try:
            require_tool(self.state.ffmpeg_path, "ffmpeg")
        except FfmpegError as exc:
            self.show_copyable_error("FFmpeg required", exc)
            return
        if Image is None:
            self.show_copyable_error("Pillow required", "Install Pillow with: pip install -r requirements.txt")
            return
        if not self.state.video_path:
            self.show_copyable_error("No video", "Open a video before exporting.")
            return
        output_dir = self._resolved_output_dir()

        jobs = self._build_export_jobs(output_dir)
        if not jobs:
            self.show_copyable_error("No clips", "No valid match ranges are available to export.")
            return

        self._set_export_state("disabled")
        self.export_thread = threading.Thread(target=self._export_worker, args=(jobs, output_dir), daemon=True)
        self.export_thread.start()
        self.log("Started export of {} clips.".format(len(jobs)))

    def generate_metadata_only(self) -> None:
        if self.export_thread and self.export_thread.is_alive():
            messagebox.showinfo("Export running", "The current export is still running.")
            return
        self.apply_match_details(show_errors=False)
        self._sync_paths_to_state()
        if Image is None:
            self.show_copyable_error("Pillow required", "Install Pillow with: pip install -r requirements.txt")
            return
        if not self.state.video_path:
            self.show_copyable_error("No video", "Open a video before generating metadata.")
            return
        output_dir = self._resolved_output_dir()
        jobs = self._build_export_jobs(output_dir)
        if not jobs:
            self.show_copyable_error("No clips", "No valid match ranges are available to generate metadata.")
            return
        self._set_export_state("disabled")
        self.export_thread = threading.Thread(target=self._metadata_only_worker, args=(jobs, output_dir), daemon=True)
        self.export_thread.start()
        self.log("Started metadata-only generation for {} clips.".format(len(jobs)))

    def _export_worker(self, jobs: List[ExportJob], output_dir: str) -> None:
        try:
            output_root = Path(output_dir)
            output_root.mkdir(parents=True, exist_ok=True)
            for job in jobs:
                folder = output_root / job.folder_name
                folder.mkdir(parents=True, exist_ok=True)
                self._thread_log("Exporting match {}.".format(job.index))
                slice_clip(
                    self.state.video_path,
                    job.start,
                    job.end,
                    job.clip_path,
                    self.state.ffmpeg_path,
                    self.state.reencode,
                    logger=self._thread_log,
                )
                self._write_export_artifacts(job)
            self.log_queue.put(("done", "Export complete."))
        except Exception as exc:
            self.log_queue.put(("error", str(exc)))

    def _metadata_only_worker(self, jobs: List[ExportJob], output_dir: str) -> None:
        try:
            output_root = Path(output_dir)
            output_root.mkdir(parents=True, exist_ok=True)
            for job in jobs:
                folder = output_root / job.folder_name
                folder.mkdir(parents=True, exist_ok=True)
                self._thread_log("Generating metadata for match {}.".format(job.index))
                self._write_export_artifacts(job)
            self.log_queue.put(("done", "Metadata generation complete."))
        except Exception as exc:
            self.log_queue.put(("error", str(exc)))

    def _write_export_artifacts(self, job: ExportJob) -> None:
        make_thumbnail(
            self.state.video_path,
            job.match,
            job.start,
            job.end,
            job.thumbnail_path,
            self._resolved_portrait_dir(),
            self.state.ffmpeg_path,
            event_name=self.state.event_name,
            background_path=self.state.thumbnail_background_path,
            logger=self._thread_log,
        )
        self._write_match_metadata(job)
        self._write_upload_sidecars(job)

    def _build_export_jobs(self, output_dir: str) -> List[ExportJob]:
        matches = self.state.sorted_matches()
        jobs: List[ExportJob] = []
        output_root = Path(output_dir)
        video_stem = safe_relative_name(self.state.video_path)

        for index, match in enumerate(matches):
            end = self._resolve_match_end(matches, index)
            if end is None:
                self.log("Skipping match {}: no end time or following boundary is available.".format(index + 1))
                continue

            if end <= match.start:
                self.log("Skipping match {}: end is not after start.".format(index + 1))
                continue

            label = "{}_{}_vs_{}".format(
                "{:02d}".format(index + 1),
                slugify(match.player1 or "Player1"),
                slugify(match.player2 or "Player2"),
            )
            if match.character1 or match.character2:
                label += "_{}_vs_{}".format(slugify(match.character1 or "Character1"), slugify(match.character2 or "Character2"))

            folder_name = slugify(label, "match_{:02d}".format(index + 1))
            folder = output_root / folder_name
            clip_path = str(folder / "{}.mp4".format(video_stem))

            jobs.append(
                ExportJob(
                    match=match,
                    index=index + 1,
                    start=match.start,
                    end=end,
                    folder_name=folder_name,
                    clip_path=clip_path,
                    thumbnail_path=str(folder / "thumbnail.jpg"),
                    metadata_path=str(folder / "match.json"),
                    title_path=str(folder / "title.txt"),
                    description_path=str(folder / "description.txt"),
                )
            )

        return jobs

    def _resolve_match_end(self, matches: List[MatchSegment], index: int) -> Optional[float]:
        match = matches[index]
        if match.end is not None:
            return min(match.end, self.state.duration) if self.state.duration > 0.0 else match.end
        if index + 1 < len(matches):
            return matches[index + 1].start
        if self.state.duration > match.start:
            return self.state.duration
        return None

    def _write_match_metadata(self, job: ExportJob) -> None:
        payload = job.match.to_dict()
        payload.update(
            {
                "index": job.index,
                "start_timestamp": seconds_to_timestamp(job.start),
                "end": job.end,
                "end_timestamp": seconds_to_timestamp(job.end),
                "duration": job.end - job.start,
                "clip": Path(job.clip_path).name,
                "thumbnail": Path(job.thumbnail_path).name,
                "title_file": Path(job.title_path).name,
                "description_file": Path(job.description_path).name,
                "event_name": self.state.event_name,
            }
        )
        with open(job.metadata_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        self._thread_log("Metadata written: {}".format(job.metadata_path))

    def _write_upload_sidecars(self, job: ExportJob) -> None:
        title = self._upload_title(job.match)
        description = self._upload_description(job)
        Path(job.title_path).write_text(title + "\n", encoding="utf-8")
        Path(job.description_path).write_text(description + "\n", encoding="utf-8")
        self._thread_log("Upload text written: {}, {}".format(job.title_path, job.description_path))

    def _upload_title(self, match: MatchSegment) -> str:
        p1 = match.player1 or "Player 1"
        p2 = match.player2 or "Player 2"
        c1 = " ({})".format(match.character1) if match.character1 else ""
        c2 = " ({})".format(match.character2) if match.character2 else ""
        parts = ["{}{} vs {}{}".format(p1, c1, p2, c2)]
        if match.round_name:
            parts.append(match.round_name)
        if self.state.event_name:
            parts.append(self.state.event_name)
        return " - ".join(parts)

    def _upload_description(self, job: ExportJob) -> str:
        match = job.match
        lines = [self._upload_title(match), ""]
        if self.state.event_name:
            lines.append("Event: {}".format(self.state.event_name))
        if match.round_name:
            lines.append("Round: {}".format(match.round_name))
        lines.append("Players: {} vs {}".format(match.player1 or "Player 1", match.player2 or "Player 2"))
        if match.character1 or match.character2:
            lines.append("Characters: {} vs {}".format(match.character1 or "Character", match.character2 or "Character"))
        lines.append("Clip time: {} - {}".format(seconds_to_timestamp(job.start), seconds_to_timestamp(job.end)))
        if match.notes:
            lines.extend(["", match.notes])
        boilerplate = self.state.description_boilerplate.strip()
        if boilerplate:
            lines.extend(["", boilerplate])
        return "\n".join(lines)

    def _schedule_preview(self) -> None:
        if self.preview_after_id is not None:
            self.after_cancel(self.preview_after_id)
        self.preview_after_id = self.after(180, self._update_preview)

    def _update_preview(self) -> None:
        self.preview_after_id = None
        if not self.state.video_path:
            return
        self._sync_paths_to_state()
        try:
            if Image is None or ImageTk is None:
                self.preview_label.configure(text="Install Pillow to show previews.", image="")
                return
            preview_path = Path(tempfile.gettempdir()) / "tekken_vod_helper_preview.png"
            extract_frame(self.state.video_path, self.current_time, str(preview_path), self.state.ffmpeg_path, width=960)
            image = Image.open(str(preview_path)).convert("RGB")
            frame_width = max(self.preview_label.winfo_width(), 640)
            frame_height = max(self.preview_label.winfo_height(), 360)
            image.thumbnail((frame_width, frame_height))
            self.preview_photo = ImageTk.PhotoImage(image)
            self.preview_label.configure(image=self.preview_photo, text="")
            self.preview_label.tkraise()
        except Exception as exc:
            self.preview_label.configure(text=str(exc), image="")
            self.preview_label.tkraise()

    def _refresh_tree(self, select_start: Optional[float] = None) -> None:
        current_selection_start = select_start
        if current_selection_start is None:
            index = self._selected_tree_index()
            matches_before = self.state.sorted_matches()
            if index is not None and index < len(matches_before):
                current_selection_start = matches_before[index].start

        for item in self.match_tree.get_children():
            self.match_tree.delete(item)
        matches = self.state.sorted_matches()
        for index, match in enumerate(matches):
            end = match.end
            players = "{} vs {}".format(match.player1 or "Player 1", match.player2 or "Player 2")
            characters = "{} vs {}".format(match.character1 or "Character", match.character2 or "Character")
            self.match_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    index + 1,
                    seconds_to_timestamp(match.start),
                    seconds_to_timestamp(end) if end is not None else "",
                    players,
                    characters,
                ),
            )
        if matches:
            selected = 0
            if current_selection_start is not None:
                for index, match in enumerate(matches):
                    if abs(match.start - current_selection_start) < 0.01:
                        selected = index
                        break
            self.match_tree.selection_set(str(selected))
            self.match_tree.focus(str(selected))
            self.selected_index = selected
            self._load_selected_form()
        else:
            self.selected_index = None
            self._load_selected_form()

    def _load_selected_form(self) -> None:
        index = self._selected_tree_index()
        matches = self.state.sorted_matches()
        self.loading_form = True
        try:
            if index is None or index >= len(matches):
                self.player1_var.set("")
                self.player2_var.set("")
                self.character1_var.set("")
                self.character2_var.set("")
                self.round_var.set("")
                self.notes_var.set("")
                self.start_var.set("00:00:00.000")
                self.end_var.set("")
                return
            match = matches[index]
            self.player1_var.set(match.player1)
            self.player2_var.set(match.player2)
            self.character1_var.set(match.character1)
            self.character2_var.set(match.character2)
            self.round_var.set(match.round_name)
            self.notes_var.set(match.notes)
            self.start_var.set(seconds_to_timestamp(match.start))
            end = self._display_match_end(matches, index)
            self.end_var.set(seconds_to_timestamp(end) if end is not None else "")
        finally:
            self.loading_form = False

    def _display_match_end(self, matches: List[MatchSegment], index: int) -> Optional[float]:
        if index >= len(matches):
            return None
        match = matches[index]
        if match.end is not None:
            return match.end
        return self._resolve_match_end(matches, index)

    def _selected_tree_index(self) -> Optional[int]:
        selection = self.match_tree.selection()
        if not selection:
            return self.selected_index
        try:
            return int(selection[0])
        except ValueError:
            return None

    def _refresh_combo_values(self) -> None:
        players = unique_sorted(self.state.players)
        characters = unique_sorted(self.state.characters)
        self.state.players = players
        self.state.characters = characters
        if hasattr(self, "player1_combo"):
            self.player1_combo.configure(values=players)
            self.player2_combo.configure(values=players)
            self.character1_combo.configure(values=characters)
            self.character2_combo.configure(values=characters)
            self._refresh_character_picker()
        if hasattr(self, "overlay_p1_combo"):
            self.overlay_p1_combo.configure(values=players)
            self.overlay_p2_combo.configure(values=players)
            self.overlay_character1_combo.configure(values=characters)
            self.overlay_character2_combo.configure(values=characters)

    def _bind_overlay_character_search(self, combo: ttk.Combobox, variable: tk.StringVar) -> None:
        def on_keyrelease(event):
            return self._autocomplete_character_combo(event, combo, variable)

        def accept(_event):
            self._accept_character_combo_match(combo, variable)
            return "break"

        combo.bind("<KeyRelease>", on_keyrelease)
        combo.bind("<Return>", accept)

    def _bind_character_search(self, combo: ttk.Combobox, slot: int) -> None:
        combo.bind("<KeyRelease>", lambda event, selected_slot=slot: self._on_character_keyrelease(event, selected_slot))
        combo.bind("<Return>", lambda event, selected_slot=slot: self._accept_character_match(selected_slot))
        combo.bind("<<ComboboxSelected>>", lambda _event: self.apply_match_details(show_errors=False))

    def _on_character_keyrelease(self, event, slot: int):
        combo = self.character1_combo if slot == 1 else self.character2_combo
        result = self._autocomplete_character_combo(event, combo, self._character_var(slot))
        if self.character_picker_slot == slot:
            self.character_picker_var.set(self._character_var(slot).get())
            self._refresh_character_picker()
        return result

    def _accept_character_match(self, slot: int):
        combo = self.character1_combo if slot == 1 else self.character2_combo
        character = self._accept_character_combo_match(combo, self._character_var(slot))
        if character:
            self._set_character(slot, character)
        return "break"

    def _autocomplete_character_combo(self, event, combo: ttk.Combobox, variable: tk.StringVar):
        ignored = {
            "Return",
            "Escape",
            "Tab",
            "Shift_L",
            "Shift_R",
            "Control_L",
            "Control_R",
            "Alt_L",
            "Alt_R",
            "Up",
            "Down",
            "Left",
            "Right",
        }
        if event.keysym in ignored:
            return None

        query = variable.get()
        matches = self._matching_characters(query, limit=12)
        combo.configure(values=matches if matches else self.state.characters)

        if event.keysym in {"BackSpace", "Delete"}:
            return None

        match = self._best_character_prefix_match(query)
        if match and match != query and match.casefold().startswith(query.casefold()):
            typed_length = len(query)
            variable.set(match)
            try:
                combo.icursor(typed_length)
                combo.selection_range(typed_length, "end")
            except tk.TclError:
                pass
        return None

    def _accept_character_combo_match(self, combo: ttk.Combobox, variable: tk.StringVar) -> str:
        match = self._best_character_prefix_match(variable.get())
        if not match:
            matches = self._matching_characters(variable.get(), limit=1)
            match = matches[0] if matches else ""
        if match:
            variable.set(match)
            combo.configure(values=self._matching_characters(match, limit=12) or self.state.characters)
            try:
                combo.icursor("end")
                combo.selection_clear()
            except tk.TclError:
                pass
        return match

    def _best_character_prefix_match(self, text: str) -> str:
        query = text.strip().casefold()
        if not query:
            return ""
        for character in unique_sorted(self.state.characters):
            if character.casefold().startswith(query):
                return character
        return ""

    def _matching_characters(self, text: str, limit: Optional[int] = 4) -> List[str]:
        query = text.strip().casefold()
        characters = unique_sorted(self.state.characters)
        if not query:
            return characters if limit is None else characters[:limit]

        starts = [name for name in characters if name.casefold().startswith(query)]
        contains = [name for name in characters if query in name.casefold() and name not in starts]
        matches = starts + contains
        return matches if limit is None else matches[:limit]

    def _character_var(self, slot: int) -> tk.StringVar:
        return self.character1_var if slot == 1 else self.character2_var

    def _set_character(self, slot: int, character: str) -> None:
        self._character_var(slot).set(character)
        combo = self.character1_combo if slot == 1 else self.character2_combo
        combo.configure(values=self._matching_characters(character, limit=12) or self.state.characters)
        if not self.loading_form:
            self.apply_match_details(show_errors=False)

    def _open_character_picker(self, slot: int) -> None:
        if self.character_picker_window is not None and self.character_picker_window.winfo_exists():
            self.character_picker_window.destroy()

        self.character_picker_slot = slot
        self.character_picker_var.set(self._character_var(slot).get())

        window = tk.Toplevel(self)
        window.title("Choose P{} Character".format(slot))
        window.transient(self)
        window.geometry("640x520")
        window.minsize(440, 360)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(2, weight=1)
        window.protocol("WM_DELETE_WINDOW", self._close_character_picker)
        self.character_picker_window = window

        search = ttk.Entry(window, textvariable=self.character_picker_var)
        search.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        search.bind("<KeyRelease>", lambda _event: self._schedule_character_picker_refresh())
        search.bind("<Return>", lambda _event: self._accept_character_picker_match())
        search.bind("<Escape>", lambda _event: self._close_character_picker())
        ttk.Label(window, textvariable=self.character_picker_status_var, anchor="w").grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))

        canvas = tk.Canvas(window, highlightthickness=0)
        scroll = ttk.Scrollbar(window, orient="vertical", command=canvas.yview)
        grid = ttk.Frame(canvas)
        grid.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=grid, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.grid(row=2, column=0, sticky="nsew", padx=(10, 0), pady=(0, 10))
        scroll.grid(row=2, column=1, sticky="ns", pady=(0, 10), padx=(0, 10))
        canvas.bind("<MouseWheel>", lambda event: canvas.yview_scroll(-1 * int(event.delta / 120), "units"))
        grid.bind("<MouseWheel>", lambda event: canvas.yview_scroll(-1 * int(event.delta / 120), "units"))
        self.character_picker_grid = grid

        self._refresh_character_picker()
        search.focus_set()
        search.selection_range(0, "end")

    def _close_character_picker(self) -> None:
        if self.character_picker_after_id is not None:
            self.after_cancel(self.character_picker_after_id)
            self.character_picker_after_id = None
        if self.character_picker_window is not None and self.character_picker_window.winfo_exists():
            self.character_picker_window.destroy()
        self.character_picker_window = None
        self.character_picker_slot = None
        self.character_picker_grid = None

    def _schedule_character_picker_refresh(self) -> None:
        if self.character_picker_after_id is not None:
            self.after_cancel(self.character_picker_after_id)
        self.character_picker_after_id = self.after(80, self._refresh_character_picker)

    def _accept_character_picker_match(self):
        if self.character_picker_slot is None:
            return "break"
        matches = self._matching_characters(self.character_picker_var.get(), limit=1)
        if matches:
            self._set_character(self.character_picker_slot, matches[0])
            self._close_character_picker()
        return "break"

    def _refresh_character_picker(self) -> None:
        self.character_picker_after_id = None
        if self.character_picker_grid is None:
            return
        for child in self.character_picker_grid.winfo_children():
            child.destroy()
        matches = self._matching_characters(self.character_picker_var.get(), limit=None)
        if not matches:
            self.character_picker_status_var.set("No matches")
            ttk.Label(self.character_picker_grid, text="No matches").grid(row=0, column=0, sticky="w", padx=10, pady=10)
            return
        visible = matches[:60]
        if len(matches) > len(visible):
            self.character_picker_status_var.set("Showing {} of {} matches. Keep typing to narrow it down.".format(len(visible), len(matches)))
        else:
            self.character_picker_status_var.set("{} match{}".format(len(matches), "" if len(matches) == 1 else "es"))
        for index, character in enumerate(visible):
            button = self._portrait_button(self.character_picker_grid, character)
            button.grid(row=index // 5, column=index % 5, padx=6, pady=6, sticky="nsew")

    def _portrait_button(self, parent: ttk.Frame, character: str):
        image = self._portrait_photo(character)
        kwargs = {
            "text": character,
            "command": lambda selected=character: self._choose_character_from_picker(selected),
            "padx": 4,
            "pady": 4,
        }
        if image is not None:
            kwargs.update({"image": image, "compound": "top"})
        else:
            kwargs["width"] = 12
        return tk.Button(parent, **kwargs)

    def _choose_character_from_picker(self, character: str) -> None:
        if self.character_picker_slot is None:
            return
        self._set_character(self.character_picker_slot, character)
        self._close_character_picker()

    def _portrait_photo(self, character: str):
        if Image is None or ImageTk is None:
            return None
        portrait_path = find_portrait(self._resolved_portrait_dir(), character)
        if portrait_path is None:
            return None
        cache_key = (str(portrait_path.resolve()), 76, 76)
        cached = self.portrait_photo_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            image = Image.open(str(portrait_path)).convert("RGBA")
            image.thumbnail((76, 76))
            photo = ImageTk.PhotoImage(image)
            self.portrait_photo_cache[cache_key] = photo
            return photo
        except Exception:
            return None

    def _sync_paths_to_state(self) -> None:
        self.state.event_name = self.event_var.get().strip()
        self.state.output_dir = self.output_var.get().strip()
        self.state.portrait_dir = self._portable_portrait_setting(self.portrait_var.get().strip())
        self.state.thumbnail_background_path = self.thumbnail_background_var.get().strip()
        self.state.ffmpeg_path = self.ffmpeg_var.get().strip()
        self.state.ffprobe_path = self.ffprobe_var.get().strip()
        self.state.reencode = bool(self.reencode_var.get())
        self.state.overlay = self._overlay_state_from_controls()
        self.state.obs = self._obs_settings_from_controls()
        self.state.startgg.token = self.startgg_token_var.get().strip()
        self.state.startgg.tournament_slug = normalize_tournament_slug(self.startgg_slug_var.get())
        if self.state.startgg.token:
            try:
                write_startgg_token(self.state.startgg.token)
            except CredentialError as exc:
                self.log("Could not save start.gg token to Windows credentials: {}".format(exc))

    def _state_to_controls(self) -> None:
        self.event_var.set(self.state.event_name)
        self.output_var.set(self.state.output_dir)
        self.portrait_var.set(self._portable_portrait_setting(self.state.portrait_dir))
        self.thumbnail_background_var.set(self.state.thumbnail_background_path)
        self.ffmpeg_var.set(self.state.ffmpeg_path)
        self.ffprobe_var.set(self.state.ffprobe_path)
        self.reencode_var.set(self.state.reencode)
        self.overlay_description_var.set(self.state.overlay.description)
        self.overlay_subtitle_var.set(self.state.overlay.subtitle)
        self.overlay_p1_var.set(self.state.overlay.p1name)
        self.overlay_p2_var.set(self.state.overlay.p2name)
        self.overlay_character1_var.set("")
        self.overlay_character2_var.set("")
        self.overlay_p1score_var.set(self.state.overlay.p1score)
        self.overlay_p2score_var.set(self.state.overlay.p2score)
        self.overlay_font_var.set(self.state.overlay.font)
        self.obs_host_var.set(self.state.obs.host)
        self.obs_port_var.set(str(self.state.obs.port))
        self.obs_password_var.set(self.state.obs.password)
        self.startgg_token_var.set(self._initial_startgg_token())
        self.startgg_slug_var.set(self.state.startgg.tournament_slug)
        self.current_time = 0.0
        self.scrub.configure(to=max(self.state.duration, 1.0))
        self.scrub_var.set(0.0)
        self._update_time_label()

    def _update_video_summary(self) -> None:
        if self.state.video_path:
            self.video_var.set("{} ({})".format(Path(self.state.video_path).name, seconds_to_timestamp(self.state.duration)))
        else:
            self.video_var.set("No video loaded")

    def _update_time_label(self) -> None:
        self.time_label_var.set(
            "{} / {}".format(seconds_to_timestamp(self.current_time), seconds_to_timestamp(self.state.duration))
        )

    def _load_default_characters(self) -> List[str]:
        path = Path(__file__).with_name("characters.txt")
        try:
            return unique_sorted(path.read_text(encoding="utf-8").splitlines())
        except OSError:
            return []

    def _apply_app_icon(self) -> None:
        ico_path = self._package_resource_path("kwtekken-icon.ico")
        if ico_path.exists() and sys.platform == "win32":
            try:
                self.iconbitmap(default=str(ico_path))
            except tk.TclError:
                pass
        path = self._package_resource_path("kwtekken-icon.png")
        if not path.exists():
            return
        try:
            icon_names = [
                "kwtekken-icon-16.png",
                "kwtekken-icon-32.png",
                "kwtekken-icon-48.png",
                "kwtekken-icon-256.png",
                "kwtekken-icon.png",
            ]
            self.app_icon_photos = [
                tk.PhotoImage(file=str(icon_path))
                for icon_path in (self._package_resource_path(name) for name in icon_names)
                if icon_path.exists()
            ]
            if self.app_icon_photos:
                self.iconphoto(True, *self.app_icon_photos)
        except tk.TclError:
            self.app_icon_photos = []

    def _default_portrait_dir(self) -> Path:
        return self._resource_path("portraits")

    def _package_resource_path(self, name: str) -> Path:
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            return Path(bundle_root) / "tekken_vod_helper" / name
        return Path(__file__).with_name(name)

    def _resource_path(self, name: str) -> Path:
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            return Path(bundle_root) / name
        return Path(__file__).resolve().parents[1] / name

    def _overlay_static_dir(self) -> Path:
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            return Path(bundle_root) / "tekken_vod_helper" / "overlay_static"
        return Path(__file__).with_name("overlay_static")

    def _resolved_output_dir(self) -> str:
        configured = self.output_var.get().strip() or self.state.output_dir.strip()
        if configured:
            return configured
        if self.state.video_path:
            return str(Path(self.state.video_path).with_suffix("")) + "_matches"
        return str(Path.cwd() / "tekken_vod_matches")

    def _resolved_portrait_dir(self) -> str:
        configured = self.portrait_var.get().strip() or self.state.portrait_dir.strip()
        return self._portrait_dir_or_default(configured)

    def _portrait_dir_or_default(self, configured: str) -> str:
        if configured and Path(configured).exists():
            return configured
        return str(self._default_portrait_dir())

    def _portable_portrait_setting(self, configured: str) -> str:
        configured = configured.strip()
        if not configured:
            return ""
        try:
            if Path(configured).resolve() == self._default_portrait_dir().resolve():
                return ""
        except OSError:
            pass
        return configured if Path(configured).exists() else ""

    def _choose_directory(self, variable: tk.StringVar, title: str) -> None:
        path = filedialog.askdirectory(title=title)
        if path:
            variable.set(path)

    def _choose_executable(self, variable: tk.StringVar) -> None:
        path = filedialog.askopenfilename(title="Choose executable", filetypes=[("Executable", "*.exe"), ("All files", "*.*")])
        if path:
            variable.set(path)

    def _choose_image(self, variable: tk.StringVar) -> None:
        path = filedialog.askopenfilename(
            title="Choose thumbnail background",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp"), ("All files", "*.*")],
        )
        if path:
            variable.set(path)

    def _reset_settings_dialog(
        self,
        output_var: tk.StringVar,
        portrait_var: tk.StringVar,
        thumbnail_background_var: tk.StringVar,
        ffmpeg_var: tk.StringVar,
        ffprobe_var: tk.StringVar,
        startgg_token_var: tk.StringVar,
        overlay_font_var: tk.StringVar,
        reencode_var: tk.BooleanVar,
        boilerplate_text: tk.Text,
    ) -> None:
        output_var.set("")
        portrait_var.set("")
        thumbnail_background_var.set("")
        ffmpeg_var.set("")
        ffprobe_var.set("")
        startgg_token_var.set("")
        overlay_font_var.set("Bahnschrift")
        reencode_var.set(False)
        boilerplate_text.delete("1.0", "end")

    def _save_settings_dialog(
        self,
        dialog: tk.Toplevel,
        output_var: tk.StringVar,
        portrait_var: tk.StringVar,
        thumbnail_background_var: tk.StringVar,
        ffmpeg_var: tk.StringVar,
        ffprobe_var: tk.StringVar,
        startgg_token_var: tk.StringVar,
        overlay_font_var: tk.StringVar,
        reencode_var: tk.BooleanVar,
        boilerplate_text: tk.Text,
    ) -> None:
        self.output_var.set(output_var.get().strip())
        self.portrait_var.set(self._portable_portrait_setting(portrait_var.get().strip()))
        self.thumbnail_background_var.set(thumbnail_background_var.get().strip())
        self.ffmpeg_var.set(ffmpeg_var.get().strip())
        self.ffprobe_var.set(ffprobe_var.get().strip())
        self.startgg_token_var.set(startgg_token_var.get().strip())
        self.overlay_font_var.set(overlay_font_var.get().strip() or "Bahnschrift")
        self.reencode_var.set(bool(reencode_var.get()))
        self.state.description_boilerplate = boilerplate_text.get("1.0", "end").strip()
        self._sync_paths_to_state()
        dialog.destroy()
        self.log("Settings saved.")

    def _poll_log_queue(self) -> None:
        while True:
            try:
                kind, message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.log(str(message))
            elif kind == "tournaments":
                self.fetching_startgg_events = False
                self._set_startgg_events_button_state("normal")
                self._load_tournaments(message)
            elif kind == "tournaments_error":
                self.fetching_startgg_events = False
                self._set_startgg_events_button_state("normal")
                self.log("start.gg event fetch error: {}".format(message))
                self._set_startgg_controls_visible(False)
                self.startgg_status_var.set("Could not reach start.gg. Load start.gg events when the token/network is ready.")
                self.show_copyable_error("start.gg event fetch failed", message)
            elif kind == "sets":
                self.fetching_startgg_sets = False
                self._set_startgg_events_button_state("normal")
                self._load_bracket_sets(message)
            elif kind == "sets_error":
                self.fetching_startgg_sets = False
                self._set_startgg_events_button_state("normal")
                self.log("start.gg error: {}".format(message))
                self.startgg_status_var.set("Could not load bracket sets. Check the tournament and try again.")
                self.show_copyable_error("start.gg fetch failed", message)
            elif kind == "done":
                self.log(str(message))
                self._set_export_state("normal")
                messagebox.showinfo("Export complete", str(message))
            elif kind == "error":
                self.log("ERROR: {}".format(message))
                self._set_export_state("normal")
                self.show_copyable_error("Export failed", message)
        self.after(150, self._poll_log_queue)

    def _thread_log(self, message: str) -> None:
        self.log_queue.put(("log", message))

    def log(self, message: str) -> None:
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")

    def destroy(self) -> None:
        self.stop_playback()
        self.overlay_server.stop()
        super().destroy()


def main() -> None:
    app = TekkenVodHelperApp()
    app.mainloop()
