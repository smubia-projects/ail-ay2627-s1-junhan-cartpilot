// CartPilot front end: plain JavaScript. The only build step writes config.js (the API's URL, see build.js).
// Flow: goal -> recipes -> ingredients -> fridge scan -> checklist -> [Approve] -> live search -> results -> leftovers.

// ---------------------------------------------------------------- state + helpers
const state = {
  goal: "", servings: 6, recipe: null,
  ingredients: [],            // [{name, quantity, unit}] for the chosen recipe
  method: null,               // {title, servings, prep_time, cook_time, steps} for the chosen recipe
  have: {},                   // name -> true if the user owns it
  flags: {},                  // name -> {confidence, note} from the fridge scan
  // live search
  abort: null, live: false, storeNames: {}, tiles: {}, storeTotals: {}, storeDone: {}, storeLow: {}, jobs: 0, done: 0,
  // results
  report: null, pick: {}, confirmed: {}, leftoverNames: [],
};
const $ = id => document.getElementById(id);

// Build DOM with textContent, never innerHTML, so text from the AI or from shops can't inject HTML.
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined && text !== null) e.textContent = text; return e; }
const money = n => "S$" + Number(n).toFixed(2);
function safeUrl(u) { try { const x = new URL(u); return (x.protocol === "http:" || x.protocol === "https:") ? x.href : "#"; } catch { return "#"; } }
function showError(msg) { $("error").textContent = msg || ""; $("error").hidden = !msg; }
function show(id, on = true) { $(id).hidden = !on; updateSteps(); }

// Progress bar at the top: the furthest visible section is the current step, earlier ones are done.
const STEP_SECTIONS = ["step-goal", "step-recipes", "step-ingredients", "step-checklist", "step-live", "step-results"];
function updateSteps() {
  let current = 0;
  STEP_SECTIONS.forEach((id, i) => { if ($(id) && !$(id).hidden) current = i; });
  if (!$("step-results").hidden || !$("step-leftovers").hidden) current = 5;
  document.querySelectorAll("#steps li").forEach(li => {
    const i = +li.dataset.step;
    li.classList.toggle("done", i < current);
    li.classList.toggle("active", i === current);
    if (i === current) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
  });
}
updateSteps();
function scrollTo_(id) { $(id).scrollIntoView({ behavior: "smooth", block: "start" }); }

// "How to make it": a collapsible card with the recipe name, servings, times and numbered steps.
// Shown on the ingredient screen and on the results page; hidden if the AI sent no steps.
function renderMethod(boxId) {
  const box = $(boxId), m = state.method;
  box.replaceChildren();
  if (!m || !m.steps.length) return;
  const details = el("details", "method");
  details.append(el("summary", "", "👩‍🍳 How to make it"));
  details.append(el("h4", "", `${m.title} · ${m.servings} servings`));
  const times = [m.prep_time && `Prep ${m.prep_time}`, m.cook_time && `Cook ${m.cook_time}`].filter(Boolean);
  if (times.length) details.append(el("p", "muted", times.join(" · ")));
  const ol = el("ol", "steps-list");
  for (const step of m.steps) ol.append(el("li", "", step));
  details.append(ol);
  box.append(details);
}

// The API runs on its own server (Cloud Run); config.js sets its address at build time.
const API = (window.CARTPILOT_API_BASE || "http://localhost:8000").replace(/\/+$/, "");

// AI Lodge limit: 429 = this visitor reached the demo's limit (CTA), 503 = the demo is paused.
// The AI step stops there; there is no substitute answer.
class LimitError extends Error {}
async function checkLimit(r) {
  if (r.status !== 429 && r.status !== 503) return;
  const data = await r.json().catch(() => ({}));
  const d = data.detail || {};
  if (r.status === 429) showRateLimitCTA(d.queries_used || "a few");
  else showDemoPaused(d.message);
  throw new LimitError(r.status === 429 ? "You've reached this demo's AI limit." : "The demo is paused right now.");
}

async function post(url, body) {
  const r = await fetch(API + url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  await checkLimit(r);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Something went wrong. Please check your input.");
  return data;
}

fetch(API + "/api/health").then(r => r.json()).then(h => {
  const b = $("mode-badge");
  b.textContent = h.demo_mode ? "Demo mode · sample AI answers, works offline" : `Live AI · ${h.model}`;
  b.className = "badge " + (h.demo_mode ? "warn" : "ok"); b.hidden = false;
}).catch(() => showError("Can't reach the server."));

// ---------------------------------------------------------------- 1-2. goal -> recipes
$("goal-form").addEventListener("submit", async e => {
  e.preventDefault(); showError("");
  state.goal = $("goal").value.trim(); state.servings = +$("servings").value;
  resetFrom("recipes");
  try {
    const { options } = await post("/api/recipes", { goal: state.goal, servings: state.servings });
    const list = $("recipe-list"); list.replaceChildren();
    for (const o of options) {
      const card = el("div", "recipe");
      card.append(el("strong", "", o.title), el("div", "", o.description), el("small", "", `${o.time_minutes} min · ${o.difficulty}`));
      card.onclick = () => pickRecipe(o);
      list.append(card);
    }
    show("step-recipes");
  } catch (err) { showError(err.message); }
});

// Example chips fill the goal box and go straight to the recipe suggestions.
document.querySelectorAll(".chip").forEach(chip => chip.addEventListener("click", () => {
  $("goal").value = chip.dataset.goal;
  $("goal-form").requestSubmit();
}));

// Hide everything after a given step so stale results never linger.
function resetFrom(step) {
  const order = ["recipes", "ingredients", "checklist", "live", "results", "leftovers"];
  for (const s of order.slice(order.indexOf(step))) show("step-" + s, false);
}

// ---------------------------------------------------------------- 3. ingredients
async function pickRecipe(recipe, owned = []) {
  showError(""); state.recipe = recipe;
  try {
    const data = await post("/api/ingredients", { goal: state.goal, recipe_title: recipe.title, servings: state.servings, owned });
    state.ingredients = data.ingredients;
    resetFrom("ingredients");
    $("ing-title").textContent = `${recipe.title} · ${state.servings} servings`;
    const ul = $("ing-list"); ul.replaceChildren();
    for (const i of data.ingredients) ul.append(el("li", "", `${i.name} — ${i.quantity} ${i.unit}`));
    state.method = { title: recipe.title, servings: state.servings, prep_time: data.prep_time || "", cook_time: data.cook_time || "",
                     steps: data.steps || [] };
    renderMethod("method-ingredients");
    show("step-ingredients");
    return data;
  } catch (err) { showError(err.message); return null; }
}

$("photo").addEventListener("change", () => {
  const f = $("photo").files[0];
  $("scan-btn").disabled = !f;
  if (f) { $("preview").src = URL.createObjectURL(f); $("preview").hidden = false; }
});

$("scan-btn").addEventListener("click", async () => {
  showError("");
  const f = $("photo").files[0]; if (!f) return;
  const form = new FormData();
  form.append("image", f);
  form.append("ingredients", JSON.stringify(state.ingredients.map(i => i.name)));
  $("scan-btn").disabled = true;
  $("scan-status").textContent = "Scanning your fridge…"; show("scan-status");
  try {
    const r = await fetch(API + "/api/fridge-scan", { method: "POST", body: form });
    await checkLimit(r);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Scan failed. Please try another photo.");
    state.have = {}; state.flags = {};
    for (const it of data.items) {
      // Only HIGH-confidence "have" answers are pre-ticked; the rest need the user's say-so.
      state.have[it.name] = it.have && it.confidence === "high";
      state.flags[it.name] = { confidence: it.confidence, note: it.note, aiHave: it.have };
    }
    $("owned-note").hidden = true;
    renderChecklist();
  } catch (err) { showError(err.message); }
  show("scan-status", false); $("scan-btn").disabled = false;
});

$("skip-btn").addEventListener("click", () => { state.have = {}; state.flags = {}; $("owned-note").hidden = true; renderChecklist(); });

// ---------------------------------------------------------------- 4. checklist
function renderChecklist() {
  for (const [listId, wantHave] of [["have-list", true], ["need-list", false]]) {
    const ul = $(listId); ul.replaceChildren();
    for (const ing of state.ingredients.filter(i => !!state.have[i.name] === wantHave)) {
      const li = el("li"), box = document.createElement("input");
      box.type = "checkbox"; box.checked = wantHave;  // ticked = "I have it"
      box.setAttribute("aria-label", `I have ${ing.name}`);
      box.onchange = () => { state.have[ing.name] = box.checked; renderChecklist(); };
      const text = el("div");
      const label = el("span", "", ing.name); label.append(el("span", "qty", ` — ${ing.quantity} ${ing.unit}`));
      text.append(label);
      const flag = state.flags[ing.name];
      if (flag && flag.confidence !== "high") {
        text.append(" ", el("span", "badge warn", "Please check"));
        if (flag.note) text.append(el("small", "", flag.note));
      }
      li.append(box, text); ul.append(li);
    }
  }
  $("have-count").textContent = $("have-list").children.length;
  $("need-count").textContent = $("need-list").children.length;
  show("step-checklist");
}

// ---------------------------------------------------------------- Approve -> start the agents
$("approve-btn").addEventListener("click", async () => {
  showError("");
  const items = state.ingredients.filter(i => !state.have[i.name]).map(i => ({ name: i.name, quantity: i.quantity, unit: i.unit }));
  if (!items.length) { showError("You already have everything on the list. Nothing to search for!"); return; }
  $("approve-btn").disabled = true;
  state.items = items;
  resetLive(); show("step-results", false); show("step-leftovers", false);
  show("step-live"); $("stop-btn").disabled = false; $("stop-btn").textContent = "⏹ Stop";
  setStatus("Starting the agents…");
  scrollTo_("step-live");
  const ctrl = new AbortController(); state.abort = ctrl;
  try {
    // `approved: true` is only ever sent from this button. The server refuses to start without it.
    // The run lives inside this one streaming request (Server-Sent Events): closing it stops the agents.
    const r = await fetch(API + "/api/runs/stream", { method: "POST", signal: ctrl.signal,
      headers: { "Content-Type": "application/json" }, body: JSON.stringify({ items, approved: true }) });
    await checkLimit(r);
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      throw new Error(typeof data.detail === "string" ? data.detail : "The search couldn't start.");
    }
    await readEvents(r.body, handle);
    if (state.live) endLive("The connection to the search was lost. Please try again.", true);
  } catch (err) {
    if (err.name === "AbortError") endLive("Stopped. No more actions will be taken.");
    else if (err instanceof LimitError) { endLive(err.message); show("step-live", false); }
    else endLive(err.message, true);
  }
});

// Read a text/event-stream body and call `fn` with each `data:` JSON event.
async function readEvents(body, fn) {
  const reader = body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += value;
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
      for (const line of chunk.split("\n")) if (line.startsWith("data: ")) fn(JSON.parse(line.slice(6)));
    }
  }
}

// ---------------------------------------------------------------- 5. live search
function resetLive() {
  Object.assign(state, { tiles: {}, storeNames: {}, storeTotals: {}, storeDone: {}, storeLow: {}, jobs: 0, done: 0, live: true, report: null });
  $("grid").replaceChildren(); $("log").replaceChildren(); $("store-cards").replaceChildren();
  $("progress-bar").style.width = "0";
}
function setStatus(t) { $("live-status").textContent = t; }

function handle(e) {
  switch (e.type) {
    case "worker": ensureTile(e.worker); setLabel(e.worker, e.store, e.label); break;
    case "view": setView(e.worker, e.html); break;
    case "log": addLog(e); break;
    case "result": applyResult(e.result); break;
    case "run_started": initStores(e.stores, e.items.length); break;
    case "run_finished": setStatus("Search finished. Checking the matches…"); break;
    case "run_stopped": endLive("Stopped. No more actions will be taken."); break;
    case "run_failed": endLive(e.message, true); break;
    case "report": endLive("Done! Here's what I found."); renderReport(e.report); break;
  }
}

function initStores(stores, nItems) {
  state.storeNames = stores; state.jobs = nItems * Object.keys(stores).length;
  const box = $("store-cards"); box.replaceChildren();
  for (const [slug, name] of Object.entries(stores)) {
    state.storeTotals[slug] = state.storeTotals[slug] || 0; state.storeDone[slug] = state.storeDone[slug] || 0; state.storeLow[slug] = state.storeLow[slug] || 0;
    const c = el("div", "store-card " + slug); c.id = "card-" + slug;
    c.append(el("div", "sname", name), el("div", "stotal", money(0)), el("div", "sprog", ""));
    box.append(c);
    updateCard(slug);
  }
  setStatus("Searching…");
}

function updateCard(slug) {
  const c = $("card-" + slug); if (!c) return;
  c.querySelector(".stotal").textContent = money(state.storeTotals[slug] || 0);
  const per = state.items ? state.items.length : "?";
  const low = state.storeLow[slug] ? ` · ${state.storeLow[slug]} to confirm` : "";
  c.querySelector(".sprog").textContent = `${state.storeDone[slug] || 0}/${per} items${low}`;
}

function ensureTile(w) {
  if (state.tiles[w]) return state.tiles[w];
  const tile = el("div", "tile"), label = el("div", "label", "Starting…"), screen = el("div", "screen");
  // The store page the agent is reading, sandboxed (no scripts, no clicks) and scaled down to the tile.
  const frame = document.createElement("iframe");
  frame.title = `Agent ${w + 1} view`; frame.setAttribute("sandbox", ""); frame.tabIndex = -1;
  screen.append(frame); tile.append(label, screen); $("grid").append(tile);
  const fit = () => { frame.style.transform = `scale(${screen.clientWidth / 900})`; };
  new ResizeObserver(fit).observe(screen); fit();
  return (state.tiles[w] = { label, frame });
}
function setLabel(w, store, text) { ensureTile(w).label.textContent = store ? `${store}: ${text}` : text; }
function setView(w, html) { ensureTile(w).frame.srcdoc = html; }

function addLog(e) {
  const log = $("log"), t = new Date((e.ts || Date.now() / 1000) * 1000).toLocaleTimeString();
  const row = el("div", e.status === "ok" ? "" : e.status);
  row.append(el("span", "ts", t + " "), document.createTextNode(`${e.store ? e.store + " · " : ""}${e.action} ${e.detail || ""}`));
  const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;  // only auto-scroll if the user is at the bottom
  log.append(row);
  while (log.children.length > 500) log.firstChild.remove();
  if (stick) log.scrollTop = log.scrollHeight;
}

function applyResult(r) {
  state.storeDone[r.store] = (state.storeDone[r.store] || 0) + 1;
  state.done++;
  const best = r.candidates && r.candidates[0];
  if (r.status === "found" && best) {
    state.storeTotals[r.store] = (state.storeTotals[r.store] || 0) + best.total_cost;
    if (best.confidence === "low") state.storeLow[r.store] = (state.storeLow[r.store] || 0) + 1;
  }
  updateCard(r.store);
  $("progress-bar").style.width = Math.min(100, (100 * state.done) / Math.max(1, state.jobs)) + "%";
}

function endLive(text, isError) {
  state.live = false; setStatus(text);
  $("stop-btn").disabled = true; $("approve-btn").disabled = false;
  if (isError) showError(text);
  state.abort = null;
  for (const t of Object.values(state.tiles)) if (/…$|^Ready/.test(t.label.textContent)) t.label.textContent = t.label.textContent.replace(/…$/, "") + (text.startsWith("Stopped") ? " (stopped)" : "");
}

$("stop-btn").addEventListener("click", async () => {
  $("stop-btn").disabled = true; $("stop-btn").textContent = "Stopping…";
  if (state.abort) state.abort.abort();  // closing the stream stops this visitor's agents on the server
});

// ---------------------------------------------------------------- 6. results
function renderReport(report) {
  const fresh = state.report === null || JSON.stringify(state.report) !== JSON.stringify(report);
  state.report = report;
  if (fresh) { state.pick = {}; state.confirmed = {}; }
  show("step-results");
  renderMethod("method-results");
  renderResults();
  if (fresh) { $("ideas").replaceChildren(); $("ideas-btn").disabled = false; }
}

const pickOf = item => item.options[state.pick[item.ingredient] || 0];
const needsConfirm = item => item.options.length && pickOf(item).confidence === "low" && !state.confirmed[item.ingredient];

function renderResults() {
  const box = $("result-items"); box.replaceChildren();
  for (const item of state.report.items) {
    const card = el("div", "item");
    const head = el("h4", "", item.ingredient); head.append(el("small", "", `need ${item.quantity} ${item.unit}`));
    card.append(head);
    if (!item.options.length) {
      card.append(el("div", "notfound", item.message || "Not found in any store."));
      if (item.substitute) card.append(el("div", "muted", `💡 Try instead: ${item.substitute}`));
      box.append(card); continue;
    }
    const idx = state.pick[item.ingredient] || 0, p = item.options[idx];
    card.classList.add(p.confidence === "high" ? "high" : p.confidence);
    if (idx === 0) head.append(el("span", "badge ok best-pick", "★ Best pick"));
    const pick = el("div", "pick");
    const name = el("div", "name");
    const a = el("a", "", p.name); a.href = safeUrl(p.url); a.target = "_blank"; a.rel = "noopener noreferrer";
    name.append(el("span", "badge store " + p.store, state.report.stores[p.store] || p.store), " ", a, el("span", "muted", `  ${p.size}`));
    pick.append(name, el("div", "price", money(p.total_cost)));
    pick.append(el("div", "meta", `${p.need_text || ""}${p.unit_price_label ? " · " + p.unit_price_label : ""}`),
                el("div", "meta", p.pack_count > 1 ? `${p.pack_count} × ${money(p.price)}` : money(p.price)));
    card.append(pick);

    if (p.confidence !== "high") {  // flags with a short reason; low-confidence picks must be confirmed or swapped
      const f = el("div", "flag " + p.confidence);
      f.append(el("span", `badge ${p.confidence === "low" ? "bad" : "warn"}`, p.confidence === "low" ? "Please confirm" : "Please check"), " ", document.createTextNode(p.reasons.join(" · ")));
      if (p.confidence === "low") {
        const lab = el("label"), box_ = document.createElement("input");
        box_.type = "checkbox"; box_.checked = !!state.confirmed[item.ingredient];
        box_.onchange = () => { state.confirmed[item.ingredient] = box_.checked; refreshTotals(); };
        lab.append(" ", box_, " I've checked – this is right");
        f.append(el("br"), lab);
      }
      card.append(f);
    }

    const others = item.options.map((o, i) => [o, i]).filter(([, i]) => i !== idx);
    if (others.length) {
      const alts = el("div", "alts"); alts.append(el("span", "muted", "Alternatives: "));
      others.slice(0, 2).forEach(([o, i]) => alts.append(swapButton(item, o, i)));
      if (others.length > 2) {
        const more = document.createElement("details"); more.append(el("summary", "muted", `${others.length - 2} more option(s)`));
        others.slice(2).forEach(([o, i]) => more.append(swapButton(item, o, i)));
        alts.append(more);
      }
      card.append(alts);
    }
    box.append(card);
  }
  refreshTotals();
}

function swapButton(item, o, i) {
  const b = el("button", "secondary small", `${state.report.stores[o.store] || o.store} · ${o.name} ${o.size} · ${money(o.total_cost)}${o.confidence !== "high" ? " ⚠" : ""}`);
  b.title = o.reasons.join(" · ") || "Swap to this product";
  b.onclick = () => { state.pick[item.ingredient] = i; delete state.confirmed[item.ingredient]; renderResults(); };
  return b;
}

// Totals, the "open all" button and the leftovers list all depend on the CURRENT picks, so they refresh together.
function refreshTotals() {
  const r = state.report; if (!r) return;
  const nItems = r.items.length;
  const cheapest = Math.min(...r.store_totals.filter(t => t.found === nItems).map(t => t.total), Infinity);
  const tbl = el("table", "totals");
  const head = el("tr"); ["Store", "Total", "Items found", "Missing"].forEach(h => head.append(el("th", "", h))); tbl.append(head);
  for (const t of r.store_totals) {
    const tr = el("tr", t.found === nItems && t.total === cheapest ? "best" : "");
    tr.append(el("td", "", t.name + (t.found === nItems && t.total === cheapest ? "  ⭐ cheapest full basket" : "")),
              el("td", "num", money(t.total)), el("td", "", `${t.found}/${nItems}` + (t.to_confirm ? ` (${t.to_confirm} to confirm)` : "")),
              el("td", "muted", t.missing.join(", ") || "–"));
    tbl.append(tr);
  }
  $("store-totals").replaceChildren(tbl);

  const picks = r.items.filter(i => i.options.length);
  const total = picks.reduce((s, i) => s + pickOf(i).total_cost, 0);
  $("basket-total").textContent = money(total);
  const missing = r.items.length - picks.length, unconfirmed = picks.filter(needsConfirm).length;
  $("basket-note").textContent = `(best mix across stores${missing ? `, ${missing} item(s) not found` : ""})`;
  // How much the mixed basket beats buying everything in one store (only when some store stocks the whole list).
  const fullTotals = r.store_totals.filter(t => t.found === nItems).map(t => t.total), sv = $("savings");
  const vsCheapest = Math.min(...fullTotals) - total, vsPriciest = Math.max(...fullTotals) - total;
  const parts = [];
  if (fullTotals.length && !missing) {
    if (vsCheapest > 0.005) parts.push(`${money(vsCheapest)} vs the cheapest single store`);
    if (fullTotals.length > 1 && vsPriciest > 0.005) parts.push(`${money(vsPriciest)} vs the priciest`);
  }
  sv.textContent = parts.length ? "💰 Saves " + parts.join(", ") : ""; sv.hidden = !parts.length;
  const btn = $("open-all"); btn.disabled = !picks.length || unconfirmed > 0;
  $("open-all-note").classList.remove("error");
  $("open-all-note").textContent = unconfirmed ? `Confirm or swap the ${unconfirmed} item(s) marked “Please confirm” first.` : "Opens each product page in a new tab. You choose what to buy there.";
  const links = $("open-links"); links.replaceChildren();
  for (const i of picks) {
    const li = el("li"), a = el("a", "", `${i.ingredient}: ${pickOf(i).name}`);
    a.href = safeUrl(pickOf(i).url); a.target = "_blank"; a.rel = "noopener noreferrer"; li.append(a); links.append(li);
  }
  renderLeftovers();
}

$("open-all").addEventListener("click", () => {
  // Browsers usually let a click open only one tab and block the rest as pop-ups. (No "noopener" feature here:
  // it makes window.open always return null, so we couldn't tell which tabs were blocked. We clear opener instead.)
  const urls = state.report.items.filter(i => i.options.length).map(i => safeUrl(pickOf(i).url));
  let opened = 0;
  for (const url of urls) {
    const w = window.open(url, "_blank");
    if (!w) continue;
    opened++;
    try { w.opener = null; } catch { /* cross-origin window: already isolated */ }
  }
  if (opened < urls.length) {
    $("open-all-note").textContent = `Your browser blocked ${urls.length - opened} of ${urls.length} tabs. Click the pop-up blocked icon in the address bar and choose “Always allow”, then press the button again, or use the links below.`;
    $("open-all-note").classList.add("error");
  }
});

// ---------------------------------------------------------------- 7. leftovers
function currentLeftovers() {
  return state.report.items.filter(i => i.options.length).map(i => ({ item: i, p: pickOf(i) }))
    .filter(({ p }) => p.significant_leftover && p.leftover_amount > 0)
    .sort((a, b) => b.p.leftover_pct - a.p.leftover_pct);
}

function renderLeftovers() {
  const lo = currentLeftovers(), ul = $("leftover-list"); ul.replaceChildren();
  for (const { item, p } of lo) ul.append(el("li", "", `${item.ingredient}: ${p.leftover_text} (${Math.round(p.leftover_pct * 100)}% of what you buy)`));
  show("step-leftovers", lo.length > 0);
}

// Seasonings and flavourings are always left over in bulk but rarely inspire a recipe, so they are not sent
// to the idea generator (they still show in the leftovers list above).
const SEASONINGS = new Set(["salt", "baking powder", "baking soda", "vanilla extract", "cinnamon", "yeast"]);

// Asked only when the visitor clicks (each suggestion is a model call).
$("ideas-btn").addEventListener("click", loadIdeas);

async function loadIdeas() {
  const all = currentLeftovers(); $("ideas").replaceChildren();
  if (!all.length) return;
  const main = all.filter(({ item }) => !SEASONINGS.has(item.ingredient));
  const lo = main.length ? main : all;
  state.leftoverNames = all.map(({ item }) => item.ingredient);
  const box = $("ideas"); box.append(el("p", "muted", "Thinking of ideas…")); $("ideas-btn").disabled = true;
  try {
    const { ideas } = await post("/api/leftover-ideas", {
      leftovers: lo.slice(0, 8).map(({ item, p }) => ({ name: item.ingredient, amount: p.leftover_amount, unit: p.leftover_unit })),
      servings: state.servings,
      exclude: [state.recipe ? state.recipe.title : "", state.goal].filter(Boolean),  // don't suggest what they just shopped for
    });
    box.replaceChildren();
    for (const idea of ideas) {
      const card = el("div", "idea"), text = el("div");
      text.append(el("strong", "", idea.title), el("div", "", idea.description), el("small", "muted", "Uses: " + idea.uses.join(", ")));
      const b = el("button", "", "Search for this recipe");
      b.onclick = () => startLeftoverSearch(idea);
      card.append(text, b); box.append(card);
    }
  } catch (err) {
    box.replaceChildren(err instanceof LimitError ? "" : el("p", "muted", "Couldn't get recipe ideas right now: " + err.message));
    $("ideas-btn").disabled = false;
  }
}

// One tap: new recipe, with the leftovers pre-marked as already owned, straight to the checklist.
async function startLeftoverSearch(idea) {
  showError("");
  state.goal = idea.title;
  const data = await pickRecipe({ title: idea.title }, state.leftoverNames);
  if (!data) return;
  state.have = {}; state.flags = {};
  for (const name of data.owned) state.have[name] = true;
  show("step-ingredients", false);  // no fridge scan needed: we already know what's owned
  const n = $("owned-note");
  n.textContent = data.owned.length ? `Marked as already owned from your last shop: ${data.owned.join(", ")}.` : "";
  n.hidden = !data.owned.length; n.className = "note";
  show("step-live", false); show("step-results", false); show("step-leftovers", false);
  renderChecklist(); scrollTo_("step-checklist");
}
