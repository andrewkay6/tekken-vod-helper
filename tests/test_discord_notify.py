from tekken_vod_helper import discord_notify as dn
from tekken_vod_helper.startgg_client import TournamentSummary


def test_bracket_message_matches_house_style():
    assert dn.bracket_message("tournament/basement-brawl-12") == (
        "@everyone\nhttps://www.start.gg/tournament/basement-brawl-12/details"
    )
    assert dn.bracket_message("https://www.start.gg/tournament/bb-12/details", "we're back") == (
        "@everyone we're back\nhttps://www.start.gg/tournament/bb-12/details"
    )


def test_vods_message():
    msg = dn.vods_message("https://www.youtube.com/playlist?list=PLabc", "they're all up")
    assert msg == "they're all up\nhttps://www.youtube.com/playlist?list=PLabc"
    default = dn.vods_message("https://www.youtube.com/playlist?list=PLabc", event="Basement Brawl #12")
    assert default.startswith("The Basement Brawl #12 playlist is live with the full VOD.") and "@everyone" not in default


def test_playlist_id_ignores_share_suffix():
    assert dn.playlist_id("https://youtube.com/playlist?list=PLabc&si=xyz") == "PLabc"


def test_pick_skips_past_and_already_posted():
    now = 1_000
    tournaments = [
        TournamentSummary("BB 10", "tournament/basement-brawl-10", start_at=500),
        TournamentSummary("BB 11", "tournament/basement-brawl-11", start_at=2_000),
        TournamentSummary("BB 12", "tournament/basement-brawl-12", start_at=3_000),
    ]
    history = [{"content": "@everyone https://www.start.gg/tournament/basement-brawl-11/details"}]
    picked = dn.pick_unannounced_tournament(tournaments, history, now=now)
    assert picked is not None and picked.slug.endswith("basement-brawl-12")


def test_pick_does_not_confuse_prefix_slugs():
    # "basement-brawl-1" is a substring of "basement-brawl-12"
    bb1 = TournamentSummary("BB 1", "tournament/basement-brawl-1", start_at=3_000)
    bb12 = TournamentSummary("BB 12", "tournament/basement-brawl-12", start_at=3_000)
    posted_bb12 = [{"content": "https://www.start.gg/tournament/basement-brawl-12/details"}]
    posted_bb1 = [{"content": "https://www.start.gg/tournament/basement-brawl-1/details"}]
    assert dn.pick_unannounced_tournament([bb1], posted_bb12, now=0) is bb1
    assert dn.pick_unannounced_tournament([bb12], posted_bb1, now=0) is bb12


def test_pick_none_when_nothing_upcoming():
    assert dn.pick_unannounced_tournament([TournamentSummary("old", "tournament/x", 5)], [], now=10) is None
