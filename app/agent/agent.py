"""The orchestrator: runs many agents in parallel, searching every store for every item.

Work is split into small jobs, one per (store, ingredient). A pool of `max_parallel` agents
("workers", one VirtualPage each) pulls jobs from a queue until it is empty. For each job a worker:
    search the store  ->  pick a product (the `chooser`)  ->  open its page  ->  read its details

Everything the agent does is reported through the `emit(event: dict)` callback: log lines,
worker labels, rendered store pages ("view") and results. The API streams these to the browser
over SSE. (The original drove real Chromium pages with Playwright; see original-playwright/.)
"""
import asyncio
import time
import uuid
from typing import Callable

from app import units
from app.agent.virtual_page import AgentStopped, UnexpectedPage, VirtualPage
from app.agent.safety import SafetyViolation
from app.agent.stores.base import StoreAdapter
from app.models import SearchHit, TaskResult

Chooser = Callable[[str, list[SearchHit]], SearchHit | None]
Emit = Callable[[dict], None]


def first_in_stock(ingredient: str, hits: list[SearchHit]) -> SearchHit | None:
    """PLACEHOLDER chooser: first in-stock result. Step 8 replaces it with real selection
    (name matching, price per unit, pack size, confidence flags)."""
    return next((h for h in hits if h.in_stock), None)


def queries_for(ingredient: str) -> list[str]:
    """Search terms to try, in order, until one finds something (max 4 attempts).

    Known ingredient: its name, canonical name, then synonyms from units.py
        "vanilla extract" -> ["vanilla extract", "vanilla essence", "vanilla", "pure vanilla extract"]
        "white sugar"     -> ["white sugar", "caster sugar", "sugar", "granulated sugar"]
    Unknown ingredient: its name, then its last word (usually the product type).
    """
    name = " ".join(ingredient.lower().split())
    canon = units.canonical_name(name)
    if canon:
        info = units.INGREDIENTS[canon]
        out = [name, canon, *info.synonyms]
        # a plain plural adds nothing ("egg" -> "eggs"); the shops' search already handles it
        out = [q for q in out if q != name + "s" and q != name + "es"] if len(out) > 1 else out
    else:
        out = [name, name.split()[-1]]
    return list(dict.fromkeys(out))[:4]


class Run:
    """One shopping search across all stores. Created, then start()ed, and can be stop()ped."""

    def __init__(self, items: list[str], adapters: list[StoreAdapter], *, approved: bool,
                 emit: Emit | None = None, chooser: Chooser = first_in_stock,
                 enricher: Callable[[TaskResult], None] | None = None, max_parallel: int = 4,
                 delay_range: tuple[float, float] | None = None):
        self.id = uuid.uuid4().hex[:8]
        self.items = items
        self.adapters = {a.slug: a for a in adapters}
        self.approved = approved
        self.chooser = chooser
        self.enricher = enricher  # called on every finished result, e.g. to attach ranked options
        self.max_parallel = max(1, max_parallel)
        self.delay_range = delay_range  # overrides each store's delay (tests use (0, 0))
        self._emit_cb = emit

        self.state = "pending"  # pending -> running -> finished | stopped | failed
        self.results: list[TaskResult] = []
        self.log: list[dict] = []
        self.workers: dict[int, dict] = {}  # worker id -> {"store": ..., "label": ...}
        self.failed_stores: dict[str, str] = {}  # store slug -> reason it was abandoned
        self.stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._store_locks = {s: asyncio.Semaphore(a.max_concurrency) for s, a in self.adapters.items()}

    # ------------------------------------------------------------ public control
    def start(self) -> asyncio.Task:
        """Begin in the background. Refuses unless the user explicitly approved."""
        if not self.approved:
            raise PermissionError("Automation can only start after the user approves the list.")
        if self._task:
            raise RuntimeError("Run already started")
        self.state = "running"
        self._task = asyncio.create_task(self._main(), name=f"run-{self.id}")
        return self._task

    def stop(self):
        """Halt everything now: flag all SafePages to refuse further actions, cancel all tasks."""
        self.stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()

    async def wait(self):
        if self._task:
            await asyncio.wait([self._task])

    def snapshot(self) -> dict:
        return {"id": self.id, "state": self.state, "items": self.items, "workers": self.workers,
                "results": [r.model_dump() for r in self.results], "log": self.log[-200:],
                "stores": {s: a.name for s, a in self.adapters.items()}}

    # ------------------------------------------------------------ events
    def emit(self, event: dict):
        event = {"ts": time.time(), "run": self.id, **event}
        if event["type"] == "log":
            self.log.append(event)
        if self._emit_cb:
            self._emit_cb(event)

    def _set_worker(self, wid: int, store: str | None, label: str):
        self.workers[wid] = {"store": store, "label": label}
        self.emit({"type": "worker", "worker": wid, "store": store, "label": label})

    # ------------------------------------------------------------ main loop
    async def _main(self):
        queue: asyncio.Queue = asyncio.Queue()
        for item in self.items:  # item-major order: all stores work on one item before the next
            for adapter in self.adapters.values():
                queue.put_nowait((adapter, item))
        n_workers = min(self.max_parallel, queue.qsize())
        self.emit({"type": "run_started", "items": self.items, "stores": {s: a.name for s, a in self.adapters.items()},
                   "workers": n_workers})
        try:
            await asyncio.gather(*(self._worker(i, queue) for i in range(n_workers)))
            self.state = "finished"
            self.emit({"type": "run_finished", "results": len(self.results)})
        except (asyncio.CancelledError, AgentStopped):
            self.state = "stopped"
            self.emit({"type": "run_stopped"})
        except Exception as e:
            self.state = "failed"
            self.emit({"type": "run_failed", "message": f"The search agents stopped unexpectedly: {e}"})

    async def _worker(self, wid: int, queue: asyncio.Queue):
        allowed = set().union(*(a.allowed_hosts for a in self.adapters.values()))
        sp = VirtualPage(wid, allowed, self.stop_event, lambda *a, **k: self._action(wid, *a, **k),
                         lambda url, html: self.emit({"type": "view", "worker": wid, "url": url, "html": html}))
        self._set_worker(wid, None, "Ready")
        while True:
            try:
                adapter, ingredient = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            sp.delay_range = self.delay_range or adapter.delay_range
            async with self._store_locks[adapter.slug]:
                result = await self._do_job(wid, sp, adapter, ingredient)
            if self.enricher:
                self.enricher(result)
            self.results.append(result)
            self.emit({"type": "result", "result": result.model_dump()})
        self._set_worker(wid, None, "Done")

    # ------------------------------------------------------------ one job
    async def _do_job(self, wid: int, sp: VirtualPage, adapter: StoreAdapter, ingredient: str) -> TaskResult:
        base = dict(store=adapter.slug, ingredient=ingredient)
        if adapter.slug in self.failed_stores:
            return TaskResult(**base, status="skipped", message=f"Skipped: {self.failed_stores[adapter.slug]}")
        self._set_worker(wid, adapter.name, f"searching {ingredient}…")
        try:
            hits, used = [], ""
            for query in queries_for(ingredient):
                used = query
                hits = await adapter.search(sp, query)
                self._action(wid, "found", f"{len(hits)} result(s) for {query!r}")
                if hits:
                    break
            if not hits:
                return TaskResult(**base, status="not_found", query_used=used,
                                  message=f"{adapter.name} has no results for “{ingredient}”.")
            hit = self.chooser(ingredient, hits)
            if hit is None:
                return TaskResult(**base, status="no_match", query_used=used, hits=hits,
                                  message="Results found, but none were suitable (e.g. all out of stock).")
            self._set_worker(wid, adapter.name, f"opening {hit.name}")
            await adapter.open_product_page(sp, hit)
            details = await adapter.get_product_details(sp, hit)
            self._set_worker(wid, adapter.name, f"found {ingredient}")
            return TaskResult(**base, status="found", query_used=used, hits=hits, chosen=details)
        except (AgentStopped, asyncio.CancelledError):
            raise
        except SafetyViolation as e:  # already logged by VirtualPage; this item is skipped, run continues
            return TaskResult(**base, status="blocked", message=e.reason)
        except UnexpectedPage as e:  # unknown store / product missing: abandon this whole store
            self.failed_stores[adapter.slug] = str(e)
            self._action(wid, "stop-store", f"{adapter.name}: {e}", "error")
            return TaskResult(**base, status="error", message=f"{e}. Stopped searching {adapter.name}.")
        except Exception as e:
            self._action(wid, "error", f"{type(e).__name__}: {e}", "error")
            return TaskResult(**base, status="error", message=f"Something went wrong searching {adapter.name}.")

    # ------------------------------------------------------------ logging
    def _action(self, wid: int, action: str, detail: str = "", status: str = "ok"):
        """Every action the agent takes goes through here: it becomes a log line in the UI."""
        store = self.workers.get(wid, {}).get("store")
        self.emit({"type": "log", "worker": wid, "store": store, "action": action, "detail": detail, "status": status})
