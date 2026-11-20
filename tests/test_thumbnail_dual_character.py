from PIL import Image

from tekken_vod_helper.models import MatchSegment
from tekken_vod_helper.thumbnails import make_thumbnail, split_characters


def test_split_characters():
    assert split_characters("Anna / Nina") == ["Anna", "Nina"]
    assert split_characters("Anna/Nina") == ["Anna", "Nina"]
    assert split_characters("Jack-8") == ["Jack-8"]
    assert split_characters("") == []


def test_dual_character_slot_draws_both_portraits(tmp_path):
    portraits = tmp_path / "portraits"
    portraits.mkdir()
    Image.new("RGBA", (200, 260), (255, 0, 0, 255)).save(portraits / "Anna.png")
    Image.new("RGBA", (200, 260), (0, 0, 255, 255)).save(portraits / "Nina.png")
    out = tmp_path / "thumb.jpg"
    match = MatchSegment(start=0, end=10, player1="googy", player2="Luna", character1="Anna / Nina", character2="")
    make_thumbnail("", match, 0, 10, str(out), str(portraits), "", event_name="BB11")
    img = Image.open(out).convert("RGB")
    # Front portrait (Anna, red) at the usual left slot; back portrait (Nina, blue, dimmed) to its right.
    front = img.getpixel((60, 650))
    back = img.getpixel((260, 650))
    assert front[0] > 150 and front[2] < 80, front
    assert back[2] > 100 and back[0] < 80, back
