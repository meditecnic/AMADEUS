from app import db
from test_memory_chart import _memory, _fact, _experience


def test_summary_matches_six_frontend_reads(app_client):
    session = "chart-session"
    for line in ("steins_gate", "beta"):
        app_client.portal.call(db.init_db)
        with _memory(line) as memory:
            _fact(memory, f"{line}-self")
            _fact(memory, f"{line}-okabe", mode="okabe")
            _fact(memory, f"{line}-other", session="other")
            _experience(memory, f"{line}-expired", expires="2000-01-01T00:00:00Z")
            _experience(memory, f"{line}-active", mode="okabe")
            _fact(memory, f"{line}-deleted", state="deleted")
        for mode in ("self", "okabe"):
            assert app_client.post("/api/conversations", json={
                "session_id": session, "worldline": line, "identity_mode": mode,
            }).status_code == 201
    summary = app_client.get("/api/worldlines/summary", params={"session_id": session})
    assert summary.status_code == 200
    for line in ("steins_gate", "beta"):
        conversations = app_client.get("/api/conversations", params={"session_id": session, "worldline": line}).json()
        count = 0
        for mode in ("self", "okabe"):
            chart = app_client.get("/api/memory/chart", params={"session_id": session, "worldline": line, "identity_mode": mode}).json()
            count += chart["counts"]["facts"] + chart["counts"]["experiences"]
        assert summary.json()[line] == {"conversations": len(conversations), "memories": count,
            "last_active_at": max(row["last_active_at"] for row in conversations)}
        assert count == 3
    empty = app_client.get("/api/worldlines/summary", params={"session_id": "empty"}).json()["beta"]
    # Existing conversation listing creates a default conversation on first read.
    assert empty["conversations"] == 1 and empty["memories"] == 0


def test_summary_propagates_unavailable_chart(app_client, monkeypatch):
    from app.services.memory_v11 import chart
    async def unavailable(scope):
        raise chart.ChartError("chart_unavailable", "unavailable")
    monkeypatch.setattr(chart, "build_chart", unavailable)
    response = app_client.get("/api/worldlines/summary?session_id=test")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "chart_unavailable"
