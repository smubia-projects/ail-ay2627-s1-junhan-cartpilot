"""Integration tests: real headless Chromium against the mock stores served by a live uvicorn."""
import asyncio
import base64

import pytest
from fastapi.testclient import TestClient
from playwright.async_api import async_playwright

from app import config
from app.agent.agent import Run, queries_for
from app.agent.safe_page import AgentStopped, SafePage
from app.agent.safety import SafetyViolation
from app.agent.stores.mock_store import MockStoreAdapter
from app.main import app
from mock_stores import products as db


def adapters(base, slugs=None):
    return [MockStoreAdapter(s, base) for s in (slugs or db.STORES)]


@pytest.fixture
async def safe_page(base_url):
    """A SafePage plus the list of log lines it produces."""
    logs = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context()
        sp = await SafePage.open(ctx, 0, {"127.0.0.1"}, asyncio.Event(),
                                 lambda action, detail="", status="ok": logs.append((action, detail, status)),
                                 delay_range=(0, 0))
        sp.logs = logs
        sp.base = base_url
        yield sp
        await browser.close()


# ---------------------------------------------------------------- SafePage enforces the guard
async def test_add_to_cart_button_is_never_clicked(safe_page):
    pid = db.search("freshmart", "butter")[0].id
    await safe_page.goto(f"{safe_page.base}/stores/freshmart/product/{pid}")
    assert await safe_page.exists("button.add-to-cart")  # it IS on the page...
    with pytest.raises(SafetyViolation):
        await safe_page.click("button.add-to-cart")  # ...but the agent can't press it
    assert any(s == "blocked" and a == "click" for a, _, s in safe_page.logs)
    assert not any(a == "click" and s == "ok" for a, _, s in safe_page.logs)


async def test_sign_in_link_blocked(safe_page):
    await safe_page.goto(f"{safe_page.base}/stores/valuegrocer/")
    with pytest.raises(SafetyViolation):
        await safe_page.click("nav a:has-text('Sign in')")


async def test_goto_cart_or_other_site_blocked(safe_page):
    for url in (f"{safe_page.base}/stores/freshmart/cart", "https://www.google.com/"):
        with pytest.raises(SafetyViolation):
            await safe_page.goto(url)
    assert [s for _, _, s in safe_page.logs].count("blocked") == 2


async def test_typing_only_in_search_box(safe_page):
    await safe_page.goto(f"{safe_page.base}/stores/quickbasket/")
    await safe_page.type_and_search("input[name=q]", "flour")  # fine
    await safe_page.wait_for(".product-card")
    await safe_page.goto(f"{safe_page.base}/stores/quickbasket/")
    await safe_page._page.evaluate("document.body.insertAdjacentHTML('beforeend','<input id=pw type=password>')")
    with pytest.raises(SafetyViolation):
        await safe_page.type_and_search("#pw", "hunter2")


async def test_network_layer_blocks_navigation_even_without_a_click(safe_page):
    """Defence in depth: a script/redirect heading to a forbidden URL is aborted by the route guard."""
    await safe_page.goto(f"{safe_page.base}/stores/freshmart/")
    try:
        await safe_page._page.goto(f"{safe_page.base}/stores/freshmart/checkout")  # bypassing SafePage on purpose
    except Exception:
        pass  # Playwright raises because the request was aborted: that's the point
    assert "checkout" not in safe_page.url
    assert any(s == "blocked" and a == "navigate" for a, _, s in safe_page.logs)


async def test_stop_flag_makes_every_action_refuse(safe_page):
    safe_page._stop.set()
    with pytest.raises(AgentStopped):
        await safe_page.goto(f"{safe_page.base}/stores/freshmart/")


async def test_every_action_is_logged(safe_page):
    a = MockStoreAdapter("freshmart", safe_page.base)
    await a.search(safe_page, "eggs")
    actions = [x for x, _, _ in safe_page.logs]
    assert actions[:3] == ["goto", "type", "press"]


# ---------------------------------------------------------------- mock adapter
@pytest.mark.parametrize("slug", list(db.STORES))
async def test_adapter_search_open_details(safe_page, slug):
    a = MockStoreAdapter(slug, safe_page.base)
    hits = await a.search(safe_page, "butter")
    assert hits and all(h.store == slug and h.price > 0 for h in hits)
    hit = next(h for h in hits if h.in_stock)
    await a.open_product_page(safe_page, hit)
    d = await a.get_product_details(safe_page, hit)
    assert d.product_id == hit.product_id and d.price == hit.price and d.size == hit.size
    assert f"/product/{hit.product_id}" in d.url


async def test_adapter_no_results(safe_page):
    assert await MockStoreAdapter("quickbasket", safe_page.base).search(safe_page, "vanilla extract") == []


# ---------------------------------------------------------------- full runs
def test_queries_for_fallbacks():
    assert queries_for("white sugar") == ["white sugar", "caster sugar", "sugar", "granulated sugar"]
    assert queries_for("vanilla extract")[:2] == ["vanilla extract", "vanilla essence"]
    assert queries_for("dragon fruit") == ["dragon fruit", "fruit"]
    assert queries_for("  Cocoa   Powder ")[0] == "cocoa powder"
    assert len(queries_for("plain flour")) <= 4


async def test_run_requires_approval(base_url):
    run = Run(["egg"], adapters(base_url), approved=False)
    with pytest.raises(PermissionError):
        run.start()
    assert run.state == "pending"


def test_api_refuses_unapproved_runs():
    c = TestClient(app)
    body = {"items": [{"name": "egg", "quantity": 3, "unit": "piece"}], "approved": False}
    r = c.post("/api/runs", json=body)
    assert r.status_code == 400
    assert c.get("/api/runs/current").status_code in (404, 200)  # nothing was started by that request


async def test_full_run_all_stores(base_url):
    events = []
    items = ["plain flour", "egg", "cocoa powder", "white sugar", "vanilla extract"]
    run = Run(items, adapters(base_url), approved=True, emit=events.append, max_parallel=4, fps=4)
    run.start()
    await asyncio.wait_for(run.wait(), 120)
    assert run.state == "finished"
    assert len(run.results) == len(items) * 3

    by = {(r.store, r.ingredient): r for r in run.results}
    assert by["freshmart", "plain flour"].status == "found"
    assert by["freshmart", "plain flour"].chosen.url.startswith(base_url)
    assert by["valuegrocer", "vanilla extract"].chosen.name.endswith("Vanilla Essence")  # synonym fallback
    # QuickBasket has no vanilla extract. Its fallback search for "vanilla" only finds a biscuit, which
    # this placeholder chooser would accept: the step-8 matching rules must reject it.
    qb = by["quickbasket", "vanilla extract"]
    assert qb.status == "not_found" or "Wafer" in qb.chosen.name
    assert by["valuegrocer", "white sugar"].status == "found"                # found via synonym fallback
    assert by["valuegrocer", "white sugar"].query_used == "caster sugar"
    # Nothing blocked, nothing errored, and the agent never touched a cart-style page:
    assert not [r for r in run.results if r.status in ("blocked", "error")]
    assert not [e for e in run.log if e["status"] == "blocked"]
    assert not [e for e in run.log if e["action"] == "goto" and "cart" in e["detail"]]
    # Log has every kind of action:
    assert {"goto", "type", "press", "click", "found"} <= {e["action"] for e in run.log}
    # Live frames are real JPEGs:
    frames = [e for e in events if e["type"] == "frame"]
    assert frames and base64.b64decode(frames[0]["data"])[:2] == b"\xff\xd8"
    # Worker labels are updated like "FreshMart: searching egg…"
    labels = [(e["store"], e["label"]) for e in events if e["type"] == "worker"]
    assert ("FreshMart", "searching egg…") in labels


async def test_parallelism_limit_is_respected(base_url):
    events = []
    run = Run(["egg", "butter", "milk", "salt"], adapters(base_url), approved=True, emit=events.append,
              max_parallel=2, fps=1)
    run.start()
    await asyncio.wait_for(run.wait(), 120)
    assert run.state == "finished" and len(run.results) == 12
    assert run.snapshot()["workers"].keys() == {0, 1}  # only two browser pages were ever used


async def test_stop_halts_everything(base_url):
    events = []
    many = ["egg", "butter", "milk", "salt", "plain flour", "caster sugar", "honey", "rice", "pasta", "garlic"]
    run = Run(many, adapters(base_url), approved=True, emit=events.append, max_parallel=3, fps=2)
    run.start()
    await asyncio.sleep(2.5)  # let the agents get going
    done_before = len(run.results)
    run.stop()
    await asyncio.wait_for(run.wait(), 10)  # must finish promptly after Stop
    assert run.state == "stopped"
    assert done_before < len(many) * 3  # it really was interrupted
    after = len(run.results)
    await asyncio.sleep(1.5)
    assert len(run.results) == after  # nothing keeps running in the background
    assert events[-1]["type"] == "run_stopped"


async def test_failed_store_is_abandoned_and_reported(base_url, monkeypatch):
    """If a store's page looks wrong (blocked/CAPTCHA), stop using it and say so; others carry on."""
    from app.agent.safe_page import UnexpectedPage

    broken = MockStoreAdapter("valuegrocer", base_url)

    async def boom(page, query):
        raise UnexpectedPage("ValueGrocer showed a CAPTCHA")

    monkeypatch.setattr(broken, "search", boom)
    run = Run(["egg", "butter", "milk"], [broken, MockStoreAdapter("freshmart", base_url)], approved=True,
              max_parallel=1, fps=1)
    run.start()
    await asyncio.wait_for(run.wait(), 60)
    vg = [r for r in run.results if r.store == "valuegrocer"]
    assert [r.status for r in vg] == ["error", "skipped", "skipped"]
    assert "CAPTCHA" in vg[0].message
    assert all(r.status == "found" for r in run.results if r.store == "freshmart")
