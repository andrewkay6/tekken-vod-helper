from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional, Tuple

from .thumbnails import Image, ImageDraw, ImageFont, ImageOps, find_portrait


@dataclass
class Top8Player:
    placement: int
    name: str
    character: str = ""
    social: str = ""


def make_top8_graphic(
    players: Iterable[Top8Player],
    output_path: str,
    event_name: str = "",
    portrait_dir: str = "",
    background_path: str = "",
    bracket_url: str = "",
    footer_left: str = "",
    footer_right: str = "www.kwtekken.ca",
    social_logo_path: str = "",
    logger: Optional[Callable[[str], None]] = None,
) -> None:
    if Image is None:
        raise RuntimeError("Pillow is required for Top 8 graphic generation.")

    entries = sorted(list(players), key=lambda item: item.placement)[:8]
    if not entries:
        raise ValueError("No Top 8 players were found.")
    while len(entries) < 8:
        entries.append(Top8Player(placement=len(entries) + 1, name="TBD", character=""))

    canvas = _background(background_path)
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    draw.rectangle((0, 0, 1280, 720), fill=(5, 8, 10, 104))

    title_font = _font(48, bold=True)
    subtitle_font = _font(18, bold=True)
    link_font = _font(15, bold=True)
    footer_font = _font(22, bold=True)
    site_font = _font(17, bold=True)

    title = event_name.strip() or "KW Tekken"
    title = title.replace(" - Tekken 8 Singles", "").replace("Tekken 8 Singles", "").strip(" -")
    _fit_outlined_text(draw, (22, 8, 960, 60), title, title_font, (255, 255, 255), anchor="lm")
    _fit_outlined_text(draw, (24, 60, 280, 84), "Tekken 8 Singles", subtitle_font, (232, 236, 244), anchor="lm")
    link = bracket_url.strip()
    if link:
        link = link.replace("https://", "").replace("http://", "")
        _fit_outlined_text(draw, (720, 60, 1256, 84), link, link_font, (232, 236, 244), anchor="rm")

    if footer_left.strip():
        _fit_outlined_text(draw, (22, 694, 560, 718), footer_left.strip(), footer_font, (255, 255, 255), anchor="lm")
    if footer_right.strip():
        _fit_outlined_text(draw, (760, 696, 1256, 718), footer_right.strip(), site_font, (255, 255, 255), anchor="rm")

    card_boxes = _card_boxes()
    logo_path = social_logo_path.strip() or _default_social_logo_path()
    for index, entry in enumerate(entries):
        _draw_player_card(overlay, card_boxes[index], entry, portrait_dir, logo_path)

    composed = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
    composed.save(output_path, quality=94)
    if logger:
        logger("Top 8 graphic written: {}".format(output_path))


def _background(background_path: str):
    size = (1280, 720)
    if background_path and Path(background_path).exists():
        try:
            image = Image.open(background_path).convert("RGB")
            return ImageOps.fit(image, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
        except Exception:
            pass
    image = Image.new("RGB", size, (14, 16, 22))
    draw = ImageDraw.Draw(image)
    for y in range(size[1]):
        red = int(14 + y * 0.03)
        green = int(16 + y * 0.02)
        blue = int(22 + y * 0.04)
        draw.line((0, y, size[0], y), fill=(red, green, blue))
    return image


def _card_boxes():
    return [
        (64, 132, 422, 622),
        (486, 132, 700, 360),
        (724, 132, 938, 360),
        (962, 132, 1176, 360),
        (486, 412, 642, 614),
        (666, 412, 822, 614),
        (846, 412, 1002, 614),
        (1026, 412, 1182, 614),
    ]


def _draw_player_card(overlay, box: Tuple[int, int, int, int], entry: Top8Player, portrait_dir: str, social_logo_path: str) -> None:
    draw = ImageDraw.Draw(overlay)
    x1, y1, x2, y2 = box
    is_hero = entry.placement == 1
    has_social = bool(entry.social.strip())
    placement_color = _placement_color(entry.placement)
    border = 0
    draw.rectangle(box, fill=(7, 9, 13, 190))
    inner = (x1 + border, y1 + border, x2 - border, y2 - border)
    ix1, iy1, ix2, iy2 = inner
    draw.rectangle(inner, fill=(0, 0, 0, 168))

    social_height = (26 if is_hero else 22) if has_social else 0
    name_bar_height = 78 if is_hero else 42
    name_y1 = iy2 - name_bar_height
    name_y2 = iy2
    draw.rectangle((ix1, name_y1, ix2, name_y2), fill=(7, 9, 13, 232))

    portrait_path = find_portrait(portrait_dir, entry.character)
    portrait_bottom = name_y1 + (8 if is_hero else 4)
    portrait_pad_x = 18 if is_hero else 14
    portrait_box = (ix1 + portrait_pad_x, iy1 + 22, ix2 - portrait_pad_x, portrait_bottom - 4)
    if portrait_path is not None:
        try:
            portrait = Image.open(str(portrait_path)).convert("RGBA")
            fitted = ImageOps.contain(
                portrait,
                (portrait_box[2] - portrait_box[0], portrait_box[3] - portrait_box[1]),
                method=Image.Resampling.LANCZOS,
            )
            px = portrait_box[0] + (portrait_box[2] - portrait_box[0] - fitted.width) // 2
            py = portrait_box[3] - fitted.height
            overlay.alpha_composite(fitted, (px, py))
        except Exception:
            _draw_missing_portrait(draw, portrait_box)
    else:
        _draw_missing_portrait(draw, portrait_box)

    rank_font = _font(118 if is_hero else 58, bold=True)
    name_font = _font(46 if is_hero else 27, bold=True)
    social_font = _font(18 if is_hero else 15, bold=True)
    rank = str(entry.placement)
    rank_x = ix1 + (42 if is_hero else 14)
    rank_y = iy1 + (18 if is_hero else 10)
    _rank_text(draw, (rank_x, rank_y), rank, rank_font)
    _fit_outlined_text(draw, (ix1 + 14, name_y1 + 12, ix2 - 14, name_y2 - 4), entry.name, name_font, (255, 255, 255), anchor="lm")
    if has_social:
        social = _social_label(entry.social)
        strip_y1 = y2 + 4
        strip_y2 = strip_y1 + social_height
        draw.rectangle((x1, strip_y1, x2, strip_y2), fill=(7, 9, 13, 220))
        logo_size = 19 if is_hero else 16
        logo_top = strip_y1 + ((social_height - logo_size) // 2)
        _draw_social_logo(draw, social_logo_path, (x1 + 14, logo_top, x1 + 14 + logo_size, logo_top + logo_size))
        _fit_outlined_text(draw, (x1 + 22 + logo_size, strip_y1 + 2, x2 - 10, strip_y2 - 2), social, social_font, (255, 255, 255), anchor="lm")


def _draw_missing_portrait(draw, box: Tuple[int, int, int, int]) -> None:
    x1, y1, x2, y2 = box
    draw.rectangle((x1, y1, x2, y2), fill=(30, 35, 45, 142), outline=(255, 255, 255, 36), width=1)
    draw.line((x1 + 2, y2 - 2, x2 - 2, y1 + 2), fill=(255, 255, 255, 26), width=2)


def _placement_color(placement: int):
    return (222, 34, 48, 255)


def _ordinal(value: int) -> str:
    if 10 <= value % 100 <= 20:
        suffix = "TH"
    else:
        suffix = {1: "ST", 2: "ND", 3: "RD"}.get(value % 10, "TH")
    return "{}{}".format(value, suffix)


def _social_label(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value.startswith("@"):
        return value
    return "@{}".format(value)


def _font(size: int, bold: bool):
    names = ["arialbd.ttf", "Bahnschrift.ttf", "Arial Bold.ttf"] if bold else ["arial.ttf", "Bahnschrift.ttf", "Arial.ttf"]
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def _fit_text(draw, xy, text: str, font, fill, anchor=None, max_width: int = 240) -> None:
    font = _fitted_font(draw, text, font, max_width)
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)


def _fit_outlined_text(draw, box, text: str, font, fill, anchor="lm") -> None:
    x1, y1, x2, y2 = box
    fitted = _fitted_font(draw, text, font, max(20, x2 - x1))
    y = y1 + ((y2 - y1) // 2)
    if anchor and anchor.startswith("r"):
        x = x2
    elif anchor and anchor.startswith("m"):
        x = x1 + ((x2 - x1) // 2)
    else:
        x = x1
    _outlined_text(draw, (x, y), text, fitted, fill, anchor=anchor)


def _fitted_font(draw, text: str, font, max_width: int):
    try:
        size = int(getattr(font, "size", 18))
        path = getattr(font, "path", "")
        while size > 9:
            bbox = draw.textbbox((0, 0), text, font=font)
            if bbox[2] - bbox[0] <= max_width:
                return font
            size -= 2
            if path:
                font = ImageFont.truetype(path, size=size)
            else:
                break
    except Exception:
        pass
    return font


def _outlined_text(draw, xy, text: str, font, fill, anchor=None) -> None:
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, 2)):
        draw.text((xy[0] + dx, xy[1] + dy), text, font=font, fill=(0, 0, 0), anchor=anchor)
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)


def _rank_text(draw, xy, text: str, font) -> None:
    x, y = xy
    for dx, dy in ((4, 4), (3, 3), (2, 2)):
        draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0, 210))
    draw.text((x, y), text, font=font, fill=(255, 255, 255))


def _draw_social_logo(draw, social_logo_path: str, box: Tuple[int, int, int, int]) -> None:
    _draw_x_logo_path(draw, box)


def _draw_x_logo_path(draw, box: Tuple[int, int, int, int]) -> None:
    x1, y1, x2, y2 = box
    width = x2 - x1
    height = y2 - y1
    scale = min(width / 300.0, height / 271.0)
    logo_width = 300 * scale
    logo_height = 271 * scale
    ox = x1 + (width - logo_width) / 2
    oy = y1 + (height - logo_height) / 2

    def point(x: float, y: float):
        return (ox + x * scale, oy + y * scale)

    outer = [
        point(236, 0),
        point(282, 0),
        point(181, 115),
        point(299, 271),
        point(206.4, 271),
        point(133.9, 176.2),
        point(50.9, 271),
        point(4.9, 271),
        point(111.9, 148),
        point(-1.1, 0),
        point(93.8, 0),
        point(159.3, 86.6),
    ]
    inner = [
        point(219.9, 244),
        point(245.4, 244),
        point(80.4, 26),
        point(53, 26),
    ]
    draw.polygon(outer, fill=(255, 255, 255))
    draw.polygon(inner, fill=(7, 9, 13))


def _default_social_logo_path() -> str:
    path = Path(__file__).with_name("x-logo-2023.svg")
    return str(path) if path.exists() else ""
