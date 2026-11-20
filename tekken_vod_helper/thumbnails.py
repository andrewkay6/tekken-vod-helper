import re
from pathlib import Path
from typing import List, Callable, Optional, Tuple

from .models import MatchSegment
from .util import normalize_key

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageTk
except ImportError:  # pragma: no cover - exercised by app runtime messaging
    Image = None
    ImageDraw = None
    ImageFont = None
    ImageOps = None
    ImageTk = None


PORTRAIT_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")


def make_thumbnail(
    video_path: str,
    match: MatchSegment,
    start: float,
    end: float,
    output_path: str,
    portrait_dir: str,
    ffmpeg_path: str,
    event_name: str = "",
    background_path: str = "",
    logger: Optional[Callable[[str], None]] = None,
) -> None:
    if Image is None:
        raise RuntimeError("Pillow is required for thumbnail generation.")

    thumb = _thumbnail_background(background_path, logger)
    overlay = Image.new("RGBA", thumb.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    draw.rectangle((0, 500, 1280, 720), fill=(10, 12, 16, 210))
    draw.rectangle((0, 500, 1280, 508), fill=(222, 34, 48, 255))
    draw.rectangle((632, 508, 648, 720), fill=(245, 245, 245, 160))

    _paste_portraits(overlay, portrait_dir, match.character1, (20, 420, 280, 700), align="left")
    _paste_portraits(overlay, portrait_dir, match.character2, (1000, 420, 1260, 700), align="right")

    p1 = match.player1 or "Player 1"
    p2 = match.player2 or "Player 2"
    c1 = match.character1 or "Character"
    c2 = match.character2 or "Character"
    event_name = event_name.strip()
    round_name = match.round_name.strip()

    # Each line shrinks until it fits its column; long event names and team-tagged players used to run off
    # the frame.
    if event_name:
        _outlined_text(draw, (640, 36), event_name, _fit_font(draw, event_name, 56, True, 1220),
                       (255, 255, 255), anchor="ma")
    if round_name:
        _outlined_text(draw, (640, 100), round_name, _fit_font(draw, round_name, 24, False, 1220),
                       (238, 238, 238), anchor="ma")

    # A slot with two portraits is wider, so its text column moves toward the centre divider.
    tx1 = 380 + (60 if len(split_characters(match.character1)) > 1 else 0)
    tx2 = 900 - (60 if len(split_characters(match.character2)) > 1 else 0)
    _outlined_text(draw, (tx1, 528), p1, _fit_font(draw, p1, 42, True, 420), (255, 255, 255), anchor="ma")
    _outlined_text(draw, (tx1, 580), c1, _fit_font(draw, c1, 30, False, 420), (232, 232, 232), anchor="ma")
    _outlined_text(draw, (tx2, 528), p2, _fit_font(draw, p2, 42, True, 420), (255, 255, 255), anchor="ma")
    _outlined_text(draw, (tx2, 580), c2, _fit_font(draw, c2, 30, False, 420), (232, 232, 232), anchor="ma")

    composed = Image.alpha_composite(thumb.convert("RGBA"), overlay).convert("RGB")
    composed.save(output_path, quality=92)
    if logger:
        logger("Thumbnail written: {}".format(output_path))


def find_portrait(portrait_dir: str, character: str) -> Optional[Path]:
    if not portrait_dir or not character:
        return None
    directory = Path(portrait_dir)
    if not directory.exists():
        return None
    wanted = normalize_key(character)
    if not wanted:
        return None

    direct_matches = []
    fuzzy_matches = []
    for path in directory.iterdir():
        if not path.is_file() or path.suffix.lower() not in PORTRAIT_EXTENSIONS:
            continue
        stem = normalize_key(path.stem)
        if stem == wanted:
            direct_matches.append(path)
        elif wanted in stem or stem in wanted:
            fuzzy_matches.append(path)

    if direct_matches:
        return sorted(direct_matches)[0]
    if fuzzy_matches:
        return sorted(fuzzy_matches)[0]
    return None


def _thumbnail_background(background_path: str, logger: Optional[Callable[[str], None]] = None):
    size = (1280, 720)
    if not background_path:
        return Image.new("RGB", size, (0, 0, 0))

    path = Path(background_path)
    if not path.exists():
        if logger:
            logger("Thumbnail background not found, using black: {}".format(background_path))
        return Image.new("RGB", size, (0, 0, 0))

    try:
        image = Image.open(str(path)).convert("RGB")
        return ImageOps.fit(image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    except Exception as exc:
        if logger:
            logger("Could not load thumbnail background, using black: {}".format(exc))
        return Image.new("RGB", size, (0, 0, 0))


def split_characters(character: str) -> List[str]:
    """A slot may name more than one character, e.g. "Anna / Nina" when a player switched mid-set."""
    return [part.strip() for part in re.split(r"\s*/\s*|\s*&\s*", character or "") if part.strip()]


def _paste_portraits(overlay, portrait_dir: str, character: str, box: Tuple[int, int, int, int], align: str) -> None:
    names = split_characters(character)
    if len(names) < 2:
        _paste_portrait(overlay, find_portrait(portrait_dir, character), box, align)
        return
    # Two characters share the slot: the second sits behind, shifted toward the centre and dimmed slightly,
    # the first in front at the usual spot. More than two keeps only the first two.
    x0, y0, x1, y1 = box
    width = x1 - x0
    # Shrunk to 92%, shifted about half the width inward and barely dimmed so it reads clearly;; the text column moves aside to make room so it stays clear of the name/character text column.
    shrink = int(width * 0.08)
    shift = int(width * 0.46)
    if align == "left":
        back_box = (x0 + shift, y0 + shrink, x1 + shift - shrink, y1)
    else:
        back_box = (x0 - shift + shrink, y0 + shrink, x1 - shift, y1)
    back = find_portrait(portrait_dir, names[1])
    if back is not None:
        layer = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
        _paste_portrait(layer, back, back_box, align)
        r, g, b, a = layer.split()
        dim = Image.merge("RGBA", (r.point(lambda v: int(v * 0.92)), g.point(lambda v: int(v * 0.92)),
                                    b.point(lambda v: int(v * 0.92)), a))
        overlay.alpha_composite(dim)
    _paste_portrait(overlay, find_portrait(portrait_dir, names[0]), box, align)


def _paste_portrait(overlay, portrait_path: Optional[Path], box: Tuple[int, int, int, int], align: str) -> None:
    draw = ImageDraw.Draw(overlay)
    if portrait_path is None:
        draw.rounded_rectangle(box, radius=18, fill=(28, 32, 38, 190), outline=(255, 255, 255, 70), width=2)
        return

    portrait = Image.open(str(portrait_path)).convert("RGBA")
    target_width = box[2] - box[0]
    target_height = box[3] - box[1]
    fitted = ImageOps.contain(portrait, (target_width, target_height), method=Image.Resampling.LANCZOS)
    x = box[0] if align == "left" else box[2] - fitted.width
    y = box[3] - fitted.height
    overlay.alpha_composite(fitted, (x, y))


def _fit_font(draw, text: str, size: int, bold: bool, max_width: int, min_size: int = 16):
    """The largest font up to ``size`` whose rendering of ``text`` is at most ``max_width`` pixels wide."""
    font = _font(size, bold)
    while size > min_size:
        left, _, right, _ = draw.textbbox((0, 0), text, font=font)
        if right - left <= max_width:
            break
        size -= 2
        font = _font(size, bold)
    return font


def _font(size: int, bold: bool):
    # Arial first (Windows/macOS), then common Linux fonts. Without a fallback the default bitmap font is
    # ~10 px whatever size is asked, which made every name on a Linux-rendered thumbnail unreadable.
    if bold:
        names = ["arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    else:
        names = ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 has no sized default
        return ImageFont.load_default()


def _outlined_text(draw, xy, text: str, font, fill, anchor=None) -> None:
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, 2)):
        draw.text((xy[0] + dx, xy[1] + dy), text, font=font, fill=(0, 0, 0), anchor=anchor)
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)
