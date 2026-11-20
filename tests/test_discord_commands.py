from tekken_vod_helper import discord_commands as cmds
from tekken_vod_helper.discord_notify import GUILD_ID
from tekken_vod_helper.startgg_client import TournamentSummary


def _cmd(name="events", guild_id=GUILD_ID, type_=2):
    interaction = {"id": "1", "token": "t", "type": type_, "data": {"name": name}}
    if guild_id is not None:
        interaction["guild_id"] = guild_id
    return interaction


def test_own_server_runs_command():
    assert cmds.route_interaction(_cmd()) == "events"


def test_other_server_and_dm_are_refused():
    assert cmds.route_interaction(_cmd(guild_id="999")) == "refuse"
    assert cmds.route_interaction(_cmd(guild_id=None)) == "refuse"


def test_unknown_command_and_non_command_ignored():
    assert cmds.route_interaction(_cmd(name="nope")) == "ignore"
    assert cmds.route_interaction(_cmd(type_=3)) == "ignore"


def test_refusal_is_private():
    body = cmds.refusal_response()
    assert body["data"]["flags"] & cmds.EPHEMERAL


def test_guilds_to_leave():
    assert cmds.guilds_to_leave([GUILD_ID, "999", "888"]) == ["999", "888"]


def test_events_lists_upcoming_soonest_first():
    tournaments = [
        TournamentSummary("BB 13", "tournament/basement-brawl-13", 300_000),
        TournamentSummary("BB 11", "tournament/basement-brawl-11", 500),
        TournamentSummary("BB 12", "tournament/basement-brawl-12", 200_000),
    ]
    text = cmds.events_reply(tournaments, now=100_000)
    assert "BB 11" not in text and "stay tuned" not in text
    assert text.index("BB 12") < text.index("BB 13")
    assert "<t:200000:F>" in text
    assert "https://www.start.gg/tournament/basement-brawl-12/details" in text


def test_events_in_progress_still_current():
    started_an_hour_ago = TournamentSummary("BB 12", "tournament/basement-brawl-12", 100_000 - 3_600)
    assert "stay tuned" not in cmds.events_reply([started_an_hour_ago], now=100_000)


def test_events_none_upcoming_shows_stay_tuned_and_last():
    tournaments = [
        TournamentSummary("BB 10", "tournament/basement-brawl-10", 1_000),
        TournamentSummary("BB 11", "tournament/basement-brawl-11", 2_000),
    ]
    text = cmds.events_reply(tournaments, now=500_000)
    assert "stay tuned" in text and "BB 11" in text and "BB 10" not in text


def test_events_no_tournaments_at_all():
    assert "stay tuned" in cmds.events_reply([], now=0)


def test_only_events_command_registered():
    assert [c["name"] for c in cmds.COMMANDS] == ["events"]
    assert cmds.route_interaction(_cmd(name="event")) == "ignore"
