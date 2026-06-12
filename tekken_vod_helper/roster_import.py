import html
import json
import re
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from .util import slugify, unique_sorted


DEFAULT_ROSTER_URL = "https://tekken.com/fighters"


@dataclass
class RosterCharacter:
    name: str
    slug: str
    file_name: str
    url: str
    alt_text: str = ""


def extract_roster_from_html(path_text: str) -> List[RosterCharacter]:
    path = Path(path_text)
    text = path.read_text(encoding="utf-8", errors="replace")
    return extract_roster_from_html_text(text)


def fetch_roster_from_url(url: str = DEFAULT_ROSTER_URL) -> List[RosterCharacter]:
    return extract_roster_from_html_text(fetch_roster_html(url))


def fetch_roster_html(url: str = DEFAULT_ROSTER_URL) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "tekken-vod-helper/0.1",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    return body.decode(charset, errors="replace")


def extract_roster_from_html_text(text: str) -> List[RosterCharacter]:
    decoded = html.unescape(text).replace("&q;", '"')
    assets = _extract_assets(decoded)

    records: List[RosterCharacter] = []
    seen = set()
    thumbnail_pattern = re.compile(
        r'\{"__typename":"Thumbnail","title":"([^"]+)",'
        r'"slug":"([^"]+)","image":\{"__ref":"([^"]+)"\}',
        re.S,
    )
    for title, slug, asset_ref in thumbnail_pattern.findall(decoded):
        asset = assets.get(asset_ref)
        if not asset or not asset.get("url"):
            continue
        key = slug.casefold()
        if key in seen:
            continue
        seen.add(key)
        records.append(
            RosterCharacter(
                name=title,
                slug=slug,
                file_name=asset.get("file_name", ""),
                url=asset.get("url", ""),
                alt_text=asset.get("alt_text", ""),
            )
        )
    return records


def merge_character_names(existing: Iterable[str], roster: Iterable[RosterCharacter]) -> List[str]:
    return unique_sorted(list(existing) + [record.name for record in roster])


def write_characters_file(path_text: str, roster: Iterable[RosterCharacter]) -> None:
    path = Path(path_text)
    names = unique_sorted(record.name for record in roster)
    path.write_text("\n".join(names) + "\n", encoding="utf-8")


def download_portraits(
    roster: Iterable[RosterCharacter],
    output_dir_text: str,
    logger: Optional[Callable[[str], None]] = None,
) -> List[Path]:
    output_dir = Path(output_dir_text)
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    metadata = []

    for record in roster:
        if not record.url:
            continue
        extension = _extension_from_file_name(record.file_name)
        output_path = output_dir / "{}{}".format(slugify(record.name), extension)
        if logger:
            logger("Downloading {} portrait...".format(record.name))
        request = urllib.request.Request(
            record.url,
            headers={"User-Agent": "tekken-vod-helper/0.1"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            output_path.write_bytes(response.read())
        written.append(output_path)
        metadata.append({**asdict(record), "local_file": output_path.name})

    metadata_path = output_dir / "roster_portraits.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return written


def _extract_assets(decoded: str) -> Dict[str, Dict[str, str]]:
    assets: Dict[str, Dict[str, str]] = {}
    asset_pattern = re.compile(
        r'"Asset:([^"]+)":\{"__typename":"Asset",(.*?)\}(?=,\s*"|}\s*</script>|})',
        re.S,
    )
    for match in asset_pattern.finditer(decoded):
        asset_ref = "Asset:" + match.group(1)
        body = match.group(2)
        file_name = _json_string_field(body, "fileName")
        url = _json_string_field(body, "url")
        if not file_name or not url:
            continue
        assets[asset_ref] = {
            "file_name": file_name,
            "url": url,
            "alt_text": _json_string_field(body, "altText"),
        }
    return assets


def _json_string_field(text: str, field_name: str) -> str:
    match = re.search(r'"{}":(?:"([^"]*)"|null)'.format(re.escape(field_name)), text)
    if not match:
        return ""
    return match.group(1) or ""


def _extension_from_file_name(file_name: str) -> str:
    suffix = Path(file_name).suffix.lower()
    if suffix in (".png", ".jpg", ".jpeg", ".webp"):
        return suffix
    return ".webp"
