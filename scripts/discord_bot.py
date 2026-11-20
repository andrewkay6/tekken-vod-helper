"""KW Tekken Discord bot: keeps the bot online and answers slash commands.

Only the KW Tekken server is served:
  - commands are registered in that server only, and global commands are cleared
  - it leaves any other server it gets added to
  - interactions from other servers or DMs get a short refusal and nothing runs

Holds one gateway connection and exits on any drop so systemd restarts it
(RestartSec keeps reconnects well under Discord's identify limit).
Exit 78 means a config problem (bad token / intents) and should not be retried.
"""

import json
import os
import platform
import sys
import threading
import time
from pathlib import Path

import websocket

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tekken_vod_helper import discord_commands as cmds  # noqa: E402
from tekken_vod_helper.discord_notify import GUILD_ID, DiscordClient, load_env_file  # noqa: E402
from tekken_vod_helper.startgg_client import fetch_owned_tournaments  # noqa: E402

GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"
INTENT_GUILDS = 1 << 0  # non-privileged; lets us see which servers we're in
FATAL_CLOSE_CODES = {4004, 4010, 4011, 4012, 4013, 4014}
EXIT_CONFIG = 78


def log(message: str) -> None:
    print(message, flush=True)


def identify_payload(token: str) -> dict:
    return {
        "op": 2,
        "d": {
            "token": token,
            "intents": INTENT_GUILDS,
            "properties": {"os": platform.system().lower(), "browser": "tvh", "device": "tvh"},
            "presence": {
                "status": "online",
                "afk": False,
                "since": None,
                "activities": [{"name": "for new VODs", "type": 3}],
            },
        },
    }


class Bot:
    def __init__(self, token: str):
        self.client = DiscordClient(token)
        self.app_id = ""

    def on_ready(self, data: dict) -> None:
        self.app_id = data["application"]["id"]
        log("Online as {}.".format(data["user"]["username"]))
        self._background(self._register_commands)
        self._leave_unknown([g["id"] for g in data.get("guilds", [])])

    def on_guild_create(self, data: dict) -> None:
        self._leave_unknown([data["id"]])

    def on_interaction(self, interaction: dict) -> None:
        route = cmds.route_interaction(interaction)
        if route == "ignore":
            return
        if route == "refuse":
            log("Refused /{} from guild {}.".format((interaction.get("data") or {}).get("name"), interaction.get("guild_id") or "DM"))
            self._background(self.client.interaction_callback, interaction["id"], interaction["token"], cmds.refusal_response())
            return
        replies = {"events": cmds.events_reply}
        if route in replies:
            self._background(self._startgg_command, interaction, replies[route])

    def _startgg_command(self, interaction: dict, build_reply) -> None:
        self.client.interaction_callback(interaction["id"], interaction["token"], {"type": cmds.RESPONSE_DEFERRED})
        try:
            content = build_reply(fetch_owned_tournaments(os.environ.get("STARTGG_TOKEN", "")), time.time())
        except Exception as exc:
            log("start.gg lookup failed: {}".format(exc))
            content = "Couldn't reach start.gg right now, try again in a bit."
        self.client.edit_interaction_reply(self.app_id, interaction["token"], {"content": content, "allowed_mentions": {"parse": []}})

    def _register_commands(self) -> None:
        self.client.set_global_commands(self.app_id, [])
        self.client.set_guild_commands(self.app_id, GUILD_ID, cmds.COMMANDS)
        log("Registered {} command(s) in KW Tekken only.".format(len(cmds.COMMANDS)))

    def _leave_unknown(self, guild_ids) -> None:
        for guild_id in cmds.guilds_to_leave(guild_ids):
            log("Leaving server {} (not on the allowlist).".format(guild_id))
            self._background(self.client.leave_guild, guild_id)

    @staticmethod
    def _background(fn, *args) -> None:
        def runner():
            try:
                fn(*args)
            except Exception as exc:
                log("{} failed: {}".format(fn.__name__, exc))

        threading.Thread(target=runner, daemon=True).start()


def run(token: str) -> int:
    bot = Bot(token)
    handlers = {"READY": bot.on_ready, "GUILD_CREATE": bot.on_guild_create, "INTERACTION_CREATE": bot.on_interaction}
    ws = websocket.create_connection(GATEWAY, timeout=30)
    try:
        hello = json.loads(ws.recv())
        interval = hello["d"]["heartbeat_interval"] / 1000.0
        ws.send(json.dumps(identify_payload(token)))
        seq = None
        acked = True
        next_beat = time.monotonic() + interval
        while True:
            now = time.monotonic()
            if now >= next_beat:
                if not acked:
                    log("No heartbeat ack; reconnecting.")
                    return 1
                ws.send(json.dumps({"op": 1, "d": seq}))
                acked = False
                next_beat = now + interval
            ws.settimeout(max(0.5, next_beat - time.monotonic()))
            try:
                opcode, frame = ws.recv_data_frame(True)
            except websocket.WebSocketTimeoutException:
                continue
            if opcode == websocket.ABNF.OPCODE_CLOSE:
                data = frame.data or b""
                code = int.from_bytes(data[:2], "big") if len(data) >= 2 else 0
                log("Gateway closed with code {}.".format(code))
                return EXIT_CONFIG if code in FATAL_CLOSE_CODES else 1
            if opcode != websocket.ABNF.OPCODE_TEXT:
                continue
            payload = json.loads(frame.data.decode("utf-8"))
            if payload.get("s") is not None:
                seq = payload["s"]
            op = payload.get("op")
            if op == 11:
                acked = True
            elif op == 1:
                next_beat = time.monotonic()
            elif op in (7, 9):
                log("Gateway asked for reconnect (op {}).".format(op))
                return 1
            elif op == 0 and payload.get("t") in handlers:
                try:
                    handlers[payload["t"]](payload["d"])
                except Exception as exc:
                    log("Handler for {} failed: {}".format(payload["t"], exc))
    finally:
        ws.close()


def main() -> int:
    load_env_file(ROOT / ".env")
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        log("DISCORD_BOT_TOKEN is not set.")
        return EXIT_CONFIG
    try:
        return run(token)
    except Exception as exc:
        log("Connection error: {}".format(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
