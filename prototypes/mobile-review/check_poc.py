#!/usr/bin/env python3
"""Self-checks for the mobile review PoC. Read-only by default; notes writes need --allow-notes-mutation.

Why an id cross-check belongs here: the UI is buildless JavaScript, so a single mistyped element name is a
runtime exception that no HTTP status would ever show. Comparing the ids the script looks up against the ids
the page actually contains is the cheapest way to know the page can run at all. It does not prove playback,
touch or layout: those belong to the browser pass listed in README.md, run against the shared Playwright
install rather than anything set up here.

    .venv/bin/python prototypes/mobile-review/check_poc.py --base http://127.0.0.1:8787
"""
import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import server                                        # noqa: E402  (the code under test, imported for its
                                                     #             interval arithmetic)
results = []


def record(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print("%-4s %-46s %s" % ("PASS" if ok else "FAIL", name, detail))


def fetch(url, method="GET", body=None, headers=None, timeout=20):
    request = urllib.request.Request(url, method=method, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8787")
    parser.add_argument("--manifest", default=str(ROOT / "build/work/bb9/draft-split-2026-11-08"
                                                  "/manifest.json"),
                        help="the manifest the server under test was started with; its project is the "
                             "source of truth the API is cross-checked against")
    parser.add_argument("--allow-notes-mutation", action="store_true",
                        help="write to /api/notes during the run. Only ever correct for an instance started "
                             "with its own --notes file: the checks PUT a probe and then POST a reset, and a "
                             "reset destroys a reviewer's work rather than restoring it")
    args = parser.parse_args()
    base = args.base.rstrip("/") + "/"
    manifest_path = args.manifest

    # -- page and assets
    status, headers, body = fetch(base)
    record("GET / serves the page", status == 200 and b"<video" in body, "%d, %d bytes" % (status, len(body)))
    for name in ("app.js", "styles.css"):
        status, headers, _ = fetch(base + name)
        record("GET %s" % name, status == 200, str(status))

    html = (HERE / "index.html").read_text(encoding="utf-8")
    js = (HERE / "app.js").read_text(encoding="utf-8")
    # The UI caches elements from one literal list; every name in it must exist in the page. The capture stops
    # at the closing bracket so the separators between names can never be read as names themselves.
    table = re.search(r"\[((?:\s*\"[^\"]+\",?)+)\]\.forEach\(\(id\) =>", js)
    record("the element cache list was found in app.js", bool(table), "")
    wanted = re.findall(r'"([^"]+)"', table.group(1)) if table else []
    present = set(re.findall(r'id="([^"]+)"', html))
    missing = [name for name in wanted if name not in present]
    record("every element id the UI looks up exists in the page",
           len(wanted) >= 25 and not missing, "%d ids, missing: %s" % (len(wanted), missing[:6]))

    # The check above only proves the registered names exist. It passed while the page was completely dead,
    # because a lookup the cache never registered is invisible to it -- so compare every access site against
    # the registry instead, including the dotted form that is never a valid dashed id.
    registered = set(wanted)
    used = set(re.findall(r'els\["([^"]+)"\]', js)) | set(re.findall(r"els\['([^']+)'\]", js))
    dotted = {m for m in re.findall(r'els\.([A-Za-z][A-Za-z0-9]*)', js) if m not in registered}
    unregistered = sorted(used - registered)
    record("every els[...] access is a registered id", not unregistered,
           "unregistered: %s" % unregistered[:5])
    record("no camelCase els.foo lookup of a dashed id", not dotted, "found: %s" % sorted(dotted)[:5])

    # A media seek must never be ordered by an event it might not catch: `loadedmetadata` fires once per load,
    # and the loader already owns that hook. A jump button whose seek is gated on it fails silently, and reads
    # to the user as "end goes to the start" rather than as a missing callback.
    seeks = re.findall(r'addEventListener\("loaded[a-z]*",\s*\(\) =>', js)
    record("no media seek is gated on a one-shot metadata listener", not seeks,
           "%d such listener(s) found" % len(seeks))

    # -- state contract
    state = json.loads(fetch(base + "api/state")[2])
    clips = state["clips"]
    duration = state["source"]["duration_seconds"]
    record("state: ten draft clips", len(clips) == 10, str(len(clips)))
    record("state: duration is the recording's", 6900 < duration < 6960, "%.3f s" % duration)
    record("state: provisional and unaccepted are stated",
           state["provenance"]["provisional"] and not state["provenance"]["operator_accepted"]
           and not state["provenance"]["independently_validated"], json.dumps(state["provenance"])[-90:])
    record("state: no absolute host path in any URL",
           not re.search(r'"(/srv/|/home/)[^"]*"', json.dumps(state)), "scanned whole payload")

    claimed = sum(end - start for start, end in
                  [(c["requested"]["start"], c["requested"]["end"]) for c in clips])
    gaps = sum(row["seconds"] for row in state["gaps"])
    record("coverage: claimed + unclaimed fills the recording",
           abs(claimed + gaps - duration) < 1.5, "claimed %.1f + gaps %.1f vs %.1f" % (claimed, gaps, duration))
    record("coverage: fraction is claimed over duration",
           abs(state["coverage"]["fraction"] - claimed / duration) < 0.001,
           str(state["coverage"]["fraction"]))
    # The BB9 drafts do not overlap, so a union could only be proven on a fixture. This one can fail: if the
    # code summed intervals instead of merging them, (0,100) twice would claim 200 s of a 100 s recording.
    merged = server.union_gaps([(10, 20), (15, 30), (100, 120)], 200)
    doubled = server.union_gaps([(0, 100), (0, 100)], 100)
    record("coverage: overlapping intervals merge, they do not add",
           merged["intervals"] == [[10.0, 30.0], [100.0, 120.0]] and len(merged["gaps"]) == 3
           and sum(end - start for start, end in doubled["intervals"]) == 100.0,
           json.dumps(merged["intervals"]) + " / doubled " + json.dumps(doubled["intervals"]))

    # -- attribution confidence: the API repeats the backend, it does not re-derive it
    # The report is worth surfacing only if the number on the card is the number the recovery layer wrote,
    # and only if a project that predates the report says "not available" instead of scoring every set zero.
    served_manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    project_document = json.loads(Path(served_manifest["project"]).read_text(encoding="utf-8"))
    recoveries = [(float(match.get("start") or 0.0), match.get("recovery") or {})
                  for match in project_document.get("matches") or []]

    def recovery_for(clip):
        near = sorted(recoveries, key=lambda row: abs(row[0] - clip["requested"]["start"]))[:1]
        return near[0][1] if near and abs(near[0][0] - clip["requested"]["start"]) <= 1.5 else {}

    mismatched, unreported, absent_labeled = [], [], []
    for clip in clips:
        recovery = recovery_for(clip)
        report = recovery.get("attribution_confidence") or {}
        conf = clip["identity"].get("confidence") or {}
        if not report:
            absent_labeled.append((clip["id"], conf.get("available")))
            continue
        want = {"score": report.get("score"), "tier": report.get("tier"),
                "needs_review": bool(report.get("needs_review")),
                "estimate": (report.get("winner_estimate") or {}).get("name"),
                # The arithmetic word is the one a card is most tempted to drop, because the name beside it
                # already reads like an answer.
                "estimate_status": (report.get("winner_estimate") or {}).get("status"),
                "estimate_decisive": bool((report.get("winner_estimate") or {}).get("decisive")),
                "authoritative_winner": recovery.get("set_winner")}
        bad = {key: (conf.get(key), value) for key, value in want.items() if conf.get(key) != value}
        if bad:
            mismatched.append((clip["id"], bad))
        if conf.get("available") is not True:
            unreported.append(clip["id"])
        # A named estimate must never look like a confirmed identity: the estimate lives in its own field.
        if conf.get("estimate") and conf["estimate"] != conf.get("authoritative_winner"):
            if clip["identity"]["confirmed"]:
                mismatched.append((clip["id"], "an estimate filled the confirmed player fields"))
    record("identity: /api/state repeats the backend's confidence report", not mismatched,
           str(mismatched[:2]) if mismatched else "%d clip(s) checked against %s"
           % (len(clips), Path(served_manifest["project"]).name))
    # An estimate that reads as a result is the failure this whole layer is built against, so the pair is
    # checked on the wire too: a name without its status word must not reach a card at all.
    unlabelled = [clip["id"] for clip in clips
                  if (clip["identity"].get("confidence") or {}).get("estimate")
                  and not clip["identity"]["confidence"].get("estimate_status")]
    record("identity: a named estimate always carries its arithmetic status", not unlabelled,
           str(unlabelled[:4]))
    decisive_claimed = [clip["id"] for clip in clips
                        if (clip["identity"].get("confidence") or {}).get("estimate_decisive")
                        and clip["identity"]["confidence"].get("estimate_status") != "decisive"]
    record("identity: only a decisive status may be marked decisive", not decisive_claimed,
           str(decisive_claimed[:4]))
    record("identity: a report present in the project is available on every clip",
           not unreported, str(unreported[:4]))
    record("identity: a project without a report says so instead of scoring zero",
           all(value is False for _, value in absent_labeled) if absent_labeled else True,
           "%d clip(s) with no backend report" % len(absent_labeled))
    reasons = [row for clip in clips for row in (clip["identity"].get("confidence") or {}).get("reasons", [])]
    record("identity: reasons travel with the score, not only the number",
           all(isinstance(row, str) and len(row) > 12 for row in reasons), "%d reason line(s)" % len(reasons))
    # The defect the browser pass caught: a set scoring below the review threshold with nothing beside the
    # number to act on. A score that cannot say why is a bare assertion wearing a decimal point.
    silent = [clip["id"] for clip in clips
              if (clip["identity"].get("confidence") or {}).get("available")
              and (clip["identity"]["confidence"].get("score") or 0) < 70
              and not clip["identity"]["confidence"].get("reasons")]
    record("identity: no low score reaches a reviewer without a reason", not silent, str(silent[:4]))
    unavailable = {(clip["id"], row) for clip in clips
                   for row in (clip["identity"].get("confidence") or {}).get("unavailable", [])}
    record("identity: unavailable evidence is published per set",
           all(len(row[1]) > 3 for row in unavailable), "%d item(s)" % len(unavailable))
    # The tap on the confidence line has to work whether or not there is a report: guarding the handler on
    # `available` made the control a dead button on every card of a pre-report project, which is the one case
    # where a reviewer most needs to be told the file has no tiers.
    handler = re.search(r'els\["confidence-note"\]\.addEventListener\("click",\s*\(\) => \{(.*?)\}\);',
                        js, re.S)
    body = re.sub(r"//[^\n]*", "", handler.group(1)) if handler else ""
    record("the confidence tap is not gated on a report being present",
           bool(handler) and "available" not in body,
           (body.strip().replace("\n", " ")[:90] if handler else "handler not found"))

    # -- media: whitelist, range behaviour, master offered for source mode only
    tokens = [row["id"] for row in clips] + [row["media"].get("poster", "").split("/")[-1] for row in clips]
    tokens = [token for token in tokens if token]
    broken = []
    for token in tokens:
        status, _, _ = fetch(base + "media/file/" + token, method="HEAD")
        if status != 200:
            broken.append((token, status))
    record("every clip and poster token serves", not broken, str(broken[:4]))
    _status, full_headers, _ = fetch(base + "media/file/" + clips[0]["id"], method="HEAD")
    whole = int(full_headers.get("Content-Length", "0"))
    status, headers, payload = fetch(base + "media/file/" + clips[0]["id"],
                                     headers={"Range": "bytes=0-2047"})
    span = headers.get("Content-Range", "")
    record("clip range request is 206 with a matching slice",
           status == 206 and len(payload) == 2048 and headers.get("Content-Length") == "2048"
           and span == "bytes 0-2047/%d" % whole,
           "%s %s (file %d)" % (status, span, whole))
    status, headers, payload = fetch(base + "media/file/" + clips[0]["id"],
                                     headers={"Range": "bytes=-4096"})
    tail = headers.get("Content-Range", "")
    record("suffix range returns the tail of the file", status == 206 and len(payload) == 4096
           and tail.startswith("bytes %d-" % (whole - 4096)) and tail.endswith("/%d" % whole), tail)
    status, headers, payload = fetch(base + "media/file/master", headers={"Range": "bytes=100000000-100004095"})
    expected = ROOT / "build/work/bb9/oct22-1440p.mp4"
    if status == 206 and len(payload) == 4096:
        with expected.open("rb") as handle:
            handle.seek(100000000)
            record("master range equals the same bytes on disk", payload == handle.read(4096),
                   "4 KiB compared at 100 MB")
    else:
        record("master range equals the same bytes on disk", False, "status %s len %d" % (status, len(payload)))
    status, headers, _ = fetch(base + "media/file/master", headers={"Range": "bytes=999999999999999-"})
    record("unsatisfiable range is 416 with Content-Range",
           status == 416 and headers.get("Content-Range", "").startswith("bytes */"),
           "%s %s" % (status, headers.get("Content-Range")))
    for bad in ("../server.py", "..%2f..%2fetc%2fpasswd", "set01%00"):
        status, _, _ = fetch(base + "media/file/" + bad)
        record("rejects %s" % bad[:22], status in (400, 404), str(status))
    status, _, _ = fetch(base + "api/nope")
    record("unknown api is 404", status == 404, str(status))
    status, _, _ = fetch(base.rstrip("/") + "/tvh-review/api/state")
    record("proxy prefix /tvh-review is accepted", status == 200, str(status))
    status, _, _ = fetch(base.rstrip("/") + "/tvh-review")
    record("bare /tvh-review serves the page", status == 200, str(status))

    # -- notes: read-only unless this run was told the store is disposable
    # A harness that PUTs a probe and then "resets" does not clean up after itself -- it empties whatever it
    # found, and an empty response is what a reviewer's deleted afternoon looks like. Restoring afterwards is
    # not safe either: a reviewer can edit between the read and the restore, and their work is gone with no
    # failing check to say so. So writes here need an explicit flag, and the flag means "this notes file is
    # scratch", which is how every run in this increment was actually done.
    status, _, body = fetch(base + "api/notes")
    payload = json.loads(body) if status == 200 else {}
    record("notes GET answers with the documented shape",
           status == 200 and "items" in payload and "revision" in payload
           and payload.get("provenance", {}).get("mock_edits_only") is True,
           "revision %s, %d item(s) left untouched" % (payload.get("revision"),
                                                       len(payload.get("items") or {})))
    if not args.allow_notes_mutation:
        record("notes writes skipped: no --allow-notes-mutation", True,
               "read-only pass; run against an isolated --notes file to exercise the writes")
    else:
        probe = {"items": {"set03": {"status": "adjust", "note": "check probe",
                                     "proposed": {"start": 1576.0, "end": 2138.0}}},
                 "missing_clips": [{"id": "check", "start": 90.0, "end": 120.0, "note": "probe"}]}
        status, _, saved = fetch(base + "api/notes", method="PUT",
                                 body=json.dumps(probe).encode(),
                                 headers={"Content-Type": "application/json"})
        payload = json.loads(saved)
        record("notes PUT persists and bumps revision",
               status == 200 and payload["items"]["set03"]["note"] == "check probe"
               and payload["revision"] >= 1, "revision %s" % payload.get("revision"))
        record("notes carry mock provenance", payload.get("provenance", {}).get("mock_edits_only") is True,
               "")
        bad_cases = [("not json", b"nope"), ("no items", b'{"x":1}'), ("array body", b"[1]")]
        for label, bad in bad_cases:
            status, _, _ = fetch(base + "api/notes", method="PUT", body=bad,
                                 headers={"Content-Type": "application/json"})
            record("rejects %s" % label, status == 400, str(status))
        status, _, _ = fetch(base + "api/notes", method="PUT",
                             body=json.dumps({"items": {"a": {"note": "y" * 300000}}}).encode(),
                             headers={"Content-Type": "application/json"})
        record("rejects an oversized note", status == 413, str(status))
        status, _, _ = fetch(base + "api/notes", method="POST")
        record("reset returns an empty note set", status == 200 and
               json.loads(fetch(base + "api/notes")[2])["items"] == {}, str(status))

    # -- originals are not written by any of the above
    project = ROOT / "build/work/bb9/codex-evaluator-rebuild-2026-11-08/bb9-machine-bounds-linked.tvh.json"
    report = json.loads((project.parent / "rebuild-report.json").read_text())
    record("delivered machine project hash still matches its rebuild report",
           hashlib.sha256(project.read_bytes()).hexdigest() == report["sha256"]["rebuilt"], "")
    revision = json.loads(Path(manifest_path).resolve().read_text(encoding="utf-8"))["project"]
    revision_report = Path(revision).parent / "rebuild-report.json"
    expected = None
    if revision_report.is_file():
        # Different revisions recorded the digest under different keys; the check is only worth making when
        # the report actually claims one, and it must say so instead of passing quietly.
        recorded = json.loads(revision_report.read_text(encoding="utf-8")).get("sha256") or {}
        expected = recorded.get("linked_project") or recorded.get("rebuilt")
    # Named so an unreported digest cannot be mistaken for a verified one: this check passes in two different
    # states and only one of them means anything.
    record("project digest verified, or unreported by that revision (see detail)",
           expected is None or hashlib.sha256(Path(revision).read_bytes()).hexdigest() == expected,
           "no digest reported by %s" % revision_report.name if expected is None
           else Path(revision).name)
    manifest = ROOT / "build/work/bb9/draft-split-2026-11-08/manifest.json"
    record("draft manifest unchanged in size", manifest.stat().st_size == 14763,
           str(manifest.stat().st_size))
    notes_file = ROOT / "build/work/bb9/mobile-review-poc/notes.json"
    record("notes live only in the PoC scratch directory", notes_file.parent.is_dir(),
           str(notes_file.parent.relative_to(ROOT)))

    failures = [row for row in results if not row[1]]
    print("\n%d checks, %d failed" % (len(results), len(failures)))
    print("Playback, touch and layout belong to the browser pass (see the QA list in README.md); these "
          "checks cover the protocol and the data, and neither claims the other.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
