"""SafePage: the ONLY way agent code touches a browser page.

Raw Playwright pages are never handed to the agent or the store adapters. SafePage offers just
goto / type-in-search-box / click / read, and every one of them
  1. stops immediately if the user pressed Stop,
  2. runs the safety guard (safety.py) and refuses forbidden actions,
  3. writes a log line (so every action is visible in the UI),
  4. then waits a small random delay (polite to real sites, and nicer to watch live).

A second, network-level check also aborts any page navigation to a forbidden or non-allowed
URL (for example a redirect to a login page), even if no click of ours caused it.
"""
import asyncio
import random
from typing import Callable
from urllib.parse import urljoin

from playwright.async_api import BrowserContext, Page

from app.agent.safety import ElementInfo, SafetyViolation, check_click, check_fill, check_navigation


class AgentStopped(Exception):
    """The user pressed Stop."""


class UnexpectedPage(Exception):
    """The page isn't what we expected (blocked, CAPTCHA, layout changed). Stop using this store."""


# Reads facts about an element for the guard. Read-only: it changes nothing on the page.
_INFO_JS = """el => {
  const anc = el.closest('a,button,[role=button]') || el;
  const form = el.closest('form');
  const cls = x => (x.className && x.className.baseVal !== undefined) ? x.className.baseVal : (x.className || '');
  return {
    tag: el.tagName, text: (el.innerText || el.textContent || '').slice(0, 200),
    aria_label: el.getAttribute('aria-label') || '', title: el.getAttribute('title') || '',
    value: el.value || el.getAttribute('value') || '', href: el.href || '', id: el.id || '',
    classes: cls(el), name: el.getAttribute('name') || '', type: el.getAttribute('type') || '',
    placeholder: el.getAttribute('placeholder') || '', role: el.getAttribute('role') || '',
    form_action: form ? form.action : '',
    anc_text: (anc.innerText || '').slice(0, 200), anc_classes: cls(anc), anc_href: anc.href || ''
  };
}"""

# Reads fields out of every element matching a selector. Also read-only.
_EXTRACT_JS = """(els, fields) => els.map(el => {
  const out = {};
  for (const [key, [sel, attr]] of Object.entries(fields)) {
    const t = sel ? el.querySelector(sel) : el;
    out[key] = t ? (attr ? t.getAttribute(attr) : (t.innerText || t.textContent || '').trim()) : null;
  }
  return out;
})"""


class SafePage:
    def __init__(self, page: Page, worker: int, allowed_hosts: set[str], stop: asyncio.Event,
                 log: Callable[..., None], delay_range: tuple[float, float] = (0.2, 0.6),
                 timeout_ms: int = 10_000):
        self._page = page  # private on purpose: nothing outside this class should use it
        self.worker = worker
        self.allowed_hosts = allowed_hosts
        self._stop = stop
        self._log = log
        self.delay_range = delay_range
        page.set_default_timeout(timeout_ms)

    @classmethod
    async def open(cls, context: BrowserContext, worker: int, allowed_hosts: set[str], stop: asyncio.Event,
                   log: Callable[..., None], **kw) -> "SafePage":
        page = await context.new_page()
        sp = cls(page, worker, allowed_hosts, stop, log, **kw)

        async def network_guard(route, request):
            # Layer 2: any top-level or iframe navigation must pass the same URL rules.
            if request.is_navigation_request():
                try:
                    check_navigation(request.url, allowed_hosts)
                except SafetyViolation as e:
                    sp._log("navigate", e.reason, "blocked")
                    await route.abort()
                    return
            await route.continue_()

        await page.route("**/*", network_guard)
        # Popups / new tabs are closed straight away: we only ever work in our own page.
        context.on("page", lambda p: asyncio.create_task(p.close()) if p is not page else None)
        page.on("dialog", lambda d: asyncio.create_task(d.dismiss()))
        return sp

    # ------------------------------------------------------------ helpers
    def _check_stop(self):
        if self._stop.is_set():
            raise AgentStopped()

    async def _pause(self):
        await asyncio.sleep(random.uniform(*self.delay_range))
        self._check_stop()

    async def _info(self, selector: str) -> ElementInfo:
        loc = self._page.locator(selector).first
        return ElementInfo.from_dict(await loc.evaluate(_INFO_JS))

    @property
    def url(self) -> str:
        return self._page.url

    # ------------------------------------------------------------ actions
    async def goto(self, url: str):
        self._check_stop()
        try:
            check_navigation(url, self.allowed_hosts)
        except SafetyViolation as e:
            self._log("goto", e.reason, "blocked")
            raise
        self._log("goto", url)
        await self._page.goto(url, wait_until="domcontentloaded")
        await self._pause()

    async def type_and_search(self, selector: str, text: str):
        """Type into the store's search box and press Enter. Refuses non-search inputs."""
        self._check_stop()
        info = await self._info(selector)
        try:
            check_fill(info)
        except SafetyViolation as e:
            self._log("type", e.reason, "blocked")
            raise
        self._log("type", f"{text!r} into {info.describe()}")
        box = self._page.locator(selector).first
        await box.fill("")
        await box.press_sequentially(text, delay=35)  # typed key by key so it's visible live
        self._check_stop()
        self._log("press", "Enter (submit search)")
        await box.press("Enter")
        await self._pause()

    async def click(self, selector: str):
        """Click one element, but only if the guard says it's safe."""
        self._check_stop()
        info = await self._info(selector)
        try:
            check_click(info, self.allowed_hosts)
        except SafetyViolation as e:
            self._log("click", e.reason, "blocked")
            raise
        self._log("click", info.describe())
        await self._page.locator(selector).first.click()
        await self._pause()

    async def wait_for(self, selector: str, timeout_ms: int | None = None):
        self._check_stop()
        await self._page.wait_for_selector(selector, timeout=timeout_ms)

    async def exists(self, selector: str) -> bool:
        return await self._page.locator(selector).count() > 0

    async def extract(self, selector: str, fields: dict[str, tuple[str | None, str | None]]) -> list[dict]:
        """Read text/attributes from every element matching `selector` (read-only).

        fields = {"name": (".title", None), "price": (".price", "data-price")}
        Each value is (css selector inside the element or None for itself, attribute or None for text).
        URLs in "href" attributes are made absolute.
        """
        self._check_stop()
        rows = await self._page.eval_on_selector_all(selector, _EXTRACT_JS, fields)
        for row in rows:
            for k, v in row.items():
                if v and k.endswith("url"):
                    row[k] = urljoin(self._page.url, v)
        return rows

    async def screenshot(self, quality: int = 50) -> bytes:
        """Small JPEG of what the page looks like right now (for the live grid)."""
        return await self._page.screenshot(type="jpeg", quality=quality)

    async def close(self):
        try:
            await self._page.context.close()
        except Exception:
            pass
