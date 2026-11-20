"""One-time YouTube sign-in for a headless box (the devbox).

Stores the OAuth client and refresh token through windows_credentials, which off Windows means
~/.config/tvh/credentials.json (mode 600). Nothing secret is printed.

    .venv/bin/python scripts/youtube_auth.py --client-json ~/Downloads/client_secret_XXXX.json
    .venv/bin/python scripts/youtube_auth.py            # client already stored; just (re)consent

Use a "Desktop app" OAuth client. After you approve, the browser lands on http://127.0.0.1:8765/...
and shows an error page. That's expected. Copy the whole address from the address bar and paste it here.
"""
import argparse
import getpass
import json
import secrets
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tekken_vod_helper import windows_credentials as wc  # noqa: E402
from tekken_vod_helper import youtube_client as yc  # noqa: E402

REDIRECT = "http://127.0.0.1:8765/"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-json", type=Path, help="client_secret_*.json downloaded from Google Cloud")
    ap.add_argument("--ask-client", action="store_true", help="type the client id and secret instead")
    args = ap.parse_args()

    if args.client_json:
        data = json.loads(args.client_json.expanduser().read_text())
        block = data.get("installed") or data.get("web") or {}
        wc.write_youtube_client_credentials(block["client_id"], block["client_secret"])
        print("stored OAuth client in", wc.credentials_file())
    elif args.ask_client:
        wc.write_youtube_client_credentials(input("client id: ").strip(), getpass.getpass("client secret: ").strip())
        print("stored OAuth client in", wc.credentials_file())

    creds = yc.read_stored_credentials(require_refresh_token=False)
    if not creds.client_id:
        print("no OAuth client stored yet; pass --client-json or --ask-client", file=sys.stderr)
        return 2
    state = secrets.token_urlsafe(16)
    print("\nOpen this link, sign in to the KW Tekken channel account, and approve:\n")
    print(yc.authorization_url(creds.client_id, REDIRECT, state))
    pasted = input("\nPaste the full 127.0.0.1 address you were sent to: ").strip()
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(pasted).query)
    if query.get("state", [""])[0] != state:
        print("state mismatch: that address is not from this sign-in", file=sys.stderr)
        return 1
    if "code" not in query:
        print("no code in that address: {}".format(query.get("error", ["?"])[0]), file=sys.stderr)
        return 1
    yc.exchange_code_for_refresh_token(query["code"][0], REDIRECT, creds)
    yc.refresh_access_token()  # proves the stored token works
    print("signed in; refresh token stored in", wc.credentials_file())
    return 0


if __name__ == "__main__":
    sys.exit(main())
