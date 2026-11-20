"""Draft (and optionally send) KW Tekken Discord announcements.

Prints the draft by default. Nothing is posted without --send.

  python scripts/discord_announce.py bracket                 # newest unannounced upcoming start.gg tournament
  python scripts/discord_announce.py bracket --slug basement-brawl-12 --note "we're back"
  python scripts/discord_announce.py vods --event "Basement Brawl #12" --playlist https://www.youtube.com/playlist?list=...   (no @everyone)
  ...add --send to post. Tokens come from .env (DISCORD_BOT_TOKEN, STARTGG_TOKEN).
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tekken_vod_helper import discord_notify as dn  # noqa: E402
from tekken_vod_helper.startgg_client import fetch_owned_tournaments  # noqa: E402


def _bracket(args, client) -> tuple:
    history = client.recent_messages(dn.CHANNEL_EVENTS)
    if args.slug:
        slug = args.slug
    else:
        tournaments = fetch_owned_tournaments(os.environ.get("STARTGG_TOKEN", ""))
        picked = dn.pick_unannounced_tournament(tournaments, history)
        if picked is None:
            print("No upcoming start.gg tournament that hasn't been announced already.")
            return None, None, False
        print("Picked: {}".format(picked.label))
        slug = picked.slug
    duplicate = dn.already_posted(history, dn.slug_key(slug))
    return dn.CHANNEL_EVENTS, dn.bracket_message(slug, args.note), duplicate


def _vods(args, client) -> tuple:
    history = client.recent_messages(dn.CHANNEL_VODS)
    note = dn.DEFAULT_VODS_NOTE if args.note is None else args.note
    duplicate = dn.already_posted(history, dn.playlist_id(args.playlist))
    return dn.CHANNEL_VODS, dn.vods_message(args.playlist, note, args.event), duplicate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="kind", required=True)
    bracket = sub.add_parser("bracket", help="announce a start.gg tournament in #upcoming-event-info")
    bracket.add_argument("--slug", help="start.gg slug or URL (default: auto-pick)")
    bracket.add_argument("--note", default="", help="short line after @everyone")
    vods = sub.add_parser("vods", help="announce a VOD playlist in #tournament-vods-results")
    vods.add_argument("--playlist", required=True, help="YouTube playlist URL")
    vods.add_argument("--note", default=None, help="short line before the link (default: the approved heads-up)")
    vods.add_argument("--event", default="", help='e.g. "Basement Brawl #12" (fills the default line)')
    for p in (bracket, vods):
        p.add_argument("--send", action="store_true", help="actually post (default: draft only)")
        p.add_argument("--allow-duplicate", action="store_true", help="post even if the link was already posted")
    args = parser.parse_args()

    dn.load_env_file(ROOT / ".env")
    try:
        client = dn.DiscordClient(os.environ.get("DISCORD_BOT_TOKEN", ""))
        channel, content, duplicate = (_bracket if args.kind == "bracket" else _vods)(args, client)
        if content is None:
            return 0
        print("--- draft for channel {} ---\n{}\n---".format(channel, content))
        if duplicate:
            print("Note: this link already appears in the channel's recent messages.")
        if not args.send:
            print("Draft only. Re-run with --send to post.")
            return 0
        if duplicate and not args.allow_duplicate:
            print("Refusing to post a duplicate. Use --allow-duplicate to override.")
            return 1
        # Only the bracket announcement may mention everyone.
        posted = client.post(channel, content, mention_everyone=(args.kind == "bracket"))
        print("Posted: https://discord.com/channels/{}/{}/{}".format(dn.GUILD_ID, channel, posted.get("id")))
        return 0
    except Exception as exc:  # surface API errors without a traceback wall
        print("Error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
