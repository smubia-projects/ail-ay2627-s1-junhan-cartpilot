"""The SSE run stream: one run per request, events then the report; and the rate-limit wiring."""
import json

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import ai, config, main
from app.main import app
from app.models import Ingredient
from app.runs import stream_run

ITEMS = [{"name": "plain flour", "quantity": 225, "unit": "g"}, {"name": "egg", "quantity": 3, "unit": "piece"}]


def _events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_stream_sends_events_then_report(monkeypatch):
    monkeypatch.setattr(config, "MAX_PARALLEL", 6)
    r = TestClient(app).post("/api/runs/stream", json={"items": ITEMS, "approved": True})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    ev = _events(r.text)
    kinds = [e["type"] for e in ev]
    assert kinds[0] == "run_started" and kinds[-1] == "report"
    assert "view" in kinds and "result" in kinds and "run_finished" in kinds
    report = ev[-1]["report"]
    assert {i["ingredient"] for i in report["items"]} == {"plain flour", "egg"}
    assert all(o["url"].startswith("http://testserver/stores/") for i in report["items"] for o in i["options"])


async def test_review_runs_only_when_asked(monkeypatch):
    calls = []

    async def fake_review(pairs):
        calls.append(pairs)
        return []

    monkeypatch.setattr(ai, "review_matches", fake_review)
    items = [Ingredient(**i) for i in ITEMS]
    for review in (False, True):
        events = [e async for e in stream_run(items, "http://testserver", review=review, delay_range=(0, 0))]
        assert events[-1]["type"] == "report"
    assert len(calls) == 1


def test_model_routes_are_rate_limited(monkeypatch):
    """Outside demo mode every model route calls the limiter; a 429 stops before any model call."""
    hits = []

    def limited(request, bucket):
        hits.append(bucket)
        raise HTTPException(status_code=429, detail={"message": "Rate limit exceeded.", "queries_used": 4})

    async def never(*a, **k):
        raise AssertionError("model called after a 429")

    monkeypatch.setattr(config, "DEMO_MODE", False)
    monkeypatch.setattr(main, "check_rate_limit", limited)
    for name in ("suggest_recipes", "generate_ingredients", "suggest_leftover_ideas", "scan_fridge"):
        monkeypatch.setattr(ai, name, never)
    c = TestClient(app)
    assert c.post("/api/recipes", json={"goal": "cake", "servings": 4}).status_code == 429
    assert c.post("/api/ingredients", json={"goal": "cake", "recipe_title": "Cake", "servings": 4}).status_code == 429
    assert c.post("/api/runs/stream", json={"items": ITEMS, "approved": True}).status_code == 429
    r = c.post("/api/leftover-ideas", json={"leftovers": [{"name": "milk", "amount": 500, "unit": "ml"}], "servings": 2})
    assert r.status_code == 429 and r.json()["detail"]["queries_used"] == 4
    assert hits == ["plan", "plan", "search", "plan"]


def test_missing_key_is_501(monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", False)
    monkeypatch.setattr(config, "API_KEY", "")
    monkeypatch.setattr(ai, "_client", None)
    r = TestClient(app).post("/api/recipes", json={"goal": "cake", "servings": 4})
    assert r.status_code == 501
