import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tekken_vod_helper.roster_import import (
    download_portraits,
    extract_roster_from_html,
    write_characters_file,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Tekken roster names and portraits from a saved tekken.com fighters HTML file.")
    parser.add_argument("html", help="Saved fighters roster HTML file")
    parser.add_argument("--characters", default="tekken_vod_helper/characters.txt", help="Character list to write")
    parser.add_argument("--portraits", default="portraits", help="Portrait output folder")
    parser.add_argument("--download-portraits", action="store_true", help="Download portrait images referenced by the HTML")
    args = parser.parse_args()

    roster = extract_roster_from_html(args.html)
    write_characters_file(args.characters, roster)
    print("Imported {} characters into {}".format(len(roster), args.characters))

    if args.download_portraits:
        written = download_portraits(roster, args.portraits, logger=print)
        print("Downloaded {} portraits into {}".format(len(written), Path(args.portraits).resolve()))


if __name__ == "__main__":
    main()
