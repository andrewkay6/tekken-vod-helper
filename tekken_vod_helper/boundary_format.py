"""Which format applies to a candidate set, and what to do when the recording cannot say.

A set is only closed against a target, and until 2026-10-31 the only target this code knew was a single
global ``games_per_set`` -- configured, never inferred, and wrong for most of this recording: Basement
Brawl #9 plays regular sets **first to two games** and reserves first to three for its top four rounds
(Winners Final, Losers Semi-Final, Losers Final, Grand Final). One global 3 reads a completed 2-0 set as
"two games of an unfinished set", which is not a conservative error -- it is a lost set.

So the target comes from the *stage the broadcast names*, per candidate, with three rules:

* **A stage selects its target; nothing selects a target from a score.** A tally is never counted to
  decide how many games a set needed -- that is circular, and the display is measured incrementing a
  concluded game's credit 34-42 s late.
* **A disputed or unreadable stage keeps alternatives rather than picking.** The band is measured naming
  the previous match inside a live game's span (``LOSERS QUARTER FINAL`` at 3,786.783 s over a game whose
  seats are the Winners Final's), so "the label at the start of the span" cannot be trusted alone. Every
  stage the candidate's own readings name yields its own verdict, and a set is only reported complete when
  all of them agree.
* **Format stays optional.** With no policy, no stage vocabulary, or a stage outside it, the caller can
  pass ``games_per_set`` as before, or nothing at all -- and then no candidate closes and the report says
  so. Format-removed is a supported mode, not a degraded one: the workflow has to recover sets without
  knowing the rules too.

Nothing here sends a request or reads pixels: stages arrive as text from answers already on disk.
"""
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_GAMES_TO_WIN = 2

# The rounds the user confirmed as first-to-three, normalized the way ``normalize_stage`` writes them.
TOP_FOUR_STAGES = ("winners final", "losers semi-final", "losers semifinal", "losers final",
                   "grand final")

# Written by hand from the confirmed rules, so a missing or half-written policy file cannot silently
# change a target: the file is the source, this is only what "no policy supplied" means.
STAGE_TARGETS = {stage: 3 for stage in TOP_FOUR_STAGES}

_KINDS = ("quarter-final", "quarterfinal", "semi-final", "semifinal", "final",
          "round 1", "round 2", "round 3", "pools final", "grand final")


def normalize_stage(text):
    # type: (Any) -> Optional[str]
    """The bracket round a band label names, or ``None`` when the label does not name one.

    The ``label`` field is not a stage field. It holds the whole top strip as the model transcribed it --
    event name, then stage -- so it arrives as ``"Basement Brawl #9 (CAFE EDITION) (Pre-Pre-Clash-Cla...
    LOSERS QUARTER FINAL"``, with the event noise, an ellipsis, and a hyphen that comes and goes between
    reads of the same screen. So this searches for the round phrase instead of matching the string, and
    returns a canonical spelling that ``winners quarter-final``/``WINNERS QUARTERFINAL`` both reduce to.

    A label that names no round is *unknown*, not ``"final"``: ``"... (Pre-Pre-Clash-Cla...)"`` contains
    the letters of a round in the event title and must not select a target.
    """
    if not text:
        return None
    raw = str(text).lower().replace("‑", "-")
    # The event's own title can contain a stage word ("... CLASH CLASH"): only text after the last
    # separator counts, because the band prints the round at the end of the strip.
    tail = raw.rsplit(")", 1)[-1] if ")" in raw else raw
    found = []
    for kind in _KINDS:
        # The band prints the round with an unstable separator: "QUARTER-FINAL", "QUARTER FINAL" and
        # "QUARTERFINAL" are all reads of the same two words, so every spelling of every round is tried.
        for candidate in (kind, kind.replace("-", " "), kind.replace("-", "")):
            index = tail.find(candidate)
            if index >= 0:
                found.append((index, kind))
                break
    if not found:
        return None
    # The round closest to the end of the strip wins: "LOSERS QUARTER FINAL" holds both "final" and
    # "quarter final", and the longer phrase starting earlier is the round, not the suffix.
    index, kind = min(found, key=lambda pair: pair[0])
    kind = kind.replace("quarterfinal", "quarter-final").replace("semifinal", "semi-final")
    bracket = ""
    for word in ("winners", "losers", "grand", "pools"):
        if word in tail[max(0, index - 12):index]:
            bracket = word + " "
            break
    return (bracket + kind).strip()


def load_policy(path):
    # type: (Any) -> Optional[Dict[str, Any]]
    """Read a user-confirmed event format file, or ``None`` when no policy was supplied.

    Malformed content raises rather than defaults: a policy that failed to parse and quietly became
    "first to two everywhere" would close or open sets by a typo.
    """
    if path in (None, "", False):
        return None
    import json
    from pathlib import Path

    data = json.loads(Path(str(path)).read_text(encoding="utf-8"))
    if data.get("kind") != "user-confirmed-event-format":
        raise ValueError("{} is not a user-confirmed event format file".format(path))
    default = data.get("default_games_to_win")
    if not isinstance(default, int) or default < 1:
        raise ValueError("policy has no usable default_games_to_win")
    overrides = {}
    for entry in data.get("stage_overrides") or []:
        stage = normalize_stage(entry.get("stage"))
        games = entry.get("games_to_win")
        if not stage or not isinstance(games, int) or games < 1:
            raise ValueError("policy override without a stage or a target: {!r}".format(entry))
        overrides[stage] = games
    return {"default_games_to_win": default, "overrides": overrides,
            "source": str(path), "event": data.get("event"),
            "confirmed_at": data.get("confirmed_at"),
            "grand_final_reset": data.get("grand_final_reset"),
            "bracket_results_verified": bool(data.get("bracket_results_verified"))}


def target_for_stage(stage, policy=None):
    # type: (Optional[str], Optional[Dict[str, Any]]) -> Optional[int]
    """How many games a set needs in one named stage, or ``None`` when the stage is unknown.

    An unknown stage falls back to the policy's default only when the policy is present -- the file says
    regular sets are first to two, so a round outside the named top four is *regular*, which is a reading
    the policy supports rather than a guess. A stage the policy never mentions with no vocabulary to
    place it returns None, because then nothing says which default applies.
    """
    if policy is None:
        return None
    if stage is None:
        return None
    if stage in policy["overrides"]:
        return policy["overrides"][stage]
    if any(stage.endswith(kind) for kind in ("final", "quarter-final", "semi-final")):
        return policy["default_games_to_win"]
    return policy["default_games_to_win"]


def applicable_formats(stages, policy=None, games_per_set=None):
    # type: (Iterable[Tuple[Any, Optional[str]]], Optional[Dict[str, Any]], Optional[int]) -> Dict[str, Any]
    """Every target this candidate could be measured against, with the reading that produced each.

    ``stages`` is ``(at, stage_text)`` pairs from the band readings inside the candidate's own span. Three
    cases, deliberately different:

    * **a stage is named** -- its target, or several when the readings inside one span disagree (they do:
      the band is measured naming the previous match inside a live game). ``ambiguous`` says so, and every
      target gets its own verdict.
    * **no stage is named and a policy exists** -- the policy admits more than one target, so all of them
      are kept as alternatives. A 2-0 candidate then reports "closed under first-to-two, not under
      first-to-three" rather than either pretending the set is over or pretending it is not.
    * **no stage and no policy** -- nothing is measured against anything. That is the format-removed
      comparison, and it is reported as such: it shows how much of a closure came from the recording and
      how much came from being told the rules.
    """
    seen = {}      # type: Dict[int, List[Tuple[Any, str]]]
    unknown = []   # type: List[Any]
    for at, text in stages:
        stage = normalize_stage(text)
        target = target_for_stage(stage, policy)
        if target is None:
            if at is not None:
                unknown.append(at)
            continue
        seen.setdefault(target, []).append((at, stage or "unnamed round"))
    targets = sorted(seen)
    basis = "stage named by the band inside this candidate's span"
    if not targets and games_per_set:
        targets = [int(games_per_set)]
        seen[targets[0]] = [("configured", "games_per_set from the caller")]
        basis = "configured by the caller, no stage reading agreed or disagreed"
    elif not targets and policy is not None:
        targets = sorted({policy["default_games_to_win"]} | set(policy["overrides"].values()))
        seen = {target: [(None, "no stage named in this span")] for target in targets}
        basis = "no stage reading inside the span: every target the event's format admits is kept"
    elif not targets:
        basis = "no format supplied and no stage named: nothing can close, which is the comparison"
    return {"targets": targets,
            "basis": basis,
            "evidence": {str(target): [{"at": at, "stage": stage} for at, stage in seen[target]]
                         for target in targets},
            "ambiguous": len(targets) > 1,
            "format_known": bool(targets),
            "readings_without_a_stage": sorted(str(round(float(x), 3)) for x in unknown),
            "policy_source": (policy or {}).get("source"),
            "default_games_to_win": (policy or {}).get("default_games_to_win"),
            "stage_vocabulary_applied": policy is not None}
