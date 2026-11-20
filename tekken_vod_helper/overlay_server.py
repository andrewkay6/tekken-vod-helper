import json
import shutil
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from .models import OverlayState


class OverlayServer:
    #: Fields whose changes are worth a history line: what viewers saw about the set. Appearance-only edits
    #: (font, colour, opacity) are not.
    HISTORY_FIELDS = ("description", "subtitle", "p1name", "p1team", "p1score", "p2name", "p2team", "p2score")

    def __init__(self, static_dir: Path, port: int = 1337, history_dir: Optional[Path] = None) -> None:
        self.static_dir = static_dir
        self.port = port
        self.runtime_dir = Path(tempfile.gettempdir()) / "tekken_vod_helper_overlay"
        # Kept next to the OBS recordings so the post-event archive picks it up. Each line is a wall-clock time
        # plus what the scoreboard showed. With the recording's start time (OBS names files by it) that becomes
        # the position in the VOD where each set's names, round and score went up.
        self.history_dir = history_dir if history_dir is not None else Path.home() / "Videos" / "tvh-overlay-history"
        self._last_history: Optional[dict] = None
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        return "http://127.0.0.1:{}".format(self.port)

    def start(self) -> None:
        if self.httpd is not None:
            return
        self._prepare_runtime_dir()
        runtime_dir = self.runtime_dir

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=str(runtime_dir), **kwargs)

            def end_headers(self):
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                super().end_headers()

            def log_message(self, _format, *_args):
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.httpd is None:
            return
        self.httpd.shutdown()
        self.httpd.server_close()
        self.httpd = None
        self.thread = None

    def write_state(self, state: OverlayState) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        path = self.runtime_dir / "state.json"
        data = state.to_dict()
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self._append_history(data)

    def _append_history(self, data: dict) -> None:
        """Append one line per visible scoreboard change. A history failure never breaks the live overlay."""
        shown = {key: data.get(key) for key in self.HISTORY_FIELDS}
        if shown == self._last_history:
            return
        try:
            self.history_dir.mkdir(parents=True, exist_ok=True)
            now = time.time()
            line = {"at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
                          + ".{:03d}".format(int(now * 1000) % 1000) + time.strftime("%z", time.localtime(now)),
                    "epoch": round(now, 3)}
            line.update(shown)
            target = self.history_dir / "overlay-{}.jsonl".format(time.strftime("%Y-%m-%d", time.localtime(now)))
            with target.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")
            self._last_history = shown
        except OSError:
            pass

    def _prepare_runtime_dir(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        for path in self.static_dir.iterdir():
            target = self.runtime_dir / path.name
            if path.is_dir():
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(path, target)
            else:
                shutil.copy2(path, target)
        if not (self.runtime_dir / "state.json").exists():
            self.write_state(OverlayState())
