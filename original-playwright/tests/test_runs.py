"""End to end: POST /api/runs -> real browsers -> events over /ws -> final report."""
import asyncio
import base64
import json

import httpx
import pytest
import websockets

from app import config
from app.runs import manager

ITEMS = [{"name": "plain flour", "quantity": 225, "unit": "g"},
         {"name": "cocoa powder", "quantity": 40, "unit": "g"},
         {"name": "egg", "quantity": 3, "unit": "piece"},
         {"name": "milk", "quantity": 150, "unit": "ml"},
         {"name": "vanilla extract", "quantity": 1, "unit": "tsp"}]


@pytest.fixture(autouse=True)
def demo_and_headless(monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", True)       # no AI review call
    monkeypatch.setattr(config, "HEADLESS", True)
    monkeypatch.setattr(config, "MAX_PARALLEL", 4)


async def collect(base_url, until, timeout=120):
    """Connect to /ws and gather events until a type in `until` arrives."""
    events = []
    async with websockets.connect(base_url.replace("http", "ws") + "/ws") as ws:
        async def reader():
            async for raw in ws:
                e = json.loads(raw)
                events.append(e)
                if e["type"] in until:
                    return
        await asyncio.wait_for(reader(), timeout)
    return events


async def start(client, items=ITEMS, approved=True):
    return await client.post("/api/runs", json={"items": items, "approved": approved})


async def test_full_run_streams_events_and_builds_report(base_url):
    async with httpx.AsyncClient(base_url=base_url) as client:
        assert (await start(client, approved=False)).status_code == 400    # no approval, no automation
        r = await start(client)
        assert r.status_code == 200
        events = await collect(base_url, until={"report"})

    types = [e["type"] for e in events]
    # The browser connects just AFTER the run starts, so early events (run_started) may be missed.
    # That's why the first message is a snapshot carrying the items, stores and workers so far.
    assert types[0] == "snapshot" and types[0:1] == ["snapshot"] and "run_finished" in types and types[-1] == "report"
    snap = events[0]["run"]
    assert snap["items"] == [i["name"] for i in ITEMS] and set(snap["stores"]) == {"freshmart", "valuegrocer", "quickbasket"}
    assert any(e["type"] == "frame" and base64.b64decode(e["data"])[:2] == b"\xff\xd8" for e in events)
    assert any(e["type"] == "worker" and e["store"] == "FreshMart" for e in events)
    results = [e for e in events if e["type"] == "result"]
    assert len(results) == len(ITEMS) * 3
    # running totals: each result carries its ranked options so the UI can total per store
    assert all(r["result"]["candidates"] for r in results if r["result"]["status"] == "found")

    report = events[-1]["report"]
    by = {i["ingredient"]: i for i in report["items"]}
    # The look-alike traps are avoided: the pick is the real product, with high confidence.
    cocoa = by["cocoa powder"]["options"][0]
    assert "Drink" not in cocoa["name"] and cocoa["confidence"] == "high"
    assert "Noodle" not in by["egg"]["options"][0]["name"]
    milk = by["milk"]["options"][0]
    # cheapest way to cover 150 ml is QuickBasket's 250 ml carton: 100 ml (40%) left over
    assert "Coconut" not in milk["name"] and milk["size"] == "250ml" and milk["total_cost"] == 1.2
    assert milk["significant_leftover"] and milk["leftover_amount"] == 100
    # Vanilla: ValueGrocer's "Vanilla Essence" is found; QuickBasket's wafer biscuit never wins.
    vanilla = by["vanilla extract"]["options"]
    assert vanilla[0]["name"].endswith("Vanilla Essence") and vanilla[0]["confidence"] == "high"
    assert not any("Wafer" in o["name"] and o["confidence"] != "low" for o in vanilla)
    # Totals per store, and alternatives available for swapping
    assert {t["store"] for t in report["store_totals"]} == {"freshmart", "valuegrocer", "quickbasket"}
    assert all(len(i["options"]) >= 2 for i in report["items"] if i["ingredient"] in ("plain flour", "egg", "milk"))
    qb = next(t for t in report["store_totals"] if t["store"] == "quickbasket")
    assert "vanilla extract" in qb["missing"] or qb["to_confirm"] >= 1


async def test_late_joiner_gets_snapshot_with_frames_and_report(base_url):
    # the previous test's run is still the current one: a new browser tab should catch up instantly
    events = await collect(base_url, until={"snapshot"}, timeout=10)
    snap = events[0]
    assert snap["run"]["state"] == "finished" and snap["report"] and snap["frames"]
    assert len(snap["run"]["results"]) == len(ITEMS) * 3


async def test_second_run_rejected_while_running_and_stop_works(base_url):
    many = [{"name": n, "quantity": 100, "unit": "g"} for n in
            ["plain flour", "caster sugar", "butter", "honey", "rice", "pasta", "salt", "oats", "raisins", "garlic"]]
    async with httpx.AsyncClient(base_url=base_url) as client:
        assert (await start(client, many)).status_code == 200
        await asyncio.sleep(1.5)
        assert (await start(client)).status_code == 409           # one search at a time
        assert (await client.post("/api/runs/stop")).status_code == 200
        events = await collect(base_url, until={"run_stopped", "snapshot"}, timeout=15)
        for _ in range(40):                                       # wait for the stop to settle
            await asyncio.sleep(0.25)
            snap = (await client.get("/api/runs/current")).json()
            if snap["run"]["state"] == "stopped":
                break
        assert snap["run"]["state"] == "stopped"
        assert snap["report"] is None                             # no report for a stopped run
        done = len(snap["run"]["results"])
        await asyncio.sleep(1.5)
        assert len((await client.get("/api/runs/current")).json()["run"]["results"]) == done
        assert not manager.running
        assert (await start(client)).status_code == 200            # and a new run can start afterwards
        await collect(base_url, until={"report"})
