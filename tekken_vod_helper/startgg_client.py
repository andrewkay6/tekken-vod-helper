import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List


STARTGG_URL = "https://api.start.gg/gql/alpha"


class StartggError(Exception):
    pass


@dataclass
class BracketSet:
    id: str
    event_name: str
    round_name: str
    player1: str
    player2: str
    state: int
    round: int = 0
    parent_set_ids: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        players = "{} vs {}".format(self.player1 or "TBD", self.player2 or "TBD")
        parts = [self.event_name, self.round_name, players]
        return " - ".join([part for part in parts if part])


@dataclass
class TournamentSummary:
    name: str
    slug: str
    start_at: int = 0

    @property
    def label(self) -> str:
        return "{} ({})".format(self.name, self.slug)


def normalize_tournament_slug(value: str) -> str:
    value = value.strip()
    match = re.search(r"start\.gg/(?:tournament/)?([^/?#]+)", value)
    if match:
        return "tournament/{}".format(match.group(1))
    if value.startswith("tournament/"):
        return value
    return "tournament/{}".format(value) if value else ""


def fetch_bracket_sets(token: str, tournament_slug: str) -> List[BracketSet]:
    if not token.strip():
        raise StartggError("Enter a start.gg token first.")
    slug = normalize_tournament_slug(tournament_slug)
    if not slug:
        raise StartggError("Enter a start.gg tournament slug or URL first.")

    query = """
query TournamentSets($slug: String!) {
  tournament(slug: $slug) {
    events {
      name
      sets(page: 1, perPage: 120, sortType: STANDARD) {
        nodes {
          id
          fullRoundText
          round
          state
          slots {
            prereqId
            prereqType
            prereqPlacement
            entrant {
              id
              name
            }
          }
        }
      }
    }
  }
}
"""
    payload = _graphql(token, query, {"slug": slug})
    tournament = payload.get("data", {}).get("tournament")
    if not tournament:
        raise StartggError("No tournament found for {}.".format(slug))

    return parse_tournament_sets(tournament)


def fetch_owned_tournaments(token: str, per_page: int = 50) -> List[TournamentSummary]:
    if not token.strip():
        raise StartggError("Enter a start.gg token first.")

    user_query = """
query CurrentUser {
  currentUser {
    id
  }
}
"""
    user_payload = _graphql(token, user_query, {})
    user_id = user_payload.get("data", {}).get("currentUser", {}).get("id")
    if not user_id:
        raise StartggError("Could not read current start.gg user from this token.")

    query = """
query TournamentsByOwner($page: Int!, $perPage: Int!, $ownerId: ID!) {
  tournaments(query: {
    perPage: $perPage
    page: $page
    sortBy: "startAt desc"
    filter: {
      ownerId: $ownerId
    }
  }) {
    pageInfo {
      totalPages
    }
    nodes {
      name
      slug
      startAt
    }
  }
}
"""
    results: List[TournamentSummary] = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        payload = _graphql(token, query, {"ownerId": user_id, "perPage": per_page, "page": page})
        tournaments = payload.get("data", {}).get("tournaments", {}) or {}
        page_info = tournaments.get("pageInfo", {}) or {}
        total_pages = int(page_info.get("totalPages", total_pages) or total_pages)
        nodes = tournaments.get("nodes", []) or []
        for node in nodes:
            if isinstance(node, dict) and node.get("slug"):
                results.append(
                    TournamentSummary(
                        name=str(node.get("name", "") or ""),
                        slug=str(node.get("slug", "") or ""),
                        start_at=int(node.get("startAt", 0) or 0),
                    )
                )
        page += 1
    return results


def parse_tournament_sets(tournament: Dict[str, Any]) -> List[BracketSet]:
    results: List[BracketSet] = []
    for event in tournament.get("events", []) or []:
        if not isinstance(event, dict):
            continue
        event_name = str(event.get("name", "") or "")
        for node in event.get("sets", {}).get("nodes", []) or []:
            if not isinstance(node, dict):
                continue
            slots = node.get("slots", []) or []
            names = [_slot_name(slot) for slot in slots[:2]]
            while len(names) < 2:
                names.append("")
            results.append(
                BracketSet(
                    id=str(node.get("id", "") or ""),
                    event_name=event_name,
                    round_name=str(node.get("fullRoundText", "") or ""),
                    player1=names[0],
                    player2=names[1],
                    state=int(node.get("state", 0) or 0),
                    round=int(node.get("round", 0) or 0),
                    parent_set_ids=_slot_parent_set_ids(slots),
                )
            )
    return sorted(results, key=lambda item: (item.state not in (1, 2), item.event_name.casefold(), item.round_name, item.id))


def _slot_name(slot: Dict[str, Any]) -> str:
    entrant = slot.get("entrant") if isinstance(slot, dict) else None
    if not isinstance(entrant, dict):
        return ""
    return _clean_entrant_name(str(entrant.get("name", "") or ""))


def _clean_entrant_name(name: str) -> str:
    cleaned = re.sub(r"\s*\[preview_[^\]]+\]", "", name)
    return re.sub(r"\s+", " ", cleaned).strip()


def _slot_parent_set_ids(slots: List[Dict[str, Any]]) -> List[str]:
    result: List[str] = []
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        prereq_id = slot.get("prereqId")
        prereq_type = str(slot.get("prereqType", "") or "").casefold()
        if prereq_id is not None and prereq_type == "set":
            result.append(str(prereq_id))
    return result


def _graphql(token: str, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        STARTGG_URL,
        data=body,
        headers={
            "Authorization": "Bearer " + token.strip(),
            "Content-Type": "application/json",
            "User-Agent": "TekkenVodHelper/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise StartggError("start.gg returned HTTP {}: {}".format(exc.code, detail)) from exc
    except Exception as exc:
        raise StartggError("Could not fetch start.gg bracket: {}".format(exc)) from exc
    if payload.get("errors"):
        raise StartggError(str(payload["errors"][0].get("message", payload["errors"][0])))
    return payload
