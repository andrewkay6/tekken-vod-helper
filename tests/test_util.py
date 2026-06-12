from tekken_vod_helper.util import parse_timestamp, seconds_to_timestamp, slugify


def test_timestamp_round_trip_shapes():
    assert seconds_to_timestamp(65.25) == "00:01:05.250"
    assert parse_timestamp("01:05.250") == 65.25
    assert parse_timestamp("00:01:05.250") == 65.25


def test_slugify_keeps_names_filesystem_safe():
    assert slugify("Alice / Bob: Grand Finals") == "Alice_Bob_Grand_Finals"
