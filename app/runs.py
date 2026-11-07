"""One search run per request: start the agents, stream their events, build the final report.

Each visitor's "Approve & start searching" opens one streaming request (SSE). The run lives inside
that request and nowhere else: when it ends the report is sent and the stream closes; when the
visitor presses Stop (or leaves) the request is cancelled and the agents stop with it. Nothing is
shared between visitors, so two people can search at once.

(The original kept one global run and fanned its events out over a WebSocket; see original-playwright/.)
"""
import asyncio
from typing import AsyncIterator

from app import ai, config
from app.agent.agent import Run
from app.agent.stores.mock_store import MockStoreAdapter
from app.matching import Selector
from app.models import Ingredient
from app.report import apply_review, build_report
from app.units import to_standard
from mock_stores.products import STORES as MOCK_STORES


async def stream_run(items: list[Ingredient], base_url: str, *, review: bool,
                     delay_range: tuple[float, float] | None = None) -> AsyncIterator[dict]:
    """Yield every agent event, then (if the run finished) the report. `review` = ask the AI to double-check."""
    events: asyncio.Queue = asyncio.Queue()
    selector = Selector({i.name: to_standard(i.name, i.quantity, i.unit) for i in items})
    adapters = [MockStoreAdapter(slug, base_url) for slug in MOCK_STORES]
    run = Run([i.name for i in items], adapters, approved=True, emit=events.put_nowait,
              chooser=selector.choose, enricher=selector.enrich, max_parallel=config.MAX_PARALLEL,
              delay_range=delay_range)
    task = run.start()
    task.add_done_callback(lambda _: events.put_nowait(None))
    try:
        while (event := await events.get()) is not None:
            yield event
        if run.state == "finished":
            await _finalize(run, items, review)
            while not events.empty():
                event = events.get_nowait()
                if event is not None:
                    yield event
    finally:
        run.stop()  # the visitor pressed Stop or left: halt every agent now


async def _finalize(run: Run, items: list[Ingredient], review: bool):
    """When the agents finish: build the report, let the AI double-check it, publish it."""
    report = build_report(items, run.results, {s: a.name for s, a in run.adapters.items()})
    pairs, owners = [], []
    for idx, item in enumerate(report.items):
        if item.options:
            pairs.append((item.ingredient, item.options[0].name))
            owners.append(idx)
    if pairs and review:
        run.emit({"type": "log", "worker": None, "store": None, "action": "review",
                  "detail": "Asking the AI to double-check the matches…", "status": "ok"})
        try:
            verdicts = await ai.review_matches(pairs)
            for v in verdicts:
                if 0 <= v.index < len(owners):
                    v.index = owners[v.index]  # pair number -> position in report.items
                else:
                    v.index = -1
            apply_review(report, verdicts)
        except ai.AIError as e:  # provider error: the rule-based flags still stand
            run.emit({"type": "log", "worker": None, "store": None, "action": "review",
                      "detail": f"AI check skipped: {e}", "status": "error"})
    run.emit({"type": "report", "report": report.model_dump()})
