# 🛒 CartPilot

**An AI shopping assistant that turns "I want to bake a chocolate cake" into a price-compared list of product pages, without ever touching a cart.**

Hackathon track: **Daily Life**.

> **AI Lodge showcase.** This fork is deployed as a public demo by the SMU BIA AI Lodge. What is deployed:
>
> * **API** (`app/`, `mock_stores/`): FastAPI on Google Cloud Run, API-only. The three mock stores are served from the
>   same service at `/stores/...`, because the results link to their product pages.
> * **UI** (`static/`): the same plain HTML/CSS/JS on Vercel; `build.js` writes `static/config.js` with the API URL
>   from `VITE_API_BASE`.
>
> What changed for the showcase:
>
> * **No Playwright.** Headless Chromium is too heavy for the demo backend, so the agents read the mock stores
>   in-process. Each step still goes through a guard (`app/agent/virtual_page.py`: Stop check, the same URL safety
>   rules, a log line) and the live grid shows the store page each agent is reading, rendered from the stores' own
>   templates in a sandboxed frame instead of a screenshot. The original Playwright code, tests and deploy files are
>   kept in `original-playwright/` (not deployed).
> * **One run per visitor.** "Approve & start searching" opens one streaming request (Server-Sent Events) and the run
>   lives inside it; **Stop** closes the stream. There is no shared global run and no WebSocket.
> * **AI through any OpenAI-compatible gateway** (`API_BASE_URL`, `API_KEY`, `MODEL_NAME`, `VISION_MODEL_NAME`),
>   in JSON mode with the same Pydantic validation. The Gemini SDK is no longer used.
> * **AI Lodge rate limit** on every model call (recipes, ingredients, leftover ideas, fridge scan, and each search run,
>   which ends with the AI match double-check). Over the limit the app shows the AI Lodge card and the AI step stops.
>   Leftover recipe ideas are now asked for with a button instead of automatically.
> * `DEMO_MODE` (canned answers) stays for local development and is off in the deployment.


---

## 1. Problem Statement

**What problem?** Turning a meal idea into a grocery shop is tedious, and it is easy to get wrong:

1. work out what the recipe needs, scaled to how many people are eating,
2. check the fridge and pantry to avoid buying duplicates,
3. open several grocery websites and search for each ingredient one by one,
4. compare products that are sold in different pack sizes ("500 g", "6 x 50ml", "12's") and so can't be compared by the sticker price alone,
5. avoid look-alike products ("cocoa drink mix" is not "cocoa powder"; "coconut milk" is not "milk").

**Who experiences it?** Anyone who cooks or bakes at home and shops across more than one store: students on a tight budget, busy households, people new to cooking who don't know how much of something a recipe needs.

**Why is it worth solving?** The sticker price is misleading, so people either overspend or over-buy. A 1 kg bag may be cheaper per gram, but it is no bargain if the recipe needs 40 g and the rest goes unused. Comparing by price per unit *and* by how much the recipe actually needs is exactly the kind of tedious arithmetic software is good at. Doing it across several stores in one go saves real time.

---

## 2. Solution Overview

CartPilot takes a cooking goal and does the legwork, while the human stays in control of every decision:

1. **You type a goal** ("bake a chocolate cake", 6 servings). The AI suggests 2–3 recipes; you pick one.
2. **The AI writes the ingredient list**, scaled to your servings, plus a short, beginner-friendly **"How to make it"** method (numbered steps with prep and cook times) from the same request.
3. **You upload a fridge/pantry photo.** The AI checks which *recipe ingredients* are visible, with a confidence level for each. Only high-confidence items are pre-ticked; unsure ones are marked **Please check**. You correct any mistakes.
4. **You click Approve.** Nothing automated happens before this.
5. **Browser agents search every store in parallel.** You watch them live in a grid of labelled browser screenshots, with an activity log and a running total per store.
6. **CartPilot picks the best product per ingredient**: the right product (not a look-alike), at the cheapest cost to cover what you need, shown as e.g. *"Need 225g → 1 x 500 g pack"*, with alternatives you can swap in.
7. **You get a list of product pages**, totals per store, and a **Leftovers** panel with recipe ideas that use up what you'll have too much of (one tap starts a new search with those leftovers counted as already owned).

**CartPilot never adds to a cart, logs in, or buys anything.** The final output is a list of links. You decide what to buy, and where.

### Demo walkthrough (about 2 minutes, works offline)

1. Keep `DEMO_MODE=true`. Type **"bake a chocolate cake"**, 6 servings → **Suggest recipes** → pick **Classic Chocolate Cake**. Open **How to make it** under the ingredient list to read the 8 cooking steps, with quantities already scaled to 6 servings.
2. Upload *any* photo as the "fridge". Demo mode reports milk and eggs as clearly present (pre-ticked), and butter and baking powder as unsure (**Please check**).
3. Click **Approve & start searching** and watch three differently-styled shops searched at once. Try **Stop** if you like.
4. On the results page notice that:
   * **cocoa powder** is the real powder, not QuickBasket's "Cocoa Powder Drink Mix" (the look-alike that QuickBasket's own search ranks *first*);
   * **baking powder** skips ValueGrocer's cheapest tin, because its size is just "Value Tin" and can't be parsed;
   * every row shows the pack maths and price per 100 g, plus swappable alternatives;
   * an item marked **Please confirm** must be ticked or swapped before **Open all product pages** unlocks;
   * the same collapsible **How to make it** card sits at the top, so you can cook from the page once you have shopped.
5. In **Leftovers**, tap **Search for this recipe** on an idea. The leftovers are pre-marked as owned and you're back at the checklist.

### Architecture

```
 browser (static/)  ──HTTP──▶  FastAPI (app/main.py)  ──▶  Gemini (app/ai.py)    recipes, ingredients, fridge scan,
      ▲                              │                                            match review, leftover ideas
      │ WebSocket /ws                ▼
      │                        RunManager (app/runs.py)
      │                              │ creates
      │                              ▼
      └──── events ◀──────── Run (app/agent/agent.py): a pool of browser pages pulls (store, ingredient) jobs
            frames, log,            │  every page is a SafePage ── safety guard (app/agent/safety.py)
            results, report         ▼
                              StoreAdapter ── mock stores (mock_stores/)
```

| Part | File | Notes |
|---|---|---|
| Schemas | `app/models.py` | Every AI reply is validated with Pydantic. |
| AI calls | `app/ai.py` | Gemini JSON mode, retries, friendly errors, demo mode. |
| Units | `app/units.py` | Pack-size parsing, recipe unit conversion, price per unit, cheapest-pack selection. |
| Matching | `app/matching.py` | Rule-based product matching, confidence flags, ranking. |
| Report | `app/report.py` | Merges stores' options, totals, applies the AI review. |
| Agent | `app/agent/` | Orchestrator, `SafePage`, safety guard, `StoreAdapter` interface, mock-store adapter. |
| Mock shops | `mock_stores/` | 3 fake grocery sites with different layouts. |
| UI | `static/` | Plain HTML/CSS/JS, no build step. |

---

## 3. Key Features

* **Recipe → shopping list**, scaled to servings, with generic searchable names (no brands).
* **"How to make it" cooking steps:** 5–10 numbered, beginner-friendly steps with prep and cook times, written by the same AI request as the ingredient list (no extra API call). Steps quote the same scaled quantities as the shopping list ("Beat 175 g butter with 200 g caster sugar"). Shown as a collapsible card on both the ingredient screen and the results page, and hidden if a recipe has no steps.
* **Fridge-photo scan** that only reports on the recipe's ingredients, with per-item confidence.
* **Parallel browser agents** (Playwright) across multiple stores, `MAX_PARALLEL` pages at a time, with retries and per-store failure handling.
* **Live view:** labelled screenshot grid (~2 fps per tile), live activity log, progress bar, running total per store, and a **Stop** button.
* **Confidence flags with reasons:** *Please check* / *Please confirm* with a short reason ("Size unclear", "Might be a different product"). Low-confidence picks must be confirmed or swapped.
* **Price per unit comparison:** sizes such as `1kg`, `500 g`, `6 x 50ml`, `12's`, `1.5L` are parsed to g / ml / pieces and priced per 100 g / 100 ml / piece.
* **Smart quantities:** recipe amounts are converted (1 cup flour ≈ 120 g, 1 tbsp butter ≈ 14 g, 1 egg = 1 piece). CartPilot picks the cheapest way to *cover the need*: "Need 200g → 1 x 500g pack", or several smaller packs when that's cheaper.
* **Results page:** one pick per ingredient, 1–2 alternatives, totals per store, "Open all product pages".
* **Leftover-aware suggestions:** leftovers over 30% of a pack are listed; the AI suggests simple recipes that use them, with a one-tap new search that treats them as owned.
* **Demo mode:** every AI feature has an offline fallback, so the demo works with no API key and no internet.
* **Friendly errors:** bad image, API failure, rate limit, busy service, or item not found (with a suggested substitute).
* **Three mock grocery sites** (FreshMart, ValueGrocer, QuickBasket) with different brands, sizes, prices, stock, and deliberate look-alike products for testing.

---

## 4. AI Integration

AI is used in **five places**, all through Google Gemini (default model `gemini-3.5-flash-lite`, configurable) using the `google-genai` SDK in **JSON mode**. Every reply is validated against a Pydantic model; an invalid reply is retried, then reported as a friendly error instead of crashing.

| # | Where | What the AI does | Input |
|---|---|---|---|
| 1 | Recipe options | Suggests 2–3 realistic recipes for the goal and servings | Text |
| 2 | Ingredient list + method | Writes a scaled ingredient list with generic names, quantities and units, and in the same reply 5–10 cooking steps with prep and cook times | Text |
| 3 | Fridge scan (**vision**) | Says which of the *recipe's* ingredients are visible in the photo, with a high/medium/low confidence | Image (resized to ~1000 px wide) + the ingredient names |
| 4 | Match review | Double-checks the chosen products ("is this really the same thing, in the right form?"). It can only **lower** confidence, never raise it | Ingredient + product name pairs |
| 5 | Leftover ideas | Suggests 2–3 simple recipes that use the biggest leftovers (never the recipe just shopped for) | Leftover names and amounts |

**Deliberately *not* done by AI** (so it is predictable and testable):
* **The browser "agents" are scripted automation, not an LLM driving a browser.** Which pages they may visit and what they may click is decided by fixed code, so a model can never talk them into buying something.
* **Unit parsing, price-per-unit maths, pack selection and leftover maths** are plain, unit-tested code.
* **Matching** is primarily rule-based; the AI review is a second opinion on top.

**Demo mode** (`DEMO_MODE=true`) replaces all five AI calls with canned, deterministic answers, so the whole flow runs offline. The rule-based matching, safety guard, browser agents and maths still run for real.

---

## 5. External Tools & Datasets

**AI / APIs**
* **Google Gemini API** (via the `google-genai` Python SDK): text and vision, free tier. Default model `gemini-3.5-flash-lite`.

**Libraries and tools**

| Tool | Used for |
|---|---|
| [FastAPI](https://fastapi.tiangolo.com/) + Uvicorn | Backend, HTTP API, WebSocket for live updates |
| [Playwright](https://playwright.dev/python/) (Chromium) | Browser automation and live screenshots |
| [Pydantic](https://docs.pydantic.dev/) | Validating every AI response and API request |
| [Pillow](https://python-pillow.org/) | Resizing uploaded photos, fixing rotation, stripping metadata |
| [Jinja2](https://jinja.palletsprojects.com/) | Rendering the mock grocery websites |
| python-dotenv | Reading settings and the API key from `.env` |
| pytest, pytest-asyncio, httpx, websockets | Tests |
| Plain HTML / CSS / JavaScript | Front end (no framework, no build step) |

**Datasets:** none external.
* The three mock grocery sites use a **hand-written synthetic catalogue** (41 items, 148 listings) with fictional brands and made-up prices. Any resemblance to real products or prices is coincidental.
* The cooking conversion table (cup → grams, typical weight of an onion or egg, and so on) is hand-written home-cooking approximations, not an official dataset.

---

## 6. Installation & Setup

**Requirements:** Python 3.12+ and `pip`. An AI key is optional, because demo mode needs none.

```bash
# 1. get the code and enter the project folder
git clone https://github.com/smubia-projects/ail-ay2627-s1-junhan-cartpilot cartpilot
cd cartpilot

# 2. create a virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

# 3. create your settings file (set DEMO_MODE=true to run without a key)
cp .env.example .env                 # Windows: copy .env.example .env

# 4. start the API
uvicorn app.main:app --reload --port 8000

# 5. in a second terminal, build and serve the UI
node build.js                        # writes static/config.js -> http://localhost:8000
python -m http.server 5173 -d static
```

Open **<http://localhost:5173>**. The badge at the top says "Demo mode" until you turn on real AI.

**To use real AI:** set `API_KEY` (any OpenAI-compatible gateway; set `API_BASE_URL` to match) and `DEMO_MODE=false`,
and restart. **Never put your key in `.env.example` or commit it.** (`.env` is git-ignored.)

### Settings (`.env`)

| Variable | Default | What it does |
|---|---|---|
| `API_BASE_URL` | `https://api.apiyi.com/v1` | OpenAI-compatible chat-completions gateway. |
| `API_KEY` | – | Your key (old name `GEMINI_API_KEY` still works). Not needed in demo mode. |
| `MODEL_NAME` | `deepseek-v4-1-flash-260910` | Text model (old name `GEMINI_MODEL` still works). |
| `VISION_MODEL_NAME` | `deepseek-v4-1-flash-260910` | Model for the fridge-photo scan (needs vision). |
| `DEMO_MODE` | `false` | `true` = canned AI answers, works offline (local development). |
| `STORE_MODE` | `mock` | Only `mock` (the three built-in fake stores) is supported. |
| `MAX_PARALLEL` | `4` | How many agents work at once in one run. |
| `CORS_ORIGINS` | `http://localhost:5173` | Browser origins allowed to call the API (the Vercel URL in production). |
| `RATE_LIMIT_*` | see `.env.example` | AI Lodge rate-limit fallbacks (the central control sheet overrides them). |

### Run the tests

```bash
python -m pytest -q                        # everything, a few seconds (no browser, canned AI)
python -m pytest tests/test_safety.py -q   # just the safety guard (instant)
```

### Troubleshooting

* **"Model … was not found"** → change `MODEL_NAME` in `.env`.
* **"AI service is busy"** → provider hiccup. Wait a few seconds, or set `DEMO_MODE=true`.
* **"Can't reach the server."** in the UI → the API isn't running, or `static/config.js` points elsewhere (`node build.js`).
* **Anything else** → the real error is printed in the terminal running `uvicorn`.

---

## 7. AI Safety & Limitations

### Risks and the guardrails against them

| Risk | Guardrail |
|---|---|
| **The agent buys something, logs in, or wanders off.** The most serious risk of any browser automation | A safety guard (`app/agent/safety.py`) is enforced **in code, not in prompts**. It refuses any click whose text, aria-label, CSS class, link target or enclosing button looks like *add to cart / buy / checkout / pay / place order / login / sign up / account*. It allows typing only in search boxes, and visiting only the stores' own hosts (never `/cart`, `/checkout`, `/login`, `?add-to-cart=`, `javascript:`). Agents only ever receive a `SafePage` wrapper, never a raw browser page. A second, network-level check aborts any forbidden navigation, such as a redirect to a login page. The agent is also scripted rather than LLM-driven, so there is no prompt to manipulate |
| **Automation starting without consent** | It cannot start without the user's explicit Approve. `Run.start()` raises without `approved=True`, and the API returns 400 |
| **Runaway automation** | **Stop** button: sets a flag every page obeys, cancels all tasks and closes the browsers. Every agent action is logged and streamed to the UI |
| **AI misreads the fridge photo** | Only recipe ingredients are checked. Each answer has a confidence. Only *high*-confidence items are pre-ticked; the rest need a human tick. The AI's list is forced to match the recipe list exactly |
| **Wrong product** (look-alike, wrong form, wrong size) | Rule-based checks flag different forms ("drink", "biscuit", "coconut"…), different ingredients, unparseable sizes and unit mismatches. An optional AI review can only *lower* confidence. **Low-confidence picks must be confirmed or swapped** before "Open all product pages" unlocks, and each flag shows a short reason |
| **Malformed or malicious AI output** | JSON mode plus Pydantic validation, with retries. UI text is inserted with `textContent` (never `innerHTML`), and product links are only opened if they are `http(s)` |
| **Prompt injection via text in a photo** | The fridge prompt tells the model to treat text in the image as content, not instructions. In any case the AI cannot control the browser |
| **Privacy of uploaded photos** | Photos are resized, re-encoded and stripped of metadata (such as GPS) before leaving the server. The app never saves them to disk. They *are* sent to Google's Gemini API in live mode. Demo mode sends nothing |
| **API key leakage** | The key is read only from `.env` (git-ignored) and never returned by any endpoint. `/api/health` reports only whether a key exists |
| **Misusing real websites** | No real store is wired in, on purpose (see below). Any future real adapter is built to stop on a CAPTCHA, a block or an unexpected page, with small random delays and at most 1–2 pages per store. No stealth plugins and no CAPTCHA solving |
| **Free-tier outages** | Retries, friendly messages, and a one-switch offline demo mode |

### Why there is no real grocery store

The original plan included one real Singapore grocer. We checked FairPrice's public `robots.txt` first. It disallows `/search` for automated visitors (as well as `/cart`, `/checkout` and `/login`). Searching is exactly what an agent does, so **we deliberately did not build a real-store adapter** and respect the site's stated rules. The `StoreAdapter` interface, the safety guard, Stop, logging and live view are already in place for any store that does permit it. Check a site's `robots.txt` and terms first, and keep the volume low.

### Testing done

**423 automated tests** (21 skipped by design, where a store doesn't sell an item), including:

* **Safety guard** (`test_safety.py`, `test_agent.py`): 100+ cases for forbidden wording, hidden labels (class-only, aria-only, inner span of a bad button), bad links, other hosts and unsafe URL schemes, plus the things that must stay *allowed*. Browser tests against real Chromium prove the "Add to cart" button on every mock product page is never clicked and that `/cart` and other sites are blocked. **We also verified the tests are real**: with the guard deliberately disabled, they fail.
* **Approval and Stop:** no run without approval; Stop halts everything and nothing keeps running afterwards; a store showing a CAPTCHA is abandoned while the others carry on.
* **Matching** (`test_matching.py`): look-alike traps, and a test over **every catalogue item in every store**, proving the pick is never a decoy.
* **Units and maths** (`test_units.py`): dozens of messy size formats, conversions, price per unit, pack choice, leftovers.
* **AI layer** (offline): schema validation, retries, error handling, fridge image preparation (bad files, size limits). A test guards against schema keywords the Gemini SDK rejects. Cooking steps: demo recipes return steps whose quantities scale with servings, the schema accepts a reply with or without steps, live mode gets steps from the same single call, and step numbering added by the model is stripped.
* **End to end:** approve → live WebSocket events → report (`test_runs.py`), and the real web page clicked through like a person (`test_ui.py`).
* **Live Gemini checks (manual):** recipes, ingredients with cooking steps, fridge scan, match review and leftover ideas were run against the real API, along with a full run.

### Limitations

* **Mock stores only.** Prices and products are synthetic, so CartPilot demonstrates the approach, not live prices from real shops.
* **Heuristic matching.** The "different form" word list and the synonym table are hand-written and English-only; unusual products can slip through. That's why anything not high-confidence needs a human's confirmation.
* **No allergen or dietary checking.** CartPilot does not read ingredient labels, and must not be relied on for allergies or dietary requirements.
* **The AI can be wrong.** It can misjudge a fridge photo (hidden, blurry or look-alike items), suggest odd recipes, or mis-scale quantities. The cooking steps are AI-written too: oven temperatures and timings are a guide, not a tested recipe, and there is no food-safety or allergen checking. In demo mode the steps are hand-written samples for the six built-in recipes. We tested the fridge scan with a simple synthetic image and a few demos, **not** with a large set of real fridge photos.
* **Rough approximations:** the cup → gram table, typical piece weights (an onion ≈ 150 g), assuming 1 g ≈ 1 ml when no density is known (flagged as "Estimated"), and the 30% leftover threshold.
* **Free-tier dependence:** live AI can be slow, busy or rate-limited. Use demo mode as the fallback.
* **Scope:** one search at a time, prices in S$, one language (English), no accounts, no saved history.
* **No accessibility audit** has been done on the web page.
