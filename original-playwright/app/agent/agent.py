"""The orchestrator: runs many browser pages in parallel, searching every store for every item.

Work is split into small jobs, one per (store, ingredient). A pool of `max_parallel` browser
pages ("workers") pulls jobs from a queue until it is empty. For each job a worker:
    search the store  ->  pick a product (the `chooser`)  ->  open its page  ->  read its details

Everything the agent does is reported through the `emit(event: dict)` callback: log lines,
worker labels, JPEG frames and results. The web layer (step 7) forwards these to the browser.
"""
import asyncio
import base64
import time
import uuid
from typing import Callable

from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright

from app import units
from app.agent.safe_page import AgentStopped, SafePage, UnexpectedPage
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
                 headless: bool = True, fps: float = 2.0, viewport: tuple[int, int] = (900, 600)):
        self.id = uuid.uuid4().hex[:8]
        self.items = items
        self.adapters = {a.slug: a for a in adapters}
        self.approved = approved
        self.chooser = chooser
        self.enricher = enricher  # called on every finished result, e.g. to attach ranked options
        self.max_parallel = max(1, max_parallel)
        self.headless = headless
        self.fps = fps
        self.viewport = viewport
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
        browser = pw = None
        try:
            pw = await async_playwright().start()
            browser = await pw.chromium.launch(headless=self.headless)
            await asyncio.gather(*(self._worker(i, queue, browser) for i in range(n_workers)))
            self.state = "finished"
            self.emit({"type": "run_finished", "results": len(self.results)})
        except (asyncio.CancelledError, AgentStopped):
            self.state = "stopped"
            self.emit({"type": "run_stopped"})
        except Exception as e:  # e.g. Chromium not installed
            self.state = "failed"
            self.emit({"type": "run_failed", "message": f"Couldn't start the browser agent: {e}"})
        finally:
            # Closing the browser kills every page, even ones in the middle of an action.
            for closer in (browser and browser.close, pw and pw.stop):
                if closer:
                    try:
                        await asyncio.shield(closer())
                    except BaseException:
                        pass

    async def _worker(self, wid: int, queue: asyncio.Queue, browser):
        allowed = set().union(*(a.allowed_hosts for a in self.adapters.values()))
        context = await browser.new_context(viewport={"width": self.viewport[0], "height": self.viewport[1]},
                                            locale="en-SG")
        sp = await SafePage.open(context, wid, allowed, self.stop_event, lambda *a, **k: self._action(wid, *a, **k))
        caster = asyncio.create_task(self._screencast(wid, sp))
        self._set_worker(wid, None, "Ready")
        try:
            while True:
                try:
                    adapter, ingredient = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                sp.delay_range = adapter.delay_range
                async with self._store_locks[adapter.slug]:
                    result = await self._do_job(wid, sp, adapter, ingredient)
                if self.enricher:
                    self.enricher(result)
                self.results.append(result)
                self.emit({"type": "result", "result": result.model_dump()})
            self._set_worker(wid, None, "Done")
            await self._send_frame(wid, sp)  # last picture, so the tile shows the final page
        finally:
            caster.cancel()
            await sp.close()

    # ------------------------------------------------------------ one job
    async def _do_job(self, wid: int, sp: SafePage, adapter: StoreAdapter, ingredient: str) -> TaskResult:
        base = dict(store=adapter.slug, ingredient=ingredient)
        if adapter.slug in self.failed_stores:
            return TaskResult(**base, status="skipped", message=f"Skipped: {self.failed_stores[adapter.slug]}")
        self._set_worker(wid, adapter.name, f"searching {ingredient}…")
        try:
            hits, used = [], ""
            for query in queries_for(ingredient):
                used = query
                hits = await self._with_retry(adapter.search, sp, query)
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
        except SafetyViolation as e:  # already logged by SafePage; this item is skipped, run continues
            return TaskResult(**base, status="blocked", message=e.reason)
        except UnexpectedPage as e:  # blocked / CAPTCHA / layout changed: abandon this whole store
            self.failed_stores[adapter.slug] = str(e)
            self._action(wid, "stop-store", f"{adapter.name}: {e}", "error")
            return TaskResult(**base, status="error", message=f"{e}. Stopped searching {adapter.name}.")
        except PlaywrightTimeout:
            self._action(wid, "timeout", f"{adapter.name} took too long", "error")
            return TaskResult(**base, status="error", message=f"{adapter.name} was too slow to respond.")
        except Exception as e:
            self._action(wid, "error", f"{type(e).__name__}: {e}", "error")
            return TaskResult(**base, status="error", message=f"Something went wrong searching {adapter.name}.")

    async def _with_retry(self, fn, sp, arg):
        """Run an adapter call; retry once on a timeout (a slow page load is usually a blip)."""
        try:
            return await fn(sp, arg)
        except PlaywrightTimeout:
            self._action(sp.worker, "retry", "timed out, trying once more", "error")
            return await fn(sp, arg)

    # ------------------------------------------------------------ logging + live frames
    def _action(self, wid: int, action: str, detail: str = "", status: str = "ok"):
        """Every action the agent takes goes through here: it becomes a log line in the UI."""
        store = self.workers.get(wid, {}).get("store")
        self.emit({"type": "log", "worker": wid, "store": store, "action": action, "detail": detail, "status": status})

    async def _send_frame(self, wid: int, sp: SafePage, last: list | None = None):
        try:
            jpeg = await sp.screenshot()
        except Exception:
            return  # page was mid-navigation or closing: just skip this frame
        if last is not None:
            if last and last[0] == jpeg:
                return  # nothing changed; don't resend the same picture
            last[:] = [jpeg]
        self.emit({"type": "frame", "worker": wid, "data": base64.b64encode(jpeg).decode()})

    async def _screencast(self, wid: int, sp: SafePage):
        last: list = []
        while True:
            await asyncio.sleep(1 / self.fps)
            await self._send_frame(wid, sp, last)
