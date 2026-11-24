"""Discord announcements for KW Tekken (new brackets, uploaded VODs).

Uses the bot token over plain REST, so no long-running gateway process is
needed. Posting is always explicit: callers build a draft, show it, and only
send when asked.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .startgg_client import TournamentSummary, normalize_tournament_slug

API_BASE = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (tekken-vod-helper, 0.1)"

GUILD_ID = "1489333245605707988"
CHANNEL_EVENTS = "1496220704318685204"  # #upcoming-event-info
CHANNEL_VODS = "1519702854523289670"  # #tournament-vods-results
ALLOWED_GUILDS = frozenset({GUILD_ID})

# @everyone is only for the start.gg bracket going live (Andy, 2026-11-20). VOD posts never mention everyone.
DEFAULT_VODS_NOTE = "The {event} playlist is live with the full VOD. Individual match videos are coming shortly."


class DiscordError(Exception):
    pass


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines into os.environ without overriding existing values."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def tournament_url(slug: str) -> str:
    return "https://www.start.gg/{}/details".format(normalize_tournament_slug(slug))


def bracket_message(slug: str, note: str = "") -> str:
    lines = ["@everyone " + note.strip() if note.strip() else "@everyone", tournament_url(slug)]
    return "\n".join(lines)


def vods_message(playlist_url: str, note: str = DEFAULT_VODS_NOTE, event: str = "") -> str:
    """VOD announcement: a short line plus the playlist link. No @everyone (that's only for brackets)."""
    text = note.replace("{event}", event or "event").strip()
    return "{}\n{}".format(text, playlist_url.strip()) if text else playlist_url.strip()


def slug_key(slug: str) -> str:
    """The bare tournament slug, used to spot earlier announcements."""
    return normalize_tournament_slug(slug).split("/", 1)[-1]


def playlist_id(url: str) -> str:
    marker = "list="
    if marker not in url:
        return url.strip()
    return url.split(marker, 1)[1].split("&", 1)[0]


def already_posted(messages: Iterable[Dict[str, Any]], needle: str) -> bool:
    """True if needle appears as a whole token (so "bb-1" doesn't match "bb-12")."""
    if not needle:
        return False
    pattern = re.compile(re.escape(needle) + r"(?![\w-])")
    return any(pattern.search(str(m.get("content", ""))) for m in messages)


def pick_unannounced_tournament(
    tournaments: Sequence[TournamentSummary],
    messages: Sequence[Dict[str, Any]],
    now: Optional[float] = None,
) -> Optional[TournamentSummary]:
    """Soonest upcoming tournament whose link hasn't been posted yet."""
    now = time.time() if now is None else now
    upcoming = [t for t in tournaments if t.start_at and t.start_at > now]
    for tournament in sorted(upcoming, key=lambda t: t.start_at):
        if not already_posted(messages, slug_key(tournament.slug)):
            return tournament
    return None


class DiscordClient:
    def __init__(self, token: str):
        if not (token or "").strip():
            raise DiscordError("DISCORD_BOT_TOKEN is not set.")
        self._token = token.strip()

    def recent_messages(self, channel_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        return self._request("GET", "/channels/{}/messages?limit={}".format(channel_id, limit))

    def post(self, channel_id: str, content: str, mention_everyone: bool = True,
             reply_to: Optional[str] = None) -> Dict[str, Any]:
        """Post ``content``. ``reply_to`` makes it a Discord reply to that message id in the same channel
        (shown with a link back to it); the reply never pings the original author."""
        body = {
            "content": content,
            "allowed_mentions": {"parse": ["everyone"] if mention_everyone else [], "replied_user": False},
        }
        if reply_to:
            body["message_reference"] = {"message_id": str(reply_to), "channel_id": str(channel_id),
                                         "fail_if_not_exists": False}
        return self._request("POST", "/channels/{}/messages".format(channel_id), body)

    def leave_guild(self, guild_id: str) -> None:
        self._request("DELETE", "/users/@me/guilds/{}".format(guild_id))

    def set_guild_commands(self, app_id: str, guild_id: str, commands: List[Dict[str, Any]]) -> Any:
        """Bulk-overwrite the commands registered in one server."""
        return self._request("PUT", "/applications/{}/guilds/{}/commands".format(app_id, guild_id), commands)

    def set_global_commands(self, app_id: str, commands: List[Dict[str, Any]]) -> Any:
        """Bulk-overwrite global commands (we keep this empty so commands exist only in our server)."""
        return self._request("PUT", "/applications/{}/commands".format(app_id), commands)

    def interaction_callback(self, interaction_id: str, token: str, body: Dict[str, Any]) -> None:
        self._request("POST", "/interactions/{}/{}/callback".format(interaction_id, token), body)

    def edit_interaction_reply(self, app_id: str, token: str, body: Dict[str, Any]) -> None:
        self._request("PATCH", "/webhooks/{}/{}/messages/@original".format(app_id, token), body)

    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            API_BASE + path,
            data=data,
            method=method,
            headers={
                "Authorization": "Bot " + self._token,
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise DiscordError("Discord returned HTTP {}: {}".format(exc.code, detail)) from exc
        except urllib.error.URLError as exc:
            raise DiscordError("Could not reach Discord: {}".format(exc)) from exc
