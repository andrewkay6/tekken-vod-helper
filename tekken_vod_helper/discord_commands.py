"""Slash commands for the KW Tekken bot, plus the server allowlist guard.

Kept free of network code so the routing and replies are unit-testable.
"""

from typing import Any, Dict, List, Optional, Sequence

from .discord_notify import ALLOWED_GUILDS, tournament_url
from .startgg_client import TournamentSummary

EPHEMERAL = 1 << 6
RESPONSE_MESSAGE = 4
RESPONSE_DEFERRED = 5
INTERACTION_COMMAND = 2

NOT_ALLOWED_TEXT = "This bot only works in the KW Tekken server."

# Registered per-server (never globally), so they don't exist anywhere else.
COMMANDS: List[Dict[str, Any]] = [
    {"name": "events", "description": "Upcoming KW Tekken events on start.gg", "type": 1},
]

# An event that started this recently still counts as the current one.
IN_PROGRESS_GRACE_SECONDS = 12 * 60 * 60


def is_allowed_guild(guild_id: Optional[str]) -> bool:
    """DMs (no guild) and any server not on the allowlist are refused."""
    return bool(guild_id) and guild_id in ALLOWED_GUILDS


def guilds_to_leave(guild_ids: Sequence[str]) -> List[str]:
    return [g for g in guild_ids if not is_allowed_guild(g)]


def refusal_response() -> Dict[str, Any]:
    return {"type": RESPONSE_MESSAGE, "data": {"content": NOT_ALLOWED_TEXT, "flags": EPHEMERAL}}


def route_interaction(interaction: Dict[str, Any]) -> str:
    """'refuse', 'ignore', or the command name to run."""
    if interaction.get("type") != INTERACTION_COMMAND:
        return "ignore"
    if not is_allowed_guild(interaction.get("guild_id")):
        return "refuse"
    name = (interaction.get("data") or {}).get("name", "")
    return name if any(c["name"] == name for c in COMMANDS) else "ignore"


def events_reply(tournaments: Sequence[TournamentSummary], now: float, limit: int = 3) -> str:
    dated = [t for t in tournaments if t.start_at]
    current = sorted((t for t in dated if t.start_at > now - IN_PROGRESS_GRACE_SECONDS), key=lambda t: t.start_at)
    if current:
        # <t:...> renders in each viewer's own timezone and clock format.
        return "\n\n".join(
            "**{}** <t:{}:F> (<t:{}:R>)\n{}".format(t.name, t.start_at, t.start_at, tournament_url(t.slug))
            for t in current[:limit]
        )
    stay_tuned = "No new event yet, stay tuned! Announcements go in <#1496220704318685204>."
    if not dated:
        return stay_tuned
    last = max(dated, key=lambda t: t.start_at)
    return "{}\n\nLast event: **{}** <t:{}:D>\n{}".format(stay_tuned, last.name, last.start_at, tournament_url(last.slug))
