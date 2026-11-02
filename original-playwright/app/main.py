"""FastAPI entry point: all HTTP routes plus the /ws WebSocket for live updates."""
import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import TypeAdapter, ValidationError

from app import ai, config
from app.models import IngredientRequest, LeftoverRequest, RecipeRequest, RunRequest
from app.runs import manager
from app.units import canonical_name
from mock_stores.server import router as mock_stores_router

ROOT = Path(__file__).resolve().parent.parent

app = FastAPI(title="CartPilot")


@app.get("/api/health")
def health():
    """Quick check that the server is up and how it is configured.

    Reports whether a key exists, never the key itself.
    """
    return {
        "status": "ok",
        "demo_mode": config.DEMO_MODE,
        "store_mode": config.STORE_MODE,
        "model": config.GEMINI_MODEL,
        "gemini_key_set": bool(config.GEMINI_API_KEY),
        "headless": config.HEADLESS,
        "max_parallel": config.MAX_PARALLEL,
    }


def _friendly(e: ai.AIError):
    # status comes from the AIError (503 = AI problem, 400 = bad input); the frontend shows e.detail to the user as-is.
    return HTTPException(status_code=e.status, detail=str(e))


@app.post("/api/recipes")
async def recipes(req: RecipeRequest):
    """Step 2a: goal + servings -> 2-3 recipe options to pick from."""
    try:
        return await ai.suggest_recipes(req.goal, req.servings)
    except ai.AIError as e:
        raise _friendly(e)


@app.post("/api/ingredients")
async def ingredients(req: IngredientRequest):
    """Step 2b: chosen recipe -> scaled ingredient list."""
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
async def fridge_scan(image: UploadFile = File(...), ingredients: str = Form(...)):
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
        return await ai.scan_fridge(await image.read(), names)
    except ai.AIError as e:
        raise _friendly(e)


# ---------------------------------------------------------------- browser agent runs
@app.post("/api/runs")
async def start_run(req: RunRequest, request: Request):
    """Start the browser agents. Only works if the user clicked Approve (approved=true)."""
    if not req.approved:
        raise HTTPException(status_code=400, detail="Searching only starts after you approve the list.")
    if manager.running:
        raise HTTPException(status_code=409, detail="A search is already running. Stop it first.")
    if config.STORE_MODE != "mock":
        raise HTTPException(status_code=501, detail="Only mock-store mode is supported (real stores were deliberately left out; see the README).")
    # The agent's browsers visit this same server, so use the address the user reached us at.
    run = manager.start(req.items, str(request.base_url))
    return {"run_id": run.id}


@app.post("/api/runs/stop")
async def stop_run():
    """The Stop button: halts every agent immediately."""
    manager.stop()
    return {"stopped": True}


@app.get("/api/runs/current")
async def current_run():
    if not manager.run:
        raise HTTPException(status_code=404, detail="No search has been started.")
    return manager.snapshot_event()


@app.websocket("/ws")
async def live(ws: WebSocket):
    """Live feed for the UI: a snapshot on connect, then every agent event as it happens."""
    await ws.accept()
    q = manager.hub.subscribe()
    try:
        await ws.send_json(manager.snapshot_event())

        async def pump():
            while True:
                await ws.send_json(await q.get())

        sender = asyncio.create_task(pump())
        receiver = asyncio.create_task(ws.receive_text())  # only here to notice the browser disconnecting
        await asyncio.wait({sender, receiver}, return_when=asyncio.FIRST_COMPLETED)
        for t in (sender, receiver):
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                pass
    except WebSocketDisconnect:
        pass
    finally:
        manager.hub.unsubscribe(q)


@app.post("/api/leftover-ideas")
async def leftover_ideas(req: LeftoverRequest):
    """Step 9: biggest leftovers -> 2-3 simple recipes that use them."""
    try:
        return await ai.suggest_leftover_ideas(req.leftovers, req.servings, req.exclude)
    except ai.AIError as e:
        raise _friendly(e)


app.include_router(mock_stores_router)  # the 3 fake grocery sites at /stores/...
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")
