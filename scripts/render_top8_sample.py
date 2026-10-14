import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tekken_vod_helper.top8_graphics import Top8Player, make_top8_graphic


def main() -> None:
    p1_social = "--p1-social" in sys.argv
    long_social = "--long-social" in sys.argv
    output_dir = Path("tekken_vod_matches/top8_samples")
    output_dir.mkdir(parents=True, exist_ok=True)
    players = [
        Top8Player(1, "Spitfire2929", "Azucena", social="Spitfire2929" if p1_social else ""),
        Top8Player(2, "andy", "Heihachi"),
        Top8Player(3, "derpingtoast", "Nina", social="derpingtoast_longhandle" if long_social else "derpingtoast"),
        Top8Player(4, "PWDDA | Ariz", "Reina"),
        Top8Player(5, "SgHen", "Lili"),
        Top8Player(5, "Mr.DJ", "Eddy"),
        Top8Player(7, "Conman", "Raven", social="Conman9505VeryLong" if long_social else "Conman9505"),
        Top8Player(7, "gandhi", "Lee"),
    ]
    suffix = "p1social" if p1_social else "default"
    if long_social:
        suffix += "_longsocial"
    output_path = output_dir / "bb8_oct1_kinder_top8_v16_{}.png".format(suffix)
    make_top8_graphic(
        players,
        str(output_path),
        event_name="Basement Brawl #8 (CAFE EDITION)",
        portrait_dir="portraits",
        background_path="C:/tmp/kinder_gym_01.png",
        bracket_url="start.gg/tournament/basement-brawl-8-caf-edition",
        footer_left="I'm #SconedIn",
        logger=print,
    )
    print(output_path)


if __name__ == "__main__":
    main()
