# FitFindr — planning.md

> Written before implementation. This spec is what I used to direct the AI tool (Claude)
> to generate each function. Updated before starting the stretch features (see the
> "Stretch Features" section near the bottom).

---

## Tools

The three required tools come first. Three additional tools (price comparison, trend
awareness, and a style-profile memory helper) are documented under **Additional Tools**.

### Tool 1: search_listings

**What it does:**
Searches the mock secondhand listings dataset (`data/listings.json`, 40 items) for pieces
matching the user's keywords, optional size, and optional price ceiling, and returns them
ranked best-match-first. This is the agent's "find something to buy" step.

**Input parameters:**
- `description` (str): Free-text keywords describing the desired item, e.g. `"vintage graphic tee"`. Tokenized and matched against each listing's title, description, style_tags, category, brand, and colors.
- `size` (str | None): Size string to filter by, e.g. `"M"`. Matched case-insensitively as a substring so `"M"` matches `"S/M"` and `"M/L"`. `None` skips size filtering.
- `max_price` (float | None): Inclusive price ceiling. A listing with `price <= max_price` passes. `None` skips price filtering.

**What it returns:**
A `list[dict]`, sorted by descending relevance score. Each dict is a full listing with the
fields: `id` (str), `title` (str), `description` (str), `category` (str), `style_tags`
(list[str]), `size` (str), `condition` (str), `price` (float), `colors` (list[str]),
`brand` (str | None), `platform` (str). Returns `[]` (empty list, no exception) when nothing
matches.

**What happens if it fails or returns nothing:**
Returns `[]`. The planning loop treats an empty list as the search failure mode: it does
**not** call `suggest_outfit`. Instead it first retries with loosened constraints (drop the
size filter, then drop the price ceiling — see Planning Loop / retry logic), and only if
every retry is still empty does it set `session["error"]` to a specific message naming what
was searched and suggesting concrete changes (broaden keywords, raise budget, remove size).

---

### Tool 2: suggest_outfit

**What it does:**
Given the selected listing and the user's wardrobe, asks the LLM (Groq
`llama-3.3-70b-versatile`) to propose 1–2 complete outfits that pair the new item with
specific pieces the user already owns, including a styling tip.

**Input parameters:**
- `new_item` (dict): The listing dict chosen by the planning loop (the item being considered). Its `title`, `category`, `colors`, and `style_tags` are formatted into the prompt.
- `wardrobe` (dict): A wardrobe dict with an `items` key holding a list of wardrobe-item dicts (`id`, `name`, `category`, `colors`, `style_tags`, `notes`). May be empty.
- *(optional)* `trends` (list[str]) and `style_prefs` (list[str]): trend tags and remembered style preferences threaded in by the planning loop (stretch features) so the suggestion can reference what's currently popular and the user's taste.

**What it returns:**
A non-empty `str` containing 1–2 named outfit combinations and a concrete styling tip
(e.g. "tuck the front, cuff the sleeves"). Written in second person, conversational.

**What happens if it fails or returns nothing:**
If `wardrobe["items"]` is empty, it does **not** error — it switches to a "general styling
advice" prompt (what categories/colors/vibes pair well with the item for a new user with no
closet entered). If the LLM call itself raises (network/auth), the function catches the
exception and returns a plain-text fallback ("Couldn't generate a styling idea right
now — but this <item> would pair well with neutral basics and your go-to shoes."), so the
agent stays useful and can still produce a fit card.

---

### Tool 3: create_fit_card

**What it does:**
Generates a short, casual, shareable caption (the kind you'd put under an OOTD post) for the
finished look, calling the LLM at a high temperature so repeated/different inputs produce
different captions.

**Input parameters:**
- `outfit` (str): The outfit suggestion string returned by `suggest_outfit`.
- `new_item` (dict): The listing dict, used to mention the item title, price, and platform naturally (once each).

**What it returns:**
A `str` of ~2–4 sentences in a casual OOTD voice — not a product description. Varies per
input and per call (temperature ≈ 0.9).

**What happens if it fails or returns nothing:**
Guards against an empty / whitespace-only `outfit` up front and returns a descriptive error
**string** (not an exception): `"Can't write a fit card without an outfit suggestion — try
generating an outfit first."` If the LLM call raises, it catches and returns a short
fallback caption built from the item title/price so the pipeline still completes.

---

### Additional Tools

#### Tool 4: compare_price  *(stretch — price comparison)*

**What it does:** Estimates whether a listing's price is fair by comparing it to comparable
listings in the same category in the dataset.

**Input parameters:**
- `item` (dict): The listing whose price is being assessed.
- *(optional)* `listings` (list[dict] | None): The comparison pool; defaults to the full dataset via `load_listings()`.

**What it returns:** A `dict`: `{"verdict": "great deal" | "fair" | "overpriced", "item_price": float, "median": float, "count": int, "reasoning": str}`. `reasoning` is a human sentence ("$22 vs a median of $24 across 14 comparable tops — a fair price").

**What happens if it fails / no comparables:** If fewer than 2 comparable items exist, returns
`{"verdict": "unknown", ...}` with reasoning that there isn't enough data — never divides by
zero or raises.

#### Tool 5: get_trends  *(stretch — trend awareness)*

**What it does:** Surfaces which styles are "currently popular" for the user's size range by
ranking the most common `style_tags` among listings that fit that size. (Honest data source:
this is a *mock* trend feed derived from tag frequency in the local listings dataset, standing
in for a public fashion-platform API — documented as such in the README.)

**Input parameters:**
- `size` (str | None): Size to scope trends to; `None` uses all listings.
- *(optional)* `top_n` (int): How many trending tags to return (default 5).

**What it returns:** A `list[str]` of trending style tags, most popular first (e.g.
`["vintage", "streetwear", "y2k", "grunge", "denim"]`). Empty list if the size range has no
listings — the planning loop then just skips trend injection.

**What happens if it fails:** Returns `[]` on no matches; the outfit suggestion proceeds
without trend context.

#### Tool 6: style profile memory  *(stretch — cross-session memory)*

`load_profile()` / `save_profile(prefs)` persist a small JSON file
(`data/style_profile.json`, gitignored) holding the user's learned `style_prefs` (style tags
seen in their wardrobe + searches). On a later session with an empty wardrobe, the agent
reuses these preferences so the user doesn't have to re-describe their taste. Failure mode:
if the file is missing or corrupt, `load_profile()` returns an empty default — never crashes.

---

## Planning Loop

**How the agent decides what to call next** — `run_agent(query, wardrobe)` runs these
ordered, *conditional* steps over a single `session` dict. It is not a fixed pipeline: each
step inspects session state and can terminate early or change its own behavior.

1. **Parse.** Extract `description`, `size`, `max_price` from the raw query with regex
   (`$NN` / `under N` → max_price; `size M` / standalone size token → size; remaining words →
   description). Store in `session["parsed"]`.
2. **Load memory (stretch).** `load_profile()`. If the wardrobe is empty but a profile exists,
   put remembered `style_prefs` into the session so later steps can use them.
3. **Search with retry/fallback (stretch).** Call `search_listings(description, size, max_price)`.
   - If results is non-empty → continue.
   - If **empty**, retry **once with `size=None`** (note "dropped size filter"); if still empty,
     retry **once with `max_price=None`** (note "removed price cap"); record any adjustment in
     `session["adjustments"]`.
   - If **still empty after all retries** → set `session["error"]` to a specific message
     (what was searched + what to change) and **`return session` immediately**. `suggest_outfit`
     and `create_fit_card` are never called. `fit_card` stays `None`.
4. **Select.** `session["selected_item"] = results[0]` (top-ranked).
5. **Assess price (stretch).** `compare_price(selected_item)` → `session["price_check"]`.
6. **Get trends (stretch).** `get_trends(parsed size)` → `session["trends"]`.
7. **Suggest outfit.** `suggest_outfit(selected_item, wardrobe, trends, style_prefs)` →
   `session["outfit_suggestion"]`. (Branches internally on empty wardrobe.)
8. **Fit card.** `create_fit_card(outfit_suggestion, selected_item)` → `session["fit_card"]`.
9. **Update memory (stretch).** Merge this item's + search's style tags into the profile and
   `save_profile()`.
10. **Return session.**

**How it knows it's done:** when `fit_card` is set (success) or when `error` is set (early
termination). The loop's behavior *changes with input*: an impossible query terminates at
step 3 with only `error` populated; a normal query runs all the way to a fit card; a query
that only matches after dropping the size filter runs fully but records an adjustment the
user is told about.

---

## State Management

A single `session` dict (created by `_new_session`) is the one source of truth for the whole
interaction. Each tool reads what it needs from the session and writes its result back, so
later tools consume earlier outputs with no re-entry by the user.

| Key | Set by | Consumed by |
|-----|--------|-------------|
| `query` | entry | parse step |
| `parsed` (description/size/max_price) | parse step | `search_listings`, `get_trends` |
| `style_prefs` | `load_profile` | `suggest_outfit` |
| `search_results` | `search_listings` | select step |
| `selected_item` | select step | `compare_price`, `suggest_outfit`, `create_fit_card` |
| `wardrobe` | entry | `suggest_outfit` |
| `price_check` | `compare_price` | UI / README |
| `trends` | `get_trends` | `suggest_outfit` |
| `outfit_suggestion` | `suggest_outfit` | `create_fit_card` |
| `fit_card` | `create_fit_card` | UI |
| `adjustments` | retry/fallback | error/UI message |
| `error` | any failed step | UI; signals early termination |

The exact `selected_item` dict produced by `search_listings` is the same object passed to
`suggest_outfit` and `create_fit_card` — verified in Milestone 4 by printing object identity.

---

## Error Handling

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| search_listings | No results match the query | Retry without the size filter, then without the price cap; if still nothing, return a specific message: e.g. *"No listings matched 'designer ballgown' in size XXS under $5 — even after relaxing size and price. Try broader keywords, a higher budget, or a different category."* The agent stops and does **not** call the next tools. |
| suggest_outfit | Wardrobe is empty (new user) | Don't crash — switch to a general-styling-advice prompt and return concrete pairing ideas/vibe for the item, noting it's general advice because no wardrobe was provided. (LLM-call exceptions are caught and a neutral fallback string is returned.) |
| create_fit_card | Outfit input missing/incomplete | Detect empty/whitespace `outfit` and return a descriptive error string asking to generate an outfit first; never raise. (LLM-call exceptions caught → short fallback caption from item details.) |
| compare_price *(stretch)* | Fewer than 2 comparable listings | Return `verdict: "unknown"` with reasoning that there isn't enough comparable data. |
| get_trends *(stretch)* | No listings for the size range | Return `[]`; planning loop proceeds without trend context. |

---

## Architecture

```
                         User query  +  wardrobe choice (Gradio app.py)
                              │
                              ▼
        ┌─────────────────────────────────────────────────────────────────┐
        │                     run_agent()  —  PLANNING LOOP                 │
        │                                                                   │
        │   parse query (regex) ──► session["parsed"]                       │
        │        │                                                          │
        │   load_profile() ───────► session["style_prefs"]   (memory)       │
        │        │                                                          │
        │        ▼                                                          │
        │   search_listings(description, size, max_price)                   │
        │        │                                                          │
        │        │ results == []                                            │
        │        ├───► retry size=None ─► retry max_price=None              │
        │        │            │ still []                                    │
        │        │            ▼                                             │
        │        │      [ERROR] session["error"]="No listings..." ─► return │◄── error path
        │        │                                                          │   ends here
        │        │ results == [item, ...]                                   │
        │        ▼                                                          │
        │   session["selected_item"] = results[0]                           │
        │        │                                                          │
        │        ├───► compare_price(item) ─► session["price_check"]        │
        │        ├───► get_trends(size)    ─► session["trends"]             │
        │        ▼                                                          │
        │   suggest_outfit(selected_item, wardrobe, trends, style_prefs)    │
        │        │   (empty wardrobe ─► general advice branch)              │
        │        ▼                                                          │
        │   session["outfit_suggestion"] = "..."                            │
        │        │                                                          │
        │        ▼                                                          │
        │   create_fit_card(outfit_suggestion, selected_item)               │
        │        │   (empty outfit ─► error-string branch)                  │
        │        ▼                                                          │
        │   session["fit_card"] = "..."                                     │
        │        │                                                          │
        │   save_profile(merged prefs)   (memory)                           │
        └────────┼──────────────────────────────────────────────────────────┘
                 ▼
        return session  ──►  app.py maps to 3 panels: listing | outfit | fit card
```

State lives in the `session` dict throughout; every arrow that writes `session[...]` is data
flowing forward to the next tool. The single error branch (empty search after retries) is the
only early exit.

---

## AI Tool Plan

**AI tool used:** Claude (Claude Code, Opus). For every component I pasted the relevant
section(s) of *this* planning.md as the spec and verified generated code against it before
running.

**Milestone 3 — Individual tool implementations:**
- **search_listings:** Give Claude the Tool 1 block (inputs, return value, scoring steps,
  empty-result behavior) + the listings field list, and ask it to implement the function
  using `load_listings()`. **Verify before trusting:** confirm it (a) filters by all three
  params, (b) case-insensitive substring size match, (c) drops score-0 items, (d) returns
  `[]` not an exception when empty. Test with `vintage graphic tee` (expect hits), `jacket
  max_price=10` (expect all ≤ $10), and `designer ballgown XXS $5` (expect `[]`).
- **suggest_outfit / create_fit_card:** Give Claude the Tool 2/3 blocks including the empty-
  wardrobe and empty-outfit branches and the Groq model name. **Verify:** outfit handles empty
  `items`; fit card guards empty string and varies across runs (bump temperature). Run each 3×.
- **compare_price / get_trends:** Give Claude the Additional Tools blocks. **Verify:** median
  math over the right comparable pool; `unknown`/`[]` on insufficient data.

**Milestone 4 — Planning loop and state management:**
- Give Claude the **Architecture diagram** + **Planning Loop** + **State Management** sections
  and ask it to implement `run_agent()` filling the `session` dict step-by-step.
  **Verify before trusting:** (a) it branches on the `search_listings` result and returns early
  with `error` when empty (does **not** call the other two tools), (b) it stores each tool's
  output in the documented session key and feeds `selected_item` / `outfit_suggestion` forward
  rather than recomputing, (c) retry/fallback adjusts constraints and records the change.
  Confirm by running both the happy path and the impossible query in `agent.py`'s `__main__`.

---

## A Complete Interaction (Step by Step)

**What FitFindr does (in my own words):** FitFindr takes a single natural-language thrifting
request and turns it into a finished, shareable outfit. It first *searches* a mock secondhand
listings dataset for items matching the user's keywords, size, and budget (`search_listings`);
if it finds something it picks the best match, checks the price and current trends, and
*styles* it against the user's wardrobe (`suggest_outfit`), then *writes* a casual caption for
the look (`create_fit_card`). If any step produces nothing useful — most importantly a
zero-result search — the agent retries with looser constraints, and if that still fails it
stops, tells the user exactly what failed and what to change, and never feeds empty data
forward.

**Example user query:** "I'm looking for a vintage graphic tee under $30. I mostly wear baggy
jeans and chunky sneakers. What's out there and how would I style it?"

**Step 1 — Parse + Search.** The loop parses the query → `description="vintage graphic tee"`,
`size=None`, `max_price=30.0`, and calls
`search_listings("vintage graphic tee", size=None, max_price=30.0)`. It returns several
matches (e.g. *Graphic Tee — 2003 Tour Bootleg Style $24*, *Vintage Band Tee — Faded Grey
$19*, *Vintage Graphic Hoodie $26*) ranked by keyword overlap. Results are non-empty, so no
error branch. `session["selected_item"]` = the top match.

**Step 2 — Price + Trends + Suggest.** `compare_price(selected_item)` reports e.g. *"$24 vs a
median of ~$21 across comparable tops — fair."* `get_trends(None)` returns top tags like
`["vintage","streetwear","y2k",...]`. Then
`suggest_outfit(selected_item, example_wardrobe, trends, style_prefs)` runs with the **same**
selected item (no re-entry) and returns something like *"Pair the bootleg tee with your baggy
straight-leg jeans and chunky white sneakers; throw the vintage black denim jacket over it.
Tuck the front hem for shape — vintage/streetwear is trending right now."* Stored in
`session["outfit_suggestion"]`.

**Step 3 — Fit card.** `create_fit_card(outfit_suggestion, selected_item)` takes the **same**
outfit string + item and returns a caption, e.g. *"found this 2003 bootleg tee on poshmark for
$24 and it was BUILT for my baggy jeans 🤘 styled it with the chunky sneakers + denim jacket,
full fit in stories."* Stored in `session["fit_card"]`. Profile memory updated.

**Final output to user:** Three panels in the Gradio UI — the top listing (title, price,
condition, platform, + price verdict), the outfit idea, and the fit-card caption. On the
impossible query *"designer ballgown size XXS under $5"*, Steps 2–3 never run; the user sees a
specific "no listings found, here's what to try" message in the listing panel only.

---

## Stretch Features (planned before implementing — per project instructions)

1. **Price comparison tool** — `compare_price` (Tool 4 above). Compares to same-category
   listings' median; returns verdict + reasoning. Wired into the loop after item selection.
2. **Style profile memory** — `load_profile` / `save_profile` (Tool 6). Persists style tags to
   `data/style_profile.json` across sessions; reused when the wardrobe is empty.
3. **Trend awareness** — `get_trends` (Tool 5). Mock trend feed from listings tag-frequency by
   size; trend tags are injected into the `suggest_outfit` prompt so they visibly shape the
   suggestion.
4. **Retry logic with fallback** — in `run_agent`, a zero-result search auto-retries with the
   size filter dropped, then the price cap dropped, and reports the adjustment to the user.
