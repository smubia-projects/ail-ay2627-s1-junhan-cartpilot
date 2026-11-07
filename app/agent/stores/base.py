"""The StoreAdapter interface: the only thing that differs between stores.

The agent (agent.py) knows nothing about any particular shop. It asks an adapter to
search, open a product page and read its details. A mock store and a real store both
implement this, so the same agent logic runs on both.
"""
from abc import ABC, abstractmethod

from app.agent.virtual_page import VirtualPage
from app.models import ProductDetails, SearchHit


class StoreAdapter(ABC):
    slug: str  # short id used in URLs and logs, e.g. "freshmart"
    name: str  # display name, e.g. "FreshMart"
    allowed_hosts: set[str]  # the agent may only visit these hosts
    delay_range: tuple[float, float] = (0.2, 0.6)  # random pause after each action (seconds)
    max_concurrency: int = 99  # max pages on this store at once (real stores use 1-2)

    @abstractmethod
    async def search(self, page: VirtualPage, query: str) -> list[SearchHit]:
        """Search the store for `query`; return the products on the results page (maybe empty).

        Raise UnexpectedPage if the page is blocked / a CAPTCHA / not what we expected.
        """

    @abstractmethod
    async def open_product_page(self, page: VirtualPage, hit: SearchHit) -> None:
        """Open the product page for a search hit (never add to cart)."""

    @abstractmethod
    async def get_product_details(self, page: VirtualPage, hit: SearchHit) -> ProductDetails:
        """Read the details from the product page that is currently open."""
