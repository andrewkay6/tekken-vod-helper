"""Temporal reasoning for boundary proposals using a vision-language model."""
import hashlib
import json
import os
import math
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_MAX_INPUT_CHARS = 500_000
_ALGORITHM = "temporal-qwen-v1"

_SYSTEM_PROMPT = (
    "You are a temporal boundary adjudicator for Tekken tournament VODs.\n"
    "Given a chronological sequence of classified observations, infer tournament SETS.\n"
    "A SET is a completed bracket match that may contain multiple games.\n\n"
    "Rules:\n"
    "1. A SET begins at the earliest confident observation within the final real stage-selection episode immediately before actual gameplay begins.\n"
    "2. Rematch menus, controller assignment dialogs, and VS/loading screens do NOT imply a set has ended.\n"
    "3. A controller prompt after a plausible completed set, especially before new opponents appear or a score reset, strengthens the case for set end.\n"
    "4. A Grand Final reset (same players, score reset to 0-0) is a separate set; require score/sequence evidence.\n"
    "5. Warmups, aborted attempts (no observed gameplay), and setup-only sequences are NOT bracket sets.\n"
    "6. If evidence is unknown or insufficient, mark disposition as uncertain or aborted.\n"
    "Broadcast names and set scores can update late during continuous gameplay. A metadata update alone does not establish a new set. Track the observed character and game sequence across such updates; an earlier stage-select and later controller prompt can bound the same set despite stale intermediate overlays. Explain the evidence and retain uncertainty when play continuity is unresolved.\n"
    "7. Do NOT invent timestamps, player names, or characters not present in the observations.\n"
    "8. Candidate proposals MUST: start at a confident stage_select observation (confidence >= 0.5), "
    "include at least one observed gameplay observation between start and end, "
    "and end at a controller_prompt observation (not controller_disconnected or assignment alone).\n\n"
    "Output: a strict JSON array of proposal objects with exactly these fields:\n"
    '[{"start_id": "<observation_id>", "end_id": "<observation_id>", '
    '"evidence_ids": ["<id>", ...], "disposition": "candidate|uncertain|aborted", '
    '"reason": "<brief explanation>"}]\n\n'
    "Use only observation_ids from the input. Do not reference historical data, window counts, "
    "or expected match counts.\n"
    "Respond with a JSON array only. No markdown fences, no text outside the JSON."
)


class TemporalAdjudicationError(Exception):
    """Raised when temporal adjudication fails; must not look successful."""
    pass


def _gameplay_metadata(segment):
    import collections
    rows = [r for r in segment if r.get("confidence", 0) >= 0.5]
    out = {}
    pairs = []
    for r in rows:
        p1 = r.get("player1")
        p2 = r.get("player2")
        if not isinstance(p1, str) or not isinstance(p2, str):
            continue
        n1, n2 = p1.strip(), p2.strip()
        if n1 and n2:
            pairs.append((r.get("timestamp"), n1, n2))
    seen = {}
    for ts, n1, n2 in pairs:
        key = tuple(sorted((n1.casefold(), n2.casefold())))
        if ts not in seen.get(key, {}):
            seen.setdefault(key, {})[ts] = (n1, n2)
    best_key, best_ts, best_pair = None, -1, None
    for key, ts_map in seen.items():
        if len(ts_map) >= 2:
            latest = max(ts_map.keys())
            if latest > best_ts:
                best_key, best_ts, best_pair = key, latest, ts_map[latest]
    if best_pair:
        cf1, cf2 = sorted((n1.casefold(), n2.casefold()))
        out["player1"], out["player2"] = (best_pair[0], best_pair[1]) if best_pair[0].casefold() == cf1 else (best_pair[1], best_pair[0])
    for key, field in [("character1", "character1"), ("character2", "character2")]:
        vals = []
        for r in rows:
            v = r.get(field)
            if isinstance(v, str) and v.strip():
                vals.append((r.get("timestamp"), v.strip()))
        if vals:
            counts = collections.Counter(t.casefold() for _, t in vals)
            max_c = max(counts.values())
            tied = [k for k, c in counts.items() if c == max_c]
            latest_ts, latest_str = max(((ts, s) for ts, s in vals if s.casefold() in tied), key=lambda x: x[0])
            out[key] = latest_str
    return out


class TemporalAdjudicator:
    """Temporal boundary adjudicator using a local vision-language model."""

    def __init__(self, endpoint, model, max_requests=1, timeout=300, output_dir=None):
        base = endpoint.rstrip("/")
        self._endpoint = (base if base.endswith("/chat/completions") else
                          base + "/chat/completions" if base.endswith("/v1") else
                          base + "/v1/chat/completions")
        self._model = model
        self._max_requests = max(1, int(max_requests))
        self._timeout = timeout
        self._output_dir = Path(output_dir) if output_dir else None
        self._calls = 0
        self._usage = {"total_calls": 0, "total_tokens": 0}

    @property
    def calls(self):
        # type: () -> int
        return self._calls

    @property
    def usage(self):
        # type: () -> Dict[str, Any]
        return dict(self._usage)

    def propose(self, timeline, policy_text):
        # type: (List[Dict[str, Any]], str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]
        """Propose temporal boundaries from a chronological classified timeline.

        Args:
            timeline: List of dicts with at least 'timestamp', 'state', 'confidence'.
            policy_text: Cutting policy text included in the prompt context.

        Returns:
            (matches, review_items, provenance)
        """
        if not timeline:
            raise TemporalAdjudicationError("Empty timeline")

        # Assign unique observation IDs (existing per-window IDs may duplicate)
        obs = []  # type: List[Dict[str, Any]]
        for i, row in enumerate(timeline):
            oid = "obs_{:04d}".format(i)
            entry = {
                "observation_id": oid,
                "timestamp": row.get("timestamp"),
                "state": row.get("state", "unknown"),
                "confidence": row.get("confidence", 0.0),
                "player1": row.get("player1", ""),
                "player2": row.get("player2", ""),
                "character1": row.get("character1", ""),
                "character2": row.get("character2", ""),
                "evidence": row.get("evidence", ""),
            }
            for key in ('score1', 'score2'):
                value = row.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    entry[key] = value
            obs.append(entry)

        # Validate finite timestamps
        for o in obs:
            ts = o["timestamp"]
            if not isinstance(ts, (int, float)) or isinstance(ts, bool) or not math.isfinite(ts):
                raise TemporalAdjudicationError(
                    "Non-finite timestamp at observation {}".format(o["observation_id"])
                )

        # Build user message
        obs_json = json.dumps({"observations": obs}, ensure_ascii=False)
        if policy_text:
            user_content = "Policy context:\n{}\n\nData:\n{}".format(policy_text, obs_json)
        else:
            user_content = obs_json

        # Bound input characters
        total_chars = len(_SYSTEM_PROMPT) + len(user_content)
        if total_chars > _MAX_INPUT_CHARS:
            raise TemporalAdjudicationError(
                "Input too large: {} chars exceeds limit of {}".format(total_chars, _MAX_INPUT_CHARS)
            )

        # Call model
        raw_response = self._call_model(user_content)

        # Parse
        proposals = self._parse_response(raw_response)

        # Validate
        obs_by_id = {o["observation_id"]: o for o in obs}
        obs_order = {o["observation_id"]: i for i, o in enumerate(obs)}
        validated = self._validate_proposals(proposals, obs_by_id, obs_order, obs)

        # Separate into matches and review
        matches = []  # type: List[Dict[str, Any]]
        review_items = []  # type: List[Dict[str, Any]]
        for p in validated:
            if p["disposition"] == "candidate":
                m = self._build_match(p, obs)
                matches.append(m)
                review_items.append({
                    "type": "bracket_validity_unverified",
                    "start": m["start"],
                    "end": m["end"],
                    "reason": "Temporal adjudication proposes this as a candidate set; "
                              "screen-state evidence does not verify completed bracket status.",
                    "disposition": "candidate",
                })
            else:
                review_items.append({
                    "type": "temporal_review",
                    "disposition": p["disposition"],
                    "start_id": p["start_id"],
                    "end_id": p["end_id"],
                    "evidence_ids": p.get("evidence_ids", []),
                    "reason": p.get("reason", ""),
                })

        # Validate no overlaps in final matches
        self._validate_no_overlap(matches)

        # Provenance
        prompt_fingerprint = hashlib.sha256(
            (_SYSTEM_PROMPT + "\n" + user_content).encode("utf-8")
        ).hexdigest()[:32]

        provenance = {
            "algorithm": _ALGORITHM,
            "prompt_fingerprint": prompt_fingerprint,
            "usage": dict(self._usage),
            "model": self._model,
            "endpoint": self._endpoint,
            "raw_response_length": len(raw_response) if raw_response else 0,
            "observations_count": len(obs),
            "proposals_count": len(validated),
            "candidate_count": len(matches),
        }

        # Save adjudication artifacts (no signed URLs, no base64)
        if self._output_dir is not None:
            self._output_dir.mkdir(parents=True, exist_ok=True)
            artifacts = {
                "algorithm": _ALGORITHM,
                "prompt_fingerprint": prompt_fingerprint,
                "usage": dict(self._usage),
                "model": self._model,
                "observations_count": len(obs),
                "proposals": validated,
                "raw_response": raw_response,
            }
            artifact_path = self._output_dir / "adjudication.json"
            artifact_path.write_text(
                json.dumps(artifacts, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8"
            )

        return matches, review_items, provenance

    def _call_model(self, user_content):
        # type: (str) -> str
        if self._calls >= self._max_requests:
            raise TemporalAdjudicationError("Model request budget exhausted ({} max)".format(self._max_requests))
        self._calls += 1

        payload = {
            "model": self._model,
            "reasoning_effort": "low",
            "max_tokens": 12000,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self._endpoint,
            data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + os.environ.get("QWEN_API_KEY", "local")},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise TemporalAdjudicationError(
                "Model endpoint HTTP error {} {}".format(exc.code, exc.reason)
            )
        except urllib.error.URLError as exc:
            raise TemporalAdjudicationError(
                "Model endpoint unreachable: {}".format(exc.reason)
            )
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise TemporalAdjudicationError("Model returned invalid JSON response")
        except OSError as exc:
            raise TemporalAdjudicationError(
                "Model endpoint connection error: {}".format(exc)
            )

        # Preserve received output even if content extraction or proposal parsing fails.
        if self._output_dir is not None:
            self._output_dir.mkdir(parents=True, exist_ok=True)
            (self._output_dir / 'adjudication-response.json').write_text(
                json.dumps(data, indent=2), encoding='utf-8')

        # Extract content from response
        content = ""
        if isinstance(data, dict):
            choices = data.get("choices", [])
            if choices and isinstance(choices, list):
                msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
                content = msg.get("content", "") if isinstance(msg, dict) else ""
            if not content:
                content = data.get("content", data.get("text", ""))
        elif isinstance(data, list):
            content = str(data[0]) if data else ""
        else:
            content = str(data)

        # Track usage
        if isinstance(data, dict):
            usage = data.get("usage", {})
            if isinstance(usage, dict):
                self._usage["total_calls"] = self._calls
                self._usage["total_tokens"] = usage.get("total_tokens", 0)

        if not content or not content.strip():
            raise TemporalAdjudicationError("Model returned empty response")

        return content

    def _parse_response(self, raw):
        # type: (str) -> List[Dict[str, Any]]
        text = raw.strip()
        # Strip markdown code fences if the model added them despite instructions
        if text.startswith("```"):
            lines = text.split("\n")
            end_idx = len(lines) - 1
            for i in range(1, len(lines)):
                if lines[i].strip() == "```":
                    end_idx = i
                    break
            text = "\n".join(lines[1:end_idx])
            text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise TemporalAdjudicationError(
                "Model response is not valid JSON: {}...".format(text[:200])
            )

        if not isinstance(data, list):
            raise TemporalAdjudicationError(
                "Expected JSON array of proposals, got {}".format(type(data).__name__)
            )

        proposals = []  # type: List[Dict[str, Any]]
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                raise TemporalAdjudicationError(
                    "Proposal index {} is not a JSON object".format(i)
                )
            required = {"start_id", "end_id", "disposition", "reason"}
            missing = required - set(item.keys())
            if missing:
                raise TemporalAdjudicationError(
                    "Proposal index {} missing required fields: {}".format(
                        i, ", ".join(sorted(missing))
                    )
                )
            disp = item["disposition"]
            if disp not in ("candidate", "uncertain", "aborted"):
                raise TemporalAdjudicationError(
                    "Proposal index {} has invalid disposition '{}'".format(i, disp)
                )
            ev_ids = item.get("evidence_ids", [])
            if not isinstance(ev_ids, list):
                raise TemporalAdjudicationError(
                    "Proposal index {} evidence_ids must be a list".format(i)
                )
            proposals.append({
                "start_id": str(item["start_id"]),
                "end_id": str(item["end_id"]),
                "evidence_ids": [str(x) for x in ev_ids],
                "disposition": disp,
                "reason": str(item["reason"]),
            })

        return proposals

    def _validate_proposals(self, proposals, obs_by_id, obs_order, obs):
        # type: (List[Dict[str, Any]], Dict[str, Dict], Dict[str, int], List[Dict[str, Any]]) -> List[Dict[str, Any]]
        obs_ids = set(obs_by_id.keys())

        for i, p in enumerate(proposals):
            sid, eid = p["start_id"], p["end_id"]

            # Unknown ID check
            if sid not in obs_ids:
                raise TemporalAdjudicationError(
                    "Proposal index {} references unknown start_id '{}'".format(i, sid)
                )
            if eid not in obs_ids:
                raise TemporalAdjudicationError(
                    "Proposal index {} references unknown end_id '{}'".format(i, eid)
                )

            # Evidence ID check
            for ev_id in p["evidence_ids"]:
                if ev_id not in obs_ids:
                    raise TemporalAdjudicationError(
                        "Proposal index {} references unknown evidence_id '{}'".format(i, ev_id)
                    )

            # Ordering check
            if obs_order[sid] >= obs_order[eid]:
                raise TemporalAdjudicationError(
                    "Proposal index {}: start_id '{}' (order {}) is not before end_id '{}' (order {})".format(
                        i, sid, obs_order[sid], eid, obs_order[eid]
                    )
                )

            # Finite timestamp check
            s_ts = obs_by_id[sid]["timestamp"]
            e_ts = obs_by_id[eid]["timestamp"]
            if not math.isfinite(s_ts) or not math.isfinite(e_ts):
                raise TemporalAdjudicationError(
                    "Proposal index {} has non-finite timestamps".format(i)
                )
            if s_ts >= e_ts:
                raise TemporalAdjudicationError(
                    "Proposal index {}: start timestamp {} >= end timestamp {}".format(i, s_ts, e_ts)
                )

            # Candidate-specific validation
            if p["disposition"] == "candidate":
                start_obs = obs_by_id[sid]
                end_obs = obs_by_id[eid]

                if start_obs["state"] != "stage_select":
                    raise TemporalAdjudicationError(
                        "Proposal index {}: candidate start must be stage_select, got '{}'".format(
                            i, start_obs["state"]
                        )
                    )
                if start_obs["confidence"] < 0.5:
                    raise TemporalAdjudicationError(
                        "Proposal index {}: candidate start stage_select confidence {} < 0.5".format(
                            i, start_obs["confidence"]
                        )
                    )
                if end_obs["state"] != "controller_prompt":
                    raise TemporalAdjudicationError(
                        "Proposal index {}: candidate end must be controller_prompt, got '{}'".format(
                            i, end_obs["state"]
                        )
                    )
                # Must include at least one gameplay observation
                has_gameplay = False
                for j in range(obs_order[sid], obs_order[eid] + 1):
                    if obs[j]["state"] == "gameplay":
                        has_gameplay = True
                        break
                if not has_gameplay:
                    raise TemporalAdjudicationError(
                        "Proposal index {}: candidate has no observed gameplay between start and end".format(i)
                    )

        # Anchor to the beginning of the observed final stage-selection episode.
        for p in proposals:
            if p['disposition'] != 'candidate':
                continue
            start = obs_order[p['start_id']]
            while start > 0:
                previous = obs[start - 1]
                if previous['state'] != 'stage_select' or previous['confidence'] < 0.5:
                    break
                start -= 1
                p['start_id'] = previous['observation_id']

        # Evidence validation before overlap processing
        for p in proposals:
            if p['disposition'] != 'candidate':
                continue
            start_idx = obs_order[p['start_id']]
            end_idx = obs_order[p['end_id']] + 1
            segment = obs[start_idx:end_idx]
            zero_pair_count = 0
            has_positive = False
            seen_timestamps = set()
            for o in segment:
                ts = o.get('timestamp')
                s1 = o.get('score1')
                s2 = o.get('score2')
                valid1 = isinstance(s1, int) and not isinstance(s1, bool) and s1 >= 0
                valid2 = isinstance(s2, int) and not isinstance(s2, bool) and s2 >= 0
                if valid1 and valid2 and s1 == 0 and s2 == 0 and ts not in seen_timestamps:
                    zero_pair_count += 1
                    seen_timestamps.add(ts)
                if (valid1 and s1 > 0) or (valid2 and s2 > 0):
                    has_positive = True
            if zero_pair_count >= 2 and not has_positive:
                p['disposition'] = 'uncertain'
                prev_reason = p.get('reason', '')
                new_reason = 'Repeated 0-0 broadcast scores without observed set-score progression; bracket status uncertain.'
                p['reason'] = (prev_reason + ' ' + new_reason).strip() if prev_reason else new_reason
        # Overlap check among candidates
        candidates = [p for p in proposals if p["disposition"] == "candidate"]
        candidates.sort(key=lambda p: obs_order[p["start_id"]])
        for i in range(1, len(candidates)):
            prev_end_order = obs_order[candidates[i - 1]["end_id"]]
            curr_start_order = obs_order[candidates[i]["start_id"]]
            if curr_start_order <= prev_end_order:
                raise TemporalAdjudicationError(
                    "Candidate proposals overlap: end_id '{}' (order {}) vs start_id '{}' (order {})".format(
                        candidates[i - 1]["end_id"], prev_end_order,
                        candidates[i]["start_id"], curr_start_order
                    )
                )

        return proposals

    def _build_match(self, proposal, obs):
        # type: (Dict[str, Any], List[Dict[str, Any]]) -> Dict[str, Any]
        obs_by_id = {o["observation_id"]: o for o in obs}
        start_idx = next(i for i, o in enumerate(obs) if o["observation_id"] == proposal["start_id"])
        end_idx = next(i for i, o in enumerate(obs) if o["observation_id"] == proposal["end_id"])

        segment = obs[start_idx:end_idx + 1]
        p1 = p2 = c1 = c2 = ""
        for o in segment:
            if o.get("player1") and not p1:
                p1 = o["player1"]
            if o.get("player2") and not p2:
                p2 = o["player2"]
            if o.get("character1") and not c1:
                c1 = o["character1"]
            if o.get("character2") and not c2:
                c2 = o["character2"]

        metadata = _gameplay_metadata(segment)
        p1, p2 = metadata.get('player1', p1), metadata.get('player2', p2)
        c1, c2 = metadata.get('character1', c1), metadata.get('character2', c2)
        return {
            "start": obs[start_idx]["timestamp"],
            "end": obs[end_idx]["timestamp"],
            "player1": p1,
            "player2": p2,
            "character1": c1,
            "character2": c2,
            "round_name": "",
            "notes": proposal.get("reason", ""),
        }

    def _validate_no_overlap(self, matches):
        # type: (List[Dict[str, Any]]) -> None
        if len(matches) <= 1:
            return
        sorted_matches = sorted(matches, key=lambda m: m["start"])
        for i in range(1, len(sorted_matches)):
            if sorted_matches[i]["start"] < sorted_matches[i - 1]["end"]:
                raise TemporalAdjudicationError(
                    "Match {} (start={}) overlaps with match {} (end={})".format(
                        i, sorted_matches[i]["start"], i - 1, sorted_matches[i - 1]["end"]
                    )
                )
