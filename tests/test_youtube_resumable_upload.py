import json
import os
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tekken_vod_helper import windows_credentials as wc
from tekken_vod_helper import youtube_client as yc

CHUNK = 256 * 1024


class FakeYouTube:
    """Minimal resumable-upload server: stores bytes, answers 308 with Range, 200 when complete."""

    def __init__(self):
        self.received = bytearray()
        self.total = None
        self.metadata = None
        self.fail_next_puts = 0
        self.die_after_chunks = None
        self.chunks = 0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, headers=None, body=b""):
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers["Content-Length"])
                fake.metadata = json.loads(self.rfile.read(length))
                fake.total = int(self.headers["X-Upload-Content-Length"])
                port = self.server.server_address[1]
                self._send(200, {"Location": "http://127.0.0.1:{}/session/1".format(port)})

            def do_PUT(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                crange = self.headers["Content-Range"]
                if crange.startswith("bytes */"):
                    return self._status()
                if fake.fail_next_puts:
                    fake.fail_next_puts -= 1
                    return self._send(503)
                start = int(crange.split(" ")[1].split("-")[0])
                assert start == len(fake.received), (start, len(fake.received))
                fake.received.extend(body)
                fake.chunks += 1
                if fake.die_after_chunks is not None and fake.chunks >= fake.die_after_chunks:
                    fake.die_after_chunks = None
                    raise ConnectionResetError("simulated crash")
                return self._status()

            def _status(self):
                if fake.total is not None and len(fake.received) == fake.total:
                    return self._send(200, {"Content-Type": "application/json"},
                                      json.dumps({"id": "vid123", "snippet": fake.metadata["snippet"]}).encode())
                headers = {"Range": "bytes=0-{}".format(len(fake.received) - 1)} if fake.received else {}
                return self._send(308, headers)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.root = "http://127.0.0.1:{}/upload".format(self.server.server_address[1])


@pytest.fixture
def fake(monkeypatch):
    server = FakeYouTube()
    monkeypatch.setattr(yc, "YOUTUBE_UPLOAD_ROOT", server.root)
    yield server
    server.server.shutdown()


def _video(tmp_path, size):
    path = tmp_path / "vod.mp4"
    path.write_bytes(os.urandom(size))
    return path


def test_uploads_in_chunks_and_returns_video(fake, tmp_path):
    path = _video(tmp_path, CHUNK * 3 + 1000)
    seen = []
    video = yc.upload_video(str(path), "BB11", "desc", chunk_size=CHUNK, token_provider=lambda: "t",
                            progress=lambda s, t: seen.append(s), state_path=str(tmp_path / "state.json"))
    assert video["id"] == "vid123"
    assert bytes(fake.received) == path.read_bytes()
    assert fake.metadata["status"]["privacyStatus"] == "unlisted"
    assert seen[-1] == path.stat().st_size
    assert json.loads((tmp_path / "state.json").read_text())["video_id"] == "vid123"


def test_retries_a_503_and_resumes_from_confirmed_offset(fake, tmp_path):
    path = _video(tmp_path, CHUNK * 2 + 10)
    fake.fail_next_puts = 2
    video = yc.upload_video(str(path), "BB11", chunk_size=CHUNK, token_provider=lambda: "t",
                            _sleep=lambda s: None)
    assert video["id"] == "vid123"
    assert bytes(fake.received) == path.read_bytes()


def test_new_process_resumes_saved_session_after_crash(fake, tmp_path):
    path = _video(tmp_path, CHUNK * 4)
    state = tmp_path / "state.json"
    fake.die_after_chunks = 2
    with pytest.raises(yc.YouTubeApiError):
        yc.upload_video(str(path), "BB11", chunk_size=CHUNK, token_provider=lambda: "t",
                        state_path=str(state), max_retries=0, _sleep=lambda s: None)
    held = len(fake.received)
    assert 0 < held < path.stat().st_size
    video = yc.upload_video(str(path), "BB11", chunk_size=CHUNK, token_provider=lambda: "t",
                            state_path=str(state), _sleep=lambda s: None)
    assert video["id"] == "vid123"
    assert bytes(fake.received) == path.read_bytes()


def test_finished_state_is_not_uploaded_twice(fake, tmp_path):
    path = _video(tmp_path, CHUNK)
    state = tmp_path / "state.json"
    yc.upload_video(str(path), "BB11", chunk_size=CHUNK, token_provider=lambda: "t", state_path=str(state))
    again = yc.upload_video(str(path), "BB11", chunk_size=CHUNK, token_provider=lambda: "t", state_path=str(state))
    assert again == {"id": "vid123", "resumed": "already-complete"}
    assert len(fake.received) == CHUNK


@pytest.mark.skipif(sys.platform.startswith("win"), reason="file store is the non-Windows backend")
def test_credential_file_store_roundtrip_and_permissions(tmp_path, monkeypatch):
    store = tmp_path / "cfg" / "credentials.json"
    monkeypatch.setenv(wc.CREDENTIALS_FILE_ENV, str(store))
    assert wc.read_youtube_refresh_token() == ""
    wc.write_youtube_client_credentials("id-1", "secret-1")
    wc.write_youtube_refresh_token("refresh-1")
    assert (wc.read_youtube_client_id(), wc.read_youtube_client_secret(), wc.read_youtube_refresh_token()) == (
        "id-1", "secret-1", "refresh-1")
    assert stat.S_IMODE(store.stat().st_mode) == 0o600
    wc.delete_youtube_refresh_token()
    assert wc.read_youtube_refresh_token() == ""
    os.chmod(store, 0o644)
    with pytest.raises(wc.CredentialError):
        wc.read_youtube_client_id()
