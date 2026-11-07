"""FastAPI entry point: the JSON API, the SSE stream for a live search run, and the 3 mock stores.

API-only: the UI (static/) is deployed separately on Vercel and calls this API across origins.
"""
import json

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import TypeAdapter, ValidationError

from app import ai, config
from app.imaging import prepare_image
from app.models import IngredientRequest, LeftoverRequest, RecipeRequest, RunRequest
from app.rate_limit import check_rate_limit
from app.runs import stream_run
from app.units import canonical_name
from mock_stores.server import router as mock_stores_router

app = FastAPI(title="CartPilot")
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["GET", "POST"],
                   allow_headers=["Content-Type"])


@app.get("/")
def root():
    return {"service": "ail-ay2627-s1-junhan-cartpilot", "health": "/api/health"}


@app.get("/api/health")
def health():
    """Quick check that the server is up and how it is configured.

    Reports whether a key exists, never the key itself.
    """
    return {
        "status": "ok",
        "demo_mode": config.DEMO_MODE,
        "store_mode": config.STORE_MODE,
        "model": config.MODEL_NAME,
        "key_set": bool(config.API_KEY),
        "max_parallel": config.MAX_PARALLEL,
    }


def _friendly(e: ai.AIError):
    # status comes from the AIError (502 = AI problem, 501 = not configured, 400 = bad input);
    # the frontend shows e.detail to the user as-is.
    return HTTPException(status_code=e.status, detail=str(e))


def _limit(request: Request, bucket: str):
    """Count one real model call. Demo mode (canned answers) makes none, so it is not limited."""
    if not config.DEMO_MODE:
        check_rate_limit(request, bucket)


@app.post("/api/recipes")
async def recipes(req: RecipeRequest, request: Request):
    """Step 2a: goal + servings -> 2-3 recipe options to pick from."""
    _limit(request, "plan")
    try:
        return await ai.suggest_recipes(req.goal, req.servings)
    except ai.AIError as e:
        raise _friendly(e)


@app.post("/api/ingredients")
async def ingredients(req: IngredientRequest, request: Request):
    """Step 2b: chosen recipe -> scaled ingredient list."""
    _limit(request, "plan")
    try:
        result = await ai.generate_ingredients(req.goal, req.recipe_title, req.servings)
    except ai.AIError as e:
        raise _friendly(e)
    # Which ingredients does the user already own (leftovers from a previous shop)? Match by canonical
    # name so "white sugar" and "caster sugar" count as the same thing.
    owned = {canonical_name(o) or o.lower().strip() for o in req.owned}
    return {**result.model_dump(),
            "owned": [i.name for i in result.ingredients if (canonical_name(i.name) or i.name.lower().strip()) in owned]}


@app.post("/api/fridge-scan")
async def fridge_scan(request: Request, image: UploadFile = File(...), ingredients: str = Form(...)):
    """Step 3: photo + recipe ingredient names -> have/not-have + confidence per ingredient.

    Sent as multipart form data because it carries a file; `ingredients` is a JSON list of names.
    """
    try:
        names = TypeAdapter(list[str]).validate_python(json.loads(ingredients))
    except (ValueError, ValidationError):
        raise HTTPException(status_code=400, detail="Ingredient list was invalid.")
    if not names or len(names) > 60:
        raise HTTPException(status_code=400, detail="Ingredient list was empty or too long.")
    try:
        jpeg = prepare_image(await image.read())  # a bad photo is rejected before it costs a model call
        _limit(request, "scan")
        return await ai.scan_fridge(jpeg, names)
    except ai.AIError as e:
        raise _friendly(e)


# ---------------------------------------------------------------- agent search runs
@app.post("/api/runs/stream")
async def run_stream(req: RunRequest, request: Request):
    """Start the agents for THIS visitor and stream every event as Server-Sent Events.

    Only works if the user clicked Approve (approved=true). Closing the stream (the Stop button)
    stops the agents. The run ends with one AI double-check of the matches, so it is limited.
    """
    if not req.approved:
        raise HTTPException(status_code=400, detail="Searching only starts after you approve the list.")
    if config.STORE_MODE != "mock":
        raise HTTPException(status_code=501, detail="Only mock-store mode is supported (real stores were deliberately left out; see the README).")
    _limit(request, "search")
    # Product links point at this server's /stores pages, so use the address the visitor reached us at.
    events = stream_run(req.items, str(request.base_url), review=not config.DEMO_MODE)

    async def sse():
        async for event in events:
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/leftover-ideas")
async def leftover_ideas(req: LeftoverRequest, request: Request):
    """Step 9: biggest leftovers -> 2-3 simple recipes that use them."""
    _limit(request, "plan")
    try:
        return await ai.suggest_leftover_ideas(req.leftovers, req.servings, req.exclude)
    except ai.AIError as e:
        raise _friendly(e)


app.include_router(mock_stores_router)  # the 3 fake grocery sites at /stores/... (the agents' product links)
