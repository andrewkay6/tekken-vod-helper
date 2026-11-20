# Ready for the private tailnet route — mobile review PoC

## RESOLVED on 2026-11-09 shortly after midnight — the app is now on its own Serve port

Applied by Qwen with the owner's approval:

```
sudo tailscale serve --bg --https=8445 http://127.0.0.1:8787
```

**Use `https://<review-host>:8445/`** — a different origin, so Agentboard's service worker cannot
intercept it (its scope cannot cross ports, which is the same reason downloads moved to `:8443`). Verified
after the change, in that order:

- `:8445/` → 200, `<title>Clip review · draft prototype`; `/app.js`, `/styles.css`, `/api/state` all 200.
- `:8445/media/file/set01` with `Range: bytes=2000000-2004095` → **HTTP/2 206**, `Accept-Ranges: bytes`,
  `Content-Range: bytes 2000000-2004095/5790040523`, exactly 4096 bytes.
- `:443/` still returns the Agentboard shell (`<title>Agentboard`, 745 B) and `:8443/` still answers the
  downloads server's 404 — both handlers byte-unchanged.

Two notes for you:

1. **`--https=<port>` only works while creating a listener.** `tailscale serve --https=8443
   --set-path=/tvh-review …` failed with `listener already exists for port 8443`; the `--bg --set-path=` form
   you used earlier works only against the default 443, which is exactly how the swallowed route got made. A
   new port is the additive path. `serve get-config` on this build emits the new `services` schema
   (`{"version":"0.0.1"}`), incompatible with the legacy `TCP`/`Web` config in use — do not push it with
   `set-config --all`, which overwrites every handler.
2. **The dead `:443/tvh-review` path is left in place.** It is harmless (the worker answers before the proxy
   is ever consulted) and removing it edits the dashboard's config block. Your call:
   `sudo tailscale serve --https=443 --set-path=/tvh-review off`.

Serve changes are not boot-persistent-by-accident: they live in tailscaled's state and survive a reboot, but
this port now belongs with the other host routes, so it is worth a line in your host notes.

## The diagnosis, kept because it will happen again


Reported by Andy on mobile: tapping the link opens the Agentboard chat instead of the review page. Same root
cause as the APK problem fixed on 2026-11-07, and the words from that session apply verbatim: *"A successful
curl from the devbox doesn't prove what Chrome on your phone receives."* My earlier "reachable over the
tailnet" claim was curl-based and is withdrawn for browsers.

Evidence, read from the live worker just now (`https://<review-host>/sw.js`, 2,101 B):

```js
e.registerRoute(new e.NavigationRoute(e.createHandlerBoundToURL("/index.html"),
  {denylist:[/^\/api(?:\/|$)/, /^\/ws(?:\/|$)/]}))
```

A Workbox `NavigationRoute` bound to the dashboard shell answers **every** navigation on that origin except
`/api` and `/ws`, so `/tvh-review/` never reaches `127.0.0.1:8787` in a browser that has ever opened the
board; `skipWaiting()` + `clientsClaim()` means it claims the tab straight away. This is exactly why `curl`,
Playwright in a fresh profile, and a laptop that never visited the dashboard all appear to work.

The fix is the one already proved for downloads — **a second origin, because a service worker's scope cannot
cross ports**:

```
sudo tailscale serve --https=8443 --set-path=/tvh-review --proxy http://127.0.0.1:8787
```

Verify with a *browser* navigation, not curl: `https://<review-host>:8443/tvh-review/` should show
the title `Clip review · draft prototype`. That path currently answers the downloads server's 404, which is
the clean way to tell "not wired yet" from "swallowed by the worker".

Nothing in the prototype needs to change: the server strips a literal leading `/tvh-review`, every URL the
page emits is relative, and the notes API is same-origin — so one build serves bare, on 443, or on 8443.
Keeping it off the 443 origin is the durable answer; clearing site data only helps until the board is opened
again.

## Everything below predates that finding

**Live now** on loopback only. Nothing else was started, changed or exposed.

| item | value |
|---|---|
| Readiness URL | `http://127.0.0.1:8787/` (verified 200 in 0.8 ms) |
| Intended tailnet path | `https://<review-host>/tvh-review/` → proxy to `127.0.0.1:8787` |
| tmux session | `tvh-mobile-review` (retained; visible to Agentboard) |
| Launch command | `.venv/bin/python -u prototypes/mobile-review/server.py --port 8787` (cwd `/srv/projects/tekken-vod-helper`) |
| Source | `prototypes/mobile-review/` — stdlib server + buildless HTML/CSS/JS. No new dependency. |
| Log | `build/work/bb9/mobile-review-poc/server.log` — prints `serving http://127.0.0.1:8787/ clips=10 duration=6928.4s coverage=75.4% media tokens=31` |

## Proxy notes for the mount

- Strip the prefix at the proxy or leave it: the server strips a leading `/tvh-review` itself, and every URL
  the page emits is **relative**, so both `…/tvh-review/` and bare loopback work unchanged. Verified:
  `/tvh-review` → 200 (page), `/tvh-review/api/state` → 200.
- `PUT` and `POST` are used by the notes API, and `HEAD` + `Range` by video streaming — pass those through
  untouched. Buffering the media response will break seeking; stream it.
- Bind stays `127.0.0.1`. No firewall, Tailscale, service, model or credential setting was changed here, and
  nothing on 443→4040, `/files`→8766 or 8443 was touched.

## Verified / not verified

`prototypes/mobile-review/check_poc.py` → **33 checks, 0 failed**: Range/206/Content-Range/HEAD/416 and a
byte-for-byte comparison against the master, traversal and encoded-path rejection, notes persistence with
malformed and oversized bodies rejected, coverage computed as an interval union and tested on an overlapping
fixture, plus a post-run hash check that the recovery project and draft manifest are untouched.

**Not verified by that script: playback, touch and layout — your browser pass owns those.** Headless browser
testing is authorised for this prototype, and you already have the isolated Playwright/Chromium at
`build/work/bb9/mobile-review-qa`, so nothing was installed here and nothing should be installed twice.

### Please include these five, they were just changed blind

A static re-read after the browser authorisation turned up three defects that only a real engine would show.
All three are fixed and `check_poc.py` still passes 33/33, but none has been *seen* working:

1. **Zoom inverted** — the strip's inner width was `(window / recording)`, so `+` *shrank* the timeline
   instead of magnifying it. Now `recording / window`, with the scroller centred on the playhead or the
   fitted clip. Check `+`, `−`, `Fit clip`, `Fit all` and horizontal panning.
2. **Marking mode also selected** — a tap on a set strip while `Mark missing from here` was active both began
   a drag and loaded that clip. Taps are now inert in marking mode.
3. **A save could echo over a live edit** — the response replaced local note state, so text typed while the
   request was in flight could flash back to the older value. The response now only supplies `revision`.
4. Seek in both modes, since a 5.8 GB stream-copy clip exercises the range path properly; confirm the readout
   reads *est. source* for the clip and plain source time in master mode.
5. A short tap in marking mode should open the sheet that says to drag, not silently create a 0-second mark.

Full list with context: `prototypes/mobile-review/README.md` → *Superseded since that pass*.

## The player now has its own transport (selector changes)

`#player` no longer carries `controls`; the bar is built in-page. New ids: `#stage`, `#tap-layer`,
`#tap-back`, `#tap-fwd`, `#big-play`, `#chrome`, `#scrub`, `#fill`, `#buffered`, `#ctime`, `#c-play`,
`#c-cut`, `#c-full`. **Removed: `#c-prev`, `#c-next`** — prev/next live on the card, not duplicated in the
transport. Double-tap on either video half skips 5 s, so a test that taps the video twice now seeks, and a
single tap only reveals the chrome (which then fades after 2.8 s while playing).

`transport_qa.cjs` covers this and can be run against your own base URL; the invocation with the two required
environment variables is in `README.md`.

## Heads-up: the UI changed again while you were reading

`Smooth / Full 1440p` are no longer on the player card, so any selector or assertion pinned to
`#quality-smooth` / `#quality-full` / `#preview-make` will not find them. What is there now:

- **Watch this cut** — plays the *master* clamped to the boundaries in force. Nudging `+5s` on the end then
  extends what you can watch, which is the point of a boundary review; a dashed line could not show that.
- **Scrub the whole recording** — master, unclamped.
- **Why is it buffering?** — reports stalls, seconds starved, buffer-ahead in seconds, dropped vs decoded
  frames, and a 48 MB uncached throughput test on the same connection.

If you re-run the interaction pass, please add: the cut window stops at the proposed end; a nudge while
watching replays the changed edge; the scrub toggle returns to the draft; and the diagnostic sheet shows real
numbers rather than zeros after playback.

## One standing risk to decide alongside the route

The owner's preference is that services he leans on run under boot-enabled systemd. This PoC is in a tmux
session because the task specified that, so it will not survive a reboot. If the route becomes something Andy
uses from his phone routinely, it should get a unit; until then a lost preview just needs the command above
re-run.

## Bandwidth, if it stalls

The drafts are 1440p stream copies at ~11 MB/s. Range requests mean the browser only fetches what it plays,
but the rate is the rate, and a phone off Wi-Fi may stall. A low-bandwidth preview for a selected window is
the cheap next step; nothing was transcoded, and doing so silently for 54 GB was explicitly avoided.

Everything on screen remains **provisional**: draft clips, machine-proposed boundaries with zero operator
decisions and no independent validation, and all reviewer edits here are mock notes in
`build/work/bb9/mobile-review-poc/notes.json` that cannot reach a recovery file.
