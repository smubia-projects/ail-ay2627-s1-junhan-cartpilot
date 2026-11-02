"""Pydantic schemas. Every AI response is validated against these,
so a malformed reply from the model becomes a clean error instead of a crash."""
from typing import Literal

from pydantic import BaseModel, Field

# Units the recipe side is allowed to use. units.py converts them to g / ml / piece.
Unit = Literal["g", "kg", "ml", "l", "tsp", "tbsp", "cup", "piece", "pinch"]


# ---- requests from the frontend ----
class RecipeRequest(BaseModel):
    goal: str = Field(min_length=2, max_length=200)
    servings: int = Field(ge=1, le=50)


class IngredientRequest(BaseModel):
    goal: str = Field(min_length=2, max_length=200)
    recipe_title: str = Field(min_length=2, max_length=200)
    servings: int = Field(ge=1, le=50)
    owned: list[str] = []  # leftovers the user already has (used by the 'cook with leftovers' flow)


# ---- AI outputs ----
class RecipeOption(BaseModel):
    title: str
    description: str
    time_minutes: int = Field(ge=1)
    difficulty: Literal["easy", "medium", "hard"]


class RecipeOptions(BaseModel):
    options: list[RecipeOption] = Field(min_length=1, max_length=3)


class Ingredient(BaseModel):
    # Generic name only ("cocoa powder", not a brand) so it can be searched in any store.
    name: str
    quantity: float = Field(ge=0.01)  # not gt=0: the Gemini SDK rejects 'exclusiveMinimum' in schemas
    unit: Unit


class IngredientList(BaseModel):
    recipe_title: str
    servings: int
    ingredients: list[Ingredient] = Field(min_length=1)
    # Beginner-friendly cooking method. Defaults keep an old or sloppy reply valid: the UI just hides the section.
    steps: list[str] = Field(default_factory=list)  # plain sentences, no leading "1."
    prep_time: str = ""  # e.g. "15 min"
    cook_time: str = ""


# ---- fridge scan ----
Confidence = Literal["high", "medium", "low"]


class FridgeItem(BaseModel):
    name: str  # exactly one of the recipe's ingredient names
    have: bool  # is it visible in the photo?
    confidence: Confidence  # how sure the AI is about that have/not-have answer
    note: str = ""  # short reason, e.g. "label partly hidden"


class FridgeScan(BaseModel):
    items: list[FridgeItem]


# ---- browser agent ----
class SearchHit(BaseModel):
    """One product found on a store's search results page."""
    store: str  # store slug, e.g. "freshmart"
    product_id: str
    name: str
    size: str  # raw text as shown by the store
    price: float
    in_stock: bool
    url: str


class ProductDetails(BaseModel):
    """What we read from the product page itself (opened to double-check the search result)."""
    store: str
    product_id: str
    name: str
    brand: str = ""
    size: str
    price: float
    in_stock: bool
    description: str = ""
    url: str


class TaskResult(BaseModel):
    """Outcome of searching ONE store for ONE ingredient."""
    store: str
    ingredient: str
    status: Literal["found", "not_found", "no_match", "blocked", "error", "skipped"]
    query_used: str = ""
    hits: list[SearchHit] = []
    chosen: ProductDetails | None = None
    candidates: list["Candidate"] = []  # this store's ranked, evaluated options (best first)
    message: str = ""  # friendly explanation shown in the UI


class RunRequest(BaseModel):
    items: list[Ingredient] = Field(min_length=1, max_length=60)  # what the user still needs to buy
    approved: bool  # must be true: set only by the "Approve & start searching" button


# ---- product matching + results page ----
class Candidate(BaseModel):
    """One product, evaluated against one ingredient: is it right, how many packs, what does it cost."""
    store: str
    product_id: str
    name: str
    size: str  # raw text from the shop
    price: float
    url: str
    in_stock: bool = True
    pack_count: int = 1  # how many packs to buy to cover the need
    total_cost: float  # pack_count * price
    unit_price: float | None = None  # price per 100g / 100ml / piece (None if size unclear)
    unit_price_label: str = ""  # e.g. "S$0.60 / 100g"
    need_text: str = ""  # e.g. "Need 200g → 1 x 500 g pack"
    leftover_amount: float = 0
    leftover_unit: str = ""
    leftover_pct: float = 0  # 0..1 of what you buy that goes unused
    significant_leftover: bool = False  # leftover > 30% of the pack(s)
    leftover_text: str = ""  # e.g. "300g left over"
    confidence: Confidence = "high"
    reasons: list[str] = []  # short human reasons for any doubt, e.g. "Size unclear"


class ReportItem(BaseModel):
    ingredient: str
    quantity: float
    unit: str
    options: list[Candidate] = []  # best first, across all stores; options[0] is the pick
    message: str = ""  # shown when nothing was found
    substitute: str = ""  # suggestion when nothing was found


class StoreTotal(BaseModel):
    store: str
    name: str
    total: float  # cost of this store's own best pick for every item it had
    found: int
    missing: list[str]  # ingredients this store had nothing usable for
    to_confirm: int  # picks with low confidence


class Report(BaseModel):
    items: list[ReportItem]
    store_totals: list[StoreTotal]
    best_mix_total: float  # sum of the overall best pick per item
    stores: dict[str, str]


# ---- AI review of matches (real mode only) ----
class ReviewVerdict(BaseModel):
    index: int  # which numbered pair this is about
    same_product: bool  # right ingredient AND right form (powder vs drink, milk vs coconut milk...)
    confidence: Confidence
    reason: str = ""


class ReviewResult(BaseModel):
    verdicts: list[ReviewVerdict]


# ---- leftovers ----
class LeftoverItem(BaseModel):
    name: str
    amount: float
    unit: str  # g / ml / piece


class LeftoverRequest(BaseModel):
    leftovers: list[LeftoverItem] = Field(min_length=1, max_length=30)
    servings: int = Field(ge=1, le=50)
    exclude: list[str] = []  # recipe titles NOT to suggest (the one the user just shopped for)


class LeftoverIdea(BaseModel):
    title: str
    description: str
    uses: list[str]  # which leftovers it uses


class LeftoverIdeas(BaseModel):
    ideas: list[LeftoverIdea] = Field(min_length=1, max_length=3)


TaskResult.model_rebuild()  # resolves the forward reference to Candidate
