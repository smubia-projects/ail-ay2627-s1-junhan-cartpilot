"""Adapter for our 3 mock grocery sites. They share one HTML contract (see mock_stores/server.py),
so a single adapter class serves all of them; only the slug differs."""
from urllib.parse import urlparse

from app.agent.safe_page import SafePage, UnexpectedPage
from app.agent.stores.base import StoreAdapter
from app.models import ProductDetails, SearchHit
from mock_stores.products import STORES

_CARD_FIELDS = {
    "product_id": (None, "data-product-id"),
    "name": ("[data-field=name]", None),
    "size": ("[data-field=size]", None),
    "price": ("[data-field=price]", "data-price"),
    "stock": ("[data-field=stock]", "data-in-stock"),
    "product_url": ("a.product-link", "href"),
}
_DETAIL_FIELDS = {
    "product_id": (None, "data-product-id"),
    "name": ("[data-field=name]", None),
    "brand": ("[data-field=brand]", None),
    "size": ("[data-field=size]", None),
    "price": ("[data-field=price]", "data-price"),
    "stock": ("[data-field=stock]", "data-in-stock"),
    "description": ("[data-field=description]", None),
}


class MockStoreAdapter(StoreAdapter):
    def __init__(self, slug: str, base_url: str):
        self.slug = slug
        self.name = STORES[slug].name
        self.base = base_url.rstrip("/")
        self.allowed_hosts = {urlparse(self.base).hostname}

    async def search(self, page: SafePage, query: str) -> list[SearchHit]:
        await page.goto(f"{self.base}/stores/{self.slug}/")
        await page.type_and_search("input[name=q]", query)
        await page.wait_for(".product-card, .no-results")
        if await page.exists(".no-results"):
            return []
        rows = await page.extract(".product-card", _CARD_FIELDS)
        try:
            return [SearchHit(store=self.slug, product_id=r["product_id"], name=r["name"], size=r["size"],
                              price=float(r["price"]), in_stock=r["stock"] == "true", url=r["product_url"])
                    for r in rows]
        except (TypeError, ValueError, KeyError):
            raise UnexpectedPage(f"{self.name}'s results page didn't look as expected")

    async def open_product_page(self, page: SafePage, hit: SearchHit) -> None:
        link = f'.product-card[data-product-id="{hit.product_id}"] a.product-link'
        if await page.exists(link):  # still on the results page: click like a person would
            await page.click(link)
        else:
            await page.goto(hit.url)
        await page.wait_for("main[data-product-id]")

    async def get_product_details(self, page: SafePage, hit: SearchHit) -> ProductDetails:
        rows = await page.extract("main[data-product-id]", _DETAIL_FIELDS)
        if not rows:
            raise UnexpectedPage(f"{self.name}'s product page didn't look as expected")
        r = rows[0]
        return ProductDetails(store=self.slug, product_id=r["product_id"], name=r["name"], brand=r["brand"] or "",
                              size=r["size"], price=float(r["price"]), in_stock=r["stock"] == "true",
                              description=r["description"] or "", url=page.url)
