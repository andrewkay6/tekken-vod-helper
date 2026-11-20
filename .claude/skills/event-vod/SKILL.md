---
name: event-vod
description: Run the post-event VOD pipeline for a KW Tekken event recording - archive the original from the streaming PC, upscale to 1440p, copy to shared storage, upload the full VOD, run TVH analysis, serve the review page for Andy's ambiguous-set decisions, then cut clips with thumbnails. Use when Andy says the streaming PC is plugged in, asks to process/archive/upscale/upload last night's VOD, or invokes /event-vod.
---

# Event VOD pipeline

Andy triggers this with a short prompt after an event. Do as much as possible without asking. Stop only at
the checkpoints marked **ASK**. Always write times to Andy in 12-hour AM/PM.

Machine facts are in memory `streaming-pc-vod-pipeline`. Check them before starting, because the PC's IP is
assigned by DHCP and can change.

## 0. Find the recording

```bash
ssh kwtekken 'powershell -NoProfile -Command "Get-ChildItem C:\Users\KWTekken\Videos -File -Filter *.mkv | Sort-Object LastWriteTime -Descending | Select -First 5 Name,Length,LastWriteTime"'
```

- The recording is the newest OBS `YYYY-MM-DD HH-MM-SS.mkv`. If one event has several MKVs (OBS restarts), they
  are numbered parts (`[raw 01]`, `[raw 02]`); see `PLAN` in `scripts/move_vods_to_storage.py`.
- Event name: `YYYY-MM-DD Basement Brawl N <subtitle>`. The number is one more than the newest folder in
  `/var/lib/ai-sandbox-media/` (read-only view of storage). Take the subtitle from the start.gg tournament or
  YouTube playlist name. **ASK** only if you can't determine it.
- `ffprobe` the file and report duration, resolution and size.

## 1. Archive the original (always first)

Goal: `VODs/<event>/recordings/<stamp> [original].mkv` on mac-studio
(`/Volumes/SharedStorage/Videos/Tekken/`), SHA-256 verified against the source both before and after the copy.
Never modify or delete anything on the PC.

- Precedent: `scripts/move_vods_to_storage.py --vods --originals`. It streams via `ssh kwtekken type ...`, hashes with
  `certutil`, and writes `metadata/manifest.jsonl`, `vod-move-log.jsonl` and `move-status.txt`.
- On the devbox, use `scripts/archive_event_vod.py --source "<name>" --dest "VODs/<event>/recordings/<stamp> [original].mkv"`.
  Run it in tmux. It stages through `build/work/archive-staging/` (watch free disk), checks four hashes (source before,
  streamed, landed via the ro mount, source after), uploads a receipt to `metadata/receipts/` and never overwrites. The
  log is `build/work/archive-staging/archive-log.txt`. First used for BB11 on 2026-11-20.
- Storage is reached over SFTP to mac-studio with the `tvh-storage` SSH alias
  (using the locally configured SFTP-only account). That account was verified 2026-11-20. It is SFTP-only (no
  shell, so you can't hash on the Mac; verify by hashing what you read back). It starts in `Videos/Tekken`, and can write,
  rename and delete only under `VODs/` and `metadata/`. Use `sftp -b` batches, and wrap them in `timeout`. Don't try other
  Mac accounts.

## 2. Upscale to 1440p on the streaming PC

The settings are those of `scripts/upscale-to-1440p.ps1`: NVENC p5, cq 18, 35M/60M, lanczos 2560x1440, audio copied.
An ssh session kills its child processes on disconnect, so run the job as a scheduled task:

1. Write a .ps1 locally (template: the one used for 2026-11-19 is in `C:\Users\KWTekken\tvh-jobs\`). It runs ffmpeg
   with `-nostdin -n -progress <job>.progress`, sends stderr to `<job>.log`, then writes `EXIT $LASTEXITCODE` to `<job>.done`.
2. `scp` it to `kwtekken:/C:/Users/KWTekken/tvh-jobs/`. (Piping multi-line PowerShell into `-Command -` fails
   silently.)
3. `schtasks /Create /F /TN TVH-Upscale-<date> /SC ONCE /ST 23:59 /IT /TR "powershell -NoProfile -ExecutionPolicy Bypass -File <ps1>"`
   then `schtasks /Run /TN ...`.
4. Output goes to `C:\Users\KWTekken\Videos\<stamp> 1440p.mp4`. Expect about 2.1-2.4x realtime, so a 2 h 10 min
   recording takes about an hour. Tell Andy the ETA. Use the Monitor tool on the `.done` file instead of polling by hand.

Windows Update can force a restart that kills the encode. On 2026-11-20 TrustedInstaller rebooted the PC at 9:52 AM,
the encode died at 79%, and it restarted from zero (a `+faststart` partial can't be salvaged). Right after the PC is
plugged in, check `HKLM:\...\WindowsUpdate\Auto Update\RebootRequired` and `Component Based Servicing\RebootPending`.
If one is set, tell Andy before starting a long encode. If the encode dies, check the System log for event 1074.

Steps 1 and 2 may overlap because the encode only reads the source, but don't skip step 1.

## 3. Copy the 1440p file to shared storage

Destination: `VODs/<event>/<event> [1440p].mp4`. Use `scripts/archive_event_vod.py` the same way as step 1. Staging needs about 55 GB free on /.
Afterwards the devbox reads it from `/var/lib/ai-sandbox-media/<event>/`.

## 4. Upload the full VOD to YouTube

- **ASK** before the first upload of each event. Confirm privacy (default `unlisted`) and the playlist
  (`suggest_playlist` in `tekken_vod_helper/youtube_client.py`).
- Thumbnail: build one with `tekken_vod_helper/thumbnails.py` (event-title variant) and set it with
  `set_video_thumbnail`.
- Upload with `scripts/upload_full_vod.py --file "<1440p in /var/lib/ai-sandbox-media>" --title ... --event ... --privacy unlisted`
  in tmux. It's resumable: rerunning the same command continues from `build/work/uploads/<stem>.state.json`.
  Credentials come from `~/.config/tvh/credentials.json` (mode 600, same target names as Windows Credential
  Manager). The one-time sign-in is `scripts/youtube_auth.py --client-json <client_secret.json>` (paste-back flow).
  Never print or commit secrets.
- **GAP (2026-11-20):** the OAuth client is stored in `~/.config/tvh/credentials.json`. The
  refresh token still needs Andy's one-time consent via `scripts/youtube_auth.py`. The old token in
  `~/.config/tekken-vod-helper/private/` was revoked, probably by the 7-day expiry while the app is in Testing. If
  uploads start failing with `invalid_grant` about a week after signing in, the app is still in Testing: Andy needs to
  publish it. There's also no full-VOD thumbnail design yet (`thumbnails.make_thumbnail` is per match). Ask Andy
  what the full-stream thumbnail should look like.

## 5. TVH analysis (AI/OCR first)

Run this on the **1080p original** as soon as step 1 is verified. Don't wait for the upscale. The analysis only produces
timestamps, and the upscale keeps timing identical (Oct 1 1440p vs MKV differed by 0.03 s over 2 h). Every pre-2026-11-20
run used 1440p input, so on the first 1080p event compare its boundaries against a BB9 1080p-vs-1440p check before
trusting them blindly.

```bash
.venv/bin/python -m tekken_vod_helper.boundary_cli detect "/var/lib/ai-sandbox-media/<event>/recordings/<stamp> [original].mkv" \
  --output-dir build/work/<short-event>/detect-<date> --cache-fine --recognition-crops --temporal \
  --max-frames 4000 --max-requests 700
```

BB11 (2 h 11 min) lessons: the default `--max-frames 2000` counts the ~1,566 coarse frames, which left the boundary
zoom only ~430 frames. That run came out "degraded" and missed 2 sets, so use 4000. It took 344 calls and about 2 h of Mac GPU
time. Reuse an existing frame cache with `--samples-manifest <dir>/frames/sample-manifest.json`. Review cards are
built from `project.tvh.json` plus the `conflicting_candidates` items, whose obs_N index into the time-sorted
`timeline-refined.json` (see `build/work/bb11/web-review-20261120-r3/`). See `docs/boundary-workflow.md` for budgets (BB9 used 263 vision calls). It uses the shared Qwen slot, so check that no
other Qwen job is running first (`~/AGENTS.md`). The tool should decide everything it can. Only genuinely ambiguous sets go
to Andy.

Build the cards with one command (needs the bracket first: fetch it with `startgg_client.fetch_bracket_sets` and TVH's own
`STARTGG_TOKEN` in `.env`, saved to `build/work/<ev>/startgg-sets.json`):
`.venv/bin/python scripts/build_review_cards.py --detect <detect dir> --master "<1440p>" --event "Basement Brawl #N"
--bracket build/work/<ev>/startgg-sets.json --out build/work/<ev>/web-review-<date>`. It takes names and rounds from start.gg,
characters from `tekken_vod_helper/character_runs.py` (per set: confident / "A / B" switch in play order / unsure), and adds
**Check here** jump spots for anything doubtful. It also includes the conflict sets. Serve `<out>/cards.manifest.json`.
When Andy answers a round, apply his answers, let TVH settle what he flagged, and serve round 2 with his confirmed cards kept.

## 6. Andy's review: served web page

Before starting any review session, look for `build/work/**/*.RETENTION.md` flags and follow them. (None open since 2026-11-20.)

Andy liked the served review page last time: the AI asks him only about ambiguous sets and boundaries. It lives at
`prototypes/mobile-review/` (stdlib server, binds 127.0.0.1:8787). It is reachable at
`https://<review-host>:8445/` through the existing Tailscale Serve on :8445. Don't change Serve config; that's
an owner action. Run it in tmux (`tvh-mobile-review`, see its README) pointed at this event's draft manifest, then send
Andy the link plus how many questions are waiting.

## 7. Cut clips and thumbnails

Cut the clips from the **1440p upscale**, not the original. The upscale is there so YouTube uses its better encoder on
every clip. Use the reviewed timestamps from step 5/6 unchanged. Before cutting, check that the 1440p duration matches the
original within about 0.1 s. After the review, export one folder per match (`.mp4`, `thumbnail.jpg`, `title.txt`, `description.txt`,
`match.json`) using the app's export path. Choosing the output dir is Andy's call. Default it next to the event in
storage once storage is writable, and confirm with him the first time.

### Frame-exact clip starts (required after the cut)

TVH's exporter stream-copies, so every clip starts on the keyframe at or before the requested start (up to about
4.2 s early in these masters). After `run_export.py cut`, always run
`.venv/bin/python scripts/recut_smartcut.py <export dir> "<1440p master>" 1 2 ... N`. It re-encodes only the head up to the
first keyframe, checks the durations, audio and a clean decode, and replaces each clip in place (BB10 2026-11-13,
BB11 2026-11-20). It skips clips that are already uploaded, so do it **before** uploading the clips.

### Thumbnail background (one per event)

Andy's rule: Tekken stage art, decent resolution, **no text**, never used before. Run
`scripts/find_thumbnail_backgrounds.py --qwen`. It searches Wallhaven (Google Images refuses scripts) and skips
anything perceptually close to `docs/thumbnail-backgrounds-used.json`. Qwen drops images with text, logos or a
character focus. A character slot can hold two characters as "A / B" (a mid-set switch); the thumbnail
then overlaps both portraits. The result is a numbered contact sheet in `build/work/thumbnail-candidates/<date>/`. Look at the
sheet yourself as well (Qwen sometimes passes fight scenes; leave those out). Then **give Andy the chooser page**:
stop the review server and run `scripts/thumbnail_chooser.py --candidates <dir> --project <owner-reviewed .tvh.json>
--event "Basement Brawl #N" --dest <owner-reviewed dir>/bbN-thumbnail-background.jpg --sample-match <a dual-character or
long-name match> --exclude <rejects>`. It serves on 127.0.0.1:8787, so Andy uses the same link,
https://<review-host>:8445/. Each candidate is shown as a finished match thumbnail; his Submit saves the full-res
file and records the stage as used. (`--pick N` on the finder still works from the CLI.) Then rerun the export's
`metadata` pass to regenerate thumbnails.

### Upload the clips and announce

After the recut: `.venv/bin/python scripts/upload_clips.py --export <export dir> --playlist <event playlist id> --privacy public`
(public is Andy's standing choice, 2026-11-20). It uploads in reverse match order, so the playlist reads full VOD, Grand
Final, ..., Winners Round 1, as BB10 does. It sets each thumbnail and is resumable per clip. @everyone is used ONLY when the start.gg bracket goes live; VOD posts never mention everyone, and no "matches are up"
post is needed. As soon as the full VOD is public and
first in the playlist, post this heads-up **without asking** (standing approval, 2026-11-20), with no @everyone, via
`DiscordClient.post(CHANNEL_VODS, text, mention_everyone=False)`, and skip it if the link is already in the channel:
"The Basement Brawl #N playlist is live with the full VOD. Individual match videos are coming shortly.\n<playlist url>"

## 8. Delete the upscale (only after clips are cut)

Policy (Andy, 2026-11-20): the 1080p MKV is the original and the only thing archived for good. The 1440p upscale
exists only to get YouTube's higher-quality encoder. Once the full VOD is uploaded and processed and the clips are cut
and exported, delete **every** 1440p copy: on the PC (`C:\Users\KWTekken\Videos\<stamp> 1440p.mp4`), in storage
(`VODs/<event>/<event> [1440p].mp4`, via `tvh-storage` sftp `rm`) and any devbox copy under `build/work/`. First confirm
that the original's receipt or `metadata/manifest.jsonl` row says `verified`. Tell Andy exactly which files you're
deleting. Never delete an original MKV.

## Report back

Give a short table of steps (done / running + ETA / blocked + why), with file paths and sizes. Update memory
`streaming-pc-vod-pipeline` if a machine fact changed (IP, accounts, paths). When a **GAP** is closed, edit this skill.
