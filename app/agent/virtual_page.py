"""VirtualPage: the ONLY way agent code "visits" a store page.

The deployed showcase has no real browser (Playwright needs Chromium, which is too heavy for the
demo backend). The mock stores live in this same process, so an agent reads them directly. To keep
the original's guarantees, every visit still goes through this class, which
  1. stops immediately if the user pressed Stop (or closed the page),
  2. runs the safety guard (safety.py) on the URL and refuses forbidden ones,
  3. writes a log line (so every action is visible in the UI),
  4. shows the rendered store page on the agent's live tile,
  5. then waits a small random delay (so the run is watchable live).

The original Playwright SafePage is kept in original-playwright/ (not deployed).
"""
import asyncio
import random
from typing import Callable

from app.agent.safety import SafetyViolation, check_navigation


class AgentStopped(Exception):
    """The user pressed Stop."""


class UnexpectedPage(Exception):
    """The page isn't what we expected (unknown store, missing product). Stop using this store."""


class VirtualPage:
    def __init__(self, worker: int, allowed_hosts: set[str], stop: asyncio.Event,
                 log: Callable[..., None], show: Callable[[str, str], None],
                 delay_range: tuple[float, float] = (0.2, 0.6)):
        self.worker = worker
        self.allowed_hosts = allowed_hosts
        self.delay_range = delay_range
        self.url = ""
        self._stop = stop
        self._log = log
        self._show = show

    def _check_stop(self):
        if self._stop.is_set():
            raise AgentStopped()

    async def _pause(self):
        await asyncio.sleep(random.uniform(*self.delay_range))
        self._check_stop()

    async def goto(self, url: str, html: str):
        """Visit `url` (already rendered as `html` by the store adapter)."""
        self._check_stop()
        try:
            check_navigation(url, self.allowed_hosts)
        except SafetyViolation as e:
            self._log("goto", e.reason, "blocked")
            raise
        self._log("goto", url)
        self.url = url
        self._show(url, html)
        await self._pause()

    async def type_search(self, text: str):
        """Log typing the query into the store's search box (the next goto shows the results)."""
        self._check_stop()
        self._log("type", f"{text!r} into the search box")
        await self._pause()
