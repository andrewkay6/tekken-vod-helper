import json
import re
import unicodedata
from difflib import SequenceMatcher
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from .windows_credentials import (
    read_youtube_client_id,
    read_youtube_client_secret,
    read_youtube_refresh_token,
    write_youtube_refresh_token,
)


AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_API_ROOT = "https://www.googleapis.com/youtube/v3"
YOUTUBE_UPLOAD_ROOT = "https://www.googleapis.com/upload/youtube/v3"
READONLY_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
MANAGE_SCOPE = "https://www.googleapis.com/auth/youtube"


class YouTubeAuthError(Exception):
    pass


class YouTubeApiError(Exception):
    pass


@dataclass
class YouTubeCredentials:
    client_id: str
    client_secret: str
    refresh_token: str = ""


@dataclass
class YouTubeVideo:
    video_id: str
    title: str
    privacy_status: str
    upload_status: str
    processing_status: str
    published_at: str
    channel_title: str
    description: str
    tags: List[str]
    thumbnail_url: str


def read_stored_credentials(require_refresh_token: bool = True) -> YouTubeCredentials:
    credentials = YouTubeCredentials(
        client_id=read_youtube_client_id(),
        client_secret=read_youtube_client_secret(),
        refresh_token=read_youtube_refresh_token(),
    )
    if not credentials.client_id or not credentials.client_secret:
        raise YouTubeAuthError("YouTube OAuth client ID/secret are not stored in Windows Credential Manager.")
    if require_refresh_token and not credentials.refresh_token:
        raise YouTubeAuthError("YouTube refresh token is not stored yet. Run the OAuth sign-in helper first.")
    return credentials


def authorization_url(client_id: str, redirect_uri: str, state: str, scope: str = MANAGE_SCOPE) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return "{}?{}".format(AUTH_URL, urllib.parse.urlencode(params))


def exchange_code_for_refresh_token(code: str, redirect_uri: str, credentials: Optional[YouTubeCredentials] = None) -> str:
    credentials = credentials or read_stored_credentials(require_refresh_token=False)
    payload = {
        "code": code,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    response = _post_form(TOKEN_URL, payload)
    refresh_token = str(response.get("refresh_token", "") or "")
    if not refresh_token:
        raise YouTubeAuthError("Google did not return a refresh token. Re-run consent with prompt=consent.")
    write_youtube_refresh_token(refresh_token)
    return refresh_token


def refresh_access_token(credentials: Optional[YouTubeCredentials] = None) -> str:
    credentials = credentials or read_stored_credentials()
    payload = {
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "refresh_token": credentials.refresh_token,
        "grant_type": "refresh_token",
    }
    response = _post_form(TOKEN_URL, payload)
    access_token = str(response.get("access_token", "") or "")
    if not access_token:
        raise YouTubeAuthError("Google did not return an access token.")
    return access_token


def list_channel_videos(max_results: int = 25, access_token: Optional[str] = None) -> List[YouTubeVideo]:
    token = access_token or refresh_access_token()
    uploads_playlist_id = _uploads_playlist_id(token)
    playlist_items = _api_get(
        "/playlistItems",
        token,
        {
            "part": "snippet,contentDetails,status",
            "playlistId": uploads_playlist_id,
            "maxResults": str(max(1, min(max_results, 50))),
        },
    ).get("items", [])
    video_ids = [
        str(item.get("contentDetails", {}).get("videoId", "") or "")
        for item in playlist_items
        if isinstance(item, dict)
    ]
    video_ids = [video_id for video_id in video_ids if video_id]
    if not video_ids:
        return []
    videos = _api_get(
        "/videos",
        token,
        {
            "part": "snippet,status,processingDetails",
            "id": ",".join(video_ids),
            "maxResults": str(len(video_ids)),
        },
    ).get("items", [])
    return [_video_from_api_item(item) for item in videos if isinstance(item, dict)]


def update_video_metadata(
    video_id: str,
    title: str,
    description: str,
    tags: Optional[List[str]] = None,
    privacy_status: str = "private",
    self_declared_made_for_kids: bool = False,
    thumbnail_path: str = "",
    access_token: Optional[str] = None,
) -> Dict[str, Any]:
    token = access_token or refresh_access_token()
    payload = {
        "id": video_id,
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags or [],
            "categoryId": "20",
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": bool(self_declared_made_for_kids),
        },
    }
    response = _api_put_json("/videos", token, {"part": "snippet,status"}, payload)
    if thumbnail_path:
        set_video_thumbnail(video_id, thumbnail_path, access_token=token)
    return response


def set_video_thumbnail(video_id: str, thumbnail_path: str, access_token: Optional[str] = None) -> Dict[str, Any]:
    token = access_token or refresh_access_token()
    url = "{}{}?{}".format(
        YOUTUBE_UPLOAD_ROOT,
        "/thumbnails/set",
        urllib.parse.urlencode({"videoId": video_id, "uploadType": "media"}),
    )
    data = _read_binary_file(thumbnail_path)
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": "Bearer {}".format(token),
            "Content-Type": _thumbnail_content_type(thumbnail_path),
        },
    )
    return _json_request(request)


def list_playlists(access_token: Optional[str] = None) -> List[Dict[str, str]]:
    token = access_token or refresh_access_token()
    playlists = []
    params = {"part": "snippet", "mine": "true", "maxResults": "50"}
    while True:
        response = _api_get("/playlists", token, params)
        playlists.extend({"id": item["id"], "title": item["snippet"]["title"]} for item in response.get("items", []))
        if not response.get("nextPageToken"):
            return playlists
        params["pageToken"] = response["nextPageToken"]


def suggest_playlist(event_name: str, playlists: List[Dict[str, str]]) -> str:
    def normalize(value):
        value = unicodedata.normalize("NFKD", value.casefold())
        return " ".join(re.findall(r"[a-z0-9]+", "".join(c for c in value if not unicodedata.combining(c))))

    event = normalize(event_name)
    if not event:
        return ""
    ranked = []
    for playlist in playlists:
        title = normalize(playlist["title"])
        # Tournament numbers must agree; a similar name for another event is not a match.
        if re.findall(r"\d+", title) != re.findall(r"\d+", event):
            continue
        score = SequenceMatcher(None, event, title).ratio()
        ranked.append((score, playlist["id"]))
    ranked.sort(reverse=True)
    if not ranked or ranked[0][0] < 0.6 or (len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.08):
        return ""
    return ranked[0][1]


def add_video_to_playlist(video_id: str, playlist_id: str, access_token: Optional[str] = None) -> Dict[str, Any]:
    token = access_token or refresh_access_token()
    existing = _api_get("/playlistItems", token, {
        "part": "id", "playlistId": playlist_id, "videoId": video_id, "maxResults": "1",
    })
    if existing.get("items"):
        return {"already_present": True}
    payload = {"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}
    request = urllib.request.Request(
        YOUTUBE_API_ROOT + "/playlistItems?part=snippet", data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json; charset=utf-8"},
    )
    return _json_request(request)


def fit_video_title(title: str, event_name: str = "") -> str:
    title = title.strip()
    if len(title) <= 100:
        return title
    suffix = " - " + event_name.strip()
    retained_suffix = ""
    if event_name.strip() and title.endswith(suffix):
        core = title[:-len(suffix)].rstrip()
        tournament = re.sub(r"\s*\([^()]*\)\s*$", "", event_name.strip()).split(" - ", 1)[0].strip()
        retained_suffix = " - " + tournament
        title = core + retained_suffix
        if len(title) > 100:
            # The last component before the tournament is the round.
            core = core.rsplit(" - ", 1)[0]
            title = core + retained_suffix
    if len(title) <= 100:
        return title
    # Retain the tournament even when exceptionally long player names need shortening.
    budget = 99 - len(retained_suffix)
    if budget < 1:
        raise ValueError("Tournament name is too long for a YouTube title. Shorten the event name.")
    core = title[:-len(retained_suffix)] if retained_suffix else title
    shortened = core[:budget].rsplit(" ", 1)[0]
    return (shortened if shortened else core[:budget]).rstrip(" -") + "…" + retained_suffix


def videos_as_rows(videos: Iterable[YouTubeVideo]) -> List[Dict[str, str]]:
    return [
        {
            "video_id": video.video_id,
            "title": video.title,
            "privacy_status": video.privacy_status,
            "upload_status": video.upload_status,
            "processing_status": video.processing_status,
            "published_at": video.published_at,
            "channel_title": video.channel_title,
            "description": video.description,
            "tags": ", ".join(video.tags),
            "thumbnail_url": video.thumbnail_url,
        }
        for video in videos
    ]


def _uploads_playlist_id(access_token: str) -> str:
    response = _api_get("/channels", access_token, {"part": "contentDetails", "mine": "true"})
    items = response.get("items", [])
    if not items:
        raise YouTubeApiError("YouTube did not return a channel for this account.")
    playlist_id = str(
        items[0]
        .get("contentDetails", {})
        .get("relatedPlaylists", {})
        .get("uploads", "")
        or ""
    )
    if not playlist_id:
        raise YouTubeApiError("YouTube did not return an uploads playlist for this channel.")
    return playlist_id


def _video_from_api_item(item: Dict[str, Any]) -> YouTubeVideo:
    snippet = item.get("snippet", {}) if isinstance(item.get("snippet"), dict) else {}
    status = item.get("status", {}) if isinstance(item.get("status"), dict) else {}
    processing = item.get("processingDetails", {}) if isinstance(item.get("processingDetails"), dict) else {}
    tags = snippet.get("tags", [])
    thumbnails = snippet.get("thumbnails", {}) if isinstance(snippet.get("thumbnails"), dict) else {}
    return YouTubeVideo(
        video_id=str(item.get("id", "") or ""),
        title=str(snippet.get("title", "") or ""),
        privacy_status=str(status.get("privacyStatus", "") or ""),
        upload_status=str(status.get("uploadStatus", "") or ""),
        processing_status=str(processing.get("processingStatus", "") or ""),
        published_at=str(snippet.get("publishedAt", "") or ""),
        channel_title=str(snippet.get("channelTitle", "") or ""),
        description=str(snippet.get("description", "") or ""),
        tags=[str(tag) for tag in tags] if isinstance(tags, list) else [],
        thumbnail_url=_best_thumbnail_url(thumbnails),
    )


def _best_thumbnail_url(thumbnails: Dict[str, Any]) -> str:
    for name in ("maxres", "standard", "high", "medium", "default"):
        item = thumbnails.get(name)
        if isinstance(item, dict) and item.get("url"):
            return str(item["url"])
    return ""


def _api_get(path: str, access_token: str, params: Dict[str, str]) -> Dict[str, Any]:
    url = "{}{}?{}".format(YOUTUBE_API_ROOT, path, urllib.parse.urlencode(params))
    request = urllib.request.Request(url, headers={"Authorization": "Bearer {}".format(access_token)})
    return _json_request(request)


def _api_put_json(path: str, access_token: str, params: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
    url = "{}{}?{}".format(YOUTUBE_API_ROOT, path, urllib.parse.urlencode(params))
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="PUT",
        headers={
            "Authorization": "Bearer {}".format(access_token),
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    return _json_request(request)


def _post_form(url: str, payload: Dict[str, str]) -> Dict[str, Any]:
    data = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    return _json_request(request)


def _read_binary_file(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _thumbnail_content_type(path: str) -> str:
    suffix = str(path).lower().rsplit(".", 1)[-1]
    if suffix in ("jpg", "jpeg"):
        return "image/jpeg"
    if suffix == "png":
        return "image/png"
    return "application/octet-stream"


def _json_request(request: urllib.request.Request) -> Dict[str, Any]:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise YouTubeApiError("YouTube API request failed with HTTP {}: {}".format(exc.code, detail)) from exc
    except urllib.error.URLError as exc:
        raise YouTubeApiError("YouTube API request failed: {}".format(exc.reason)) from exc


# --- Resumable video upload -------------------------------------------------------------------------
#
# Full event VODs are 30-120 GB, so uploads use YouTube's resumable protocol: one session URI, sent in
# fixed chunks (multiples of 256 KiB). The session URI is saved next to a state file so a crashed or
# rebooted process resumes from the byte YouTube confirms, instead of starting over. Sessions last about
# a week. Access tokens last an hour and are refreshed on a timer and on any 401.

UPLOAD_CHUNK = 64 * 1024 * 1024
_RETRYABLE = {500, 502, 503, 504}


def upload_video(
    path: str,
    title: str,
    description: str = "",
    tags: Optional[List[str]] = None,
    privacy_status: str = "unlisted",
    state_path: Optional[str] = None,
    chunk_size: int = UPLOAD_CHUNK,
    max_retries: int = 8,
    progress: Optional[Any] = None,
    token_provider: Optional[Any] = None,
    _sleep: Any = None,
) -> Dict[str, Any]:
    """Upload ``path`` and return the created video resource (``id`` is the video id).

    ``state_path`` stores the session URI and file identity. If it exists and matches this file, the
    upload resumes. ``progress(sent, total)`` is called after each confirmed chunk.
    """
    import http.client
    import os
    import time

    if chunk_size % (256 * 1024):
        raise ValueError("chunk_size must be a multiple of 256 KiB")
    sleep = _sleep or time.sleep
    get_token = token_provider or refresh_access_token
    total = os.path.getsize(path)
    stat = os.stat(path)
    identity = {"path": os.path.abspath(path), "bytes": total, "mtime": int(stat.st_mtime)}

    token = {"value": get_token(), "at": time.monotonic()}

    def bearer() -> str:
        if time.monotonic() - token["at"] > 40 * 60:
            token["value"], token["at"] = get_token(), time.monotonic()
        return "Bearer {}".format(token["value"])

    def renew() -> None:
        token["value"], token["at"] = get_token(), time.monotonic()

    def request(method: str, url: str, body: bytes = b"", headers: Optional[Dict[str, str]] = None):
        parts = urllib.parse.urlsplit(url)
        conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(parts.netloc, timeout=300)
        target = parts.path + ("?" + parts.query if parts.query else "")
        all_headers = {"Authorization": bearer()}
        all_headers.update(headers or {})
        try:
            conn.request(method, target, body=body, headers=all_headers)
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
        finally:
            conn.close()

    state: Dict[str, Any] = {}
    if state_path and os.path.exists(state_path):
        with open(state_path, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        if saved.get("identity") == identity and saved.get("session_uri"):
            state = saved
        elif saved.get("video_id"):
            raise YouTubeApiError("state file {} records a finished upload ({}) of a different file".format(
                state_path, saved["video_id"]))

    def save_state() -> None:
        if not state_path:
            return
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
        os.replace(tmp, state_path)

    if state.get("video_id"):
        return {"id": state["video_id"], "resumed": "already-complete"}

    resuming = bool(state)
    if not state:
        metadata = {
            "snippet": {"title": title, "description": description, "tags": tags or [], "categoryId": "20"},
            "status": {"privacyStatus": privacy_status, "selfDeclaredMadeForKids": False},
        }
        url = "{}/videos?{}".format(YOUTUBE_UPLOAD_ROOT, urllib.parse.urlencode(
            {"uploadType": "resumable", "part": "snippet,status"}))
        body = json.dumps(metadata).encode("utf-8")
        for attempt in range(max_retries + 1):
            status, headers, data = request("POST", url, body, {
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(total),
                "X-Upload-Content-Type": "video/*",
            })
            if status == 401 and attempt == 0:
                renew()
                continue
            if status in _RETRYABLE and attempt < max_retries:
                sleep(min(2 ** attempt, 60))
                continue
            if status != 200 or "location" not in headers:
                raise YouTubeApiError("could not start upload: HTTP {} {}".format(
                    status, data.decode("utf-8", "replace")[:400]))
            break
        state = {"identity": identity, "session_uri": headers["location"], "title": title,
                 "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        save_state()

    session = state["session_uri"]

    def confirmed_offset() -> int:
        """Ask YouTube how many bytes it holds (308 + Range), or finish if it already has them all."""
        status, headers, data = request("PUT", session, b"", {"Content-Range": "bytes */{}".format(total)})
        if status in (200, 201):
            raise _Finished(json.loads(data.decode("utf-8")))
        if status == 308:
            rng = headers.get("range")
            return int(rng.rsplit("-", 1)[1]) + 1 if rng else 0
        if status == 401:
            renew()
            raise _Retry("token expired")
        if status in _RETRYABLE:
            raise _Retry("HTTP {}".format(status))
        raise YouTubeApiError("upload session check failed: HTTP {} {}".format(
            status, data.decode("utf-8", "replace")[:400]))

    failures = 0
    offset = 0
    if resuming:
        try:
            offset = confirmed_offset()
        except _Finished as done:
            return _finish(state, save_state, done.video)
        except _Retry:
            offset = 0  # the first chunk's 308 reply corrects this

    with open(path, "rb") as fh:
        while True:
            try:
                if offset >= total:
                    offset = confirmed_offset()
                    if offset >= total:
                        raise YouTubeApiError("YouTube holds all bytes but has not returned the video")
                fh.seek(offset)
                chunk = fh.read(chunk_size)
                end = offset + len(chunk) - 1
                status, headers, data = request("PUT", session, chunk, {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": "bytes {}-{}/{}".format(offset, end, total),
                })
                if status in (200, 201):
                    if progress:
                        progress(total, total)
                    return _finish(state, save_state, json.loads(data.decode("utf-8")))
                if status == 308:
                    rng = headers.get("range")
                    offset = int(rng.rsplit("-", 1)[1]) + 1 if rng else 0
                    failures = 0
                    if progress:
                        progress(offset, total)
                    continue
                if status == 401:
                    renew()
                    raise _Retry("token expired")
                if status in _RETRYABLE:
                    raise _Retry("HTTP {}".format(status))
                if status == 404:
                    raise YouTubeApiError("upload session expired (404); delete {} to start a new one".format(
                        state_path or "the state file"))
                raise YouTubeApiError("chunk upload failed: HTTP {} {}".format(
                    status, data.decode("utf-8", "replace")[:400]))
            except _Finished as done:
                return _finish(state, save_state, done.video)
            except (_Retry, OSError, http.client.HTTPException) as exc:
                failures += 1
                if failures > max_retries:
                    raise YouTubeApiError("upload gave up after {} retries: {}".format(max_retries, exc)) from exc
                sleep(min(2 ** failures, 120))
                try:
                    offset = confirmed_offset()
                except _Finished as done:
                    return _finish(state, save_state, done.video)
                except (_Retry, OSError, http.client.HTTPException):
                    continue


class _Finished(Exception):
    def __init__(self, video: Dict[str, Any]):
        super().__init__("finished")
        self.video = video


class _Retry(Exception):
    pass


def _finish(state: Dict[str, Any], save_state: Any, video: Dict[str, Any]) -> Dict[str, Any]:
    state["video_id"] = video.get("id", "")
    save_state()
    return video
