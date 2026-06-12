import re
from pathlib import Path
from typing import Iterable, List


def seconds_to_timestamp(seconds: float, millis: bool = True) -> str:
    seconds = max(float(seconds), 0.0)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if not millis:
        return "{:02d}:{:02d}:{:02d}".format(hours, minutes, secs)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms == 1000:
        secs += 1
        ms = 0
    return "{:02d}:{:02d}:{:02d}.{:03d}".format(hours, minutes, secs, ms)


def parse_timestamp(value: str) -> float:
    value = value.strip()
    if not value:
        return 0.0
    if re.fullmatch(r"\d+(\.\d+)?", value):
        return float(value)

    parts = value.split(":")
    if len(parts) not in (2, 3):
        raise ValueError("Use seconds, MM:SS, or HH:MM:SS")

    parts = ["0"] * (3 - len(parts)) + parts
    hours, minutes, seconds = parts
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def slugify(value: str, fallback: str = "item") -> str:
    value = value.strip().replace("&", " and ")
    value = re.sub(r"[^\w\s.-]+", "", value, flags=re.UNICODE)
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"_+", "_", value).strip("._ ")
    return value or fallback


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def unique_sorted(values: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        cleaned = str(value).strip()
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return sorted(result, key=str.casefold)


def safe_relative_name(path_text: str) -> str:
    return slugify(Path(path_text).stem, "video")
