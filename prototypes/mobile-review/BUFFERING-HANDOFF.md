# Mobile PoC buffering — handoff at context limit (2026-11-08 around 10:20 PM)

**Symptom, still unfixed:** playback of the 1440p60 draft clips buffers on Andy's Galaxy S21 over local
Wi-Fi. Owner's standing objection is correct: a LAN should carry 88 Mbit/s easily.

## Claims already WITHDRAWN — do not repeat them

1. **"The phone can't sustain 1440p / link is ~0.4 MB/s."** Wrong. That figure was a *per-connection share*
   across ~10 concurrent Chrome range reads, not a link capacity.
2. **"It's the bitrate, transcode to 576p."** Not established. Sizing was done off the bogus 0.4 MB/s.
3. **"MTU/tunnel setting."** Ruled out: `tailscale ping` direct via <phone-address> in 14 ms, no DERP.
   `tailscale0` MTU 1280 is Tailscale's normal default and is not the fault.
4. **My diagnostic printed 1.2 MB/s for an 11.04 MB/s file** — divided the wrong two numbers. Fixed; rates now
   come from each file's own byte count.

## Fixes already shipped (verified, but symptom persists)

- `request_queue_size` 5 → **128** (`ss` confirms backlog 128). Python's default made requests past the 5th
  wait behind long-lived streams.
- Open-ended `Range: bytes=N-` clamped to an **8 MB window** (legal 206; `Content-Range: bytes 0-8388607/…`).
  Previously each such request tried to stream the whole remaining 5.5 GB.
- Live progress logging (`MEDIA n of N MB … MB/s`, plus `ABORTED`) — earlier logging only fired on completion,
  so it recorded nothing about a stalled stream, and `tee` overwrites meant my restarts destroyed the evidence.
- Auto-report `POST /api/clientstat` from the browser every 30 s of playback (stalls, starved seconds,
  buffer-ahead, `navigator.connection.downlink`, viewport) — so the phone's own numbers reach the log without
  asking the owner to read a screen.
- `33/33` checks pass in `prototypes/mobile-review/check_poc.py`. App live on `:8445` (own origin, because
  Agentboard's service worker swallows every navigation on `:443`).

## NEXT STEP — read this first, it is the whole point

Reproduce once on the phone, then **read the log; do not theorise**:

    grep -E "CLIENT|MEDIA|ABORTED" build/work/bb9/mobile-review-poc/server.log | tail -40

Decide from the numbers:
- `CLIENT.downlink_mbps` / link test MB/s low → genuinely the radio/AP path.
- `MEDIA … MB/s` high while the phone still starves → the browser isn't receiving what I send: look at
  **Tailscale Serve's HTTP/2 flow-control window**, the one segment I have never isolated (my 491 MB/s test was
  the devbox fetching its own tailnet URL, which does not prove the last hop).
- Many concurrent `opening` lines again → still contention; consider serving a **bounded GOP-aligned segment**
  rather than the whole file, or pointing `<video>` at a pre-cut byte range.
- **Not yet tested: the existing 720p preview (`set01`, 0.38 MB/s) on the phone.** If *that* plays smoothly the
  problem is throughput; if it also stalls, throughput is ruled out and this is the request/serving pattern.
  Do that test before any further encoding.

Also unexamined: whether Serve's proxy buffers a 5.5 GB range response, and Chrome's read pattern on a file
with `moov` at byte 36 (faststart confirmed on both files).

## Other state to preserve

- **Nothing recovery-related changed.** No owner decisions, no boundary edits, no commits. Draft clips,
  manifest, and `bb9-machine-bounds-linked.tvh.json` are byte-identical (hash-checked by `check_poc.py`).
- **BB10 phase 1 is prepared and unspent**: 168 frames, manifest digest `f01651d04f914468`, ceiling 22 attempts
  incl. retries, 0 temporal / 0 investigate. `run_bb10_phase1.py --check` prints it; `--authorize
  BB10-PHASE1-22-ATTEMPTS` is the only way to run it. No vision call has been sent.
- **Codex edits these same files live** (`app.js`, `styles.css`, server QA dir). Re-read before every edit —
  I twice hit "modified since read" and once created a duplicate-`const` JS syntax error.
- Stale memory `reference/phone-cannot-play-1440p-direct.md` in the **user** store still carries the withdrawn
  claim; deleting it was blocked by auto-mode and must not be worked around. Corrected copy is in project memory.
- My repeated tool-name slip: the edit tool is **`edit`**, never `edit_file`.
