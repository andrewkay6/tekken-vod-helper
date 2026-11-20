# Mobile review prototype — draft clip selector and coverage timeline

A deliberately small, buildless web page for judging one thing on a phone: does a clip-per-set reviewer with a
whole-recording coverage timeline feel usable enough to justify building properly? It streams the **ten real
draft MP4s** already rendered under `build/work/bb9/draft-split-2026-11-08/`, from the files, without copying
or re-encoding them.

This is a prototype, not the production app and not the `plan/full-stack-single-app` work. It contains no
recovery algorithm, no boundary logic and no claim that BB9's boundaries are accepted.

## Run it

```
.venv/bin/python -u prototypes/mobile-review/server.py --port 8787      # http://127.0.0.1:8787/
```

Nothing to install: standard library only, no framework, no build step. To keep it alive across a terminal
closing, as the devbox instance runs it now:

```
tmux new-session -d -s tvh-mobile-review "cd /srv/projects/tekken-vod-helper && \
  .venv/bin/python -u prototypes/mobile-review/server.py --port 8787 2>&1 | \
  tee build/work/bb9/mobile-review-poc/server.log"
```

It binds **127.0.0.1 only**, so nothing is reachable except through the owner's Tailscale Serve. The host
firewall is untouched and no Serve setting was changed here. Every URL the page uses is relative and the server
strips a leading `/tvh-review`, so the same process works bare or behind a proxy mount without a rebuild.

### Why the phone opens the dashboard instead of this page

Mounted at `https://<review-host>/tvh-review/` (443) it looked fine to `curl` and to headless
Chromium, and still showed Andy the Agentboard chat. That is not a proxy bug: Agentboard registers a
service worker on that origin whose final route is

```js
e.registerRoute(new e.NavigationRoute(e.createHandlerBoundToURL("/index.html"),
  {denylist:[/^\/api(?:\/|$)/, /^\/ws(?:\/|$)/]}))
```

— every navigation on the origin, except `/api` and `/ws`, is answered from the cached dashboard shell and
never reaches this server. `skipWaiting()`/`clientsClaim()` means the tab is claimed as soon as the board has
been opened once. **A service worker's scope cannot cross ports**, which is why the download endpoint lives on
`:8443`. The app therefore got its own port (owner-approved, 2026-11-09):

```
sudo tailscale serve --bg --https=8445 http://127.0.0.1:8787
```

**Open `https://<review-host>:8445/` on the phone** (Tailscale connected). `:8443` was unavailable
for a second path: `tailscale serve --https=8443 --set-path=…` refuses with `listener already exists for port
8443`, and that flag form only works while *creating* a listener.

The lesson, recorded because it was learned for the APK on 2026-11-07 and then re-made here by me: **a
successful `curl` from the devbox proves nothing about what a phone browser receives.** Verify route changes by
browser navigation and check the page `<title>` for `Clip review · draft prototype`.


## Checks

```
.venv/bin/python prototypes/mobile-review/check_poc.py --base http://127.0.0.1:8787
```

33 checks: page and assets, the element ids the script looks up against the ids the page contains, the state
contract, interval-union coverage against a fixture that can actually fail, Range/HEAD/416/suffix behaviour
including a byte-for-byte comparison against the master on disk, rejection of traversal and encoded paths,
notes persistence with malformed and oversized bodies, and finally that the machine project and draft manifest
are untouched afterwards.

**Covered by the checks above:** protocol, data contract, whitelist behaviour and note persistence.
**Covered by a browser, not by this script:** playback, touch and layout. Headless Chromium — a shared
Playwright install at `build/work/bb9/mobile-review-qa`, nothing was installed here — loaded the page at a
390 px viewport, showed `Set 01`, `75%` and ten clips, and **played and seeked the real 5.8 GB clip**
(`played: true`, `seeked: true`, `duration 524.639019`, `error: null`) with 12 partial (206) media replies, ten
posters and zero console errors (`browser-smoke.json`). A second pass exercised verdict, a start-only nudge
that preserved the end, note persistence across reload, undo, missing-gap flag, gap source preview, JSON
download, reset, next clip and widths 360/390/430 (`browser-interactions.json`, `passed: true`). Because it ran
in a fresh browser profile, **neither pass could have caught the service-worker problem above** — that needs a
device that has opened the dashboard.

That pass also caught a real defect the static review missed: `.sheet-backdrop` and `.badge-row` declare
`display: flex`, which **overrode the HTML `hidden` attribute**, so the modal backdrop was on screen swallowing
taps from load. The `[hidden] { display: none !important }` rule at the end of `styles.css` is the browser
pass's fix, not mine.

### Superseded since that pass

The `Smooth / Full 1440p` buttons and their preview-status text are **gone from the player card**: nothing is
transcoded unless a measurement says it is needed, and then only the one set. `setQuality`/`makePreview` remain,
reached from the buffering answer instead of the main screen. A ready preview exists for `set01` only
(200 MB, 1280x720 High@L3.2, **3.04 Mbit/s against the draft's 88.3 Mbit/s** — 29x less bandwidth, 3% of the
bytes, same 524.6 s so the timeline mapping is unchanged). The VAAPI encoder on this box's Vega GPU is
unusable to an unprivileged process (`/dev/dri/renderD128` is `root:render`; `andy` is not in `render`), so any
preview is CPU work at roughly 4x realtime, which is exactly why it is opt-in per clip and not a batch.

Behaviours still never seen in a browser: the same three as before (zoom magnifies, marking mode does not
select, typing survives a save) **plus** Watch-this-cut clamping, the scrub toggle, and both diagnostic paths.



## What is on screen

### Transport

The stock `controls` attribute is off, because it renders as a system-specific bar that cannot carry a review
affordance. What replaces it follows the conventions people already know from YouTube: a centre play button
that fades once playing, a thin red scrub rail that grows on hover or drag, **double-tap the left or right half
to skip 5 s** with a ripple, and a chrome row that fades after 2.8 s while playing and never hides while paused.
Every icon is drawn SVG — media-control *codepoints* came out as tofu boxes on the first real render, and an
`aria-label` does not fix an invisible button.

The scrub bar deliberately works in the loaded media's own clock (clip-local, cut window, or source, depending
on mode) because that is the only timeline the element can seek on. The source position stays in the readout
below the player, labelled estimate or source as appropriate.

### Browser pass

`transport_qa.cjs` drives the transport in the shared Chromium at `build/work/bb9/mobile-review-qa` (nothing is
installed by this prototype):

```
QA=$PWD/build/work/bb9/mobile-review-qa
LD_LIBRARY_PATH="$QA/runtime/usr/lib/x86_64-linux-gnu" PLAYWRIGHT_BROWSERS_PATH="$QA/browsers" \
  node prototypes/mobile-review/transport_qa.cjs http://127.0.0.1:8787/
```

17 checks: play, chrome fade, both double-tap directions by measured `currentTime` deltas rather than by
"no error thrown", scrub-to-position within 15% of where it was dragged, pause, the cut switch proven by
`duration > 6000` on the master, jump-to-start/end landing 520 s apart, 360 px with no horizontal overflow, and
every transport button actually painting and fitting the viewport. Screenshots land in `$QA/transport/`.

That last check exists because the first pass was **16/16 green while the player was visibly broken**: the
buttons were boxes and `CUT` was clipped off-screen. Asserting behaviour without looking is how that happened.

- **Player** — three sources for one element:
  - **draft clip** (default) — the file that was actually cut;
  - **Watch this cut** — the *master*, clamped to the boundaries in force, so nudging `+5s` on the end
    reveals the five seconds that the proposal now includes instead of only redrawing a marker. Playback
    stops at the proposed end and the readout counts down what is left in the cut. This is how a boundary
    question gets answered without re-cutting anything;
  - **Scrub the whole recording** — the master, unclamped, for looking either side of a boundary.
  Only the master gives a truthful source position, so it is the only mode where the playhead is a source
  time; clip mode is labelled an estimate.
- **Why is it buffering?** — counts stalls, seconds starved, seconds of buffer ahead and dropped-versus-decoded
  frames, then offers a 48 MB uncached fetch over the same connection. Starvation, decoder and pipe are
  different faults with different fixes, and the first version of this page asserted one of them without
  measuring.
- **Boundary readout** — the planned interval, its duration, and separately what was delivered: container
  length, cut mode, and whether the source range was verified. For these drafts it was not: they are stream
  copies, so a clip can begin before the planned start and clip-local time is an estimate, never a precise
  source position. The UI says so instead of inventing a number.
- **Mock verdicts** — Good / Needs adjustment / Too inaccurate / Unsure, a note field, and ±1s / ±5s nudges
  plus `Set at playhead` for either edge. Proposed edges appear as a dashed **ghost** on the timeline and are
  labelled as proposals: playback stays the existing clip, nothing is re-cut.
- **Coverage timeline** — the whole 115 m 28 s recording with each set strip at source time, unclaimed regions
  hatched (unclaimed is not the same as missed gameplay), ticks, playhead, note markers and a zoom
  (`+ / − / Fit clip / Fit all`). Tap a strip to play that set; tap a gap to preview the source there or mark
  it as a missing clip. `Mark missing from here` enters a drag mode so a range can be claimed with a thumb.
- **Clip list** — numbered cards with the decoded first-frame still, planned interval, duration, cautious
  identity (these drafts have **no confirmed players**) and flags for approximate starts and flagged fallbacks.

## Data contract

`GET /api/state` — everything the page draws, with no host paths:

```json
{ "kind": "tvh-mobile-review-poc", "schema_version": 1,
  "provenance": { "draft_manifest": "manifest.json", "project": "bb9-machine-bounds-linked.tvh.json",
                   "provisional": true, "operator_accepted": false,
                   "independently_validated": false, "notes_are_mock_edits": true },
  "source": { "duration_seconds": 6928.421, "media": { "master": "media/file/master" },
               "limitations": [ "…" ] },
  "coverage": { "claimed_seconds": 5226.201, "unclaimed_seconds": 1702.22, "fraction": 0.7543,
                 "method": "union of requested intervals, not a sum of clip lengths" },
  "gaps": [ { "start": 0.0, "end": 51.051, "seconds": 51.051 } ],
  "clips": [ { "id": "set01", "number": 1,
                "requested": { "start": 51.051, "end": 574.572, "duration": 523.521 },
                "delivered": { "container_seconds": 524.639, "length_delta_seconds": 1.118,
                                "mode": "stream-copy", "start_is_exact": false,
                                "source_range_verified": false, "note": "…" },
                "media": { "clip": "media/file/set01", "poster": "media/file/set01_first",
                            "lastStill": "media/file/set01_last" },
                "identity": { "player1": "", "player2": "", "round": "", "confirmed": false, "withheld": "…" },
                "evidence": { "startBasis": "…", "onsetUncertaintySeconds": 5.005, "endBasis": "…",
                               "missing": [ "…" ], "winner": null },
                "status": "rendered_and_decode_checked" } ] }
```

`GET | PUT | POST /api/notes` — mock review notes. `PUT` stores the whole document and bumps `revision`;
`POST` clears it. Shape:

```json
{ "kind": "tvh-mobile-review-notes-mock", "schema_version": 1, "revision": 3,
  "provenance": { "mock_edits_only": true, "note": "…" },
  "items": { "set01": { "status": "adjust", "note": "starts in the intro",
                         "proposed": { "start": 50.5, "end": 575.0 } } },
  "missing_clips": [ { "id": "m1", "start": 700.0, "end": 760.0, "note": "unclaimed gap" } ] }
```

The client also mirrors notes to `localStorage` so a reload is never empty if the API is unreachable, and
`Export notes` downloads the same document as JSON. `proposed` is an *intention*, not a rendered cut: nothing
consumes it yet, which is the point of a prototype. `status` is one of `good | adjust | inaccurate | unsure`
or empty; unknown values are dropped rather than stored.

## What it will not do

- It writes one file: `build/work/bb9/mobile-review-poc/notes.json`, in its own scratch directory, atomically,
  capped at 256 KB, and refusing a body that is not an object with an `items` object.
- It never opens a recovery project, a decision journal, an export folder or an upload path for writing, and
  no route exists that could reach them.
- It makes no model calls and cannot: there is no endpoint URL anywhere in this directory.
- Media requests resolve through a whitelist built from the draft manifest, so there is no arbitrary file
  read, no directory listing, and no absolute path in any URL the browser sees.

## Bandwidth

The drafts run at 80-88 Mbit/s (~11 MB/s) because they are stream copies: the cut policy preserved the master's
own rate rather than choosing one, which is right for an archive and absurd for a review UI on a handset. Range
requests stop the browser fetching what it will not play, but they cannot lower the bitrate.

**Measured on the owner's phone, 2026-11-08, using the page's own diagnostics** (so this is the handset's
number, not mine):

```
observed: 32 s since playback began
stalls: 3, 22.5 s waiting (71.1% of the time)
buffer ahead of playhead: 0.4 s      the download is losing to playback
frames: 93 decoded, 0 dropped        the decoder is keeping up
what is playing now needs ~11.0 MB/s (88 Mbit/s)
```

Decoder is clean, so this is delivery. The sharper number is the frame count: 93 frames decoded is ~1.55 s of
59.94 fps media in a 32 s window, i.e. playback advanced at roughly **5% of real time**, which puts the phone's
sustained throughput nearer **0.5 MB/s than 11**. That is far below what direct-LAN Wi-Fi should do, so the
cause is still open — cellular via the tailnet, a weak AP, or a throttled link — and it matters, because a
720p preview at 0.38 MB/s is only comfortable if the real rate is a few MB/s, not if it is 0.5. Hence the
48 MB link test inside the diagnostic: **size the preview from the measurement, do not assume 3 Mbit/s is
safe.** An earlier version of this panel printed 1.2 MB/s for an 11.04 MB/s file because it divided the wrong
two numbers; the rates now come from each file's own byte count.
