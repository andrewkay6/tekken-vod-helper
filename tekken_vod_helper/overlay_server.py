import json
import shutil
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from .models import OverlayState


class OverlayServer:
    def __init__(self, static_dir: Path, port: int = 1337) -> None:
        self.static_dir = static_dir
        self.port = port
        self.runtime_dir = Path(tempfile.gettempdir()) / "tekken_vod_helper_overlay"
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
        path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")

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
