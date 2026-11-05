"""Archive creation-time filters and byte compatibility with dispatch 204 item 1."""

import json
from pathlib import Path

import pytest

from test_memory_chart import (
    FIRST,
    SCOPE,
    _experience,
    _fact,
    _memory,
    _topic,
    client as client,
)

LEFT = "2026-01-10T00:00:00.123456Z"
MIDDLE = "2026-01-10T00:00:00.123457+00:00"
RIGHT = "2026-01-20T00:00:00Z"


@pytest.fixture
def seeded(client):
    with _memory() as db:
        _topic(db, "t-a")
        _topic(db, "t-other")
        for name, created in (("before", FIRST), ("left", LEFT),
                              ("middle", MIDDLE), ("right", RIGHT)):
            _fact(db, name, created=created, topic="t-a", pinned=True,
                  text=f"keep {name}", versions=2)
            _experience(db, name, created=created, pinned=True)
        _fact(db, "other-topic", created=LEFT, topic="t-other", pinned=True, text="keep other")
        _fact(db, "unpinned", created=LEFT, topic="t-a", text="keep unpinned")
        _fact(db, "wrong-text", created=LEFT, topic="t-a", pinned=True, text="skip")
        _experience(db, "unpinned", created=LEFT)
        _experience(db, "wrong-text", created=LEFT, pinned=True)
        db.execute("UPDATE experiences SET display_text='keep ' || experience_id")
        db.execute("UPDATE experiences SET display_text='skip' WHERE experience_id='wrong-text'")
        _fact(db, "deleted", created=LEFT, state="deleted", pinned=True, text="keep hidden")
        _experience(db, "deleted", created=LEFT, status="deleted", pinned=True)
        _experience(db, "pending", created=LEFT, status="pending_source_delete", pinned=True)
        _fact(db, "other-mode", created=LEFT, mode="okabe")
        _experience(db, "other-session", created=LEFT, session="other")
    return client


def _params(surface, filtered):
    if not filtered:
        return SCOPE
    params = SCOPE | {"query": "keep", "pinned_only": "true", "limit": 1, "offset": 1}
    return params | ({"topic_id": "t-a"} if surface == "facts" else {})


@pytest.mark.parametrize("surface", ["facts", "experiences"])
@pytest.mark.parametrize("filtered", [False, True])
def test_omitted_created_filters_preserve_response_bytes(seeded, surface, filtered):
    snapshots = json.loads(
        (Path(__file__).parent / "fixtures/archive_browse_before_created_filters.json")
        .read_text(encoding="utf-8")
    )
    response = seeded.get(f"/api/memory/{surface}", params=_params(surface, filtered))
    expected = snapshots[f"{surface}-{filtered}"]
    assert response.status_code == 200
    assert response.content == expected.encode("utf-8")


@pytest.mark.parametrize("surface", ["facts", "experiences"])
@pytest.mark.parametrize("bounds,expected", [
    ({"created_from": LEFT, "created_to": RIGHT}, {"left", "middle"}),
    ({"created_from": LEFT, "created_to": MIDDLE}, {"left"}),
    ({"created_from": LEFT, "created_to": LEFT}, set()),
    ({"created_to": LEFT}, {"before"}),
    ({"created_from": RIGHT}, {"right"}),
    ({"created_from": "2026-01-10T02:00:00.123456+02:00",
      "created_to": "2026-01-20T00:00:00"}, {"left", "middle"}),
])
def test_created_bounds_are_left_closed_right_open(seeded, surface, bounds, expected):
    params = _params(surface, True) | {"limit": 100, "offset": 0} | bounds
    response = seeded.get(f"/api/memory/{surface}", params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    id_key = "fact_id" if surface == "facts" else "experience_id"
    assert {row[id_key] for row in body[surface]} == expected
    assert body["pagination"]["total"] == len(expected)
    assert body["pagination"]["has_more"] is False


@pytest.mark.parametrize("surface", ["facts", "experiences"])
def test_created_filters_compose_before_pagination(seeded, surface):
    params = _params(surface, True) | {"created_from": LEFT, "created_to": RIGHT}
    response = seeded.get(f"/api/memory/{surface}", params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    id_key = "fact_id" if surface == "facts" else "experience_id"
    expected = "middle" if surface == "facts" else "left"
    assert [row[id_key] for row in body[surface]] == [expected]
    assert body["pagination"] == {"limit": 1, "offset": 1, "total": 2, "has_more": False}
    first = seeded.get(f"/api/memory/{surface}", params=params | {"offset": 0}).json()
    assert first["pagination"]["has_more"] is True
    assert first[surface][0][id_key] != expected
    if surface == "facts":
        unfiltered = seeded.get("/api/memory/facts", params=SCOPE).json()
        assert body["resident_count"] == unfiltered["resident_count"]


@pytest.mark.parametrize("surface", ["facts", "experiences"])
def test_created_filter_preserves_scope_and_visibility(seeded, surface):
    with _memory() as db:
        if surface == "experiences":
            db.execute("UPDATE experiences SET status='expired' WHERE experience_id='left'")
    response = seeded.get(f"/api/memory/{surface}", params=SCOPE | {
        "created_from": LEFT, "created_to": RIGHT,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    id_key = "fact_id" if surface == "facts" else "experience_id"
    expected = {"left", "middle", "unpinned", "wrong-text"}
    if surface == "facts":
        expected.add("other-topic")
    assert {row[id_key] for row in body[surface]} == expected
    assert body["pagination"]["total"] == len(expected)


@pytest.mark.parametrize("surface", ["facts", "experiences"])
@pytest.mark.parametrize("name", ["created_from", "created_to"])
@pytest.mark.parametrize("value", ["", "bad-time", "2026-02-30T00:00:00Z", "2026-01-01T00:00:00+99:00"])
def test_invalid_created_times_use_existing_422_envelope(client, surface, name, value):
    response = client.get(f"/api/memory/{surface}", params=SCOPE | {name: value})
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": {
        "code": f"invalid_{name}", "message": f"{name} must be RFC3339 UTC",
    }}
