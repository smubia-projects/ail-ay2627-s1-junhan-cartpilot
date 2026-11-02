"""In-process agents against the mock stores (the deployed showcase has no real browser).

The original Playwright tests (real Chromium, SafePage) are kept in original-playwright/tests/."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.agent.agent import Run, queries_for
from app.agent.safety import SafetyViolation
from app.agent.stores.mock_store import MockStoreAdapter
from app.agent.virtual_page import AgentStopped, UnexpectedPage, VirtualPage
from app.main import app
from mock_stores import products as db

BASE = "http://testserver"
FAST = (0, 0)


def adapters(slugs=None):
    return [MockStoreAdapter(s, BASE) for s in (slugs or db.STORES)]


@pytest.fixture
def page():
    logs, views = [], []
    p = VirtualPage(0, {"testserver"}, asyncio.Event(), lambda a, d="", s="ok": logs.append((a, d, s)),
                    lambda url, html: views.append((url, html)), delay_range=FAST)
    p.logs, p.views = logs, views
    return p


# ---------------------------------------------------------------- VirtualPage enforces the guard
async def test_goto_cart_or_other_site_blocked(page):
    for url in (f"{BASE}/stores/freshmart/cart", "https://www.google.com/", f"{BASE}/stores/x?add-to-cart=1"):
        with pytest.raises(SafetyViolation):
            await page.goto(url, "<html></html>")
    assert [s for _, _, s in page.logs].count("blocked") == 3
    assert page.views == []  # a blocked page is never shown


async def test_stop_flag_makes_every_action_refuse(page):
    page._stop.set()
    with pytest.raises(AgentStopped):
        await page.goto(f"{BASE}/stores/freshmart/", "")
    with pytest.raises(AgentStopped):
        await page.type_search("egg")


# ---------------------------------------------------------------- mock adapter
@pytest.mark.parametrize("slug", list(db.STORES))
async def test_adapter_search_open_details(page, slug):
    a = MockStoreAdapter(slug, BASE)
    hits = await a.search(page, "butter")
    assert hits and all(h.store == slug and h.price > 0 for h in hits)
    hit = next(h for h in hits if h.in_stock)
    await a.open_product_page(page, hit)
    d = await a.get_product_details(page, hit)
    assert d.product_id == hit.product_id and d.price == hit.price and d.size == hit.size
    assert d.url == f"{BASE}/stores/{slug}/product/{hit.product_id}"
    # every visit is logged and rendered from the store's own templates
    assert [action for action, _, _ in page.logs] == ["goto", "type", "goto", "goto"]
    assert f'data-product-id="{hit.product_id}"' in page.views[-1][1]


async def test_adapter_no_results(page):
    assert await MockStoreAdapter("quickbasket", BASE).search(page, "vanilla extract") == []
    assert "no-results" in page.views[-1][1]


# ---------------------------------------------------------------- full runs
def test_queries_for_fallbacks():
    assert queries_for("white sugar") == ["white sugar", "caster sugar", "sugar", "granulated sugar"]
    assert queries_for("vanilla extract")[:2] == ["vanilla extract", "vanilla essence"]
    assert queries_for("dragon fruit") == ["dragon fruit", "fruit"]
    assert queries_for("  Cocoa   Powder ")[0] == "cocoa powder"
    assert len(queries_for("plain flour")) <= 4


async def test_run_requires_approval():
    run = Run(["egg"], adapters(), approved=False)
    with pytest.raises(PermissionError):
        run.start()
    assert run.state == "pending"


def test_api_refuses_unapproved_runs():
    body = {"items": [{"name": "egg", "quantity": 3, "unit": "piece"}], "approved": False}
    assert TestClient(app).post("/api/runs/stream", json=body).status_code == 400


async def test_full_run_all_stores():
    events = []
    items = ["plain flour", "egg", "cocoa powder", "white sugar", "vanilla extract"]
    run = Run(items, adapters(), approved=True, emit=events.append, max_parallel=4, delay_range=FAST)
    run.start()
    await asyncio.wait_for(run.wait(), 30)
    assert run.state == "finished"
    assert len(run.results) == len(items) * 3

    by = {(r.store, r.ingredient): r for r in run.results}
    assert by["freshmart", "plain flour"].status == "found"
    assert by["freshmart", "plain flour"].chosen.url.startswith(BASE)
    assert by["valuegrocer", "vanilla extract"].chosen.name.endswith("Vanilla Essence")  # synonym fallback
    assert by["valuegrocer", "white sugar"].status == "found"                # found via synonym fallback
    assert by["valuegrocer", "white sugar"].query_used == "caster sugar"
    assert not [r for r in run.results if r.status in ("blocked", "error")]
    assert not [e for e in run.log if e["status"] == "blocked"]
    assert {"goto", "type", "found"} <= {e["action"] for e in run.log}
    views = [e for e in events if e["type"] == "view"]
    assert views and views[0]["html"].lstrip().lower().startswith("<!doctype html>")
    labels = [(e["store"], e["label"]) for e in events if e["type"] == "worker"]
    assert ("FreshMart", "searching egg…") in labels


async def test_parallelism_limit_is_respected():
    run = Run(["egg", "butter", "milk", "salt"], adapters(), approved=True, max_parallel=2, delay_range=FAST)
    run.start()
    await asyncio.wait_for(run.wait(), 30)
    assert run.state == "finished" and len(run.results) == 12
    assert run.snapshot()["workers"].keys() == {0, 1}  # only two agents were ever used


async def test_stop_halts_everything():
    events = []
    many = ["egg", "butter", "milk", "salt", "plain flour", "caster sugar", "honey", "rice", "pasta", "garlic"]
    run = Run(many, adapters(), approved=True, emit=events.append, max_parallel=3, delay_range=(0.05, 0.1))
    run.start()
    await asyncio.sleep(0.5)
    run.stop()
    await asyncio.wait_for(run.wait(), 5)
    assert run.state == "stopped"
    assert len(run.results) < len(many) * 3  # it really was interrupted
    after = len(run.results)
    await asyncio.sleep(0.5)
    assert len(run.results) == after  # nothing keeps running in the background
    assert events[-1]["type"] == "run_stopped"


async def test_failed_store_is_abandoned_and_reported(monkeypatch):
    """If a store's page looks wrong, stop using it and say so; others carry on."""
    broken = MockStoreAdapter("valuegrocer", BASE)

    async def boom(page, query):
        raise UnexpectedPage("ValueGrocer showed a CAPTCHA")

    monkeypatch.setattr(broken, "search", boom)
    run = Run(["egg", "butter", "milk"], [broken, MockStoreAdapter("freshmart", BASE)], approved=True,
              max_parallel=1, delay_range=FAST)
    run.start()
    await asyncio.wait_for(run.wait(), 30)
    vg = [r for r in run.results if r.store == "valuegrocer"]
    assert [r.status for r in vg] == ["error", "skipped", "skipped"]
    assert "CAPTCHA" in vg[0].message
    assert all(r.status == "found" for r in run.results if r.store == "freshmart")
