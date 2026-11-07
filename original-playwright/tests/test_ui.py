"""Drive the real web page in a real browser, in demo mode, like a person would.

Set SHOT_DIR=/some/folder to also save screenshots of each stage.
"""
import io
import os

import pytest
from PIL import Image
from playwright.async_api import async_playwright

from app import config

SHOTS = os.environ.get("SHOT_DIR")


@pytest.fixture(autouse=True)
def demo_mode(monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", True)
    monkeypatch.setattr(config, "HEADLESS", True)
    monkeypatch.setattr(config, "MAX_PARALLEL", 4)


def png() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (800, 600), "#dde").save(b, "PNG")
    return b.getvalue()


async def shot(page, name):
    if SHOTS:
        await page.screenshot(path=f"{SHOTS}/{name}.png", full_page=True)


async def test_whole_user_journey(base_url):
    errors = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1100, "height": 900})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        await page.goto(base_url)
        await page.wait_for_selector("#mode-badge:not([hidden])")
        assert "Demo mode" in await page.inner_text("#mode-badge")

        # 1-2. goal and recipe
        await page.fill("#goal", "bake a chocolate cake")
        await page.click("#goal-form button")
        await page.wait_for_selector(".recipe")
        assert await page.locator(".recipe").count() == 3
        await page.click(".recipe >> nth=0")
        await page.wait_for_selector("#ing-list li")
        assert await page.locator("#ing-list li").count() == 9
        assert "How to make it" in await page.inner_text("#method-ingredients")
        await page.click("#method-ingredients summary")                       # collapsed until opened
        assert await page.locator("#method-ingredients .steps-list li").count() == 8
        assert "225 g plain flour" in await page.inner_text("#method-ingredients")

        # 3-4. fridge photo -> checklist with confidence badges
        await page.set_input_files("#photo", {"name": "fridge.png", "mimeType": "image/png", "buffer": png()})
        await page.click("#scan-btn")
        await page.wait_for_selector("#step-checklist:not([hidden])")
        have = await page.inner_text("#have-list")
        assert "milk" in have and "egg" in have                   # high confidence: pre-ticked
        assert "butter" not in have and "baking powder" not in have   # medium/low: not pre-ticked
        assert await page.locator("#need-list .badge.warn").count() == 2   # ...and flagged "Please check"
        await shot(page, "1-checklist")

        # 5. approve -> live view
        await page.click("#approve-btn")
        await page.wait_for_selector("#step-live:not([hidden])")
        await page.wait_for_selector(".tile img[src^='data:image/jpeg']", timeout=30_000)
        await page.wait_for_selector("#log div")
        assert await page.locator(".store-card").count() == 3
        await page.wait_for_function("document.querySelector('.tile .label').textContent.includes(':')", timeout=30_000)
        await shot(page, "2-live")

        # 6. results
        await page.wait_for_selector("#step-results:not([hidden])", timeout=120_000)
        await shot(page, "3-results")
        await page.click("#method-results summary")
        assert await page.locator("#method-results .steps-list li").count() == 8
        assert "Classic Chocolate Cake" in await page.inner_text("#method-results")
        assert "Prep 20 min" in await page.inner_text("#method-results")
        names = await page.locator(".item h4").all_inner_texts()
        assert len(names) == 7 and "egg" not in " ".join(names)      # milk + egg were ticked as owned
        cocoa = page.locator(".item", has_text="cocoa powder").first
        assert "Drink" not in await cocoa.locator(".pick").inner_text()
        assert await page.locator("#store-totals tr").count() == 4        # header + 3 stores
        assert await page.inner_text("#basket-total") != "S$0.00"
        assert await page.locator("#open-all").is_enabled()
        assert await page.locator("#open-links li").count() == 7

        # swapping to an alternative changes the pick and the total
        before = await page.inner_text("#basket-total")
        flour = page.locator(".item", has_text="plain flour").first
        await flour.locator(".alts button").first.click()
        assert await page.inner_text("#basket-total") != before

        # low-confidence pick must be confirmed (or swapped) before "open all" is enabled
        await page.evaluate("""() => {
          const r = JSON.parse(JSON.stringify(state.report));
          r.items[0].options[0].confidence = 'low'; r.items[0].options[0].reasons = ['Size unclear'];
          state.report = null; renderReport(r);
        }""")
        assert not await page.locator("#open-all").is_enabled()
        assert "Please confirm" in await page.inner_text("#result-items")
        assert "Size unclear" in await page.inner_text("#result-items")
        await page.locator(".flag.low input[type=checkbox]").first.check()
        assert await page.locator("#open-all").is_enabled()

        # 7. leftovers -> ideas -> one-tap new search with leftovers marked as owned
        await page.wait_for_selector("#step-leftovers:not([hidden])")
        assert await page.locator("#leftover-list li").count() >= 1
        await page.wait_for_selector(".idea button")
        await shot(page, "4-leftovers")
        await page.locator(".idea button").first.click()
        await page.wait_for_selector("#owned-note:not([hidden])")
        assert await page.locator("#have-list li").count() >= 1
        assert await page.locator("#step-results").is_hidden() and await page.locator("#step-live").is_hidden()
        await shot(page, "5-leftover-checklist")
        await browser.close()
    assert not errors, errors


async def test_stop_button_halts_the_live_search(base_url):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1100, "height": 900})
        await page.goto(base_url)
        await page.fill("#goal", "pancakes")
        await page.click("#goal-form button")
        await page.click(".recipe >> nth=2")
        await page.click("#skip-btn")
        await page.click("#approve-btn")
        await page.wait_for_selector(".tile img[src^='data:image/jpeg']", timeout=30_000)
        await page.click("#stop-btn")
        await page.wait_for_function("document.getElementById('live-status').textContent.startsWith('Stopped')", timeout=15_000)
        await shot(page, "6-stopped")
        assert await page.locator("#step-results").is_hidden()           # a stopped run produces no results page
        assert await page.locator("#approve-btn").is_enabled()           # and the user can start again
        await browser.close()
