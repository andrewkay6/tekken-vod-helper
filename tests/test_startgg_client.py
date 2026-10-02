import pytest

from tekken_vod_helper import startgg_client


def test_fetch_tournaments_includes_admin_events_and_all_pages(monkeypatch):
    calls = []

    def graphql(token, query, variables):
        calls.append((token, query, variables))
        page = variables["page"]
        return {"data": {"currentUser": {"tournaments": {
            "pageInfo": {"totalPages": 2},
            "nodes": [{"name": "Event " + str(page),
                       "slug": "tournament/event-" + str(page),
                       "startAt": 2000000000 if page == 1 else 1000000000}],
        }}}}

    monkeypatch.setattr(startgg_client, "_graphql", graphql)
    events = startgg_client.fetch_owned_tournaments("test-token", per_page=1)

    assert [event.slug for event in events] == [
        "tournament/event-1", "tournament/event-2"]
    assert [call[2] for call in calls] == [
        {"page": 1, "perPage": 1}, {"page": 2, "perPage": 1}]
    for _, query, _ in calls:
        assert 'tournamentView: "admin"' in query
        assert "ownerId" not in query
        assert "upcoming:" not in query
        assert "past:" not in query


def test_fetch_tournaments_requires_token(monkeypatch):
    def graphql(*args):
        pytest.fail("Blank token must not make an API request")

    monkeypatch.setattr(startgg_client, "_graphql", graphql)
    with pytest.raises(startgg_client.StartggError, match="token"):
        startgg_client.fetch_owned_tournaments("  ")
