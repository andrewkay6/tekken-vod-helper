/* Mobile review prototype: draft clip selection and coverage notes.
 *
 * Nothing here decides a boundary. Proposed edges are mock edits held in a scratch notes file so the
 * interaction can be judged on a phone; the real recovery project, its decisions and its exports are never
 * touched, and no request in this file can reach them.
 */
(() => {
  "use strict";

  // Relative URLs, resolved against the page path so the same build works bare on loopback and under a
  // future /tvh-review proxy mount.
  const BASE = location.pathname.endsWith("/")
    ? location.pathname
    : location.pathname.replace(/[^/]*$/, "");
  const url = (path) => BASE + path;

  const els = {};
  const q = (id) => document.getElementById(id);
  ["coverage", "coverage-pct", "clip-title", "clip-sub", "clip-status", "player", "mode-badge", "map-badge",
   "time-readout", "boundary-readout", "prev-clip", "next-clip", "toggle-mode", "proposed-readout",
   "note-input", "save-state", "undo", "reset", "export", "ticks", "lane", "ruler", "timeline-inner",
   "timeline-scroll", "zoom-label", "zoom-in", "zoom-out", "zoom-fit-clip", "zoom-all", "mark-mode",
   "mark-help", "clip-list", "clips-count", "notes-list", "about-body", "sheet-backdrop", "sheet-title",
   "sheet-body", "sheet-actions", "live", "timeline-hint", "quality-note", "mode-scrub", "diagnose",
   "preview-toggle", "edge-start", "edge-end", "mode-hint", "stage", "tap-layer", "tap-back", "tap-fwd",
   "big-play", "chrome", "scrub", "fill", "buffered", "ctime", "c-play",
   "c-cut", "c-full", "confidence-note", "tabs", "tab-questions", "tab-editor", "tab-count",
   "question-view", "editor-view", "q-progress", "q-card", "q-eyebrow", "q-title", "q-text", "q-player",
   "q-callout", "q-now", "q-rewind", "q-flag", "q-set", "q-jumps", "q-spots", "dl-players", "dl-characters", "dl-rounds", "q-choices", "q-mark", "q-mark-prompt", "q-mark-value", "q-mark-btn", "q-note",
   "q-note-label", "q-prev", "q-save", "q-skip", "q-save-state", "q-observations", "q-done", "q-done-help",
   "q-summary", "q-export", "q-footnote"].forEach((id) => { els[id] = q(id); });

  const state = {
    data: null,
    notes: { items: {}, missing_clips: [], answers: {} },
    tab: "editor",
    q: { index: 0, drafts: {}, loadedSrc: null },
    revision: 0,
    selected: null,
    mode: "clip",              // "clip" plays the rendered draft, "source" plays the master at source time,
                               // "window" plays the master clamped to the proposed boundaries
    window: null,              // [start, end] in source seconds while mode === "window"
    quality: "full",           // "full" is the draft as cut; "preview" is only used if one was made
    previewTimer: 0,
    diagnosed: false,
    view: null,                // [start, end] of the visible timeline window
    markMode: false,
    drag: null,
    undoStack: [],
    saveTimer: 0,
  };

  const STATUS_LABEL = { good: "Good", adjust: "Needs adjustment", inaccurate: "Too inaccurate",
                         unsure: "Unsure" };
  const STATUS_CLASS = { good: "pill-good", adjust: "pill-adjust", inaccurate: "pill-inaccurate",
                         unsure: "pill-unsure" };

  // ---------------------------------------------------------------- helpers
  const clamp = (value, low, high) => Math.min(Math.max(value, low), high);
  const round3 = (value) => Math.round(value * 1000) / 1000;

  function clock(seconds, withTenths) {
    if (!Number.isFinite(seconds)) return "--:--";
    const total = Math.max(0, seconds);
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = Math.floor(total % 60);
    const t = withTenths ? "." + Math.floor((total % 1) * 10) : "";
    const mm = String(m).padStart(2, "0");
    const ss = String(s).padStart(2, "0");
    return (h ? h + ":" : "") + mm + ":" + ss + t;
  }

  function seconds(mins, secs) { return mins * 60 + secs; }

  function say(message) { els.live.textContent = message; }

  function clip() {
    return state.data.clips.find((row) => row.id === state.selected) || null;
  }

  function duration() { return state.data.source.duration_seconds; }

  // Where the playhead sits in source time. In clip mode this is an estimate: the draft is a stream copy
  // that can begin before the planned start, and its container clock is rebased, so clip-local time is not
  // source time. Say so rather than showing a precision the file does not have.
  function sourcePlayhead() {
    const media = els.player;
    const local = Number.isFinite(media.currentTime) ? media.currentTime : 0;
    const row = clip();
    if (!row) return { seconds: 0, exact: false, available: false };
    // Only the master gives a truthful source position. A clip's own clock is clip-local, and a stream copy
    // can begin before the planned start, so adding its time to a plan is an estimate and is labelled one.
    if (state.mode === "source" || state.mode === "window") return { seconds: local, exact: true,
                                                                      available: true };
    return { seconds: row.requested.start + local, exact: false, available: true };
  }

  function windowBounds(row) {
    const note = state.notes.items[row.id] || {};
    const bounds = note.proposed || { start: row.requested.start, end: row.requested.end };
    return [bounds.start, bounds.end];
  }

  // ---------------------------------------------------------------- notes
  function noteFor(id) {
    if (!state.notes.items[id]) state.notes.items[id] = { status: "", note: "", proposed: null };
    return state.notes.items[id];
  }

  function snapshot() {
    state.undoStack.push(JSON.stringify({ items: state.notes.items, missing: state.notes.missing_clips }));
    if (state.undoStack.length > 40) state.undoStack.shift();
  }

  function scheduleSave() {
    els["save-state"].textContent = "saving…";
    clearTimeout(state.saveTimer);
    state.saveTimer = setTimeout(saveNow, 400);
  }

  function saveNow() {
    const body = Object.assign({}, state.notes, { kind: "tvh-mobile-review-notes-mock", schema_version: 1 });
    try { localStorage.setItem("tvh-mobile-review-notes", JSON.stringify(body)); } catch (err) { /* quota */ }
    return fetch(url("api/notes"), { method: "PUT", headers: { "Content-Type": "application/json" },
                                     body: JSON.stringify(body) })
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("HTTP " + response.status)))
      .then((saved) => {
        // Take the server's revision, never its copy of the notes: an edit made while this request was in
        // flight is newer than the document being echoed back, and replacing local state with it would put
        // a character the reviewer already typed back on screen as the older text.
        state.revision = saved.revision || 0;
        els["save-state"].textContent = "saved · revision " + state.revision + " · mock notes only";
      })
      .catch((error) => { els["save-state"].textContent = "kept on this device only (" + error.message + ")"; });
  }

  function normalise(payload) {
    const notes = (payload && typeof payload === "object") ? payload : {};
    const items = (notes.items && typeof notes.items === "object") ? notes.items : {};
    Object.keys(items).forEach((key) => {
      const row = items[key] || {};
      const proposed = (row.proposed && Number.isFinite(row.proposed.start) && Number.isFinite(row.proposed.end))
        ? { start: round3(row.proposed.start), end: round3(row.proposed.end) } : null;
      items[key] = { status: STATUS_LABEL[row.status] ? row.status : "", note: String(row.note || "").slice(0, 600),
                     proposed: proposed };
    });
    const missing = Array.isArray(notes.missing_clips)
      ? notes.missing_clips
          .filter((row) => row && Number.isFinite(row.start) && Number.isFinite(row.end) && row.end > row.start)
          .map((row) => ({ id: String(row.id || ("m" + Math.round(row.start * 1000))),
                           start: round3(row.start), end: round3(row.end),
                           note: String(row.note || "").slice(0, 300) }))
      : [];
    const answers = {};
    if (notes.answers && typeof notes.answers === "object") {
      Object.keys(notes.answers).forEach((key) => {
        const row = notes.answers[key];
        if (!row || typeof row !== "object" || !row.choice) return;
        const fields = (row.fields && typeof row.fields === "object") ? setFields(row.fields) : undefined;
        answers[key] = { clip_id: String(row.clip_id || key), choice: String(row.choice), fields,
                         kind: String(row.kind || "question"), choice_label: String(row.choice_label || ""),
                         at: Number.isFinite(row.at) ? round3(row.at) : null,
                         note: String(row.note || "").slice(0, 600), question: String(row.question || ""),
                         saved_at: String(row.saved_at || "") };
      });
    }
    return { items, missing_clips: missing, answers };
  }

  // ---------------------------------------------------------------- player
  // Which file to pull. A draft is a stream copy at the master's own ~88 Mbit/s — a fine archive and an
  // unreasonable thing to ask a phone to sustain — so the 720p preview wins whenever one exists.
  function playableFor(row) {
    if (state.mode === "source") return { src: state.data.source.media.master, at: null };
    if (state.mode === "window") {
      // Watching the cut means watching the master between the boundaries in force, so moving an edge reveals
      // the footage on the other side of it instead of only moving a line on a diagram.
      const bounds = state.window || [row.requested.start, row.requested.end];
      return { src: state.data.source.media.master, at: bounds[0] };
    }
    if (state.quality === "preview" && preview.state === "ready") return { src: preview.media, at: 0 };
    // Asking for a preview that this page load does not know about yet must be loud. Falling back to the
    // 11 MB/s draft while still labelling itself "preview" is exactly what made the last buffering report
    // unreadable: the number was true and the label was not.
    return { src: row.media.clip, at: 0, fellBack: state.quality === "preview" };
  }

  function loadMedia(sourceAt = null, keepAt = null) {
    const row = clip();
    if (!row) return;
    const chosen = playableFor(row);
    const src = chosen.src;
    const start = state.mode === "source"
      ? (Number.isFinite(sourceAt) ? sourceAt : row.requested.start)
      : (Number.isFinite(keepAt) ? keepAt : chosen.at);
    if (!src) { say("no source stream offered"); return; }
    const resume = () => {
      try { els.player.currentTime = start; } catch (err) { /* metadata not ready */ }
      renderMeta();
    };
    els.player.onloadedmetadata = resume;
    els.player.muted = false;                // sound on; nothing plays until the owner taps
    els.player.src = url(src);
    els.player.load();
    renderMeta();
  }

  function setQuality(quality) {
    const row = clip();
    if (!row) return;
    if (quality === "preview" && (row.preview || {}).state !== "ready") {
      // The page's copy of the preview state goes stale the moment a preview is made after it loaded, and
      // quietly playing the draft instead is worse than one extra request.
      els["quality-note"].textContent = "checking for a preview…";
      fetch(url("api/preview/" + row.id)).then((response) => response.json()).then((info) => {
        row.preview = info;
        if (info.state === "ready") {
          state.quality = "preview";
          loadMedia();
          renderPlayer();
          say("smooth preview");
        } else {
          els["quality-note"].textContent = "no preview for this set yet";
          renderPlayer();
        }
      }).catch(() => { els["quality-note"].textContent = "could not reach the preview check"; });
      return;
    }
    const at = Number.isFinite(els.player.currentTime) ? els.player.currentTime : 0;
    const wasPlaying = !els.player.paused;
    state.quality = quality;
    loadMedia(null, at);
    if (wasPlaying) els.player.play().catch(() => { /* they can tap play */ });
    renderPlayer();
    say(quality === "preview" ? "smooth preview" : "full 1440p draft");
  }

  function pollPreview() {
    const row = clip();
    if (!row || (row.preview || {}).state !== "generating") return;
    clearTimeout(state.previewTimer);
    state.previewTimer = setTimeout(() => {
      fetch(url("api/preview/" + row.id)).then((response) => response.json())
        .then((info) => {
          row.preview = info;
          renderPlayer();
          if (info.state === "ready") { say("preview ready"); setQuality("preview"); }
          else pollPreview();
        })
        .catch(() => pollPreview());
    }, 4000);
  }

  function makePreview() {
    const row = clip();
    if (!row) return;
    fetch(url("api/preview/" + row.id), { method: "POST" }).then((response) => response.json())
      .then((info) => { row.preview = info; renderPlayer(); say("making a preview"); pollPreview(); })
      .catch(() => { els["quality-note"].textContent = "could not start a preview"; });
  }

  function selectClip(id, options) {
    state.selected = id;
    state.mode = "clip";
    state.window = null;                      // the cut being watched belongs to one set, never the next
    state.previewTimer && clearTimeout(state.previewTimer);
    renderPlayer();
    renderList();
    renderTimeline();
    if (!(options && options.silent)) say("selected " + (clip() ? "set " + clip().number : "clip"));
  }

  function watchCut() {
    const row = clip();
    if (!row) return;
    if (!state.data.source.media.master) { say("no source stream offered"); return; }
    if (state.mode === "window") {
      state.mode = "clip";
      state.window = null;
      renderPlayer();
      loadMedia();
      say("back to the rendered draft");
      return;
    }
    state.mode = "window";
    state.window = windowBounds(row);
    renderPlayer();
    loadMedia();
    els.player.play().catch(() => { /* a phone needs the first tap from the user */ });
    say("playing the source between the boundaries in force");
  }

  // A nudge is a question about footage the draft does not contain, so answering it with a dashed line is a
  // non-answer. Moving an edge switches playback onto the recording itself, clamped to the new boundaries,
  // and lands on the part that changed.
  function replayChangedEdge(edge) {
    const row = clip();
    if (!row) return;
    const entering = state.mode !== "window";
    state.mode = "window";
    state.window = windowBounds(row);
    renderPlayer();
    if (entering) {
      // Same reason as goToEdge: give the loader the destination rather than racing its own start-of-window
      // seek. No media seek should ever be ordered by an event it might not catch.
      loadMedia(null, edgeTime(edge));
      els.player.play().catch(() => { /* the first gesture belongs to the user */ });
      return;
    }
    seekToEdge(edge);
    els.player.play().catch(() => { /* user gesture needed */ });
    renderMeta();
  }

  // Rewind a moment before the moved edge so the change is seen in context, not as a hard cut in.
  function goToEdge(edge) {
    const row = clip();
    if (!row) return;
    if (state.mode !== "window") {
      state.mode = "window";
      state.window = windowBounds(row);
      renderPlayer();
      // Hand the destination to the loader instead of seeking after it. loadMedia installs its own
      // onloadedmetadata handler that parks the playhead at the start of the window, and that handler runs
      // after any seek made here, overwriting it -- which is why "end" went to the start twice.
      loadMedia(null, edgeTime(edge));
      return;
    }
    state.window = windowBounds(row);
    seekToEdge(edge);
    renderMeta();
  }

  // `loadedmetadata` fires once per load, and `loadMedia` claims it via the onloadedmetadata property, so a
  // listener added afterwards can wait forever and the jump silently does nothing — which is how "end" landed
  // on the start. Seek now when the element can take it, otherwise on the next state change.
  function seekWhenReady(at) {
    const media = els.player;
    const apply = () => {
      try { media.currentTime = at; } catch (err) { /* not seekable yet */ }
      renderMeta();
    };
    if (media.readyState >= 1) { apply(); return; }
    media.addEventListener("loadeddata", apply, { once: true });
  }

  function edgeTime(edge) {
    const [from, to] = state.window;
    const lookback = Math.min(3, Math.max(1, (to - from) * 0.15));
    return clamp(edge === "end" ? to - lookback : from, from, to);
  }

  function seekToEdge(edge) {
    seekWhenReady(edgeTime(edge));
  }

  function nudge(edge, delta) {
    const row = clip();
    if (!row) return;
    snapshot();
    const note = noteFor(row.id);
    const base = note.proposed || { start: row.requested.start, end: row.requested.end };
    if (edge === "start") {
      base.start = round3(clamp(base.start + delta, 0, base.end - 1));
    } else {
      base.end = round3(clamp(base.end + delta, base.start + 1, duration()));
    }
    note.proposed = (Math.abs(base.start - row.requested.start) < 0.001
                     && Math.abs(base.end - row.requested.end) < 0.001) ? null : base;
    scheduleSave();
    renderPlayer();
    renderTimeline();
    renderNotes();
    replayChangedEdge(edge);
    say(edge + " " + (delta > 0 ? "later" : "earlier") + " by " + Math.abs(delta) + " seconds");
  }

  function setEdgeAtPlayhead(edge) {
    const row = clip();
    if (!row) return;
    const head = sourcePlayhead();
    if (!head.available) { say("play the clip first"); return; }
    snapshot();
    const note = noteFor(row.id);
    const base = note.proposed || { start: row.requested.start, end: row.requested.end };
    const at = round3(head.seconds);
    if (edge === "start") base.start = Math.min(at, base.end - 1);
    else base.end = Math.max(at, base.start + 1);
    note.proposed = { start: round3(base.start), end: round3(base.end) };
    scheduleSave();
    renderPlayer();
    renderTimeline();
    renderNotes();
    replayChangedEdge(edge);
    say((head.exact ? "set from source playhead " : "set from clip estimate ") + clock(at, true));
  }

  function setStatus(status) {
    const row = clip();
    if (!row) return;
    snapshot();
    const note = noteFor(row.id);
    note.status = note.status === status ? "" : status;
    scheduleSave();
    renderPlayer();
    renderList();
    renderNotes();
    say(row.id + " marked " + (note.status || "cleared"));
  }

  // ---------------------------------------------------------------- missing clips
  function addMissing(start, end, note) {
    snapshot();
    state.notes.missing_clips.push({ id: "m" + Date.now(), start: round3(start), end: round3(end),
                                     note: note || "" });
    scheduleSave();
    renderTimeline();
    renderNotes();
    say("missing clip marked from " + clock(start, true));
  }

  function removeMissing(id) {
    snapshot();
    state.notes.missing_clips = state.notes.missing_clips.filter((row) => row.id !== id);
    scheduleSave();
    renderTimeline();
    renderNotes();
  }

  // ---------------------------------------------------------------- sheets
  function sheet(title, body, actions) {
    els["sheet-title"].textContent = title;
    els["sheet-body"].textContent = body;
    els["sheet-actions"].innerHTML = "";
    actions.concat([{ label: "Close", kind: "quiet", action: closeSheet }]).forEach((entry) => {
      const button = document.createElement("button");
      button.className = "btn" + (entry.kind === "quiet" ? " btn-quiet" : "");
      button.textContent = entry.label;
      button.addEventListener("click", entry.action);
      els["sheet-actions"].appendChild(button);
    });
    els["sheet-backdrop"].hidden = false;
  }

  function closeSheet() { els["sheet-backdrop"].hidden = true; }

  // ------------------------------------------------------------------ attribution confidence
  // The backend scored these names; this only says what it scored and why, in one line. Nothing here may
  // imply a verdict: an estimate is a hint about where to look, and the words "estimated" and "review" have
  // to stay attached to it.
  function confidenceHeadline(conf) {
    if (!conf || conf.available === false) {
      // Silence here would read as "nothing was found". This is not nothing: the file under review predates
      // the report, so the tier exists nowhere and that has to be the thing said out loud.
      return "confidence not reported for this project";
    }
    const target = (conf.targets || []).length
      ? "first to " + (conf.targets.length === 1 ? conf.targets[0] : conf.targets.join(" or "))
      : "no target applies";
    const worth = conf.tier + " confidence " + conf.score;
    if (conf.estimate) {
      // The arithmetic word and the evidence word always travel together. "Decisive" is only ever about the
      // tally against the targets this set admits; on its own it reads like a result, which is the exact
      // misunderstanding this line exists to prevent.
      const arithmetic = conf.estimate_status === "decisive" ? "arithmetic closed"
        : "arithmetic NOT closed" + (conf.estimate_conditional_because
            ? " (" + conf.estimate_conditional_because + ")" : "");
      return "estimated winner " + conf.estimate + " (" + (conf.estimate_wins || "?") + " games, "
        + (conf.estimate_source === "credited_wins" ? "credited wins" : "nameplate tally") + ") · "
        + arithmetic + " · " + target + " · " + worth;
    }
    if (conf.leader) {
      return "no name clears every target (" + conf.leader + " leads on " + conf.leader_wins + ") · "
        + target + " · " + worth;
    }
    return "no name to weigh · " + target + " · " + worth;
  }

  function confidenceSheet(row) {
    const conf = row.identity.confidence || {};
    if (conf.available === false) {
      // Say what is missing instead of showing nothing: a silent gap reads as "the machine found no names",
      // which is a different and much more comfortable claim than "this file has no report in it".
      sheet("Set " + String(row.number).padStart(2, "0") + " · no confidence report",
            conf.why || "the backend published no attribution-confidence report for this project", []);
      return;
    }
    const lines = [
      confidenceHeadline(conf),
      conf.needs_review ? "Flagged for review" + (conf.identity_conflict
        ? " — two names claim one seat" : "") : "Not flagged",
      conf.authoritative_winner ? "Authoritative winner: " + conf.authoritative_winner
        + (conf.agrees_with_authoritative ? " (the estimate agrees)" : " (the estimate does not)")
        : "No authoritative winner: the recovery withheld one",
      conf.because || conf.why_not || "",
      (conf.excluded_names || []).length
        ? "Names not carried as candidates:\n- " + conf.excluded_names.map((row) => row.name + ": "
            + row.because).join("\n- ")
        : "",
      (conf.reasons || []).length ? "Why:\n- " + conf.reasons.join("\n- ") : "",
      conf.witness_note || "",
      conf.score_meaning || "",
      (conf.unavailable || []).length ? "Missing evidence:\n- " + conf.unavailable.join("\n- ") : "",
      (conf.display_text_observations || []).length
        ? "Text corrected by reading a saved frame (one instant each; not an identity, not an "
          + "acceptance):\n- " + conf.display_text_observations.map((row) => {
            const where = row.side + " at " + row.at + " s: \"" + row.row_text + "\" drawn as \""
              + row.observed_text + "\" in " + row.frame_file + " (" + row.frame_sha256 + "\u2026)";
            const repeated = (row.reference_count || 1) > 1
              ? ", mentioned " + row.reference_count + "\u00d7 in this set (one observation)" : "";
            return where + " \u2014 role: " + (row.role || "unrecorded") + repeated;
          }).join("\n- ")
        : "",
      (conf.display_text_correction_refusals || []).length
        ? "Readings this set did not apply:\n- " + conf.display_text_correction_refusals.map((row) =>
            row.side + " at " + row.at + " s: the row carries \"" + row.row_text + "\" while the saved "
            + "reading names \"" + row.declared_text + "\" \u2014 neither was applied").join("\n- ")
        : "",
      (conf.alias_uncertainty || []).length
        ? "Spellings that are not settled:\n- " + conf.alias_uncertainty.map((row) => row.spellings.join(" / ")
            + " on " + row.side + " (" + Math.round((row.similarity || 0) * 100) + "% alike) — one "
            + "participant is suspected, not established; " + row.settled_by).join("\n- ")
        : "",
      conf.games_observed ? "Characters: " + (conf.characters_with_a_pair || 0) + " of "
        + conf.games_observed + " games carry a pair, " + (conf.characters_side_bound ? "some bound to a "
        + "side" : "none bound to a side") : "",
    ].filter(Boolean);
    sheet("Set " + String(row.number).padStart(2, "0") + " · how much to trust these names",
          lines.join("\n\n"), []);
  }

  function gapSheet(gap) {
    const span = gap.end - gap.start;
    sheet(clock(gap.start, true) + " → " + clock(gap.end, true),
          "Unclaimed for " + clock(span) + ". Unclaimed means no draft clip covers it — it may be "
          + "intro, interviews, menus or play nobody grouped. Preview the source here, or mark it as a "
          + "missing clip.",
          [
            { label: "Preview source here", action: () => {
                closeSheet();
                state.mode = "source";
                renderPlayer();
                const seekTo = gap.start + Math.min(5, span / 2);
                loadMedia(seekTo);
                say("playing source from " + clock(seekTo, true));
              } },
            { label: "Mark missing clip here", action: () => {
                closeSheet();
                addMissing(gap.start, gap.end, "from an unclaimed region");
              } },
          ]);
  }

  // ---------------------------------------------------------------- timeline
  function view() { return state.view || [0, duration()]; }

  function position(start, end) {
    const [from, to] = view();
    const span = Math.max(0.001, to - from);
    const left = clamp(((start - from) / span) * 100, -100, 200);
    const width = clamp(((end - start) / span) * 100, 0, 400);
    return { left: left + "%", width: width + "%" };
  }

  function renderTimeline() {
    const [from, to] = view();
    const row = clip();
    // The inner element is the visible window magnified: half the recording showing means twice the width, so
    // the lane can be scrolled to pan. (The inverse reads plausibly and shrinks the strip when you zoom in.)
    els["timeline-inner"].style.width = Math.max(100, duration() / Math.max(0.001, to - from) * 100) + "%";
    els["zoom-label"].textContent = (to - from) / duration() > 0.98 ? "whole recording"
      : clock(to - from) + " window";

    els.lane.innerHTML = "";
    els.ruler.innerHTML = "";
    els.ticks.innerHTML = "";

    state.data.clips.forEach((item) => {
      const button = document.createElement("button");
      button.className = "seg" + (row && item.id === row.id ? " sel" : "");
      Object.assign(button.style, position(item.requested.start, item.requested.end));
      button.title = "Set " + item.number + " · " + clock(item.requested.start, true) + " → "
        + clock(item.requested.end, true);
      button.setAttribute("aria-label", button.title);
      const number = document.createElement("span");
      number.className = "num";
      number.textContent = String(item.number);
      button.appendChild(number);
      // In marking mode a tap belongs to the drag gesture, not to selection: without this, tapping the strip
      // to start a range would also load a different clip underneath it.
      button.addEventListener("click", () => { if (!state.markMode) { selectClip(item.id); loadMedia(); } });
      els.lane.appendChild(button);
    });

    state.data.gaps.forEach((gap) => {
      const button = document.createElement("button");
      button.className = "gap";
      Object.assign(button.style, position(gap.start, gap.end));
      button.setAttribute("aria-label", "unclaimed " + clock(gap.start, true) + " to "
        + clock(gap.end, true) + ", " + clock(gap.end - gap.start) + " long");
      button.addEventListener("click", () => gapSheet(gap));
      els.lane.appendChild(button);
    });

    // Proposed edits and missing-clip marks are drawn from the notes, over the strip they belong to.
    Object.keys(state.notes.items).forEach((id) => {
      const proposed = (state.notes.items[id] || {}).proposed;
      const item = state.data.clips.find((row2) => row2.id === id);
      if (!proposed || !item) return;
      const ghost = document.createElement("div");
      ghost.className = "ghost";
      Object.assign(ghost.style, position(proposed.start, proposed.end));
      els.lane.appendChild(ghost);
    });

    state.notes.missing_clips.forEach((mark) => {
      const dot = document.createElement("div");
      dot.className = "marker";
      dot.style.left = position(mark.start, mark.start).left;
      els.lane.appendChild(dot);
      const band = document.createElement("div");
      band.className = "ghost";
      Object.assign(band.style, position(mark.start, mark.end));
      els.lane.appendChild(band);
    });

    // ticks and ruler: choose a readable step for the visible window
    const span = to - from;
    const maxTicks = Math.max(2, Math.floor(els["timeline-scroll"].clientWidth / 72));
    const step = [10, 30, 60, 120, 300, 600, 900, 1800, 3600]
      .find((candidate) => span / candidate <= maxTicks) || 3600;
    for (let at = Math.ceil(from / step) * step; at <= to; at += step) {
      const tick = document.createElement("div");
      tick.className = "tick";
      tick.style.left = position(at, at).left;
      const label = document.createElement("span");
      label.textContent = clock(at);
      tick.appendChild(label);
      els.ticks.appendChild(tick);
      const ruler = document.createElement("div");
      ruler.className = "rt";
      ruler.style.left = position(at, at).left;
      ruler.textContent = clock(at);
      els.ruler.appendChild(ruler);
    }
    renderPlayhead();
  }

  function renderPlayhead() {
    const head = sourcePlayhead();
    let mark = els.lane.querySelector(".playhead");
    if (!head.available) { if (mark) mark.remove(); return; }
    if (!mark) { mark = document.createElement("div"); mark.className = "playhead"; els.lane.appendChild(mark); }
    mark.style.left = position(head.seconds, head.seconds).left;
  }

  function scrollTimeIntoView(at) {
    // A magnified strip is only useful if it opens somewhere sensible: centre the anchor in the scroller.
    const [from, to] = view();
    const span = Math.max(0.001, to - from);
    const inner = els["timeline-inner"].offsetWidth || els["timeline-scroll"].clientWidth;
    const fraction = clamp((at - from) / span, 0, 1);
    els["timeline-scroll"].scrollLeft = clamp(fraction * inner - els["timeline-scroll"].clientWidth / 2, 0,
                                              inner);
  }

  function setZoom(next, anchor) {
    const [from, to] = view();
    const span = to - from;
    const pivot = Number.isFinite(anchor) ? anchor : (from + span / 2);
    const width = clamp(span * next, 20, duration());
    state.view = [clamp(pivot - width / 2, 0, Math.max(0, duration() - width)), 0];
    state.view[1] = Math.min(duration(), state.view[0] + width);
    renderTimeline();
    scrollTimeIntoView(pivot);
  }

  function fitToClip() {
    const row = clip();
    if (!row) { state.view = null; renderTimeline(); return; }
    const pad = Math.max(30, (row.requested.end - row.requested.start) * 0.35);
    state.view = [clamp(row.requested.start - pad, 0, duration()),
                  clamp(row.requested.end + pad, 0, duration())];
    renderTimeline();
    scrollTimeIntoView((row.requested.start + row.requested.end) / 2);
  }

  // ---------------------------------------------------------------- render
  function renderMeta() {
    const row = clip();
    const media = els.player;
    if (!row) return;
    const local = Number.isFinite(media.currentTime) ? media.currentTime : 0;
    const total = Number.isFinite(media.duration) ? media.duration : 0;
    const head = sourcePlayhead();

    if (state.mode === "window" && state.window) {
      const [from, to] = state.window;
      // Only a *playing* playhead may be corrected. This clamp exists to stop the cut overrunning its end,
      // but on a paused video the playhead sits below the window start whenever the file was loaded from
      // clip-local 0 -- so it dragged every jump back to the start. The end jump computed the right number
      // and was overwritten by the next timeupdate, which is why it looked like the button did nothing.
      if (!media.paused) {
        if (local > to + 0.05) {
          media.pause();
          media.currentTime = to;
          say("end of the proposed cut");
        } else if (local < from - 0.75) {
          media.currentTime = from;
        }
      }
      els["time-readout"].innerHTML = "<b>" + clock(head.seconds, true) + "</b> "
        + "<span class='muted'>source · cut " + clock(from, true) + " → " + clock(to, true)
        + " · " + clock(Math.max(0, to - head.seconds)) + " left in it</span>";
      renderPlayhead();
      return;
    }
    els["time-readout"].innerHTML = state.mode === "source"
      ? "<b>" + clock(head.seconds, true) + "</b> <span class='muted'>source · " + clock(total) + " buffered</span>"
      : "<b>" + clock(head.seconds, true) + "</b> <span class='muted'>est. source · clip "
        + clock(local) + " / " + clock(total) + "</span>";
    renderPlayhead();
  }

  function renderPlayer() {
    const row = clip();
    if (!row) return;
    const note = state.notes.items[row.id] || {};
    els["clip-title"].textContent = "Set " + String(row.number).padStart(2, "0")
      + (row.identity.confirmed ? " · " + row.identity.player1 + " vs " + row.identity.player2 : "");
    els["clip-sub"].textContent = row.identity.confirmed
      ? (row.identity.round || "identity from the machine pass")
      : ["identity not certified",
         (row.identity.hypotheses || []).length ? "nameplate says " + row.identity.hypotheses.join(" vs ") : "",
         (row.identity.characters || []).length ? "characters " + row.identity.characters.join("/"
          ) + (row.identity.characters_side_bound ? "" : " (side unknown)") : ""].filter(Boolean).join(" · ");
    const conf = row.identity.confidence || {};
    const confNote = els["confidence-note"];
    confNote.textContent = confidenceHeadline(conf) + (conf.needs_review ? " · review" : "");
    confNote.hidden = !confNote.textContent;
    confNote.classList.toggle("confidence-review", Boolean(conf.needs_review));
    const badge = els["clip-status"];
    badge.className = "pill " + (note.status ? STATUS_CLASS[note.status] : "");
    badge.textContent = note.status ? STATUS_LABEL[note.status] : "not reviewed";

    const watching = state.mode === "window";
    els["mode-badge"].textContent = watching ? "source · the cut"
      : state.mode === "source" ? "source · scrubbing" : "draft clip";
    els["map-badge"].hidden = state.mode !== "clip";
    els["toggle-mode"].textContent = watching ? "Back to the draft clip" : "Watch this cut";
    els["mode-hint"].hidden = watching;
    els["boundary-readout"].classList.toggle("active-cut", watching);
    if (!state.diagnosed) els["quality-note"].textContent = "";
    const preview = row.preview || {};
    const toggle = els["preview-toggle"];
    toggle.hidden = preview.state !== "ready" || state.mode !== "clip";
    const smooth = state.quality === "preview" && preview.state === "ready";
    toggle.textContent = smooth ? "Play the full 1440p draft" : "Play the smooth preview";
    toggle.setAttribute("aria-pressed", String(smooth));
    if (preview.state === "generating") pollPreview();

    els["boundary-readout"].innerHTML = "planned <b class='mono'>" + clock(row.requested.start, true)
      + " → " + clock(row.requested.end, true) + "</b> (" + clock(row.requested.duration)
      + ") · delivered " + clock(row.delivered.container_seconds) + ", "
      + row.delivered.mode + ", " + (row.delivered.source_range_verified ? "range verified"
        : "source range <b>not verified</b>")
      + (row.delivered.length_delta_seconds ? " · +" + row.delivered.length_delta_seconds.toFixed(1)
        + "s longer than planned" : "");

    const proposed = note.proposed;
    els["proposed-readout"].hidden = !proposed;
    if (proposed) {
      els["proposed-readout"].innerHTML = "proposed <b class='mono'>" + clock(proposed.start, true) + " → "
        + clock(proposed.end, true) + "</b> (start " + signed(proposed.start - row.requested.start)
        + ", end " + signed(proposed.end - row.requested.end) + ") — playback is still the existing clip";
    }
    els["note-input"].value = note.note || "";
    document.querySelectorAll(".btn-verdict").forEach((button) => {
      button.setAttribute("aria-pressed", String(note.status === button.dataset.status));
    });
    renderMeta();
  }

  const signed = (value) => (value >= 0 ? "+" : "−") + Math.abs(value).toFixed(1) + "s";

  function renderList() {
    els["clip-list"].innerHTML = "";
    els["clips-count"].textContent = state.data.clips.length + " draft clips";
    state.data.clips.forEach((row) => {
      const note = state.notes.items[row.id] || {};
      const li = document.createElement("li");
      li.className = "clip" + (row.id === state.selected ? " sel" : "");
      const poster = document.createElement("img");
      poster.loading = "lazy";
      poster.alt = "last frame of set " + row.number;
      if (row.media.poster) poster.src = url(row.media.poster);
      const body = document.createElement("div");
      body.className = "clip-body";
      const title = document.createElement("div");
      title.className = "clip-title";
      title.innerHTML = "<strong>Set " + String(row.number).padStart(2, "0") + "</strong>";
      const pill = document.createElement("span");
      pill.className = "pill " + (note.status ? STATUS_CLASS[note.status] : "pill-unsure");
      pill.textContent = note.status ? STATUS_LABEL[note.status] : "unreviewed";
      title.appendChild(pill);
      const line = document.createElement("p");
      line.className = "clip-line mono";
      line.textContent = clock(row.requested.start, true) + " → " + clock(row.requested.end, true)
        + "  " + clock(row.requested.duration);
      const ident = document.createElement("p");
      ident.className = "clip-line";
      // An empty identity field means "the machine would not certify it", not "nothing was read". Say which.
      ident.textContent = row.identity.confirmed
        ? row.identity.player1 + " vs " + row.identity.player2
        : ((row.identity.hypotheses || []).length
            ? row.identity.hypotheses.join(" vs ") + "  (nameplate hypothesis)"
            : "no nameplate reading");
      const chars = document.createElement("p");
      chars.className = "clip-line";
      chars.textContent = (row.identity.characters || []).length
        ? row.identity.characters.join(" · ")
          + (row.identity.characters_side_bound ? "" : "  (not tied to a side)")
        : "";
      const wins = document.createElement("p");
      wins.className = "clip-line mono";
      const tally = (row.identity.attributed_wins || [])
        .map((w) => w.name.split(" | ").pop().slice(0, 10) + " " + w.wins).join("  ");
      wins.textContent = tally
        ? row.identity.games_with_winner + "/" + row.identity.games_total
          + " games won \u00b7 " + tally
        : row.identity.games_with_winner + " of " + row.identity.games_total + " games won";
      const confLine = document.createElement("p");
      confLine.className = "clip-line clip-conf";
      confLine.textContent = confidenceHeadline(row.identity.confidence || {});
      confLine.hidden = !confLine.textContent;
      const flags = document.createElement("div");
      flags.className = "flags";
      // The flag is deliberately blunt and carries no name: an estimate has to look like a hint about
      // where to look, not like a result, and a row that a reviewer skips because it looked settled is
      // the failure this whole layer exists to prevent.
      if ((row.identity.confidence || {}).needs_review) {
        flags.innerHTML += "<span class='flag flag-warn'>low name confidence</span>";
      }
      if (confLine.textContent) {
        confLine.textContent += (row.identity.confidence || {}).available ? " — tap for why"
                                                                         : " — tap to read why";
        confLine.addEventListener("click", () => confidenceSheet(row));
      }
      if (note.proposed) flags.innerHTML += "<span class='flag'>proposed edges</span>";
      if (note.note) flags.innerHTML += "<span class='flag'>note</span>";
      if (!row.delivered.start_is_exact) flags.innerHTML += "<span class='flag flag-warn'>copy · approximate start</span>";
      if (row.evidence.startBasis && /fallback/i.test(row.evidence.startBasis)) {
        flags.innerHTML += "<span class='flag flag-warn'>start is a flagged fallback</span>";
      }
      if (Number.isFinite(row.evidence.onsetUncertaintySeconds)) {
        flags.innerHTML += "<span class='flag'>±" + row.evidence.onsetUncertaintySeconds + "s onset</span>";
      }
      const open = document.createElement("button");
      open.className = "btn clip-open";
      open.textContent = "Play";
      open.addEventListener("click", () => { selectClip(row.id); loadMedia(); scrollPlayerIntoView(); });
      body.append(title, line, ident, chars, wins, confLine, flags, open);
      li.append(poster, body);
      els["clip-list"].appendChild(li);
    });
  }

  function scrollPlayerIntoView() {
    document.querySelector(".player-card").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderNotes() {
    const rows = [];
    state.data.clips.forEach((clip2) => {
      const note = state.notes.items[clip2.id] || {};
      if (!note.status && !note.note && !note.proposed) return;
      rows.push("<div class='note'><b>Set " + String(clip2.number).padStart(2, "0") + "</b> "
        + (note.status ? "<span class='pill " + STATUS_CLASS[note.status] + "'>" + STATUS_LABEL[note.status]
          + "</span> " : "")
        + (note.proposed ? "<span class='muted mono'>propose " + clock(note.proposed.start, true) + " → "
          + clock(note.proposed.end, true) + "</span>" : "")
        + (note.note ? "<div>" + escapeHtml(note.note) + "</div>" : "")
        + "<div class='muted'>" + clock(clip2.requested.start, true) + " → "
        + clock(clip2.requested.end, true) + " · " + clip2.delivered.mode + "</div></div>");
    });
    state.notes.missing_clips.forEach((mark) => {
      rows.push("<div class='note'><b>Missing clip</b> <span class='muted mono'>" + clock(mark.start, true)
        + " → " + clock(mark.end, true) + "</span><div>" + escapeHtml(mark.note || "") + "</div>"
        + "<div class='row'><button class='btn btn-mini' data-jump='" + mark.start + "'>Preview</button>"
        + "<button class='btn btn-mini btn-quiet' data-remove='" + mark.id + "'>Remove</button></div></div>");
    });
    els["notes-list"].innerHTML = rows.length ? rows.join("")
      : "<p class='empty'>Nothing marked yet. Verdicts, nudges and notes appear here and on the timeline.</p>";
    els["notes-list"].querySelectorAll("[data-remove]").forEach((button) => {
      button.addEventListener("click", () => removeMissing(button.dataset.remove));
    });
    els["notes-list"].querySelectorAll("[data-jump]").forEach((button) => {
      button.addEventListener("click", () => {
        const at = parseFloat(button.dataset.jump);
        state.mode = "source";
        renderPlayer();
        loadMedia(at);
        scrollPlayerIntoView();
      });
    });
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"]/g, (chr) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
                                                       '"': "&quot;" }[chr]));
  }

  // ---------------------------------------------------------------- set cards
  const SET_TEXT = ["player1", "player2", "character1", "character2", "round_name"];
  function setFields(raw) {
    const out = { start: Number.isFinite(raw.start) ? round3(raw.start) : null,
                  end: Number.isFinite(raw.end) ? round3(raw.end) : null };
    SET_TEXT.forEach((key) => { out[key] = String(raw[key] || "").trim().slice(0, 80); });
    return out;
  }
  function sameFields(a, b) { return JSON.stringify(setFields(a || {})) === JSON.stringify(setFields(b || {})); }
  function setSummary(fields) {
    const side = (p, c) => (p || "?") + (c ? " (" + c + ")" : "");
    return clock(fields.start, true) + "–" + clock(fields.end, true) + " · "
      + side(fields.player1, fields.character1) + " vs " + side(fields.player2, fields.character2)
      + " · " + (fields.round_name || "round not set");
  }

  // ---------------------------------------------------------------- question flow
  // One question at a time, with its own player and answer choices written for that question. The clip
  // editor below is a separate tab: a verdict on a clip is not an answer to "is this a new set?".
  function queue() { return (state.data.review_run && state.data.review_run.queue) || []; }
  // A question's own id when it has one: a follow-up round asks more than one question about a clip.
  function qkey(item) { return item.id || item.clip_id; }
  function answerFor(item) { return state.notes.answers[qkey(item)] || null; }
  function answeredCount() { return queue().filter((item) => answerFor(item)).length; }

  function draftFor(item) {
    if (!state.q.drafts[qkey(item)]) {
      const saved = answerFor(item);
      state.q.drafts[qkey(item)] = saved
        ? { choice: saved.choice, at: saved.at, note: saved.note,
            fields: saved.fields ? Object.assign({}, saved.fields) : null }
        : { choice: item.kind === "set" ? "confirmed" : "", at: null, note: "",
            fields: item.kind === "set" ? setFields(item.fields || {}) : null };
    }
    return state.q.drafts[qkey(item)];
  }

  const SET_CHOICES = [{ id: "confirmed", label: "Confirmed set" }, { id: "remove", label: "Not a real set" }];
  function choiceOf(item, id) {
    return (item.kind === "set" ? SET_CHOICES : (item.choices || [])).find((c) => c.id === id) || null;
  }

  function draftReady(item, draft) {
    if (item.kind === "set" && draft.choice === "confirmed") {
      const f = draft.fields;
      return Number.isFinite(f.start) && Number.isFinite(f.end) && f.end > f.start && !!f.player1 && !!f.player2;
    }
    const choice = choiceOf(item, draft.choice);
    if (!choice) return false;
    return !choice.mark || Number.isFinite(draft.at);
  }

  function draftMatchesSaved(item, draft) {
    const saved = answerFor(item);
    if (!saved) return false;
    const at = choiceOf(item, draft.choice) && choiceOf(item, draft.choice).mark ? draft.at : null;
    return saved.choice === draft.choice && (saved.at ?? null) === (at ?? null)
      && saved.note === draft.note.trim()
      && (item.kind !== "set" || draft.choice === "remove" || sameFields(saved.fields, draft.fields));
  }

  function setTab(tab) {
    state.tab = tab;
    const questions = tab === "questions";
    els["question-view"].hidden = !questions;
    els["editor-view"].hidden = questions;
    els.coverage.hidden = questions;
    els["tab-questions"].setAttribute("aria-selected", String(questions));
    els["tab-editor"].setAttribute("aria-selected", String(!questions));
    (questions ? els.player : els["q-player"]).pause();
    try { history.replaceState(null, "", questions ? "#questions" : "#editor"); } catch (err) { /* ignore */ }
    if (questions) renderQuestion();
    else if (state.data && !els.player.getAttribute("src")) loadMedia();
  }

  function renderProgress() {
    const items = queue();
    els["tab-count"].textContent = answeredCount() + "/" + items.length;
    els["q-progress"].innerHTML = "";
    items.forEach((item, index) => {
      const dot = document.createElement("button");
      dot.className = "q-dot" + (answerFor(item) ? " done" : "") + (index === state.q.index && !state.q.summary
        ? " current" : "");
      dot.textContent = answerFor(item) ? "✓" : String(index + 1);
      dot.setAttribute("aria-label", "Question " + (index + 1) + ": " + item.title
                       + (answerFor(item) ? " (answered)" : " (not answered)"));
      dot.addEventListener("click", () => goToQuestion(index));
      els["q-progress"].appendChild(dot);
    });
    const summary = document.createElement("button");
    summary.className = "q-dot q-dot-summary" + (state.q.summary ? " current" : "");
    summary.textContent = "All";
    summary.setAttribute("aria-label", "All answers");
    summary.addEventListener("click", showSummary);
    els["q-progress"].appendChild(summary);
  }

  // The range shown is the clip's; playback opens at its start, or earlier when the flagged moment is earlier
  // (an opening question deliberately looks before the draft start). Opening mid-range made the reviewer
  // scrub back to find the start of what they were asked about.
  function rangeFor(item) {
    if (Array.isArray(item.range)) return { start: item.range[0], end: item.range[1] };
    const row = state.data.clips.find((c) => c.id === item.clip_id);
    if (!row) return { start: item.source_at, end: null };
    return { start: Math.min(item.source_at, row.requested.start), end: row.requested.end };
  }

  function loadQuestionVideo(item) {
    const master = state.data.source.media.master;
    if (!master) { els["q-now"].textContent = "no recording"; return; }
    const video = els["q-player"];
    video.pause();
    const seek = () => { try { video.currentTime = rangeFor(item).start; } catch (err) { /* not ready */ } };
    if (state.q.loadedSrc === master && video.readyState >= 1) { seek(); return; }
    state.q.loadedSrc = master;
    video.onloadedmetadata = seek;
    video.muted = false;
    video.src = url(master);
    video.load();
  }

  function renderQuestion() {
    const items = queue();
    renderProgress();
    els["q-done"].hidden = !state.q.summary;
    els["q-card"].hidden = !!state.q.summary;
    if (state.q.summary || !items.length) return;
    const item = items[state.q.index];
    const draft = draftFor(item);
    const range = rangeFor(item);
    els["q-eyebrow"].textContent = "Question " + (state.q.index + 1) + " of " + items.length
      + (Number.isFinite(range.end) ? (Array.isArray(item.range) ? " · video shows " : " · clip ")
        + clock(range.start) + "–" + clock(range.end) : "");
    els["q-title"].textContent = item.title;
    els["q-text"].textContent = item.question;
    els["q-observations"].textContent = item.observations;
    const context = els["q-observations"].closest("details");
    context.hidden = item.kind === "set";
    els["q-callout"].hidden = item.kind !== "set" || !item.observations;
    els["q-callout"].textContent = item.kind === "set" ? item.observations : "";
    els["q-rewind"].textContent = "↺ Start (" + clock(range.start) + ")";
    els["q-flag"].textContent = "⚑ " + (item.flag_label || "Flagged moment") + " (" + clock(item.source_at) + ")";
    els["q-flag"].hidden = Math.abs(item.source_at - range.start) < 1;

    const isSet = item.kind === "set";
    els["q-set"].hidden = !isSet;
    els["q-choices"].hidden = isSet;
    els["q-jumps"].hidden = isSet;
    // "Check here" spots: moments the analysis isn't sure about (e.g. a possible character switch). Each
    // button jumps the player to that source time so the reviewer can confirm in seconds.
    const spots = Array.isArray(item.spots) ? item.spots : [];
    els["q-spots"].hidden = !spots.length;
    els["q-spots"].innerHTML = spots.length ? "<span class='q-spots-label'>Check here:</span>" : "";
    spots.forEach((spot) => {
      const button = document.createElement("button");
      button.className = "btn btn-mini";
      button.textContent = "⚑ " + clock(spot.at) + " · " + (spot.label || "check");
      button.addEventListener("click", () => {
        if (els["q-player"].readyState >= 1) els["q-player"].currentTime = spot.at;
      });
      els["q-spots"].appendChild(button);
    });
    if (isSet) renderSetCard(item, draft);
    els["q-choices"].innerHTML = "";
    (isSet ? [] : (item.choices || [])).forEach((choice) => {
      const button = document.createElement("button");
      button.className = "q-choice";
      button.setAttribute("role", "radio");
      button.setAttribute("aria-checked", String(draft.choice === choice.id));
      button.innerHTML = "<span class='q-radio' aria-hidden='true'></span><span></span>";
      button.lastChild.textContent = choice.label;
      button.addEventListener("click", () => {
        draft.choice = choice.id;
        renderQuestion();
        if (choice.mark) els["q-mark"].scrollIntoView({ behavior: "smooth", block: "nearest" });
      });
      els["q-choices"].appendChild(button);
    });

    const chosen = choiceOf(item, draft.choice);
    els["q-mark"].hidden = !(chosen && chosen.mark);
    if (!isSet) state.q.builtFor = null;
    if (chosen && chosen.mark) {
      els["q-mark-prompt"].textContent = chosen.mark;
      els["q-mark-value"].textContent = Number.isFinite(draft.at) ? clock(draft.at, true) : "not marked yet";
      els["q-mark-value"].classList.toggle("set", Number.isFinite(draft.at));
      els["q-mark-btn"].textContent = Number.isFinite(draft.at) ? "Re-mark at current time"
        : "Mark the current video time";
    }
    els["q-note-label"].textContent = item.note_prompt || "Anything else (optional)";
    if (document.activeElement !== els["q-note"]) els["q-note"].value = draft.note;

    const ready = draftReady(item, draft);
    const same = draftMatchesSaved(item, draft);
    const last = state.q.index === items.length - 1;
    els["q-save"].disabled = !ready;
    els["q-save"].textContent = same ? (last ? "Saved ✓ · See all" : "Saved ✓ · Next")
      : (answerFor(item) ? "Update answer" : "Save answer");
    els["q-prev"].disabled = state.q.index === 0;
    els["q-skip"].textContent = last ? "See all ▶" : "Skip ▶";
    const saved = answerFor(item);
    els["q-save-state"].textContent = !draft.choice ? "Pick an answer above."
      : (!ready ? (isSet ? "A set needs a start before its end, and both players." : "Mark the time in the video to save this answer.")
      : (same ? "Saved." : (saved ? "Changed. Tap Update to save it." : "Not saved yet.")));
    renderQuestionClock();
  }

  function renderSetCard(item, draft) {
    const key = qkey(item);
    const src = item.sources || {};
    const tag = (group) => "<span class='q-src' data-src='" + group + "'></span>";
    if (state.q.builtFor !== key) {
      state.q.builtFor = key;
      const timeRow = (edge, label, hint) =>
        "<div class='q-field q-time' data-edge='" + edge + "'><div class='q-field-head'><span>" + label + "</span>"
        + tag(edge) + "</div><div class='q-time-row'><span class='mono q-time-val'></span>"
        + "<button class='btn btn-mini' data-go='" + edge + "'>Go to</button>"
        + "<button class='btn btn-mini' data-mark-edge='" + edge + "'>Mark here</button></div>"
        + "<p class='hint'>" + hint + "</p></div>";
      const text = (field, list, placeholder) => "<input class='q-input' data-field='" + field + "' list='" + list
        + "' placeholder='" + placeholder + "' autocomplete='off' autocapitalize='off' spellcheck='false'>";
      els["q-set"].innerHTML =
        "<div class='q-seg' role='radiogroup'>"
        + SET_CHOICES.map((c) => "<button class='q-seg-btn' role='radio' data-set-choice='" + c.id + "'>"
          + c.label + "</button>").join("") + "</div>"
        + "<div class='q-set-body'>"
        + timeRow("start", "Start", "Middle of the stage select.")
        + timeRow("end", "End", "Where the set's clip should finish.")
        + "<div class='q-field'><div class='q-field-head'><span>Players and characters</span>"
        + tag("players") + "</div><div class='q-sides'>"
        + "<div class='q-side'><span class='q-side-label'>Player 1 (left)</span>" + text("player1", "dl-players", "player")
        + text("character1", "dl-characters", "character") + "</div>"
        + "<div class='q-side'><span class='q-side-label'>Player 2 (right)</span>" + text("player2", "dl-players", "player")
        + text("character2", "dl-characters", "character") + "</div></div>"
        + "<button class='btn btn-mini btn-quiet q-swap' data-swap>⇄ Swap sides</button></div>"
        + "<div class='q-field'><div class='q-field-head'><span>Round</span>" + tag("round_name") + "</div>"
        + text("round_name", "dl-rounds", "e.g. Winners Final") + "</div></div>";
      els["q-set"].querySelectorAll("[data-set-choice]").forEach((b) => b.addEventListener("click", () => {
        draft.choice = b.dataset.setChoice; renderQuestion(); }));
      els["q-set"].querySelectorAll("[data-field]").forEach((input) => {
        input.value = draft.fields[input.dataset.field] || "";
        input.addEventListener("input", () => { draft.fields[input.dataset.field] = input.value; renderQuestion(); });
      });
      els["q-set"].querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => {
        const at = draft.fields[b.dataset.go];
        if (Number.isFinite(at) && els["q-player"].readyState >= 1)
          els["q-player"].currentTime = at;
      }));
      els["q-set"].querySelectorAll("[data-mark-edge]").forEach((b) => b.addEventListener("click", () => {
        const video = els["q-player"];
        if (video.readyState < 1) { say("The video hasn't loaded yet"); return; }
        video.pause();
        draft.fields[b.dataset.markEdge] = round3(video.currentTime);
        renderQuestion();
      }));
      els["q-set"].querySelector("[data-swap]").addEventListener("click", () => {
        const f = draft.fields;
        [f.player1, f.player2, f.character1, f.character2] = [f.player2, f.player1, f.character2, f.character1];
        state.q.builtFor = null; renderQuestion();
      });
    }
    els["q-set"].querySelectorAll("[data-set-choice]").forEach((b) =>
      b.setAttribute("aria-checked", String(draft.choice === b.dataset.setChoice)));
    els["q-set"].querySelector(".q-set-body").hidden = draft.choice !== "confirmed";
    ["start", "end"].forEach((edge) => {
      const row = els["q-set"].querySelector("[data-edge='" + edge + "']");
      const at = draft.fields[edge];
      row.querySelector(".q-time-val").textContent = Number.isFinite(at) ? clock(at, true) : "not set";
      const changed = !item.fields || !Number.isFinite(item.fields[edge]) || Math.abs(at - item.fields[edge]) > 0.05;
      row.classList.toggle("changed", changed);
    });
    const orig = item.fields || {};
    const groups = { start: ["start"], end: ["end"], players: ["player1", "player2", "character1", "character2"],
                     round_name: ["round_name"] };
    Object.keys(groups).forEach((group) => {
      const edited = groups[group].some((f) => typeof orig[f] === "number"
        ? Math.abs(draft.fields[f] - orig[f]) > 0.05 : String(draft.fields[f] || "").trim() !== String(orig[f] || ""));
      const el = els["q-set"].querySelector("[data-src='" + group + "']");
      const text = edited ? "edited by you" : (src[group] || "");
      el.textContent = text;
      el.hidden = !text;
      el.classList.toggle("mine", edited || text.startsWith("your"));
    });
  }

  function renderQuestionClock() {
    const video = els["q-player"];
    els["q-now"].textContent = video.readyState >= 1 ? clock(video.currentTime, true) : "loading…";
  }

  function goToQuestion(index) {
    state.q.summary = false;
    state.q.builtFor = null;
    state.q.index = clamp(index, 0, queue().length - 1);
    renderQuestion();
    loadQuestionVideo(queue()[state.q.index]);
    els["q-card"].scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function nextQuestion() {
    const items = queue();
    if (state.q.index >= items.length - 1) { showSummary(); return; }
    goToQuestion(state.q.index + 1);
  }

  function showSummary() {
    state.q.summary = true;
    els["q-player"].pause();
    const items = queue();
    const done = answeredCount();
    els["q-done"].querySelector("h2").textContent = done === items.length ? "All questions answered"
      : done + " of " + items.length + " questions answered";
    els["q-done-help"].textContent = "Saved to the review notes file. Nothing here changes the TVH project; "
      + "the answers are used later to make reviewed edits.";
    els["q-summary"].innerHTML = "";
    items.forEach((item, index) => {
      const answer = answerFor(item);
      const li = document.createElement("li");
      const title = document.createElement("b");
      title.textContent = item.title;
      const body = document.createElement("p");
      body.className = answer ? "" : "muted";
      body.textContent = answer && answer.fields ? setSummary(answer.fields)
          + (answer.note ? " · “" + answer.note + "”" : "")
        : answer
        ? answer.choice_label + (Number.isFinite(answer.at) ? " · at " + clock(answer.at, true) : "")
          + (answer.note ? " · “" + answer.note + "”" : "")
        : "Not answered";
      const edit = document.createElement("button");
      edit.className = "btn btn-mini";
      edit.textContent = answer ? "Change" : "Answer";
      edit.addEventListener("click", () => goToQuestion(index));
      li.append(title, body, edit);
      els["q-summary"].appendChild(li);
    });
    renderQuestion();
  }

  function saveAnswer() {
    const item = queue()[state.q.index];
    const draft = draftFor(item);
    if (!draftReady(item, draft)) return;
    if (draftMatchesSaved(item, draft)) { nextQuestion(); return; }
    const choice = choiceOf(item, draft.choice);
    state.notes.answers[qkey(item)] = { clip_id: item.clip_id,
      choice: choice.id, choice_label: choice.label,
      at: choice.mark && Number.isFinite(draft.at) ? round3(draft.at) : null,
      note: draft.note.trim(), question: item.question, saved_at: new Date().toISOString(),
      fields: item.kind === "set" && choice.id === "confirmed" ? setFields(draft.fields) : undefined,
      kind: item.kind || "question" };
    els["q-save-state"].textContent = "Saving…";
    clearTimeout(state.saveTimer);
    saveNow().then(() => {
      const ok = /^saved/.test(els["save-state"].textContent);
      say(ok ? "Answer saved" : "Answer kept on this device only");
      nextQuestion();
      if (!ok) els["q-save-state"].textContent = "Couldn't reach the server; kept on this device only.";
    });
  }

  function bindQuestions() {
    els["tab-questions"].addEventListener("click", () => setTab("questions"));
    els["tab-editor"].addEventListener("click", () => setTab("editor"));
    els["q-player"].addEventListener("timeupdate", renderQuestionClock);
    els["q-player"].addEventListener("seeked", renderQuestionClock);
    els["q-player"].addEventListener("loadedmetadata", renderQuestionClock);
    document.querySelectorAll("[data-q-hop]").forEach((button) => button.addEventListener("click", () => {
      const video = els["q-player"];
      if (video.readyState < 1) return;
      video.currentTime = clamp(video.currentTime + Number(button.dataset.qHop), 0, duration());
    }));
    els["q-rewind"].addEventListener("click", () => {
      const item = queue()[state.q.index];
      if (item && els["q-player"].readyState >= 1) els["q-player"].currentTime = rangeFor(item).start;
    });
    els["q-flag"].addEventListener("click", () => {
      const item = queue()[state.q.index];
      if (item && els["q-player"].readyState >= 1) els["q-player"].currentTime = item.source_at;
    });
    els["q-mark-btn"].addEventListener("click", () => {
      const video = els["q-player"];
      if (video.readyState < 1) { say("The video hasn't loaded yet"); return; }
      video.pause();
      draftFor(queue()[state.q.index]).at = round3(video.currentTime);
      renderQuestion();
    });
    els["q-note"].addEventListener("input", () => {
      draftFor(queue()[state.q.index]).note = els["q-note"].value.slice(0, 600);
      renderQuestion();
    });
    els["q-save"].addEventListener("click", saveAnswer);
    els["q-skip"].addEventListener("click", nextQuestion);
    els["q-prev"].addEventListener("click", () => goToQuestion(state.q.index - 1));
    els["q-export"].addEventListener("click", () => els.export.click());
  }

  function startQuestions() {
    const run = state.data.review_run;
    const items = queue();
    els.tabs.hidden = !items.length;
    if (!items.length) { setTab("editor"); return; }
    els["q-footnote"].textContent = run.footnote || ("Round " + run.round + " questions about the existing "
      + "draft clips. The sandbox patch has not been applied to this project.");
    const lists = run.lists || {};
    [["dl-players", lists.players], ["dl-characters", lists.characters], ["dl-rounds", lists.rounds]]
      .forEach(([id, values]) => {
        els[id].innerHTML = "";
        (values || []).forEach((value) => { const o = document.createElement("option"); o.value = value;
                                            els[id].appendChild(o); });
      });
    const first = items.findIndex((item) => !answerFor(item));
    state.q.index = first >= 0 ? first : 0;
    state.q.summary = first < 0;
    setTab(location.hash === "#editor" ? "editor" : "questions");
    if (first < 0) showSummary();
    loadQuestionVideo(items[state.q.index]);
  }

  function renderAll() {
    els["coverage-pct"].textContent = Math.round(state.data.coverage.fraction * 100) + "%";
    els.coverage.querySelector(".coverage-label").innerHTML =
      clock(state.data.coverage.claimed_seconds) + " claimed<br>"
      + clock(state.data.coverage.unclaimed_seconds) + " unclaimed";
    els["about-body"].innerHTML = "Build <b class='mono'>" + (state.data.build || "?")
      + "</b>. Ten draft stream-copy clips from <b>" + state.data.provenance.draft_manifest
      + "</b>, with set bounds from <b>" + state.data.provenance.project + "</b>. Recording is "
      + clock(duration()) + " long. Clips are provisional: grouping, identities and ends are machine output "
      + "with no operator decision and no independent validation, and a stream copy can include surrounding "
      + "footage, so clip-local time is not exact source time. Your verdicts and notes are mock edits kept "
      + "in this prototype's own scratch file; nothing here writes to the recovery project. "
      + "Keyboard: p/n clip, 1–4 verdict, u undo, arrows nudge the start by 1s/5s with shift.";
  }

  // ---------------------------------------------------------------- mark drag
  function timeAt(clientX) {
    const rect = els.lane.getBoundingClientRect();
    const [from, to] = view();
    // The lane can be wider than the viewport when zoomed; map through the lane's own box.
    const fraction = clamp((clientX - rect.left) / rect.width, 0, 1);
    return from + fraction * (to - from);
  }

  function bindLane() {
    let band = null;
    const startDrag = (event) => {
      if (!state.markMode) return;
      event.preventDefault();
      const point = event.touches ? event.touches[0] : event;
      state.drag = { from: timeAt(point.clientX), to: timeAt(point.clientX) };
      band = document.createElement("div");
      band.className = "missing-drag";
      els.lane.appendChild(band);
      moveDrag(event);
    };
    const moveDrag = (event) => {
      if (!state.drag) return;
      const point = event.touches ? event.touches[0] : event;
      state.drag.to = timeAt(point.clientX);
      const low = Math.min(state.drag.from, state.drag.to);
      const high = Math.max(state.drag.from, state.drag.to);
      Object.assign(band.style, position(low, high));
    };
    const endDrag = () => {
      if (!state.drag) return;
      const low = Math.min(state.drag.from, state.drag.to);
      const high = Math.max(state.drag.from, state.drag.to);
      state.drag = null;
      if (band) band.remove();
      band = null;
      if (high - low < 1) {
        sheet("Mark a missing clip", "Drag across the timeline to give the mark a range, or tap a gap to "
              + "use all of it.", []);
        return;
      }
      addMissing(low, high, "marked by dragging the timeline");
      setMarkMode(false);
    };
    els.lane.addEventListener("mousedown", startDrag);
    els.lane.addEventListener("mousemove", moveDrag);
    ["mouseup", "mouseleave"].forEach((name) => els.lane.addEventListener(name, endDrag));
    els.lane.addEventListener("touchstart", startDrag, { passive: false });
    els.lane.addEventListener("touchmove", moveDrag, { passive: false });
    ["touchend", "touchcancel"].forEach((name) => els.lane.addEventListener(name, endDrag));
  }

  function setMarkMode(on) {
    state.markMode = on;
    els["mark-mode"].setAttribute("aria-pressed", String(on));
    els.lane.classList.toggle("marking", on);
    els["mark-help"].textContent = on ? "Dragging marks a range. Tap the button again to leave marking mode."
      : "Drag across the timeline to give it a range.";
    els["timeline-hint"].textContent = on
      ? "Marking mode: drag on the timeline to claim a missing clip."
      : "Tap a strip to play that clip. Tap a gap for source context or to mark a missing clip.";
  }

  // ---------------------------------------------------------------- diagnostics
  // "It's buffering" has at least three causes with different fixes: the pipe cannot keep up, the decoder is
  // dropping frames, or the server is slow to answer a seek. This counts the evidence for each instead of
  // asserting one of them, which is what I did last time.
  const probe = { stalls: 0, stallSeconds: 0, since: 0, waitingStart: 0, seeks: 0, bytes: 0, t0: 0 };

  function bufferedAhead() {
    const media = els.player;
    const at = media.currentTime || 0;
    for (let i = 0; i < media.buffered.length; i += 1) {
      if (media.buffered.start(i) <= at && at <= media.buffered.end(i)) {
        return media.buffered.end(i) - at;
      }
    }
    return 0;
  }

  function decodedFrames() {
    const media = els.player;
    const get = media.getVideoPlaybackQuality && media.getVideoPlaybackQuality();
    return get ? { decoded: get.totalVideoFrames || 0, dropped: get.droppedVideoFrames || 0 }
               : { decoded: media.webkitDecodedFrameCount || 0, dropped: media.webkitDroppedFrameCount || 0 };
  }

  function reportStats(kind, extra) {
    const row = clip();
    // Report the address the player is really fetching, not the mode the interface thinks it is in. The
    // first version of this line sent state.quality, which let a page that had silently kept the 11 MB/s
    // draft file announce itself as "preview" — and I believed it and drew the wrong conclusion about the
    // network from it.
    let loaded = null;
    try {
      loaded = (els.player.currentSrc || els.player.src || "").replace(location.origin, "") || null;
    } catch (err) { loaded = null; }
    const body = Object.assign({
      kind: kind,
      build: state.data ? state.data.build : null,
      at: new Date().toISOString(),
      clip: row ? row.id : null,
      mode: state.mode,
      quality: state.quality,
      loaded: loaded,
      window: state.window ? [Math.round(state.window[0] * 100) / 100,
                              Math.round(state.window[1] * 100) / 100] : null,
      proposed: ((state.notes.items[state.selected] || {}).proposed) || null,
      needs_mbytes_per_second: /prev_/.test(loaded || "") ? (row ? row.preview.mbytes_per_second : null)
        : (state.mode === "clip" && row ? row.delivered.mbytes_per_second
                                        : state.data.source.mbytes_per_second),
      stalls: probe.stalls,
      starved_seconds: Math.round(probe.stallSeconds * 10) / 10,
      observed_seconds: probe.since ? Math.round((Date.now() - probe.since) / 100) / 10 : 0,
      buffered_ahead: Math.round(bufferedAhead() * 10) / 10,
      viewport: window.innerWidth,
      connection: (navigator.connection || {}).effectiveType || null,
      downlink_mbps: (navigator.connection || {}).downlink || null,
    }, extra || {});
    fetch(url("api/clientstat"), { method: "POST", headers: { "Content-Type": "application/json" },
                                   body: JSON.stringify(body) }).catch(() => { /* diagnostics only */ });
  }

  function diagnose() {
    state.diagnosed = true;
    const row = clip();
    const seconds = Math.max(1, (Date.now() - probe.since) / 1000);
    const dropped = decodedFrames();
    // The rate must come from the bytes this particular file holds. An earlier version of this panel divided
    // the planned duration by the container duration and printed 1.2 MB/s for a file that really moves at
    // 11 MB/s, which made the whole report look self-contradictory — and it was.
    const usingPreview = state.quality === "preview" && row && (row.preview || {}).state === "ready";
    const playing = (state.mode === "window" || state.mode === "source")
      ? state.data.source : (row ? (usingPreview ? row.preview : row.delivered) : null);
    const needed = playing ? playing.mbytes_per_second : null;
    const ahead = bufferedAhead();
    const starved = 100 * probe.stallSeconds / seconds;
    const lines = [
      "observed: " + seconds.toFixed(0) + " s since playback began",
      "stalls: " + probe.stalls + ", " + probe.stallSeconds.toFixed(1) + " s waiting ("
        + starved.toFixed(1) + "% of the time)",
      "buffer ahead of playhead: " + ahead.toFixed(1) + " s  →  "
        + (ahead < 2 ? "the download is losing to playback" : "the download is keeping up"),
      "frames: " + dropped.decoded + " decoded, " + dropped.dropped + " dropped  →  "
        + (dropped.dropped / Math.max(1, dropped.decoded) > 0.02
            ? "the decoder is the limit, not the network" : "the decoder is keeping up"),
      needed ? ("what is playing now needs ~" + needed.toFixed(1) + " MB/s ("
                 + (needed * 8).toFixed(0) + " Mbit/s)" + (usingPreview ? "" : "; a 720p preview needs ~3"))
             : "could not work out which file is playing",
    ];
    sheet("What the player is actually doing", lines.join("\n"),
          [{ label: "Test this link (48 MB)", action: runSpeedTest }]);
    els["quality-note"].textContent = lines[1] + " · " + lines[2];
  }

  function runSpeedTest() {
    sheet("Testing the link", "Downloading 48 MB that is never cached. Watch the number: if it is under "
          + "about 11 MB/s, the full-resolution drafts simply cannot be streamed on this connection.", []);
    const started = performance.now();
    fetch(url("api/speedtest?mb=48") + "&_=" + Date.now(), { cache: "no-store" })
      .then((response) => response.arrayBuffer())
      .then((buffer) => {
        const seconds = (performance.now() - started) / 1000;
        const mbps = (buffer.byteLength / 1e6 * 8) / seconds;
        reportStats("link", { bytes: buffer.byteLength, seconds: Math.round(seconds * 10) / 10,
                              mbytes_per_second: Math.round((buffer.byteLength / 1048576 / seconds) * 100) / 100,
                              link_downlink_mbps: (navigator.connection || {}).downlink || null });
        const verdict = (buffer.byteLength / 1048576) / seconds >= 12
          ? "that is enough for the 88 Mbit/s drafts, so the buffering is not the pipe"
          : "not enough for the drafts at 11 MB/s — previews or a lower rate are the fix";
        els["quality-note"].textContent = "link " + mbps.toFixed(0) + " Mbit/s ("
          + ((buffer.byteLength / 1048576) / seconds).toFixed(1) + " MB/s) — " + verdict;
        const enough = (buffer.byteLength / 1048576) / seconds >= 12;
        const actions = enough ? [] : [{ label: "Make a 720p preview of this set", action: () => {
            closeSheet();
            makePreview();
          } }];
        sheet("Measured over this connection",
              (buffer.byteLength / 1048576).toFixed(0) + " MB in " + seconds.toFixed(1) + " s = "
              + mbps.toFixed(0) + " Mbit/s. " + verdict
              + " The drafts are 80-88 Mbit/s because they are stream copies; a 720p preview is about 3, "
              + "which costs one CPU transcode of that set and nothing else.",
              actions);
      })
      .catch((error) => { els["quality-note"].textContent = "speed test failed: " + error.message; });
  }


  // A single-stream bulk fetch, run once on load, is the only way to measure the last hop: the server
  // cannot reach the phone's radio, and per-video-connection rates are shares of this number, not the
  // number itself. It reports home rather than showing anything, so the answer lands in the log.
  function autoProbe() {
    const started = performance.now();
    fetch(url("api/speedtest?mb=24") + "&_=" + Date.now(), { cache: "no-store" })
      .then((response) => response.arrayBuffer())
      .then((buffer) => {
        const seconds = (performance.now() - started) / 1000;
        reportStats("link-probe", { bytes: buffer.byteLength,
                                    seconds: Math.round(seconds * 10) / 10,
                                    mbytes_per_second: Math.round((buffer.byteLength / 1048576 / seconds) * 100) / 100 });
      })
      .catch(() => reportStats("link-probe", { error: "fetch failed" }));
  }


  // ---------------------------------------------------------------- transport
  // Native controls are off, so this layer owns play/pause, scrubbing and skipping. Two details are
  // deliberate: the scrub bar works in the media's own clock (clip-local, source, or cut-window, whichever
  // is loaded) because that is the only timeline the element can seek on, while the card keeps showing the
  // source position; and the chrome never hides during a pause, because a frozen frame with no controls is
  // worse than a covered frame.
  let idleTimer = 0;
  function showChrome() {
    els.chrome.classList.remove("hide");
    els.stage.classList.remove("idle");
    clearTimeout(idleTimer);
    if (!els.player.paused && !els.player.seeking) {
      idleTimer = setTimeout(() => els.chrome.classList.add("hide"), 2800);
    }
  }

  function transportSeek(fraction) {
    const total = els.player.duration;
    if (!Number.isFinite(total) || total <= 0) { say("nothing loaded to seek in"); return; }
    const at = clamp(fraction, 0, 1) * total;
    try { els.player.currentTime = at; } catch (err) { return /* not seekable yet */ }
    renderTransport();
    renderMeta();
  }

  function renderTransport() {
    const media = els.player;
    const total = Number.isFinite(media.duration) ? media.duration : 0;
    const at = Number.isFinite(media.currentTime) ? media.currentTime : 0;
    const pct = total ? (100 * at / total) : 0;
    let end = 0;
    for (let i = 0; i < media.buffered.length; i += 1) end = Math.max(end, media.buffered.end(i));
    els.fill.style.width = pct + "%";
    els.buffered.style.width = (total ? 100 * end / total : 0) + "%";
    els.ctime.textContent = clock(at) + " / " + clock(total);
    els.scrub.setAttribute("aria-valuenow", Math.round(pct));
    els.scrub.setAttribute("aria-valuetext", clock(at) + " of " + clock(total));
    const playing = !media.paused && !media.ended;
    els["big-play"].classList.toggle("playing", playing);
    const glyph = playing ? "M6 5h4v14H6zM14 5h4v14h-4z" : "M8 5v14l11-7z";
    els["c-play"].innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="' + glyph + '"/></svg>';
    els["c-play"].setAttribute("aria-label", playing ? "Pause" : "Play");
    els["c-cut"].setAttribute("aria-pressed", String(state.mode === "window"));
  }

  function hop(seconds, zone) {
    const media = els.player;
    if (!Number.isFinite(media.duration)) { say("nothing loaded to skip in"); return; }
    media.currentTime = clamp(media.currentTime + seconds, 0, media.duration);
    if (zone) {
      zone.classList.remove("hop");
      void zone.offsetWidth;                          // restart the ripple animation on a repeat tap
      zone.classList.add("hop");
      setTimeout(() => zone.classList.remove("hop"), 460);
    }
    renderTransport();
    renderMeta();
    say((seconds > 0 ? "+" : "") + seconds + "s");
  }

  function bindTransport() {
    const toggle = () => { if (els.player.paused) els.player.play().catch(() => {});
                           else els.player.pause(); showChrome(); };
    els["big-play"].addEventListener("click", toggle);
    els["c-play"].addEventListener("click", toggle);
    document.querySelectorAll(".cbtn-hop").forEach((button) =>
      button.addEventListener("click", () => hop(parseFloat(button.dataset.hop))));
    els["c-cut"].addEventListener("click", () => { watchCut(); renderTransport(); });
    els["c-full"].addEventListener("click", () => {
      const node = els.stage;
      if (document.fullscreenElement) document.exitFullscreen();
      else if (node.requestFullscreen) node.requestFullscreen().catch(() => say("fullscreen refused"));
      else if (node.webkitEnterFullscreen) node.webkitEnterFullscreen();
      else say("this browser offered no fullscreen control");
    });

    // Double-tap an edge skips, single tap toggles the chrome: the gesture people already know, and it
    // keeps the tap area out of the way of the scrub rail below it.
    const lastTap = { at: 0 };
    els["tap-layer"].addEventListener("click", (event) => {
      const now = Date.now();
      const double = now - lastTap.at < 320;
      lastTap.at = now;
      const zone = event.target.closest(".tap-zone");
      if (double && zone) {
        hop(zone === els["tap-fwd"] ? 5 : -5, zone);
        return;
      }
      showChrome();
    });
    els["tap-layer"].addEventListener("dblclick", (event) => event.preventDefault());

    let dragging = false;
    const fractionAt = (clientX) => {
      const box = els.scrub.getBoundingClientRect();
      return clamp((clientX - box.left) / Math.max(1, box.width), 0, 1);
    };
    const startDrag = (event) => {
      dragging = true;
      els.scrub.classList.add("dragging");
      els.scrub.setPointerCapture && els.scrub.setPointerCapture(event.pointerId);
      els.fill.style.width = (fractionAt(event.clientX) * 100) + "%";
    };
    els.scrub.addEventListener("pointerdown", startDrag);
    els.scrub.addEventListener("pointermove", (event) => {
      if (!dragging) return;
      els.fill.style.width = (fractionAt(event.clientX) * 100) + "%";
    });
    ["pointerup", "pointercancel"].forEach((name) => els.scrub.addEventListener(name, (event) => {
      if (!dragging) return;
      dragging = false;
      els.scrub.classList.remove("dragging");
      transportSeek(fractionAt(event.clientX));
      showChrome();
    }));
    els.scrub.addEventListener("keydown", (event) => {
      if (event.key === "ArrowRight") hop(5);
      else if (event.key === "ArrowLeft") hop(-5);
      else return;
      event.preventDefault();
    });

    ["pointermove", "pointerdown"].forEach((name) => els.stage.addEventListener(name, showChrome));
    ["play", "pause", "ended", "seeked", "timeupdate", "progress", "loadedmetadata"].forEach((name) =>
      els.player.addEventListener(name, () => { renderTransport(); showChrome(); }));
    renderTransport();
  }

  // ---------------------------------------------------------------- wiring
  function bind() {
    els["prev-clip"].addEventListener("click", () => step(-1));
    els["next-clip"].addEventListener("click", () => step(1));
    function step(direction) {
      const index = state.data.clips.findIndex((row) => row.id === state.selected);
      const next = state.data.clips[clamp(index + direction, 0, state.data.clips.length - 1)];
      if (next) { selectClip(next.id); loadMedia(); }
    }
    // Starvation is counted, not assumed: the number of stalls and the seconds spent waiting are what the
    // "why is it buffering" report is built from, and they separate a small pipe from a slow decoder.
    els.player.addEventListener("playing", () => {
      if (!probe.since) probe.since = Date.now();
      if (probe.waitingStart) {
        probe.stallSeconds += (Date.now() - probe.waitingStart) / 1000;
        probe.waitingStart = 0;
      }
      // One automatic report per half minute of playback. Asking the reviewer to read a phone screen is a
      // worse interface than having the number arrive in the server log on its own.
      clearTimeout(probe.timer);
      probe.timer = setTimeout(() => reportStats("playback"), 30000);
    });
    els.player.addEventListener("waiting", () => {
      probe.stalls += 1;
      if (!probe.waitingStart) probe.waitingStart = Date.now();
      if (!probe.since) probe.since = Date.now();
    });
    els["toggle-mode"].addEventListener("click", watchCut);
    els["confidence-note"].addEventListener("click", () => {
      const row = clip();
      // No `available` guard here: what is missing is itself the thing to explain, and a control that does
      // nothing on a tap is indistinguishable from a broken one.
      if (row) confidenceSheet(row);
    });
    els["mode-scrub"].addEventListener("click", () => {
      if (!state.data.source.media.master) { say("no source stream offered"); return; }
      const wasWindow = state.mode === "window";
      state.mode = state.mode === "source" ? "clip" : "source";
      state.window = null;
      renderPlayer();
      loadMedia(wasWindow ? els.player.currentTime : null);
    });
    els.diagnose.addEventListener("click", diagnose);
    els["preview-toggle"].addEventListener("click", () => setQuality(state.quality === "preview"
                                                                          ? "full" : "preview"));

    // Both boundaries of a nine minute set have to be inspectable from a thumb, and the cut window has no
    // scrub affordance worth reaching on a phone screen. Jumping is the whole point of a clamped window.
    els["edge-start"].addEventListener("click", () => goToEdge("start"));
    els["edge-end"].addEventListener("click", () => goToEdge("end"));
    document.querySelectorAll("[data-edge][data-delta]").forEach((button) => {
      button.addEventListener("click", () => nudge(button.dataset.edge, parseFloat(button.dataset.delta)));
    });
    document.querySelectorAll(".btn-set").forEach((button) => {
      button.addEventListener("click", () => setEdgeAtPlayhead(button.dataset.edge));
    });
    document.querySelectorAll(".btn-verdict").forEach((button) => {
      button.addEventListener("click", () => setStatus(button.dataset.status));
    });
    els["note-input"].addEventListener("input", () => {
      const row = clip();
      if (!row) return;
      noteFor(row.id).note = els["note-input"].value.slice(0, 600);
      scheduleSave();
      renderNotes();
    });
    els.undo.addEventListener("click", () => {
      const previous = state.undoStack.pop();
      if (!previous) { say("nothing to undo"); return; }
      const parsed = JSON.parse(previous);
      state.notes = Object.assign(normalise({ items: parsed.items, missing_clips: parsed.missing }),
                                  { answers: state.notes.answers });
      scheduleSave();
      renderPlayer(); renderList(); renderTimeline(); renderNotes();
      say("undone");
    });
    els.reset.addEventListener("click", () => {
      sheet("Reset every clip-editor edit?", "This clears verdicts, notes, proposed boundaries and missing-clip "
            + "marks in the prototype only. Your answers on the Questions tab are kept. No recovery file is "
            + "touched.",
            [{ label: "Reset notes", action: () => {
                snapshot();
                state.notes = { items: {}, missing_clips: [], answers: state.notes.answers || {} };
                closeSheet();
                return fetch(url("api/notes"), { method: "POST" })
                  .then(() => saveNow())
                  .then(() => { renderPlayer(); renderList(); renderTimeline(); renderNotes(); });
              } }]);
    });
    els.export.addEventListener("click", () => {
      const body = Object.assign({ exported_at: new Date().toISOString(),
                                   source_duration_seconds: duration(),
                                   clips: state.data.clips.map((row) => ({ id: row.id, number: row.number,
                                     requested: row.requested, delivered: row.delivered })) },
                                 state.notes, { kind: "tvh-mobile-review-notes-mock", schema_version: 1,
                                                provenance: state.data.provenance });
      const blob = new Blob([JSON.stringify(body, null, 2)], { type: "application/json" });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "bb9-draft-review-notes.json";
      document.body.appendChild(link);
      link.click();
      link.remove();
      say("notes exported");
    });
    els["zoom-in"].addEventListener("click", () => setZoom(0.5, sourcePlayhead().seconds));
    els["zoom-out"].addEventListener("click", () => setZoom(2, sourcePlayhead().seconds));
    els["zoom-fit-clip"].addEventListener("click", fitToClip);
    els["zoom-all"].addEventListener("click", () => { state.view = null; renderTimeline(); });
    els["mark-mode"].addEventListener("click", () => setMarkMode(!state.markMode));
    els["sheet-backdrop"].addEventListener("click", (event) => {
      if (event.target === els["sheet-backdrop"]) closeSheet();
    });
    els.player.addEventListener("timeupdate", renderMeta);
    els.player.addEventListener("seeked", renderMeta);
    els.player.addEventListener("error", () => {
      els["time-readout"].innerHTML = "<span class='muted'>this browser could not play the draft — try "
        + "Safari, or ask for the low-bandwidth preview</span>";
    });
    els.player.addEventListener("play", () => say(state.mode === "source" ? "playing source"
                                                                          : "playing draft clip"));
    bindLane();
    bindTransport();
    document.addEventListener("keydown", (event) => {
      if (event.target.matches("input, textarea, select")) return;
      if (state.tab !== "editor") return;
      const key = event.key.toLowerCase();
      if (key === "p") step(-1);
      else if (key === "n") step(1);
      else if (key === "u") els.undo.click();
      else if (["1", "2", "3", "4"].includes(key)) {
        const map = { "1": "good", "2": "adjust", "3": "inaccurate", "4": "unsure" };
        setStatus(map[key]);
      } else if (key === "arrowleft") nudge("start", event.shiftKey ? -5 : -1);
      else if (key === "arrowright") nudge("start", event.shiftKey ? 5 : 1);
      else return;
      event.preventDefault();
    });
  }

  // ---------------------------------------------------------------- boot
  function localNotes() {
    try {
      const local = JSON.parse(localStorage.getItem("tvh-mobile-review-notes") || "null");
      return local ? normalise(local) : { items: {}, missing_clips: [], answers: {} };
    } catch (err) { return { items: {}, missing_clips: [], answers: {} }; }
  }

  function boot() {
    Promise.all([fetch(url("api/state")).then((r) => r.json()),
                 fetch(url("api/notes")).then(async (r) => {
                   if (!r.ok) throw new Error("HTTP " + r.status);
                   return { ok: true, notes: await r.json() };
                 }).catch(() => ({ ok: false }))])
      .then(([data, stored]) => {
        state.data = data;
        // The server owns the notes. A device copy is used *only* when the server could not be read at all:
        // an empty result is a legitimate state -- it is what a reset looks like -- and treating it as
        // "nothing yet, try localStorage" resurrects whatever stale proposal the browser happens to be
        // holding. That is how "end" kept landing at ~49s after the server-side notes were cleared: the
        // phone restored a 14-second proposed window and then obeyed it faithfully.
        state.notes = stored.ok ? normalise(stored.notes) : localNotes();
        state.revision = stored.ok ? Number(stored.notes.revision || 0) : 0;
        if (stored.ok) {
          try { localStorage.removeItem("tvh-mobile-review-notes"); } catch (err) { /* not fatal */ }
        }
        state.selected = data.clips.length ? data.clips[0].id : null;
        renderAll();
        renderPlayer();
        renderList();
        renderTimeline();
        renderNotes();
        startQuestions();
        if (state.tab === "editor") loadMedia();
        els["save-state"].textContent = "ready · mock notes, saved to the prototype only";
        setTimeout(autoProbe, 2500);
      })
      .catch((error) => {
        els["clip-title"].textContent = "Could not load";
        els["clip-sub"].textContent = error.message;
      });
  }

  bind();
  bindQuestions();
  boot();
  // The harness needs to read the live window, not infer it from a screenshot.
  window.__tvh = state;

})();
