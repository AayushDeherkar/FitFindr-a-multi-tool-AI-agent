# FitFindr 🛍️ — a multi-tool AI agent for thrifting

FitFindr turns a single natural-language thrifting request into a finished,
shareable outfit. It **searches** a mock secondhand listings dataset, **styles**
the best find against your existing wardrobe (with an LLM), and **writes** a
casual caption for the look — handling the messy cases where a tool returns
nothing useful.

```
"vintage graphic tee under $30, size M"
        │
        ▼  search_listings  →  suggest_outfit  →  create_fit_card
        ▼
  top listing + price check + trends  |  outfit idea  |  shareable fit card
```

---

## Setup

```bash
python -m venv .venv
source .venv/Scripts/activate     # Windows (Git Bash);  .venv\Scripts\activate on cmd
# source .venv/bin/activate        # macOS / Linux
pip install -r requirements.txt
```

Create a `.env` in the repo root (gitignored — never commit it):

```
GROQ_API_KEY=your_key_here        # free key at console.groq.com
```

Run it:

```bash
python app.py        # opens the Gradio UI at http://localhost:7860
python agent.py      # CLI: runs the happy path + the no-results path
pytest tests/        # 14 tool tests (LLM tests auto-skip without a key)
```

**Stack:** Groq `llama-3.3-70b-versatile` (LLM), Gradio (UI), pytest (tests),
mock data in `data/`. No external APIs beyond Groq.

---

## Tool Inventory

The documented interfaces below match the actual function signatures in
[`tools.py`](tools.py).

### Required tools

| Tool | Inputs | Returns | Purpose |
|------|--------|---------|---------|
| **`search_listings`** | `description (str)`, `size (str \| None)`, `max_price (float \| None)` | `list[dict]` — matching listing dicts (each with `id, title, description, category, style_tags (list[str]), size, condition, price (float), colors (list[str]), brand (str\|None), platform`), sorted best-match first. `[]` if nothing matches. | Find candidate items in the dataset by keyword relevance, with optional size and price filters. |
| **`suggest_outfit`** | `new_item (dict)`, `wardrobe (dict)`, `trends (list[str] \| None)`, `style_prefs (list[str] \| None)` | `str` — 1–2 complete outfit ideas + a styling tip. | Style the found item against the user's owned pieces (or give general advice if the wardrobe is empty). |
| **`create_fit_card`** | `outfit (str)`, `new_item (dict)` | `str` — a casual 2–4 sentence OOTD caption (or an error string if `outfit` is empty). | Produce a shareable, post-worthy caption for the finished look. Uses high temperature so captions vary. |

### Additional tools (stretch)

| Tool | Inputs | Returns | Purpose |
|------|--------|---------|---------|
| **`compare_price`** | `item (dict)`, `listings (list[dict] \| None)` | `dict` — `{verdict, item_price, median, count, reasoning}` (`verdict` ∈ `great deal / fair / overpriced / unknown`) | Judge whether a listing's price is fair vs. the median of same-category listings. |
| **`get_trends`** | `size (str \| None)`, `top_n (int)` | `list[str]` — trending style tags, most popular first (`[]` if none). | Surface "currently popular" styles for a size range (mock trend feed — see below). |
| **`load_profile` / `save_profile` / `update_profile`** ([`utils/profile.py`](utils/profile.py)) | `update_profile(tags: list[str], max_prefs=12)` | `dict` — `{style_prefs, seen_items}` | Cross-session style memory persisted to `data/style_profile.json`. |

---

## How the Planning Loop Works

`run_agent(query, wardrobe)` in [`agent.py`](agent.py) drives a **conditional**
loop over one `session` dict — it is *not* a fixed call-every-tool sequence. The
agent's behavior changes with what each step returns:

1. **Parse** the query with regex → `description`, `size`, `max_price`.
2. **Load memory:** if the wardrobe is empty *and* a saved profile exists, pull
   remembered `style_prefs` into the session.
3. **Search** with `search_listings`. **This is the decision point:**
   - **Non-empty** → continue.
   - **Empty *and* a size was given** → retry once with `size=None` (record
     "removed the size filter").
   - **Still empty *and* a price cap was given** → retry once with
     `max_price=None` (record "removed the price cap").
   - **Still empty after retries** → set `session["error"]` to a specific message
     and **`return` immediately**. `suggest_outfit` and `create_fit_card` are
     **never called**, and `fit_card` stays `None`.
4. **Select** `results[0]` as `selected_item`.
5. **`compare_price`** on the item → `price_check`.
6. **`get_trends`** for the size → `trends`.
7. **`suggest_outfit`** with the selected item + wardrobe (+ trends + prefs).
8. **`create_fit_card`** from that outfit + the same item.
9. **Update memory** with the item's tags.

**The loop checks `session` state to decide each branch** — specifically the
emptiness of the `search_listings` result drives whether it retries, errors out
early, or proceeds; and the emptiness of `wardrobe["items"]` drives whether
`suggest_outfit` styles against owned pieces or gives general advice. It's "done"
when `fit_card` is set (success) or `error` is set (early termination).

**When `search_listings` returns no results specifically:** the agent does not
just say "no results." It first **automatically retries with loosened
constraints** (drops the size filter, then the price cap) and tells the user what
it adjusted. Only if every relaxed search is still empty does it return a message
naming exactly what was searched (keywords, size, budget) and three concrete
things to change (broaden keywords, raise budget, different category).

---

## State Management

A single `session` dict (built by `_new_session`) is the one source of truth for
the whole interaction. Each tool reads what it needs from the session and writes
its result back, so later tools consume earlier outputs **with no re-entry by the
user**.

| Key | Written by | Read by |
|-----|-----------|---------|
| `parsed` (description/size/max_price) | parse step | `search_listings`, `get_trends` |
| `style_prefs` | `load_profile` | `suggest_outfit` |
| `search_results` | `search_listings` | select step |
| `selected_item` | select step | `compare_price`, `suggest_outfit`, `create_fit_card` |
| `price_check` | `compare_price` | UI |
| `trends` | `get_trends` | `suggest_outfit` |
| `outfit_suggestion` | `suggest_outfit` | `create_fit_card` |
| `fit_card` | `create_fit_card` | UI |
| `adjustments` | retry/fallback | UI / error message |
| `error` | any failed step | UI; signals early termination |

The **exact** dict that `search_listings` produces is the object passed into both
`suggest_outfit` and `create_fit_card` — verified by object identity:

```
selected_item IS the dict passed to suggest_outfit:  True
selected_item IS the dict passed to create_fit_card: True
outfit_suggestion IS the string passed to create_fit_card: True
```

---

## Interaction Walkthrough

**User query:** `"I'm looking for a vintage graphic tee under $30. I mostly wear
baggy jeans and chunky sneakers. What's out there and how would I style it?"`

**Step 1 — `search_listings`**
- Input: `search_listings("vintage graphic tee ...", size=None, max_price=30.0)` (parsed from the query)
- Why: the query is a "find me something" request — searching the dataset is the only step that can start the flow.
- Output: several matches; top result = **"Graphic Tee — 2003 Tour Bootleg Style", $24, good, depop**. Non-empty → no error branch. Stored as `selected_item`.

**Step 2 — `compare_price` + `get_trends` + `suggest_outfit`**
- Input: the same `selected_item`, plus `get_example_wardrobe()`, `trends`, `style_prefs`.
- Why: now that an item is selected, the agent assesses its price (`compare_price` → *"$24 vs a median of $20.5 across 14 comparable tops — overpriced"*), pulls trends (`["vintage","classic","streetwear",...]`), then styles it against owned pieces.
- Output: *"Pair the bootleg tee with your baggy straight-leg jeans and black combat boots… layer the vintage black denim jacket over it; tuck the front hem. Leans into the vintage/streetwear trend."* Stored as `outfit_suggestion`.

**Step 3 — `create_fit_card`**
- Input: the same `outfit_suggestion` string + the same `selected_item`.
- Why: the final shareable artifact — turns the styling into a caption.
- Output: *"Just scored this sick Graphic Tee — 2003 Tour Bootleg Style on depop for $24 and I'm obsessed. Paired it with my baggy straight-leg jeans and black combat boots for a grunge-inspired look giving major 90s vibes 🤘…"*

**Final output to user:** three Gradio panels — **Top listing** (title, price,
condition, platform, size, tags, price verdict, trends), **Outfit idea**, and the
**Fit card** caption.

---

## Error Handling and Fail Points

Every tool owns its failure mode; "fail silently" and "crash" are never used. Full
captured evidence is in [docs/failure_modes.md](docs/failure_modes.md).

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| `search_listings` | No results match the query | Returns `[]` (no exception). The loop **retries with the size filter dropped, then the price cap dropped**; if still empty it sets a specific error ("No listings matched '…', size …, under $… — even after relaxing size and price. Try broader keywords, a higher budget, or a different category.") and **stops without calling the other tools**. |
| `suggest_outfit` | Wardrobe is empty (new user) | Switches to a general-styling-advice branch and returns concrete pairing ideas, explicitly noting it's general advice since no wardrobe was provided. LLM-call exceptions are caught → neutral fallback string. |
| `create_fit_card` | Outfit string empty/whitespace | Returns a descriptive error **string** ("Can't write a fit card without an outfit suggestion — try generating an outfit first."), never an exception. LLM-call exceptions caught → short fallback caption from item details. |
| `compare_price` *(stretch)* | < 2 comparable listings | Returns `verdict: "unknown"` with reasoning that there isn't enough data — never divides by zero. |
| `get_trends` *(stretch)* | No listings for the size range | Returns `[]`; the loop proceeds without trend context. |

**Concrete example from testing** (zero-result search, full agent):

```
$ python -c "from tools import search_listings; print(search_listings('designer ballgown', size='XXS', max_price=5))"
[]

# through run_agent:
error: No listings matched 'designer ballgown', size XXS, under $5 -- even after
relaxing size and price. Try broader keywords, a higher budget, or a different category.
suggest_outfit called? False
fit_card: None
```

---

## Stretch Features (all four implemented)

1. **Price comparison tool** — `compare_price` compares an item to the **median of
   same-category listings** in the dataset and returns a verdict + reasoning. It's
   wired into the loop right after item selection and shown in the listing panel.
2. **Style profile memory** — `utils/profile.py` persists learned style tags to
   `data/style_profile.json` across sessions. Verified: session 1 (a grunge/flannel
   search) writes prefs `['flannel','grunge','streetwear','vintage',…]`; session 2
   with an **empty wardrobe** reuses those prefs in the outfit prompt without the
   user re-describing their taste.
3. **Trend awareness** — `get_trends` ranks the most common `style_tags` among
   listings in the user's size range and the loop injects them into the
   `suggest_outfit` prompt, so the suggestion visibly references what's trending.
   **Data source (honest):** this is a *mock* trend feed derived from tag frequency
   in the local `listings.json`, standing in for a public fashion-platform API — no
   live network call.
4. **Retry logic with fallback** — a zero-result `search_listings` auto-retries with
   the size filter dropped, then the price cap dropped, recording each change in
   `session["adjustments"]` and reporting it to the user (e.g. *"Adjusted your
   search: removed the size filter (was 'XXS')."*).

---

## AI Usage

I used **Claude (Claude Code, Opus 4.8)** throughout, directing it from my
`planning.md` spec and reviewing every output against that spec before trusting it.

**Instance 1 — `search_listings` implementation.** I gave Claude my Tool 1 spec
block (the three parameters with types, the scoring-by-keyword-overlap steps, and
the "return `[]`, never raise" failure mode) plus the listing field list, and asked
it to implement the function using `load_listings()`. **What I reviewed/overrode:**
the first cut used a loose bidirectional size match (`size in item_size or
item_size in size`), which I caught when a test for an impossible size
(`"ZZZ-not-a-size"`) returned trends/results — single-letter listing sizes like
`"S"` were substrings of the bogus query. I changed it to a forward-only substring
match (`size in item_size`) so `"M"` still matches `"S/M"` but garbage sizes match
nothing, and updated the spec wording to match.

**Instance 2 — the planning loop in `run_agent`.** I gave Claude my Architecture
diagram + Planning Loop + State Management sections and asked it to fill the
`session` dict step-by-step. **What I reviewed/overrode:** I verified it branches on
the `search_listings` result and returns early (not calling the downstream tools)
when empty, and that it feeds `selected_item`/`outfit_suggestion` *forward* rather
than recomputing them. I then added a stricter check of my own — wrapping the tools
with spies to confirm `selected_item` is passed into the next tools **by object
identity**, not as a copy — which is how I proved state management rather than
assuming it.

---

## Spec Reflection

**One way `planning.md` helped during implementation:** Writing the Planning Loop
section as explicit conditional branches ("if results empty → retry size=None →
retry price=None → else error and return early") meant the loop in `run_agent`
practically transcribed itself, and the early-return that protects `suggest_outfit`
from empty input was designed *before* I wrote any code rather than patched in
after a crash. The state table likewise told me exactly which `session` key each
tool reads and writes, so wiring tools together was mechanical.

**One divergence from the spec, and why:** I planned `search_listings`'s size match
as a "bidirectional substring" match, but during testing that let single-character
listing sizes match unrelated queries (a size like `"S"` is a substring of almost
anything). I diverged to a **forward-only** substring match (`query size in listing
size`), which preserves the real requirement (`"M"` matching `"S/M"`/`"M/L"`) while
rejecting garbage, and I updated `planning.md` so the spec and code agree.
