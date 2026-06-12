from tekken_vod_helper.roster_import import extract_roster_from_html, extract_roster_from_html_text, fetch_roster_from_url


def test_extract_roster_from_saved_html_state(tmp_path):
    path = tmp_path / "fighters.html"
    path.write_text(
        """
        <script>
        {&q;Asset:abc&q;:{&q;__typename&q;:&q;Asset&q;,&q;altText&q;:&q;Devil Jin Thumbnail&q;,&q;id&q;:&q;abc&q;,&q;fileName&q;:&q;devil-jin-fighter-select.png&q;,&q;mimeType&q;:&q;image/png&q;,&q;url&q;:&q;https://example.test/deviljin&q;},
        &q;Asset:def&q;:{&q;__typename&q;:&q;Asset&q;,&q;altText&q;:null,&q;id&q;:&q;def&q;,&q;fileName&q;:&q;fighter-selector-dlc-7@2x.webp&q;,&q;mimeType&q;:&q;image/webp&q;,&q;url&q;:&q;https://example.test/armorking&q;}}
        [{&q;__typename&q;:&q;Thumbnail&q;,&q;title&q;:&q;Devil Jin&q;,&q;slug&q;:&q;devil-jin&q;,&q;image&q;:{&q;__ref&q;:&q;Asset:abc&q;}},
        {&q;__typename&q;:&q;Thumbnail&q;,&q;title&q;:&q;Armor King&q;,&q;slug&q;:&q;armor-king&q;,&q;image&q;:{&q;__ref&q;:&q;Asset:def&q;}}]
        </script>
        """,
        encoding="utf-8",
    )

    roster = extract_roster_from_html(str(path))

    assert [record.name for record in roster] == ["Devil Jin", "Armor King"]
    assert roster[0].file_name == "devil-jin-fighter-select.png"
    assert roster[1].url == "https://example.test/armorking"


def test_extract_roster_from_html_text():
    roster = extract_roster_from_html_text(
        """
        {"Asset:abc":{"__typename":"Asset","fileName":"jin-fighter-select.webp","url":"https://example.test/jin"}}
        [{"__typename":"Thumbnail","title":"Jin","slug":"jin-kazama","image":{"__ref":"Asset:abc"}}]
        """
    )

    assert roster[0].name == "Jin"
    assert roster[0].url == "https://example.test/jin"


def test_fetch_roster_from_url(monkeypatch):
    class FakeHeaders:
        def get_content_charset(self):
            return "utf-8"

    class FakeResponse:
        headers = FakeHeaders()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return b"""
            {"Asset:abc":{"__typename":"Asset","fileName":"jin-fighter-select.webp","url":"https://example.test/jin"}}
            [{"__typename":"Thumbnail","title":"Jin","slug":"jin-kazama","image":{"__ref":"Asset:abc"}}]
            """

    def fake_urlopen(request, timeout):
        assert request.full_url == "https://tekken.example/fighters"
        assert timeout == 30
        return FakeResponse()

    monkeypatch.setattr("tekken_vod_helper.roster_import.urllib.request.urlopen", fake_urlopen)

    roster = fetch_roster_from_url("https://tekken.example/fighters")

    assert [record.name for record in roster] == ["Jin"]
