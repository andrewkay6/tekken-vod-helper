#!/usr/bin/env python3
"""Ask the running PoC server to build a smooth preview for every draft clip, one at a time.

Sequential on purpose. Nine concurrent 1440p decodes on twelve cores would starve whatever else is on the box
(a browser QA pass, an export), and the server would happily start them if asked. Each preview is small: about
3 Mbit/s against the drafts' 80-88 Mbit/s, which is the entire point.

    .venv/bin/python prototypes/mobile-review/make_previews.py
"""
import json
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8787"
POLL_SECONDS = 10
MAX_WAIT_SECONDS = 3600


def call(path, method="GET"):
    request = urllib.request.Request(BASE + path, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def main():
    clips = call("/api/state")["clips"]
    print("queued previews for %d clips, one at a time" % len(clips), flush=True)
    for row in clips:
        cid = row["id"]
        state = call("/api/preview/" + cid).get("state")
        if state == "ready":
            print("set %s: already there" % cid, flush=True)
            continue
        started = time.monotonic()
        call("/api/preview/" + cid, "POST")
        while time.monotonic() - started < MAX_WAIT_SECONDS:
            time.sleep(POLL_SECONDS)
            info = call("/api/preview/" + cid)
            if info.get("state") == "ready":
                print("set %s: ready in %.0fs (%.1f MB)"
                      % (cid, time.monotonic() - started, int(info.get("bytes", 0)) / 1e6), flush=True)
                break
            if info.get("state") == "failed":
                print("set %s: FAILED -- %s" % (cid, str(info.get("detail"))[:180]), flush=True)
                break
        else:
            print("set %s: timed out waiting" % cid, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
