import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
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
    character1: str = ""
    character2: str = ""

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
          state
          slots {
            entrant {
              id
              name
            }
          }
          games {
            selections {
              entrant {
                id
              }
              character {
                name
              }
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
            entrant_ids = [_slot_entrant_id(slot) for slot in slots[:2]]
            while len(names) < 2:
                names.append("")
            while len(entrant_ids) < 2:
                entrant_ids.append("")
            characters_by_entrant = _latest_characters_by_entrant(node)
            results.append(
                BracketSet(
                    id=str(node.get("id", "") or ""),
                    event_name=event_name,
                    round_name=str(node.get("fullRoundText", "") or ""),
                    player1=names[0],
                    player2=names[1],
                    state=int(node.get("state", 0) or 0),
                    character1=characters_by_entrant.get(entrant_ids[0], ""),
                    character2=characters_by_entrant.get(entrant_ids[1], ""),
                )
            )
    return sorted(results, key=lambda item: (item.state not in (1, 2), item.event_name.casefold(), item.round_name, item.id))


def _slot_name(slot: Dict[str, Any]) -> str:
    entrant = slot.get("entrant") if isinstance(slot, dict) else None
    if not isinstance(entrant, dict):
        return ""
    return str(entrant.get("name", "") or "")


def _slot_entrant_id(slot: Dict[str, Any]) -> str:
    entrant = slot.get("entrant") if isinstance(slot, dict) else None
    if not isinstance(entrant, dict):
        return ""
    return str(entrant.get("id", "") or "")


def _latest_characters_by_entrant(set_node: Dict[str, Any]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for game in set_node.get("games", []) or []:
        if not isinstance(game, dict):
            continue
        for selection in game.get("selections", []) or []:
            if not isinstance(selection, dict):
                continue
            entrant = selection.get("entrant") or {}
            character = selection.get("character") or {}
            entrant_id = str(entrant.get("id", "") or "")
            character_name = str(character.get("name", "") or "")
            if entrant_id and character_name:
                result[entrant_id] = character_name
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
