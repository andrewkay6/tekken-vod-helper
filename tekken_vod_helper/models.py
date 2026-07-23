from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MatchSegment:
    start: float
    end: Optional[float] = None
    player1: str = ""
    player2: str = ""
    character1: str = ""
    character2: str = ""
    round_name: str = ""
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "player1": self.player1,
            "player2": self.player2,
            "character1": self.character1,
            "character2": self.character2,
            "round_name": self.round_name,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MatchSegment":
        raw_end = data.get("end", None)
        end = None if raw_end is None or raw_end == "" else float(raw_end)

        return cls(
            start=float(data.get("start", 0.0)),
            end=end,
            player1=str(data.get("player1", "") or ""),
            player2=str(data.get("player2", "") or ""),
            character1=str(data.get("character1", "") or ""),
            character2=str(data.get("character2", "") or ""),
            round_name=str(data.get("round_name", "") or ""),
            notes=str(data.get("notes", "") or ""),
        )


@dataclass
class OverlayState:
    description: str = ""
    subtitle: str = ""
    p1name: str = ""
    p1country: str = ""
    p1score: int = 0
    p1team: str = ""
    p2name: str = ""
    p2country: str = ""
    p2score: int = 0
    p2team: str = ""
    font: str = "Bahnschrift"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "description": self.description,
            "subtitle": self.subtitle,
            "p1name": self.p1name,
            "p1country": self.p1country,
            "p1score": self.p1score,
            "p1team": self.p1team,
            "p2name": self.p2name,
            "p2country": self.p2country,
            "p2score": self.p2score,
            "p2team": self.p2team,
            "font": self.font,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OverlayState":
        return cls(
            description=str(data.get("description", "") or ""),
            subtitle=str(data.get("subtitle", "") or ""),
            p1name=str(data.get("p1name", "") or ""),
            p1country=str(data.get("p1country", "") or ""),
            p1score=int(data.get("p1score", 0) or 0),
            p1team=str(data.get("p1team", "") or ""),
            p2name=str(data.get("p2name", "") or ""),
            p2country=str(data.get("p2country", "") or ""),
            p2score=int(data.get("p2score", 0) or 0),
            p2team=str(data.get("p2team", "") or ""),
            font=str(data.get("font", "Bahnschrift") or "Bahnschrift"),
        )


@dataclass
class ObsSettings:
    host: str = "127.0.0.1"
    port: int = 4455
    password: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "password": self.password,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ObsSettings":
        return cls(
            host=str(data.get("host", "127.0.0.1") or "127.0.0.1"),
            port=int(data.get("port", 4455) or 4455),
            password=str(data.get("password", "") or ""),
        )


@dataclass
class StartggSettings:
    token: str = ""
    tournament_slug: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tournament_slug": self.tournament_slug,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StartggSettings":
        return cls(
            tournament_slug=str(data.get("tournament_slug", "") or ""),
        )


@dataclass
class ProjectState:
    video_path: str = ""
    event_name: str = ""
    description_boilerplate: str = ""
    output_dir: str = ""
    portrait_dir: str = ""
    thumbnail_background_path: str = ""
    ffmpeg_path: str = ""
    ffprobe_path: str = ""
    reencode: bool = False
    duration: float = 0.0
    players: List[str] = field(default_factory=list)
    characters: List[str] = field(default_factory=list)
    matches: List[MatchSegment] = field(default_factory=list)
    overlay: OverlayState = field(default_factory=OverlayState)
    obs: ObsSettings = field(default_factory=ObsSettings)
    startgg: StartggSettings = field(default_factory=StartggSettings)

    def sorted_matches(self) -> List[MatchSegment]:
        return sorted(self.matches, key=lambda segment: segment.start)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "video_path": self.video_path,
            "event_name": self.event_name,
            "description_boilerplate": self.description_boilerplate,
            "output_dir": self.output_dir,
            "portrait_dir": self.portrait_dir,
            "thumbnail_background_path": self.thumbnail_background_path,
            "ffmpeg_path": self.ffmpeg_path,
            "ffprobe_path": self.ffprobe_path,
            "reencode": self.reencode,
            "duration": self.duration,
            "players": self.players,
            "characters": self.characters,
            "matches": [match.to_dict() for match in self.sorted_matches()],
            "overlay": self.overlay.to_dict(),
            "obs": self.obs.to_dict(),
            "startgg": self.startgg.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectState":
        return cls(
            video_path=str(data.get("video_path", "") or ""),
            event_name=str(data.get("event_name", "") or ""),
            description_boilerplate=str(data.get("description_boilerplate", "") or ""),
            output_dir=str(data.get("output_dir", "") or ""),
            portrait_dir=str(data.get("portrait_dir", "") or ""),
            thumbnail_background_path=str(data.get("thumbnail_background_path", "") or ""),
            ffmpeg_path=str(data.get("ffmpeg_path", "") or ""),
            ffprobe_path=str(data.get("ffprobe_path", "") or ""),
            reencode=bool(data.get("reencode", False)),
            duration=float(data.get("duration", 0.0) or 0.0),
            players=_string_list(data.get("players")),
            characters=_string_list(data.get("characters")),
            matches=[
                MatchSegment.from_dict(item)
                for item in data.get("matches", [])
                if isinstance(item, dict)
            ],
            overlay=OverlayState.from_dict(data.get("overlay", {}) if isinstance(data.get("overlay"), dict) else {}),
            obs=ObsSettings.from_dict(data.get("obs", {}) if isinstance(data.get("obs"), dict) else {}),
            startgg=StartggSettings.from_dict(data.get("startgg", {}) if isinstance(data.get("startgg"), dict) else {}),
        )


@dataclass
class ExportJob:
    match: MatchSegment
    index: int
    start: float
    end: float
    folder_name: str
    clip_path: str
    thumbnail_path: str
    metadata_path: str
    title_path: str
    description_path: str


def _string_list(value: Optional[Any]) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]
