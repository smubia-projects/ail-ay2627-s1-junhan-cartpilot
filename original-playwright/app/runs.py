"""Run lifecycle for the web app: start a search, stream its events, build the final report.

Hub        fans events out to every connected browser (WebSocket). Frames may be dropped for a slow
           client; log lines and results are kept.
RunManager owns the single active Run, the matcher (Selector) and the finished Report.
"""
import asyncio

from app import ai, config
from app.agent.agent import Run
from app.agent.stores.mock_store import MockStoreAdapter
from app.matching import Selector
from app.models import Ingredient, Report
from app.report import apply_review, build_report
from app.units import to_standard
from mock_stores.products import STORES as MOCK_STORES


class Hub:
    def __init__(self):
        self.clients: set[asyncio.Queue] = set()
        self.last_frames: dict[int, dict] = {}  # newest picture per browser tile, for late joiners

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self.clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self.clients.discard(q)

    def reset(self):
        self.last_frames.clear()

    def publish(self, event: dict):
        if event["type"] == "frame":
            self.last_frames[event["worker"]] = event
        for q in list(self.clients):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # a stuck client misses events; it will resync from the snapshot on reconnect


class RunManager:
    def __init__(self):
        self.hub = Hub()
        self.run: Run | None = None
        self.items: list[Ingredient] = []
        self.report: Report | None = None
        self._finalizer: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return bool(self.run and self.run.state == "running") or bool(self._finalizer and not self._finalizer.done())

    def start(self, items: list[Ingredient], base_url: str) -> Run:
        """Create and start a run. The caller (the API) has already checked the user approved."""
        self.hub.reset()
        self.items, self.report = items, None
        selector = Selector({i.name: to_standard(i.name, i.quantity, i.unit) for i in items})
        adapters = [MockStoreAdapter(slug, base_url) for slug in MOCK_STORES]
        self.run = Run([i.name for i in items], adapters, approved=True, emit=self.hub.publish,
                       chooser=selector.choose, enricher=selector.enrich, max_parallel=config.MAX_PARALLEL,
                       headless=config.HEADLESS, fps=config.SCREENSHOT_FPS)
        self.run.start()
        self._finalizer = asyncio.create_task(self._finalize(self.run))
        return self.run

    def stop(self):
        if self.run:
            self.run.stop()

    async def _finalize(self, run: Run):
        """When the agents finish: build the report, let the AI double-check it, publish it."""
        await run.wait()
        if run.state != "finished":
            return
        report = build_report(self.items, run.results, {s: a.name for s, a in run.adapters.items()})
        pairs, owners = [], []
        for idx, item in enumerate(report.items):
            if item.options:
                pairs.append((item.ingredient, item.options[0].name))
                owners.append(idx)
        if pairs and not config.DEMO_MODE:
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
            except ai.AIError as e:  # the rule-based flags still stand
                run.emit({"type": "log", "worker": None, "store": None, "action": "review",
                          "detail": f"AI check skipped: {e}", "status": "error"})
        self.report = report
        run.emit({"type": "report", "report": report.model_dump()})

    def snapshot_event(self) -> dict:
        return {"type": "snapshot", "run": self.run.snapshot() if self.run else None,
                "frames": list(self.hub.last_frames.values()),
                "report": self.report.model_dump() if self.report else None}


manager = RunManager()
