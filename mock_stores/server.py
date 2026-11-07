"""Routes for the three mock grocery sites, mounted under /stores by app/main.py.

    /stores/                         list of stores
    /stores/<store>/                 home page (search box + featured items)
    /stores/<store>/search?q=...     search results
    /stores/<store>/product/<id>     product page

THE SHARED "CONTRACT": the three stores look and are laid out differently, but each page
marks its data with the same `data-*` attributes (like a real shop's test-ids). The mock-store
adapter (step 6) relies on only these, so one adapter works for all three:

    input[name=q]                 the search box
    .product-card                 one search result; has data-product-id
      a.product-link              link to the product page
      [data-field=name|size|price|stock]   (price also has data-price, stock has data-in-stock)
    .no-results                   shown when a search finds nothing
    main[data-product-id]         product page; same data-field hooks plus brand/description

The pages have no cart or checkout. The "Sign in" link and "Add to cart" button on product pages
are inert look-alikes (no handler, no form), there so the safety guard has something real to refuse.
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from mock_stores import products as db

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")
router = APIRouter(prefix="/stores")


def _store(slug: str) -> db.Store:
    if slug not in db.STORES:
        raise HTTPException(status_code=404, detail="No such store")
    return db.STORES[slug]


def _render(request: Request, slug: str, page: str, **ctx):
    return TEMPLATES.TemplateResponse(
        request, f"{slug}/{page}.html", {"store": _store(slug), "stores": db.STORES.values(), **ctx}
    )


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    return TEMPLATES.TemplateResponse(request, "index.html", {"stores": db.STORES.values()})


@router.get("/{slug}/", response_class=HTMLResponse)
@router.get("/{slug}", response_class=HTMLResponse, include_in_schema=False)
def home(request: Request, slug: str):
    return _render(request, slug, "search", q="", products=db.featured(slug), is_home=True)


@router.get("/{slug}/search", response_class=HTMLResponse)
def search(request: Request, slug: str, q: str = ""):
    _store(slug)
    return _render(request, slug, "search", q=q, products=db.search(slug, q[:100]), is_home=False)


@router.get("/{slug}/product/{product_id}", response_class=HTMLResponse)
def product(request: Request, slug: str, product_id: str):
    _store(slug)
    p = db.BY_ID.get(product_id)
    if not p or p.store != slug:
        raise HTTPException(status_code=404, detail="Product not found")
    return _render(request, slug, "product", p=p, q="")


def render_html(slug: str, page: str, **ctx) -> str:
    """The same page as HTML text, for the in-process agents' live tiles (no HTTP request)."""
    return TEMPLATES.env.get_template(f"{slug}/{page}.html").render(store=_store(slug), stores=db.STORES.values(), **ctx)
