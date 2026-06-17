"""
agent.py

The FitFindr planning loop. Orchestrates the tools in response to a natural
language user query, passing state between them via a single session dict.

The loop is conditional, not a fixed pipeline:
  - a zero-result search retries with looser constraints, and if still empty
    terminates early with an error (suggest_outfit / create_fit_card never run);
  - a normal query runs all the way to a fit card.

Usage:
    from agent import run_agent
    from utils.data_loader import get_example_wardrobe

    result = run_agent("vintage graphic tee under $30, size M",
                       get_example_wardrobe())
    print(result["fit_card"])
    print(result["error"])   # None on success
"""

import re

from tools import (
    search_listings,
    suggest_outfit,
    create_fit_card,
    compare_price,
    get_trends,
)
from utils.profile import load_profile, update_profile


# -- query parsing -------------------------------------------------------------

_SIZE_TOKENS = {"xxs", "xs", "s", "m", "l", "xl", "xxl"}


def _parse_query(query: str) -> dict:
    """
    Extract description, size, and max_price from a free-text query.

    Strategy (documented in planning.md): regex for the price ceiling
    ("$30", "under 30", "below 40"); regex for an explicit size ("size M")
    or a standalone size token; the leftover text is the description.
    """
    text = query.strip()
    lowered = text.lower()

    # --- max_price ---
    max_price = None
    m = re.search(r"(?:under|below|less than|<=?|max)\s*\$?\s*(\d+(?:\.\d+)?)", lowered)
    if not m:
        m = re.search(r"\$\s*(\d+(?:\.\d+)?)", lowered)
    if m:
        max_price = float(m.group(1))

    # --- size ---
    size = None
    sm = re.search(r"size\s+([a-z0-9./]+)", lowered)
    if sm:
        size = sm.group(1).upper()
    else:
        for tok in re.findall(r"[a-z]+", lowered):
            if tok in _SIZE_TOKENS:
                size = tok.upper()
                break

    # --- description: strip the price/size phrases out of the query ---
    desc = re.sub(r"(?:under|below|less than|<=?|max)\s*\$?\s*\d+(?:\.\d+)?", " ", lowered)
    desc = re.sub(r"\$\s*\d+(?:\.\d+)?", " ", desc)
    desc = re.sub(r"size\s+[a-z0-9./]+", " ", desc)
    desc = re.sub(r"\s+", " ", desc).strip()

    return {"description": desc or query, "size": size, "max_price": max_price}


# -- session state -------------------------------------------------------------

def _new_session(query: str, wardrobe: dict) -> dict:
    """Initialize and return a fresh session dict for one user interaction."""
    return {
        "query": query,              # original user query
        "parsed": {},                # extracted description / size / max_price
        "style_prefs": [],           # remembered style tags (stretch: memory)
        "search_results": [],        # list of matching listing dicts
        "selected_item": None,       # top result, passed into suggest_outfit
        "wardrobe": wardrobe,        # user's wardrobe dict
        "price_check": None,         # compare_price result (stretch)
        "trends": [],                # get_trends result (stretch)
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "adjustments": [],           # constraint relaxations applied (stretch: retry)
        "error": None,               # set if the interaction ended early
    }


# -- planning loop -------------------------------------------------------------

def run_agent(query: str, wardrobe: dict) -> dict:
    """
    Run the FitFindr planning loop for a single user interaction and return the
    completed session dict. Check session["error"] first -- if not None, the
    interaction ended early and outfit_suggestion / fit_card are None.
    """
    session = _new_session(query, wardrobe)

    # Step 1: parse the query.
    session["parsed"] = _parse_query(query)
    description = session["parsed"]["description"]
    size = session["parsed"]["size"]
    max_price = session["parsed"]["max_price"]

    # Step 2: load cross-session style memory (stretch). If the wardrobe is
    # empty but we remember the user's taste, surface it for later steps.
    profile = load_profile()
    items = wardrobe.get("items", []) if isinstance(wardrobe, dict) else []
    if not items and profile.get("style_prefs"):
        session["style_prefs"] = profile["style_prefs"]

    # Step 3: search, with retry/fallback on an empty result (stretch).
    results = search_listings(description, size, max_price)

    if not results and size is not None:
        # Fallback 1: drop the size filter.
        results = search_listings(description, None, max_price)
        if results:
            session["adjustments"].append(f"removed the size filter (was '{size}')")

    if not results and max_price is not None:
        # Fallback 2: drop the price ceiling.
        retry_size = size if not session["adjustments"] else None
        results = search_listings(description, retry_size, None)
        if results:
            session["adjustments"].append(f"removed the price cap (was ${max_price:g})")

    session["search_results"] = results

    # Branch: still nothing -> set error and terminate early. The next two
    # tools are NOT called.
    if not results:
        bits = [f"'{description}'"]
        if size:
            bits.append(f"size {size}")
        if max_price is not None:
            bits.append(f"under ${max_price:g}")
        session["error"] = (
            "No listings matched " + ", ".join(bits) +
            " -- even after relaxing size and price. "
            "Try broader keywords, a higher budget, or a different category."
        )
        return session

    # Step 4: select the top-ranked item.
    session["selected_item"] = results[0]
    item = session["selected_item"]

    # Step 5: assess the price vs comparable listings (stretch).
    session["price_check"] = compare_price(item)

    # Step 6: fetch trending styles for the size range (stretch).
    session["trends"] = get_trends(size)

    # Step 7: suggest an outfit using the SAME selected item (no re-entry).
    session["outfit_suggestion"] = suggest_outfit(
        item, wardrobe, trends=session["trends"], style_prefs=session["style_prefs"]
    )

    # Step 8: write the fit card from the SAME outfit + item.
    session["fit_card"] = create_fit_card(session["outfit_suggestion"], item)

    # Step 9: update style memory (stretch).
    learned = list(item.get("style_tags", [])) + [
        t for t in re.findall(r"[a-z]+", description.lower()) if len(t) > 2
    ]
    update_profile(learned)

    # Step 10: return the completed session.
    return session


# -- CLI test ------------------------------------------------------------------

if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe

    print("=== Happy path: graphic tee ===\n")
    session = run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        print(f"Found: {session['selected_item']['title']}")
        print(f"Price check: {session['price_check']['reasoning']}")
        print(f"Trends: {session['trends']}")
        print(f"\nOutfit: {session['outfit_suggestion']}")
        print(f"\nFit card: {session['fit_card']}")

    print("\n\n=== No-results path ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Error message: {session2['error']}")
    print(f"fit_card is None: {session2['fit_card'] is None}")
    print(f"Adjustments tried: {session2['adjustments']}")
