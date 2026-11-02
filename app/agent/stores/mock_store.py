"""Adapter for our 3 mock grocery sites. They live in this process, so the adapter reads their
catalogue directly (mock_stores/products.py) instead of driving a browser. Each step still "visits"
the page through VirtualPage (safety guard + log + live tile), rendered from the same templates the
/stores routes serve. A single adapter class serves all three stores; only the slug differs."""
from urllib.parse import urlencode, urlparse

from app.agent.virtual_page import UnexpectedPage, VirtualPage
from app.agent.stores.base import StoreAdapter
from app.models import ProductDetails, SearchHit
from mock_stores import products as db
from mock_stores.server import render_html


class MockStoreAdapter(StoreAdapter):
    def __init__(self, slug: str, base_url: str):
        if slug not in db.STORES:
            raise UnexpectedPage(f"No such store: {slug}")
        self.slug = slug
        self.name = db.STORES[slug].name
        self.base = base_url.rstrip("/")
        self.allowed_hosts = {urlparse(self.base).hostname}

    def _url(self, path: str) -> str:
        return f"{self.base}/stores/{self.slug}/{path}"

    async def search(self, page: VirtualPage, query: str) -> list[SearchHit]:
        await page.goto(self._url(""), render_html(self.slug, "search", q="", products=db.featured(self.slug), is_home=True))
        await page.type_search(query)
        products = db.search(self.slug, query[:100])
        await page.goto(self._url("search?" + urlencode({"q": query})),
                        render_html(self.slug, "search", q=query, products=products, is_home=False))
        return [SearchHit(store=self.slug, product_id=p.id, name=f"{p.brand} {p.name}", size=p.size, price=p.price,
                          in_stock=p.in_stock, url=self._url(f"product/{p.id}"))
                for p in products]

    async def open_product_page(self, page: VirtualPage, hit: SearchHit) -> None:
        p = db.BY_ID.get(hit.product_id)
        if not p or p.store != self.slug:
            raise UnexpectedPage(f"{self.name}'s product page didn't look as expected")
        await page.goto(hit.url, render_html(self.slug, "product", p=p, q=""))

    async def get_product_details(self, page: VirtualPage, hit: SearchHit) -> ProductDetails:
        p = db.BY_ID.get(hit.product_id)
        if not p or p.store != self.slug:
            raise UnexpectedPage(f"{self.name}'s product page didn't look as expected")
        return ProductDetails(store=self.slug, product_id=p.id, name=f"{p.brand} {p.name}", brand=p.brand,
                              size=p.size, price=p.price, in_stock=p.in_stock, description=p.description, url=page.url)
