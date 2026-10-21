import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from PIL import Image as PillowImage
from PIL import ImageChops, ImageStat
from PIL import ImageFilter, ImageOps

from .ffmpeg_tools import extract_frame
from .models import MatchSegment


@dataclass
class BoundaryProposal:
    match_index: int
    original_start: float
    proposed_start: Optional[float]
    start_score: float
    original_end: Optional[float]
    proposed_end: Optional[float]
    end_score: float


@dataclass
class FrameIdentifier:
    state: str
    confidence: float
    features: Dict[str, float]


@dataclass
class AnalyzerConfig:
    start_reference_path: str
    end_reference_path: str
    start_search_before: float = 180.0
    start_search_after: float = 90.0
    end_search_before_next_start: float = 420.0
    end_search_after_start: float = 75.0
    coarse_step: float = 5.0
    fine_step: float = 1.0
    start_min_score: float = 0.70
    end_min_score: float = 0.78
    stable_match_count: int = 2


FrameExtractor = Callable[[float, str], None]
ProgressLogger = Callable[[str], None]


STAGE_SELECT = "stage_select"
CHARACTER_SELECT = "character_select"
CONTROLLER_PROMPT = "controller_prompt"
VS_SCREEN = "vs_screen"
STAGE_INTRO = "stage_intro"
GAMEPLAY = "gameplay"
POST_MATCH_MENU = "post_match_menu"
UNKNOWN = "unknown"


def analyze_match_boundaries(
    video_path: str,
    duration: float,
    matches: Iterable[MatchSegment],
    ffmpeg_path: str,
    config: AnalyzerConfig,
    logger: Optional[ProgressLogger] = None,
    frame_extractor: Optional[FrameExtractor] = None,
    next_starts: Optional[List[float]] = None,
) -> List[BoundaryProposal]:
    ordered = sorted(matches, key=lambda segment: segment.start)
    if not ordered:
        return []

    start_references = _load_references(config.start_reference_path)
    end_references = _load_references(config.end_reference_path)
    extractor = frame_extractor or (
        lambda timestamp, output_path: extract_frame(video_path, timestamp, output_path, ffmpeg_path, width=960)
    )

    proposals: List[BoundaryProposal] = []
    with tempfile.TemporaryDirectory(prefix="tekken_vod_analyzer_") as temp_dir:
        temp_root = Path(temp_dir)
        for index, match in enumerate(ordered):
            has_following_match = index + 1 < len(ordered)
            if next_starts is not None and index < len(next_starts):
                next_start = next_starts[index]
                has_following_match = next_start < duration
            else:
                next_start = ordered[index + 1].start if index + 1 < len(ordered) else duration
            if logger:
                logger("Analyzing match {} around {}.".format(index + 1, _seconds_to_log_time(match.start)))

            start_begin = max(0.0, match.start - config.start_search_before)
            start_end = min(duration, match.start + config.start_search_after, next_start - 1.0)
            proposed_start, start_score = _last_matching_timestamp(
                extractor,
                temp_root,
                start_references,
                start_begin,
                start_end,
                config.coarse_step,
                config.fine_step,
                config.start_min_score,
                _stage_select_similarity,
            )

            end_begin = max((proposed_start or match.start) + config.end_search_after_start, match.start)
            end_end = min(duration, next_start - 1.0)
            if has_following_match:
                end_begin = max(end_begin, next_start - config.end_search_before_next_start)
            proposed_end, end_score = _first_stable_matching_timestamp(
                extractor,
                temp_root,
                end_references,
                end_begin,
                end_end,
                config.coarse_step,
                config.fine_step,
                config.end_min_score,
                max(1, config.stable_match_count),
                _center_dialog_similarity,
            )

            proposals.append(
                BoundaryProposal(
                    match_index=index,
                    original_start=match.start,
                    proposed_start=proposed_start if start_score >= config.start_min_score else None,
                    start_score=start_score,
                    original_end=match.end,
                    proposed_end=proposed_end if end_score >= config.end_min_score else None,
                    end_score=end_score,
                )
            )
    return proposals


def apply_boundary_proposals(
    matches: Iterable[MatchSegment],
    proposals: Iterable[BoundaryProposal],
    update_starts: bool = True,
    update_ends: bool = True,
) -> int:
    ordered = sorted(matches, key=lambda segment: segment.start)
    changed = 0
    for proposal in proposals:
        if proposal.match_index < 0 or proposal.match_index >= len(ordered):
            continue
        match = ordered[proposal.match_index]
        if update_starts and proposal.proposed_start is not None and abs(match.start - proposal.proposed_start) >= 0.001:
            match.start = round(proposal.proposed_start, 3)
            changed += 1
        if update_ends and proposal.proposed_end is not None:
            rounded_end = round(proposal.proposed_end, 3)
            if match.end is None or abs(match.end - rounded_end) >= 0.001:
                match.end = rounded_end
                changed += 1
    return changed


def _best_timestamp(
    extractor: FrameExtractor,
    temp_root: Path,
    references: List[PillowImage.Image],
    begin: float,
    end: float,
    coarse_step: float,
    fine_step: float,
    scorer: Callable[[PillowImage.Image, PillowImage.Image], float],
) -> Tuple[Optional[float], float]:
    if end < begin:
        return None, 0.0
    coarse = _scan_window(extractor, temp_root, references, begin, end, max(coarse_step, 0.5), scorer)
    if coarse[0] is None:
        return None, 0.0
    fine_begin = max(begin, coarse[0] - coarse_step)
    fine_end = min(end, coarse[0] + coarse_step)
    return _scan_window(extractor, temp_root, references, fine_begin, fine_end, max(fine_step, 0.25), scorer)


def _last_matching_timestamp(
    extractor: FrameExtractor,
    temp_root: Path,
    references: List[PillowImage.Image],
    begin: float,
    end: float,
    coarse_step: float,
    fine_step: float,
    min_score: float,
    scorer: Callable[[PillowImage.Image, PillowImage.Image], float],
) -> Tuple[Optional[float], float]:
    coarse_matches = _scan_window_matches(
        extractor,
        temp_root,
        references,
        begin,
        end,
        max(coarse_step, 0.5),
        min_score,
        scorer,
    )
    if not coarse_matches:
        near_matches = _scan_window_matches(
            extractor,
            temp_root,
            references,
            begin,
            end,
            max(coarse_step, 0.5),
            max(0.0, min_score - 0.12),
            scorer,
        )
        fine_matches: List[Tuple[float, float]] = []
        for near_timestamp, _ in near_matches:
            fine_matches.extend(
                _scan_window_matches(
                    extractor,
                    temp_root,
                    references,
                    max(begin, near_timestamp - coarse_step),
                    min(end, near_timestamp + coarse_step),
                    max(fine_step, 0.25),
                    min_score,
                    scorer,
                )
            )
        if fine_matches:
            return max(fine_matches, key=lambda item: (item[0], item[1]))
        return _best_timestamp(extractor, temp_root, references, begin, end, coarse_step, fine_step, scorer)
    last_coarse_timestamp = coarse_matches[-1][0]
    fine_begin = max(begin, last_coarse_timestamp - coarse_step)
    fine_end = min(end, last_coarse_timestamp + coarse_step)
    fine_matches = _scan_window_matches(
        extractor,
        temp_root,
        references,
        fine_begin,
        fine_end,
        max(fine_step, 0.25),
        min_score,
        scorer,
    )
    if fine_matches:
        return max(fine_matches, key=lambda item: (item[1], item[0]))
    return max(coarse_matches, key=lambda item: (item[1], item[0]))


def _first_stable_matching_timestamp(
    extractor: FrameExtractor,
    temp_root: Path,
    references: List[PillowImage.Image],
    begin: float,
    end: float,
    coarse_step: float,
    fine_step: float,
    min_score: float,
    stable_match_count: int,
    scorer: Callable[[PillowImage.Image, PillowImage.Image], float],
) -> Tuple[Optional[float], float]:
    coarse_step = max(coarse_step, 0.5)
    coarse_matches = _scan_window_matches(
        extractor,
        temp_root,
        references,
        begin,
        end,
        coarse_step,
        min_score,
        scorer,
    )
    stable_run = _first_stable_run(coarse_matches, coarse_step, stable_match_count)
    if not stable_run:
        return _best_timestamp(extractor, temp_root, references, begin, end, coarse_step, fine_step, scorer)
    fine_begin = max(begin, stable_run[0][0] - coarse_step)
    fine_end = min(end, stable_run[0][0] + coarse_step)
    fine_matches = _scan_window_matches(
        extractor,
        temp_root,
        references,
        fine_begin,
        fine_end,
        max(fine_step, 0.25),
        min_score,
        scorer,
    )
    fine_run = _first_stable_run(fine_matches, max(fine_step, 0.25), stable_match_count)
    if fine_run:
        return fine_run[0]
    return stable_run[0]


def _first_stable_run(matches: List[Tuple[float, float]], step: float, stable_match_count: int) -> List[Tuple[float, float]]:
    if not matches:
        return []
    current = [matches[0]]
    max_gap = max(step * 1.6, step + 0.01)
    for item in matches[1:]:
        if item[0] - current[-1][0] <= max_gap:
            current.append(item)
        else:
            if len(current) >= stable_match_count:
                return current
            current = [item]
    return current if len(current) >= stable_match_count else []


def _scan_window(
    extractor: FrameExtractor,
    temp_root: Path,
    references: List[PillowImage.Image],
    begin: float,
    end: float,
    step: float,
    scorer: Callable[[PillowImage.Image, PillowImage.Image], float],
) -> Tuple[Optional[float], float]:
    best_timestamp: Optional[float] = None
    best_score = 0.0
    for timestamp in _timestamp_range(begin, end, step):
        frame_path = temp_root / "frame-{:.3f}.png".format(timestamp)
        extractor(timestamp, str(frame_path))
        with PillowImage.open(str(frame_path)) as raw_frame:
            score = _best_reference_score(raw_frame.convert("RGB"), references, scorer)
        try:
            frame_path.unlink()
        except OSError:
            pass
        if score > best_score:
            best_timestamp = timestamp
            best_score = score
    return best_timestamp, best_score


def _scan_window_matches(
    extractor: FrameExtractor,
    temp_root: Path,
    references: List[PillowImage.Image],
    begin: float,
    end: float,
    step: float,
    min_score: float,
    scorer: Callable[[PillowImage.Image, PillowImage.Image], float],
) -> List[Tuple[float, float]]:
    matches: List[Tuple[float, float]] = []
    for timestamp in _timestamp_range(begin, end, step):
        frame_path = temp_root / "frame-{:.3f}.png".format(timestamp)
        extractor(timestamp, str(frame_path))
        with PillowImage.open(str(frame_path)) as raw_frame:
            score = _best_reference_score(raw_frame.convert("RGB"), references, scorer)
        try:
            frame_path.unlink()
        except OSError:
            pass
        if score >= min_score:
            matches.append((timestamp, score))
    return matches


def _best_reference_score(
    frame: PillowImage.Image,
    references: List[PillowImage.Image],
    scorer: Callable[[PillowImage.Image, PillowImage.Image], float],
) -> float:
    return max(scorer(frame, reference) for reference in references)


def identify_frame_state(frame: PillowImage.Image, start_references: Optional[List[PillowImage.Image]] = None) -> FrameIdentifier:
    image = frame.convert("RGB")
    stage_score = (
        _best_reference_score(image, start_references, _stage_select_similarity)
        if start_references
        else 0.0
    )
    active_hud = _active_match_hud_penalty(image)
    gameplay_hud = _gameplay_hud_penalty(image)
    controller_prompt = _controller_prompt_score(image)
    character_select = _character_select_score(image)
    vs_screen = _vs_screen_score(image)
    post_match_menu = _post_match_menu_score(image)
    dark_transition = _dark_transition_score(image)
    stage_anchor = _stage_select_anchor_score(image)
    fight_health = _fight_health_bar_score(image)
    character_grid = _character_grid_score(image)
    character_layout = _character_select_layout_score(image)
    side_menu = _side_menu_score(image)
    whiteout = _whiteout_score(image)
    bright_intro = _bright_intro_title_score(image)
    poor_mosaic = _poor_stage_mosaic_score(image)
    center_intro = _center_character_intro_score(image)
    features = {
        "stage_score": stage_score,
        "stage_anchor": stage_anchor,
        "fight_health": fight_health,
        "character_grid": character_grid,
        "character_layout": character_layout,
        "side_menu": side_menu,
        "whiteout": whiteout,
        "bright_intro": bright_intro,
        "poor_mosaic": poor_mosaic,
        "center_intro": center_intro,
        "active_hud": active_hud,
        "gameplay_hud": gameplay_hud,
        "controller_prompt": controller_prompt,
        "character_select": character_select,
        "vs_screen": vs_screen,
        "post_match_menu": post_match_menu,
        "dark_transition": dark_transition,
    }
    stage_layout = _stage_select_layout_score(image)
    features["stage_layout"] = stage_layout
    if fight_health >= 0.18 or gameplay_hud >= 0.45:
        state, confidence = GAMEPLAY, max(fight_health, gameplay_hud, active_hud)
    elif side_menu >= 0.60:
        state, confidence = POST_MATCH_MENU, side_menu
    elif whiteout >= 0.60 or bright_intro >= 0.70 or center_intro >= 0.55:
        state, confidence = STAGE_INTRO, max(whiteout, bright_intro, center_intro)
    elif poor_mosaic >= 0.70:
        state, confidence = STAGE_INTRO, poor_mosaic
    elif stage_layout >= 0.62 and stage_anchor >= 0.25 and vs_screen < 0.58 and max(character_grid, character_layout) < 0.60:
        state, confidence = STAGE_SELECT, max(stage_score, stage_layout)
    elif max(active_hud, gameplay_hud) >= 0.55:
        state, confidence = GAMEPLAY, max(active_hud, gameplay_hud)
    elif controller_prompt >= 0.70:
        state, confidence = CONTROLLER_PROMPT, controller_prompt
    elif vs_screen >= 0.58:
        state, confidence = VS_SCREEN, vs_screen
    elif post_match_menu >= 0.55:
        state, confidence = POST_MATCH_MENU, post_match_menu
    elif character_select >= 0.60 or character_grid >= 0.60 or character_layout >= 0.60:
        state, confidence = CHARACTER_SELECT, max(character_select, character_grid, character_layout)
    elif stage_score >= 0.70 and stage_anchor >= 0.25:
        state, confidence = STAGE_SELECT, stage_score
    elif dark_transition >= 0.50:
        state, confidence = STAGE_INTRO, dark_transition
    else:
        state, confidence = UNKNOWN, max(features.values())
    return FrameIdentifier(state=state, confidence=confidence, features=features)


def _stage_select_similarity(frame: PillowImage.Image, reference: PillowImage.Image) -> float:
    frame_grid = _relative_crop(frame, 0.16, 0.52, 0.90, 0.80)
    reference_grid = _relative_crop(reference, 0.16, 0.52, 0.90, 0.80)
    card_grid = _resized_similarity(frame_grid, reference_grid, (180, 68))
    card_edges = _edge_similarity(frame_grid, reference_grid, (180, 68))
    title_band = _resized_similarity(
        _relative_crop(frame, 0.02, 0.06, 0.48, 0.22),
        _relative_crop(reference, 0.02, 0.06, 0.48, 0.22),
        (120, 36),
    )
    base_score = (card_edges * 0.55) + (card_grid * 0.30) + (title_band * 0.15)
    grid_presence = _stage_grid_presence(frame_grid) if _stage_grid_presence(reference_grid) >= 0.45 else 1.0
    stage_score = base_score * ((grid_presence * 0.70) + 0.30)
    title_score = _stage_title_score(frame)
    anchor_score = _stage_select_anchor_score(frame)
    layout_score = _stage_select_layout_score(frame)
    penalty_gate = 1.0 - max(title_score, anchor_score * 0.85)
    prompt_penalty = max(_controller_prompt_score(frame) - 0.55, 0.0) * penalty_gate
    vs_penalty = max(_vs_screen_score(frame) - 0.50, 0.0)
    low_layout_vs_penalty = max(_vs_screen_score(frame) - 0.38, 0.0) if layout_score < 0.66 else 0.0
    low_layout_active_penalty = max(_active_match_hud_penalty(frame) - 0.75, 0.0) if layout_score < 0.66 else 0.0
    setup_menu_penalty = max(_character_setup_menu_score(frame) - 0.70, 0.0) if layout_score < 0.70 else 0.0
    low_layout_prompt_penalty = max(_controller_prompt_score(frame) - 0.50, 0.0) if layout_score < 0.66 else 0.0
    candidate_score = max(stage_score, layout_score)
    if anchor_score < 0.25:
        candidate_score = min(candidate_score, 0.60 + (anchor_score * 0.40))
    if _character_grid_score(frame) >= 0.60:
        candidate_score = min(candidate_score, 0.62)
    if _character_select_layout_score(frame) >= 0.60:
        candidate_score = min(candidate_score, 0.62)
    if layout_score < 0.62 and _character_grid_score(frame) >= 0.25:
        candidate_score = min(candidate_score, 0.66)
    if _side_menu_score(frame) >= 0.60:
        candidate_score = min(candidate_score, 0.66)
    if _whiteout_score(frame) >= 0.60:
        candidate_score = min(candidate_score, 0.60)
    if _bright_intro_title_score(frame) >= 0.70:
        candidate_score = min(candidate_score, 0.66)
    if _poor_stage_mosaic_score(frame) >= 0.70:
        candidate_score = min(candidate_score, 0.66)
    if _center_character_intro_score(frame) >= 0.55:
        candidate_score = min(candidate_score, 0.66)
    if (
        layout_score < 0.55
        and anchor_score < 0.85
        and _center_dialog_content_score(frame) >= 0.30
    ):
        candidate_score = min(candidate_score, 0.66)
    return max(
        0.0,
        candidate_score
        - (_gameplay_hud_penalty(frame) * 0.35 * penalty_gate)
        - (_active_match_hud_penalty(frame) * 0.50 * penalty_gate)
        - (max(_gameplay_hud_penalty(frame) - 0.25, 0.0) * 1.20)
        - (max(_fight_health_bar_score(frame) - 0.16, 0.0) * 3.00)
        - (prompt_penalty * 1.25)
        - (vs_penalty * 1.25)
        - (low_layout_vs_penalty * 3.00)
        - (low_layout_active_penalty * 2.00)
        - (setup_menu_penalty * 2.20)
        - (low_layout_prompt_penalty * 1.80),
    )


def _center_dialog_similarity(frame: PillowImage.Image, reference: PillowImage.Image) -> float:
    similarity = _resized_similarity(
        _relative_crop(frame, 0.24, 0.32, 0.78, 0.64),
        _relative_crop(reference, 0.24, 0.32, 0.78, 0.64),
        (160, 72),
    )
    content = _center_dialog_content_score(frame)
    if content < 0.12:
        similarity = min(similarity, 0.58 + (content * 1.25))
    active_match = max(_active_match_hud_penalty(frame) - 0.55, 0.0)
    return max(
        0.0,
        similarity
        - (_gameplay_hud_penalty(frame) * 0.30)
        - (active_match * 1.20)
        - (_dark_transition_score(frame) * 0.45),
    )


def _center_dialog_content_score(image: PillowImage.Image) -> float:
    center = _relative_crop(image, 0.28, 0.34, 0.78, 0.66).convert("L")
    pixels = list(center.getdata())
    if not pixels:
        return 0.0
    stats = ImageStat.Stat(center)
    mid_fraction = sum(1 for value in pixels if 30 <= value <= 95) / float(len(pixels))
    bright_fraction = sum(1 for value in pixels if value >= 145) / float(len(pixels))
    mean_presence = min(max((stats.mean[0] - 8.0) / 45.0, 0.0), 1.0)
    edge_presence = min(_edge_density(center, 70) / 0.045, 1.0)
    return max(0.0, min(1.0, (mid_fraction * 0.45) + (bright_fraction * 1.6 * 0.15) + (mean_presence * 0.25) + (edge_presence * 0.15)))


def _resized_similarity(left: PillowImage.Image, right: PillowImage.Image, size: Tuple[int, int]) -> float:
    left_small = left.convert("L").resize(size)
    right_small = right.convert("L").resize(size)
    diff = ImageChops.difference(left_small, right_small)
    rms = sum(value * value for value in ImageStat.Stat(diff).rms) ** 0.5
    return max(0.0, 1.0 - (rms / 255.0))


def _edge_similarity(left: PillowImage.Image, right: PillowImage.Image, size: Tuple[int, int]) -> float:
    left_edges = ImageOps.autocontrast(left.convert("L").filter(ImageFilter.FIND_EDGES)).resize(size)
    right_edges = ImageOps.autocontrast(right.convert("L").filter(ImageFilter.FIND_EDGES)).resize(size)
    diff = ImageChops.difference(left_edges, right_edges)
    rms = sum(value * value for value in ImageStat.Stat(diff).rms) ** 0.5
    return max(0.0, 1.0 - (rms / 255.0))


def _stage_grid_presence(image: PillowImage.Image) -> float:
    grid = image.convert("L").resize((180, 68))
    edges = ImageOps.autocontrast(grid.filter(ImageFilter.FIND_EDGES))
    grid_stats = ImageStat.Stat(grid)
    edge_stats = ImageStat.Stat(edges)
    contrast_presence = min(grid_stats.stddev[0] / 35.0, 1.0)
    edge_presence = min(edge_stats.stddev[0] / 55.0, 1.0)
    return (contrast_presence * 0.55) + (edge_presence * 0.45)


def _stage_select_layout_score(image: PillowImage.Image) -> float:
    grid = _stage_grid_presence(_relative_crop(image, 0.16, 0.52, 0.90, 0.80))
    title = _stage_title_score(image)
    anchor = _stage_select_anchor_score(image)
    return max(0.0, min(1.0, (grid * 0.40) + (title * 0.40) + (anchor * 0.20)))


def _stage_select_anchor_score(image: PillowImage.Image) -> float:
    title = _stage_title_score(image)
    highlight = _stage_selection_highlight_score(image)
    return max(title, highlight)


def _stage_title_score(image: PillowImage.Image) -> float:
    title = _relative_crop(image, 0.02, 0.07, 0.46, 0.25).convert("L")
    pixels = list(title.getdata())
    if not pixels:
        return 0.0
    bright_fraction = sum(1 for value in pixels if value >= 170) / float(len(pixels))
    edge_fraction = _edge_density(title, 90)
    mean = ImageStat.Stat(title).mean[0]
    brightness = min(max((mean - 45.0) / 95.0, 0.0), 1.0)
    return max(0.0, min(1.0, (bright_fraction * 1.4 * 0.50) + (edge_fraction * 2.2 * 0.20) + (brightness * 0.30)))


def _stage_selection_highlight_score(image: PillowImage.Image) -> float:
    selector = _relative_crop(image, 0.03, 0.48, 0.28, 0.76).convert("HSV")
    pixels = list(selector.getdata())
    if not pixels:
        return 0.0
    magenta_red = sum(
        1
        for hue, saturation, value in pixels
        if (hue < 14 or hue > 232 or 218 < hue < 240) and saturation > 70 and value > 85
    ) / float(len(pixels))
    return max(0.0, min(1.0, magenta_red / 0.055))


def _controller_prompt_score(image: PillowImage.Image) -> float:
    dialog = _relative_crop(image, 0.24, 0.18, 0.82, 0.66).convert("L")
    center = _relative_crop(image, 0.34, 0.30, 0.76, 0.58).convert("L")
    dialog_stats = ImageStat.Stat(dialog)
    center_pixels = list(center.getdata())
    if not center_pixels:
        return 0.0
    bright_fraction = sum(1 for value in center_pixels if value >= 165) / float(len(center_pixels))
    dark_dialog = 1.0 - min(max((dialog_stats.mean[0] - 70.0) / 85.0, 0.0), 1.0)
    calm_hud = 1.0 - min(_active_match_hud_penalty(image), 1.0)
    stage_title = _stage_title_score(image)
    score = (bright_fraction * 2.8 * 0.55) + (dark_dialog * 0.25) + (calm_hud * 0.20)
    return max(0.0, min(1.0, score * (1.0 - (stage_title * 0.85))))


def _character_select_score(image: PillowImage.Image) -> float:
    lower = _relative_crop(image, 0.20, 0.52, 0.82, 0.92).convert("L")
    stats = ImageStat.Stat(lower)
    dense_edges = _edge_density(lower, 90)
    calm_hud = 1.0 - min(_active_match_hud_penalty(image), 1.0)
    return max(0.0, min(1.0, (dense_edges * 1.8 * 0.55) + (min(stats.stddev[0] / 65.0, 1.0) * 0.25) + (calm_hud * 0.20)))


def _character_grid_score(image: PillowImage.Image) -> float:
    grid = _relative_crop(image, 0.16, 0.52, 0.90, 0.80).convert("HSV")
    pixels = list(grid.getdata())
    if not pixels:
        return 0.0
    red_tiles = sum(
        1
        for hue, saturation, value in pixels
        if (hue < 14 or hue > 232) and saturation > 70 and value > 85
    ) / float(len(pixels))
    return max(0.0, min(1.0, (red_tiles - 0.12) / 0.22))


def _character_select_layout_score(image: PillowImage.Image) -> float:
    portrait_grid = _relative_crop(image, 0.22, 0.52, 0.70, 0.79).convert("L")
    preset_panel = _relative_crop(image, 0.50, 0.12, 0.94, 0.50).convert("L")
    portrait_edges = _edge_density(portrait_grid, 90)
    panel_mean = ImageStat.Stat(preset_panel).mean[0]
    portrait_score = max(0.0, min(1.0, (portrait_edges - 0.08) / 0.04))
    panel_score = max(0.0, min(1.0, (110.0 - panel_mean) / 55.0))
    return (portrait_score * 0.65) + (panel_score * 0.35)


def _side_menu_score(image: PillowImage.Image) -> float:
    return max(
        _menu_panel_score(_relative_crop(image, 0.02, 0.45, 0.32, 0.85)),
        _menu_panel_score(_relative_crop(image, 0.70, 0.45, 0.98, 0.85)),
    )


def _menu_panel_score(image: PillowImage.Image) -> float:
    menu = image.convert("HSV")
    pixels = list(menu.getdata())
    if not pixels:
        return 0.0
    red_fraction = sum(
        1
        for hue, saturation, value in pixels
        if (hue < 14 or hue > 232) and saturation > 60 and value > 70
    ) / float(len(pixels))
    dark_fraction = sum(1 for _, _, value in pixels if value < 70) / float(len(pixels))
    red_score = max(0.0, min(1.0, (red_fraction - 0.04) / 0.08))
    dark_score = max(0.0, min(1.0, (dark_fraction - 0.42) / 0.28))
    return (red_score * 0.55) + (dark_score * 0.45)


def _whiteout_score(image: PillowImage.Image) -> float:
    pixels = list(image.convert("HSV").getdata())
    if not pixels:
        return 0.0
    white_fraction = sum(1 for _, saturation, value in pixels if value > 210 and saturation < 45) / float(len(pixels))
    return max(0.0, min(1.0, (white_fraction - 0.45) / 0.30))


def _bright_intro_title_score(image: PillowImage.Image) -> float:
    title = _relative_crop(image, 0.03, 0.10, 0.30, 0.26).convert("L")
    pixels = list(title.getdata())
    if not pixels:
        return 0.0
    mean = ImageStat.Stat(title).mean[0]
    bright_fraction = sum(1 for value in pixels if value > 170) / float(len(pixels))
    low_edges = 1.0 - min(_edge_density(title, 90) / 0.06, 1.0)
    mean_score = max(0.0, min(1.0, (mean - 165.0) / 70.0))
    bright_score = max(0.0, min(1.0, (bright_fraction - 0.55) / 0.35))
    return (mean_score * 0.35) + (bright_score * 0.35) + (low_edges * 0.30)


def _poor_stage_mosaic_score(image: PillowImage.Image) -> float:
    grid = _relative_crop(image, 0.16, 0.52, 0.90, 0.80).convert("HSV")
    pixels = list(grid.getdata())
    if not pixels:
        return 0.0
    saturated_tiles = sum(1 for _, saturation, value in pixels if saturation > 80 and value > 80) / float(len(pixels))
    dark_fraction = sum(1 for _, _, value in pixels if value < 80) / float(len(pixels))
    low_saturation_score = max(0.0, min(1.0, (0.13 - saturated_tiles) / 0.08))
    dark_score = max(0.0, min(1.0, (dark_fraction - 0.50) / 0.16))
    return (low_saturation_score * 0.50) + (dark_score * 0.50)


def _center_character_intro_score(image: PillowImage.Image) -> float:
    body = _relative_crop(image, 0.25, 0.48, 0.75, 0.85).convert("HSV")
    pixels = list(body.getdata())
    if not pixels:
        return 0.0
    skin_fraction = sum(
        1
        for hue, saturation, value in pixels
        if 8 < hue < 30 and 40 < saturation < 150 and value > 90
    ) / float(len(pixels))
    bright_fraction = sum(1 for _, _, value in pixels if value > 170) / float(len(pixels))
    edge_density = _edge_density(body.convert("L"), 90)
    skin_score = max(0.0, min(1.0, (skin_fraction - 0.12) / 0.18))
    bright_score = max(0.0, min(1.0, (bright_fraction - 0.25) / 0.25))
    low_edge_score = max(0.0, min(1.0, (0.055 - edge_density) / 0.035))
    return (skin_score * 0.45) + (bright_score * 0.25) + (low_edge_score * 0.30)


def _vs_screen_score(image: PillowImage.Image) -> float:
    center = _relative_crop(image, 0.32, 0.34, 0.68, 0.64).convert("L")
    vs_mark = _relative_crop(image, 0.40, 0.32, 0.62, 0.62).convert("HSV")
    lower_names = _relative_crop(image, 0.02, 0.58, 0.98, 0.83).convert("L")
    center_edges = _edge_density(center, 90)
    lower_edges = _edge_density(lower_names, 105)
    mark_pixels = list(vs_mark.getdata())
    pink_mark = 0.0
    if mark_pixels:
        pink_mark = sum(
            1
            for hue, saturation, value in mark_pixels
            if (hue < 18 or hue > 220) and saturation > 75 and value > 95
        ) / float(len(mark_pixels))
    calm_hud = 1.0 - min(_active_match_hud_penalty(image), 1.0)
    return max(0.0, min(1.0, (pink_mark * 2.4 * 0.55) + (center_edges * 1.7 * 0.20) + (lower_edges * 1.4 * 0.10) + (calm_hud * 0.15)))


def _post_match_menu_score(image: PillowImage.Image) -> float:
    right_menu = _relative_crop(image, 0.70, 0.42, 0.96, 0.78).convert("L")
    center_dialog = _relative_crop(image, 0.28, 0.34, 0.78, 0.66).convert("L")
    right_edges = _edge_density(right_menu, 70)
    center_edges = _edge_density(center_dialog, 70)
    dark = 1.0 - min(ImageStat.Stat(center_dialog).mean[0] / 120.0, 1.0)
    return max(0.0, min(1.0, (max(right_edges, center_edges) * 3.0 * 0.65) + (dark * 0.35)))


def _dark_transition_score(image: PillowImage.Image) -> float:
    grayscale = image.convert("L")
    stats = ImageStat.Stat(grayscale)
    return max(0.0, min(1.0, (1.0 - min(stats.mean[0] / 65.0, 1.0)) * (1.0 - min(stats.stddev[0] / 55.0, 1.0))))


def _edge_density(image: PillowImage.Image, threshold: int) -> float:
    edges = ImageOps.autocontrast(image.convert("L").filter(ImageFilter.FIND_EDGES))
    pixels = list(edges.getdata())
    if not pixels:
        return 0.0
    return sum(1 for value in pixels if value >= threshold) / float(len(pixels))


def _relative_crop(image: PillowImage.Image, left: float, top: float, right: float, bottom: float) -> PillowImage.Image:
    width, height = image.size
    return image.crop((int(width * left), int(height * top), int(width * right), int(height * bottom)))


def _gameplay_hud_penalty(image: PillowImage.Image) -> float:
    left_bar = _relative_crop(image, 0.10, 0.08, 0.45, 0.18).convert("HSV")
    right_bar = _relative_crop(image, 0.55, 0.08, 0.90, 0.18).convert("HSV")
    return max(_saturated_bright_fraction(left_bar), _saturated_bright_fraction(right_bar))


def _active_match_hud_penalty(image: PillowImage.Image) -> float:
    top_band = _relative_crop(image, 0.08, 0.02, 0.92, 0.16).convert("L")
    timer = _relative_crop(image, 0.42, 0.02, 0.58, 0.15).convert("L")
    bars = _relative_crop(image, 0.10, 0.05, 0.90, 0.13).convert("L")
    top_stats = ImageStat.Stat(top_band)
    timer_stats = ImageStat.Stat(timer)
    bar_stats = ImageStat.Stat(bars)
    top_activity = min(max(top_stats.stddev[0] - 35.0, 0.0) / 35.0, 1.0)
    timer_activity = min(max(timer_stats.mean[0] - 48.0, 0.0) / 70.0, 1.0)
    bar_activity = min(max(bar_stats.stddev[0] - 42.0, 0.0) / 35.0, 1.0)
    return max(top_activity, timer_activity, bar_activity)


def _fight_health_bar_score(image: PillowImage.Image) -> float:
    left_bar = _relative_crop(image, 0.12, 0.06, 0.44, 0.14).convert("HSV")
    right_bar = _relative_crop(image, 0.56, 0.06, 0.88, 0.14).convert("HSV")
    return max(_orange_health_fraction(left_bar), _orange_health_fraction(right_bar))


def _character_setup_menu_score(image: PillowImage.Image) -> float:
    panel_scores = []
    for crop_box in ((0.08, 0.16, 0.43, 0.88), (0.57, 0.16, 0.92, 0.88)):
        panel = _relative_crop(image, *crop_box).convert("L")
        pixels = list(panel.getdata())
        if not pixels:
            panel_scores.append(0.0)
            continue
        dark_fraction = sum(1 for value in pixels if value < 45) / float(len(pixels))
        bright_fraction = sum(1 for value in pixels if value > 145) / float(len(pixels))
        edge_fraction = _edge_density(panel, 70)
        panel_scores.append(
            max(
                0.0,
                min(
                    1.0,
                    (min(dark_fraction / 0.70, 1.0) * 0.45)
                    + (min(edge_fraction / 0.05, 1.0) * 0.35)
                    + (min(bright_fraction / 0.05, 1.0) * 0.20),
                ),
            )
        )
    return min(panel_scores) if panel_scores else 0.0


def _orange_health_fraction(image: PillowImage.Image) -> float:
    pixels = list(image.getdata())
    if not pixels:
        return 0.0
    health_bar = sum(
        1
        for hue, saturation, value in pixels
        if (hue <= 10 or hue >= 235 or 12 <= hue <= 42) and saturation >= 65 and value >= 120
    )
    return health_bar / float(len(pixels))


def _saturated_bright_fraction(image: PillowImage.Image) -> float:
    pixels = list(image.getdata())
    if not pixels:
        return 0.0
    colorful = sum(1 for _hue, saturation, value in pixels if saturation >= 80 and value >= 120)
    return colorful / float(len(pixels))


def _load_references(path: str) -> List[PillowImage.Image]:
    clean_path = path.strip()
    if not clean_path:
        raise ValueError("Choose both a stage select start reference and a press-start end reference.")
    source = Path(clean_path)
    if not source.exists():
        raise ValueError("Reference image does not exist: {}".format(clean_path))
    if source.is_dir():
        paths = [
            item
            for item in sorted(source.iterdir())
            if item.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        ]
    else:
        paths = [source]
    if not paths:
        raise ValueError("No reference images found in: {}".format(clean_path))
    return [PillowImage.open(str(item)).convert("RGB") for item in paths]


def _timestamp_range(begin: float, end: float, step: float) -> Iterable[float]:
    current = max(0.0, begin)
    final = max(current, end)
    while current <= final + 0.0001:
        yield round(current, 3)
        current += step


def _seconds_to_log_time(seconds: float) -> str:
    whole = int(max(seconds, 0.0))
    return "{:02d}:{:02d}:{:02d}".format(whole // 3600, (whole % 3600) // 60, whole % 60)
