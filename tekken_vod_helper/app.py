import json
import queue
import sys
import threading
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import List, Optional, Tuple

from .ffmpeg_tools import FfmpegError, extract_frame, probe_duration, require_tool, slice_clip
from .models import ExportJob, MatchSegment, ProjectState
from .thumbnails import Image, ImageTk, find_portrait, make_thumbnail
from .util import parse_timestamp, safe_relative_name, seconds_to_timestamp, slugify, unique_sorted

try:
    import vlc
except ImportError:  # pragma: no cover - exercised by app runtime messaging
    vlc = None


class TekkenVodHelperApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Tekken VOD Helper")
        self.geometry("1280x820")
        self.minsize(1040, 680)

        self.state = ProjectState(
            characters=self._load_default_characters(),
            portrait_dir=str(self._default_portrait_dir()),
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
        self.output_var = tk.StringVar(value=self.state.output_dir)
        self.event_var = tk.StringVar(value=self.state.event_name)
        self.portrait_var = tk.StringVar(value=self.state.portrait_dir)
        self.thumbnail_background_var = tk.StringVar(value=self.state.thumbnail_background_path)
        self.ffmpeg_var = tk.StringVar(value=self.state.ffmpeg_path)
        self.ffprobe_var = tk.StringVar(value=self.state.ffprobe_path)
        self.reencode_var = tk.BooleanVar(value=self.state.reencode)
        self.menu_items = {}
        self.portrait_photo_cache = {}

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._configure_styles()
        self._build_menu()
        self._build_ui()
        self._refresh_combo_values()
        self._refresh_tree()
        self.after(150, self._poll_log_queue)

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=False)
        self._add_menu_command(file_menu, "Open Video...", self.open_video, accelerator="Ctrl+O")
        file_menu.add_separator()
        self._add_menu_command(file_menu, "Load Project...", self.load_project)
        self._add_menu_command(file_menu, "Save Project", self.save_project, accelerator="Ctrl+S")
        self._add_menu_command(file_menu, "Save Project As...", self.save_project_as)
        file_menu.add_separator()
        self._add_menu_command(file_menu, "Exit", self.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

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

    def _invoke_menu_command(self, command):
        command()
        return "break"

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(0, weight=1)

        left = ttk.Frame(self, padding=(10, 10, 5, 10))
        right = ttk.Frame(self, padding=(5, 10, 10, 10))
        left.grid(row=0, column=0, sticky="nsew")
        right.grid(row=0, column=1, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        self._build_video_panel(left)
        self._build_match_panel(right)
        self._build_log_panel(left)

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

        ttk.Button(editor, text="Apply Details", command=self.apply_match_details).grid(row=3, column=0, sticky="ew", pady=(8, 0))

        self.export_button = ttk.Button(
            parent,
            text="Export Clips",
            command=self.export_clips,
            style="Export.TButton",
        )
        self.export_button.grid(row=3, column=0, sticky="ew", pady=(12, 0))

    def _build_log_panel(self, parent: ttk.Frame) -> None:
        log_frame = ttk.LabelFrame(parent, text="Log", padding=6)
        log_frame.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, height=7, wrap="word")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)

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
            messagebox.showerror("Could not read video", str(exc))
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
        portrait_var = tk.StringVar(value=self._resolved_portrait_dir())
        thumbnail_background_var = tk.StringVar(value=self.state.thumbnail_background_path)
        ffmpeg_var = tk.StringVar(value=self.state.ffmpeg_path)
        ffprobe_var = tk.StringVar(value=self.state.ffprobe_path)
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

        ttk.Label(body, text="Thumbnail background").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(body, textvariable=thumbnail_background_var, width=56).grid(row=3, column=1, sticky="ew", pady=4)
        ttk.Button(body, text="Browse", command=lambda: self._choose_image(thumbnail_background_var)).grid(row=3, column=2, padx=(6, 0), pady=4)
        ttk.Label(body, text="Blank uses a black background.").grid(row=4, column=1, sticky="w", pady=(0, 8))

        ttk.Label(body, text="ffmpeg").grid(row=5, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(body, textvariable=ffmpeg_var, width=56).grid(row=5, column=1, sticky="ew", pady=4)
        ttk.Button(body, text="Browse", command=lambda: self._choose_executable(ffmpeg_var)).grid(row=5, column=2, padx=(6, 0), pady=4)

        ttk.Label(body, text="ffprobe").grid(row=6, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(body, textvariable=ffprobe_var, width=56).grid(row=6, column=1, sticky="ew", pady=4)
        ttk.Button(body, text="Browse", command=lambda: self._choose_executable(ffprobe_var)).grid(row=6, column=2, padx=(6, 0), pady=4)

        ttk.Label(body, text="Description boilerplate").grid(row=7, column=0, sticky="nw", padx=(0, 8), pady=4)
        boilerplate_text = tk.Text(body, width=56, height=5, wrap="word")
        boilerplate_text.grid(row=7, column=1, columnspan=2, sticky="ew", pady=4)
        boilerplate_text.insert("1.0", self.state.description_boilerplate)

        ttk.Checkbutton(body, text="Re-encode for more exact cuts", variable=reencode_var).grid(row=8, column=1, sticky="w", pady=(6, 10))

        buttons = ttk.Frame(body)
        buttons.grid(row=9, column=0, columnspan=3, sticky="e")
        ttk.Button(buttons, text="Use Defaults", command=lambda: self._reset_settings_dialog(output_var, portrait_var, thumbnail_background_var, ffmpeg_var, ffprobe_var, reencode_var, boilerplate_text)).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).grid(row=0, column=1, padx=6)
        ttk.Button(buttons, text="Save", command=lambda: self._save_settings_dialog(dialog, output_var, portrait_var, thumbnail_background_var, ffmpeg_var, ffprobe_var, reencode_var, boilerplate_text)).grid(row=0, column=2, padx=(6, 0))

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
            messagebox.showerror("Could not load project", str(exc))
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
            messagebox.showerror("Could not save project", str(exc))
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
            messagebox.showerror("VLC required", "Install python-vlc and VLC media player to use embedded playback.")
            return False
        if self.vlc_player is not None and self.vlc_media_path == self.state.video_path:
            return True
        try:
            self._load_vlc_video()
        except Exception as exc:
            messagebox.showerror("Could not start VLC playback", str(exc))
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
            messagebox.showerror("Invalid end time", "Match end must be after the match start.")
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
            messagebox.showerror("Invalid time", str(exc))
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
                messagebox.showerror("Invalid time", str(exc))
                return
            end = min(max(end, 0.0), max(self.state.duration, 0.0))

        matches = self.state.sorted_matches()
        if index >= len(matches):
            return

        match = matches[index]
        if end is not None and end <= match.start:
            messagebox.showerror("Invalid end time", "Match end must be after the match start.")
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
            messagebox.showerror("FFmpeg required", str(exc))
            return
        if Image is None:
            messagebox.showerror("Pillow required", "Install Pillow with: pip install -r requirements.txt")
            return
        if not self.state.video_path:
            messagebox.showerror("No video", "Open a video before exporting.")
            return
        output_dir = self._resolved_output_dir()

        jobs = self._build_export_jobs(output_dir)
        if not jobs:
            messagebox.showerror("No clips", "No valid match ranges are available to export.")
            return

        self._set_export_state("disabled")
        self.export_thread = threading.Thread(target=self._export_worker, args=(jobs, output_dir), daemon=True)
        self.export_thread.start()
        self.log("Started export of {} clips.".format(len(jobs)))

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
                make_thumbnail(
                    self.state.video_path,
                    job.match,
                    job.start,
                    job.end,
                    job.thumbnail_path,
                    self.state.portrait_dir,
                    self.state.ffmpeg_path,
                    event_name=self.state.event_name,
                    background_path=self.state.thumbnail_background_path,
                    logger=self._thread_log,
                )
                self._write_match_metadata(job)
                self._write_upload_sidecars(job)
            self.log_queue.put(("done", "Export complete."))
        except Exception as exc:
            self.log_queue.put(("error", str(exc)))

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

    def _bind_character_search(self, combo: ttk.Combobox, slot: int) -> None:
        combo.bind("<KeyRelease>", lambda event, selected_slot=slot: self._on_character_keyrelease(event, selected_slot))
        combo.bind("<Return>", lambda event, selected_slot=slot: self._accept_character_match(selected_slot))
        combo.bind("<<ComboboxSelected>>", lambda _event: self.apply_match_details(show_errors=False))

    def _on_character_keyrelease(self, event, slot: int):
        if event.keysym in {"Return", "Escape", "Tab", "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Up", "Down", "Left", "Right"}:
            return None
        combo = self.character1_combo if slot == 1 else self.character2_combo
        matches = self._matching_characters(self._character_var(slot).get(), limit=12)
        combo.configure(values=matches if matches else self.state.characters)
        if self.character_picker_slot == slot:
            self.character_picker_var.set(self._character_var(slot).get())
            self._refresh_character_picker()
        return None

    def _accept_character_match(self, slot: int):
        matches = self._matching_characters(self._character_var(slot).get(), limit=1)
        if matches:
            self._set_character(slot, matches[0])
        return "break"

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
        self.state.portrait_dir = self._portrait_dir_or_default(self.portrait_var.get().strip())
        self.state.thumbnail_background_path = self.thumbnail_background_var.get().strip()
        self.state.ffmpeg_path = self.ffmpeg_var.get().strip()
        self.state.ffprobe_path = self.ffprobe_var.get().strip()
        self.state.reencode = bool(self.reencode_var.get())

    def _state_to_controls(self) -> None:
        self.event_var.set(self.state.event_name)
        self.output_var.set(self.state.output_dir)
        self.portrait_var.set(self._portrait_dir_or_default(self.state.portrait_dir))
        self.thumbnail_background_var.set(self.state.thumbnail_background_path)
        self.ffmpeg_var.set(self.state.ffmpeg_path)
        self.ffprobe_var.set(self.state.ffprobe_path)
        self.reencode_var.set(self.state.reencode)
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

    def _default_portrait_dir(self) -> Path:
        return self._resource_path("portraits")

    def _resource_path(self, name: str) -> Path:
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            return Path(bundle_root) / name
        return Path(__file__).resolve().parents[1] / name

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
        reencode_var: tk.BooleanVar,
        boilerplate_text: tk.Text,
    ) -> None:
        output_var.set("")
        portrait_var.set(str(self._default_portrait_dir()))
        thumbnail_background_var.set("")
        ffmpeg_var.set("")
        ffprobe_var.set("")
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
        reencode_var: tk.BooleanVar,
        boilerplate_text: tk.Text,
    ) -> None:
        self.output_var.set(output_var.get().strip())
        self.portrait_var.set(portrait_var.get().strip() or str(self._default_portrait_dir()))
        self.thumbnail_background_var.set(thumbnail_background_var.get().strip())
        self.ffmpeg_var.set(ffmpeg_var.get().strip())
        self.ffprobe_var.set(ffprobe_var.get().strip())
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
            elif kind == "done":
                self.log(str(message))
                self._set_export_state("normal")
                messagebox.showinfo("Export complete", str(message))
            elif kind == "error":
                self.log("ERROR: {}".format(message))
                self._set_export_state("normal")
                messagebox.showerror("Export failed", str(message))
        self.after(150, self._poll_log_queue)

    def _thread_log(self, message: str) -> None:
        self.log_queue.put(("log", message))

    def log(self, message: str) -> None:
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")

    def destroy(self) -> None:
        self.stop_playback()
        super().destroy()


def main() -> None:
    app = TekkenVodHelperApp()
    app.mainloop()
