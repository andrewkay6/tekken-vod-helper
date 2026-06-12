from pathlib import Path
from typing import Callable, Optional, Tuple

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

    portrait1 = find_portrait(portrait_dir, match.character1)
    portrait2 = find_portrait(portrait_dir, match.character2)
    _paste_portrait(overlay, portrait1, (20, 420, 280, 700), align="left")
    _paste_portrait(overlay, portrait2, (1000, 420, 1260, 700), align="right")

    title_font = _font(56, bold=True)
    name_font = _font(42, bold=True)
    char_font = _font(30, bold=False)
    small_font = _font(24, bold=False)

    p1 = match.player1 or "Player 1"
    p2 = match.player2 or "Player 2"
    c1 = match.character1 or "Character"
    c2 = match.character2 or "Character"
    event_name = event_name.strip()
    round_name = match.round_name.strip()

    if event_name:
        _outlined_text(draw, (640, 36), event_name, title_font, (255, 255, 255), anchor="ma")
    if round_name:
        _outlined_text(draw, (640, 100), round_name, small_font, (238, 238, 238), anchor="ma")

    _outlined_text(draw, (380, 528), p1, name_font, (255, 255, 255), anchor="ma")
    _outlined_text(draw, (380, 580), c1, char_font, (232, 232, 232), anchor="ma")
    _outlined_text(draw, (900, 528), p2, name_font, (255, 255, 255), anchor="ma")
    _outlined_text(draw, (900, 580), c2, char_font, (232, 232, 232), anchor="ma")

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


def _font(size: int, bold: bool):
    names = ["arialbd.ttf", "Arial Bold.ttf"] if bold else ["arial.ttf", "Arial.ttf"]
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def _outlined_text(draw, xy, text: str, font, fill, anchor=None) -> None:
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, 2)):
        draw.text((xy[0] + dx, xy[1] + dy), text, font=font, fill=(0, 0, 0), anchor=anchor)
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)
