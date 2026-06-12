import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tekken_vod_helper.roster_import import (  # noqa: E402
    DEFAULT_ROSTER_URL,
    download_portraits,
    fetch_roster_from_url,
    write_characters_file,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Tekken roster names and portraits from tekken.com/fighters.")
    parser.add_argument("--url", default=DEFAULT_ROSTER_URL, help="Roster page URL")
    parser.add_argument("--characters", default="tekken_vod_helper/characters.txt", help="Character list to write")
    parser.add_argument("--portraits", default="portraits", help="Portrait output folder")
    parser.add_argument("--download-portraits", action="store_true", help="Download portrait images referenced by the page")
    args = parser.parse_args()

    roster = fetch_roster_from_url(args.url)
    write_characters_file(args.characters, roster)
    print("Imported {} characters from {} into {}".format(len(roster), args.url, args.characters))

    if args.download_portraits:
        written = download_portraits(roster, args.portraits, logger=print)
        print("Downloaded {} portraits into {}".format(len(written), Path(args.portraits).resolve()))


if __name__ == "__main__":
    main()
