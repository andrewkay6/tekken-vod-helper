from tekken_vod_helper import youtube_client
from urllib.parse import parse_qs, urlparse


def test_thumbnail_posts_image_bytes_to_media_upload_endpoint(monkeypatch, tmp_path):
    thumbnail = tmp_path / "thumbnail.jpg"
    image_bytes = b"\xff\xd8\xff\xe0thumbnail-test\xff\xd9"
    thumbnail.write_bytes(image_bytes)
    requests = []

    def fake_json_request(request):
        requests.append(request)
        return {"items": []}

    monkeypatch.setattr(youtube_client, "_json_request", fake_json_request)
    result = youtube_client.set_video_thumbnail("abc123", str(thumbnail), access_token="test-token")

    assert result == {"items": []}
    assert len(requests) == 1
    request = requests[0]
    url = urlparse(request.full_url)
    assert url.scheme == "https"
    assert url.netloc == "www.googleapis.com"
    assert url.path == "/upload/youtube/v3/thumbnails/set"
    assert parse_qs(url.query) == {"videoId": ["abc123"], "uploadType": ["media"]}
    assert request.get_method() == "POST"
    assert request.data == image_bytes
    assert request.get_header("Content-type") == "image/jpeg"
    assert request.get_header("Authorization") == "Bearer test-token"


def test_authorization_url_uses_manage_scope():
    url = youtube_client.authorization_url("client-id", "http://127.0.0.1:8765/callback", "state-token")

    assert "client_id=client-id" in url
    assert "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fyoutube&" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url


def test_authorization_url_allows_explicit_readonly_scope():
    url = youtube_client.authorization_url(
        "client-id", "http://127.0.0.1:8765/callback", "state-token",
        scope=youtube_client.READONLY_SCOPE,
    )

    assert "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fyoutube.readonly&" in url


def test_video_from_api_item_preserves_status_shape():
    video = youtube_client._video_from_api_item(
        {
            "id": "abc123",
            "snippet": {
                "title": "tvh-001-aaaaaa match",
                "publishedAt": "2026-09-22T20:00:00Z",
                "channelTitle": "KW Tekken",
                "description": "Description",
                "tags": ["Tekken"],
                "thumbnails": {
                    "default": {"url": "https://example.test/default.jpg"},
                    "high": {"url": "https://example.test/high.jpg"},
                },
            },
            "status": {
                "privacyStatus": "private",
                "uploadStatus": "uploaded",
            },
            "processingDetails": {
                "processingStatus": "processing",
            },
        }
    )

    assert video.video_id == "abc123"
    assert video.title == "tvh-001-aaaaaa match"
    assert video.privacy_status == "private"
    assert video.upload_status == "uploaded"
    assert video.processing_status == "processing"
    assert video.tags == ["Tekken"]
    assert video.thumbnail_url == "https://example.test/high.jpg"


def test_update_video_metadata_sends_status_and_thumbnail(monkeypatch):
    api_calls = []
    thumbnail_calls = []

    def fake_api_put_json(path, access_token, params, payload):
        api_calls.append((path, access_token, params, payload))
        return {"id": payload["id"], "kind": "youtube#video"}

    def fake_set_video_thumbnail(video_id, thumbnail_path, access_token=None):
        thumbnail_calls.append((video_id, thumbnail_path, access_token))
        return {"items": []}

    monkeypatch.setattr(youtube_client, "refresh_access_token", lambda: "access-token")
    monkeypatch.setattr(youtube_client, "_api_put_json", fake_api_put_json)
    monkeypatch.setattr(youtube_client, "set_video_thumbnail", fake_set_video_thumbnail)

    response = youtube_client.update_video_metadata(
        video_id="abc123",
        title="A good Tekken set",
        description="KW Tekken socials blurb",
        tags=["Tekken 8", "KWTekken"],
        privacy_status="unlisted",
        self_declared_made_for_kids=False,
        thumbnail_path="C:/tmp/thumb.jpg",
    )

    assert response == {"id": "abc123", "kind": "youtube#video"}
    assert thumbnail_calls == [("abc123", "C:/tmp/thumb.jpg", "access-token")]
    path, token, params, payload = api_calls[0]
    assert path == "/videos"
    assert token == "access-token"
    assert params == {"part": "snippet,status"}
    assert payload["snippet"] == {
        "title": "A good Tekken set",
        "description": "KW Tekken socials blurb",
        "tags": ["Tekken 8", "KWTekken"],
        "categoryId": "20",
    }
    assert payload["status"] == {
        "privacyStatus": "unlisted",
        "selfDeclaredMadeForKids": False,
    }
