import argparse
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tekken_vod_helper import youtube_client


REDIRECT_PATH = "/oauth2callback"


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    server_version = "TekkenVodHelperOAuth/1.0"

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != REDIRECT_PATH:
            self.send_response(204)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        self.server.oauth_result = {
            "path": parsed.path,
            "code": params.get("code", [""])[0],
            "state": params.get("state", [""])[0],
            "error": params.get("error", [""])[0],
        }
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body><h1>Tekken VOD Helper is connected.</h1><p>You can close this tab.</p></body></html>"
        )

    def log_message(self, _format, *_args):
        return


def run_auth(args) -> int:
    credentials = youtube_client.read_stored_credentials(require_refresh_token=False)
    state = secrets.token_urlsafe(24)
    server = HTTPServer(("127.0.0.1", args.port), OAuthCallbackHandler)
    server.oauth_result = {}
    redirect_uri = "http://127.0.0.1:{}{}".format(server.server_port, REDIRECT_PATH)
    url = youtube_client.authorization_url(credentials.client_id, redirect_uri, state, scope=args.scope)

    stop_at = time.monotonic() + args.timeout

    def wait_for_callback():
        while time.monotonic() < stop_at and not server.oauth_result:
            server.timeout = max(0.1, min(1.0, stop_at - time.monotonic()))
            server.handle_request()

    thread = threading.Thread(target=wait_for_callback, daemon=True)
    thread.start()
    print("Opening Google OAuth consent in your browser.")
    print("If it does not open, paste this URL into your browser:")
    print(url)
    webbrowser.open(url)
    thread.join(timeout=args.timeout)
    server.server_close()

    result = server.oauth_result
    if not result:
        print("Timed out waiting for OAuth callback.", file=sys.stderr)
        return 1
    if result.get("path") != REDIRECT_PATH:
        print("Unexpected OAuth callback path.", file=sys.stderr)
        return 1
    if result.get("state") != state:
        print("OAuth state did not match.", file=sys.stderr)
        return 1
    if result.get("error"):
        print("OAuth error: {}".format(result["error"]), file=sys.stderr)
        return 1
    youtube_client.exchange_code_for_refresh_token(result.get("code", ""), redirect_uri, credentials)
    print("Stored YouTube refresh token in Windows Credential Manager.")
    return 0


def run_list(args) -> int:
    videos = youtube_client.list_channel_videos(max_results=args.max_results)
    rows = youtube_client.videos_as_rows(videos)
    if args.json:
        print(youtube_client.json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("No videos returned.")
        return 0
    for row in rows:
        print(
            "{video_id} | {privacy_status}/{upload_status}/{processing_status} | {published_at} | {title}".format(
                **row
            )
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="YouTube authorization and listing helper for Tekken VOD Helper.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    auth_parser = subparsers.add_parser("auth", help="Authorize YouTube access and store refresh token.")
    auth_parser.add_argument("--port", type=int, default=8765)
    auth_parser.add_argument("--timeout", type=int, default=180)
    auth_parser.add_argument("--scope", default=youtube_client.MANAGE_SCOPE)
    auth_parser.set_defaults(func=run_auth)

    list_parser = subparsers.add_parser("list", help="List recent channel uploads with read-only access.")
    list_parser.add_argument("--max-results", type=int, default=25)
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(func=run_list)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
