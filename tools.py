"""
tools.py

The FitFindr tools. Each tool is a standalone function that can be called and
tested independently before being wired into the agent loop (see tests/test_tools.py).

Required tools:
    search_listings(description, size, max_price)  -> list[dict]
    suggest_outfit(new_item, wardrobe)             -> str
    create_fit_card(outfit, new_item)              -> str

Stretch tools:
    compare_price(item, listings=None)             -> dict   (price comparison)
    get_trends(size, top_n=5)                      -> list[str]  (trend awareness)
"""

import os
import re
from collections import Counter
from statistics import median

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

load_dotenv()

MODEL = "llama-3.3-70b-versatile"

# Words that carry no matching signal in a search query.
_STOPWORDS = {
    "a", "an", "the", "for", "to", "in", "of", "and", "or", "with", "my", "me",
    "i", "im", "looking", "want", "need", "find", "some", "something", "under",
    "size", "that", "this", "is", "are", "it", "out", "there", "what", "how",
    "would", "wear", "mostly", "really", "like", "love", "got", "get",
}


# -- Groq client ---------------------------------------------------------------

def _get_groq_client():
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Add it to a .env file in the project root."
        )
    return Groq(api_key=api_key)


def _llm(prompt: str, temperature: float = 0.7, max_tokens: int = 350) -> str:
    """Single-shot chat completion helper. Raises on failure (callers catch)."""
    client = _get_groq_client()
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens with stopwords removed."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 1]


# -- Tool 1: search_listings ---------------------------------------------------

def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Args:
        description: Keywords describing what the user is looking for.
        size:        Size string to filter by (case-insensitive substring match
                     so "M" matches "S/M" and "M/L"), or None to skip.
        max_price:   Maximum price (inclusive), or None to skip price filtering.

    Returns:
        A list of matching listing dicts, sorted by relevance (best match first).
        Returns an empty list if nothing matches -- does NOT raise an exception.
    """
    try:
        listings = load_listings()
    except Exception:
        # Data file missing/corrupt: degrade to empty result, never crash.
        return []

    query_tokens = _tokenize(description)
    size_norm = size.strip().lower() if size else None

    scored = []
    for item in listings:
        # --- price filter ---
        if max_price is not None and item.get("price", 0) > max_price:
            continue

        # --- size filter (case-insensitive substring: "m" matches "s/m") ---
        if size_norm:
            item_size = str(item.get("size", "")).lower()
            if size_norm not in item_size:
                continue

        # --- relevance scoring by keyword overlap ---
        haystack_parts = [
            item.get("title", ""),
            item.get("description", ""),
            item.get("category", ""),
            item.get("brand") or "",
            " ".join(item.get("style_tags", [])),
            " ".join(item.get("colors", [])),
        ]
        haystack = _tokenize(" ".join(haystack_parts))
        haystack_set = set(haystack)

        score = 0
        for tok in query_tokens:
            if tok in haystack_set:
                # weight tags/title hits a bit higher via raw frequency
                score += 2 + haystack.count(tok)

        # If the user gave no usable keywords, treat filters alone as a match.
        if not query_tokens:
            score = 1

        if score > 0:
            scored.append((score, item))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _score, item in scored]


# -- Tool 2: suggest_outfit ----------------------------------------------------

def suggest_outfit(
    new_item: dict,
    wardrobe: dict,
    trends: list[str] | None = None,
    style_prefs: list[str] | None = None,
) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1-2 complete outfits.

    Args:
        new_item:    A listing dict (the item the user is considering).
        wardrobe:    A wardrobe dict with an 'items' key (list of item dicts).
                     May be empty -- handled gracefully.
        trends:      Optional trending style tags (stretch) to weave in.
        style_prefs: Optional remembered style preferences (stretch).

    Returns:
        A non-empty string with outfit suggestions. If the wardrobe is empty,
        returns general styling advice instead of raising.
    """
    title = new_item.get("title", "this piece")
    category = new_item.get("category", "item")
    colors = ", ".join(new_item.get("colors", [])) or "neutral"
    tags = ", ".join(new_item.get("style_tags", [])) or "versatile"

    trend_line = ""
    if trends:
        trend_line = f"\nCurrently trending styles to lean into: {', '.join(trends)}."
    pref_line = ""
    if style_prefs:
        pref_line = f"\nThe user's known style preferences: {', '.join(style_prefs)}."

    items = wardrobe.get("items", []) if isinstance(wardrobe, dict) else []

    try:
        if not items:
            # --- empty wardrobe branch: general styling advice ---
            prompt = (
                f"A shopper is considering a secondhand '{title}' "
                f"(category: {category}, colors: {colors}, style: {tags}). "
                "They haven't entered any wardrobe yet, so give GENERAL styling advice: "
                "describe 1-2 complete outfit directions (what kinds of bottoms/tops/"
                "shoes/layers pair well), the vibe it suits, and one concrete styling tip. "
                "Be specific and friendly, 4-6 sentences. Make clear this is general advice "
                "since no wardrobe was provided."
                + trend_line + pref_line
            )
            return _llm(prompt, temperature=0.7)

        # --- wardrobe branch: pair with named owned pieces ---
        closet = "\n".join(
            f"- {it.get('name', '?')} ({it.get('category', '?')}; "
            f"{', '.join(it.get('colors', []))}; {', '.join(it.get('style_tags', []))})"
            for it in items
        )
        prompt = (
            f"The user found a secondhand '{title}' "
            f"(category: {category}, colors: {colors}, style: {tags}).\n\n"
            f"Their current wardrobe:\n{closet}\n\n"
            "Suggest 1-2 COMPLETE outfits that pair this new item with SPECIFIC pieces "
            "they already own (name the wardrobe pieces). Include one concrete styling tip "
            "(tuck, cuff, layer, etc.). Conversational second person, 4-6 sentences."
            + trend_line + pref_line
        )
        return _llm(prompt, temperature=0.7)

    except Exception:
        # LLM/network failure: useful fallback so the agent keeps working.
        return (
            f"Couldn't generate a custom styling idea right now, but this {title} "
            f"({tags}) would pair well with neutral basics, your go-to bottoms, and "
            "simple shoes for an easy everyday look."
        )


# -- Tool 3: create_fit_card ---------------------------------------------------

def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Args:
        outfit:   The outfit suggestion string from suggest_outfit().
        new_item: The listing dict for the thrifted item.

    Returns:
        A 2-4 sentence casual caption string. If outfit is empty/whitespace,
        returns a descriptive error message string (does NOT raise).
    """
    if not outfit or not outfit.strip():
        return (
            "Can't write a fit card without an outfit suggestion - "
            "try generating an outfit first."
        )

    title = new_item.get("title", "this piece")
    price = new_item.get("price")
    price_str = f"${price:g}" if isinstance(price, (int, float)) else "a steal"
    platform = new_item.get("platform", "online")

    prompt = (
        "Write a short, casual social-media caption (2-4 sentences) for an OOTD post "
        "about a secondhand fashion find. It should sound like a real person posting, "
        "NOT a product description. Mention the item, the price, and the platform "
        "naturally (each once). Capture the outfit vibe in specific terms. "
        "A tasteful emoji or two is fine.\n\n"
        f"Item: {title}\nPrice: {price_str}\nPlatform: {platform}\n"
        f"The outfit: {outfit}\n\nCaption:"
    )
    try:
        # High temperature => varied captions across runs/inputs.
        return _llm(prompt, temperature=0.95, max_tokens=160)
    except Exception:
        return (
            f"thrifted this {title} for {price_str} on {platform} and i'm obsessed - "
            "styled it up and it's going straight into the rotation."
        )


# -- Tool 4: compare_price (stretch) -------------------------------------------

def compare_price(item: dict, listings: list[dict] | None = None) -> dict:
    """
    Estimate whether an item's price is fair vs. comparable listings in the
    same category.

    Args:
        item:     The listing dict to assess.
        listings: Comparison pool; defaults to the full dataset.

    Returns:
        dict with keys: verdict ('great deal'|'fair'|'overpriced'|'unknown'),
        item_price (float), median (float|None), count (int), reasoning (str).
        Never raises / never divides by zero.
    """
    if listings is None:
        try:
            listings = load_listings()
        except Exception:
            listings = []

    category = item.get("category")
    item_price = float(item.get("price", 0) or 0)

    comps = [
        float(l["price"])
        for l in listings
        if l.get("category") == category
        and l.get("id") != item.get("id")
        and isinstance(l.get("price"), (int, float))
    ]

    if len(comps) < 2:
        return {
            "verdict": "unknown",
            "item_price": item_price,
            "median": None,
            "count": len(comps),
            "reasoning": (
                f"Not enough comparable {category or 'similar'} listings "
                "to judge whether this price is fair."
            ),
        }

    med = median(comps)
    # 10% bands around the category median.
    if item_price <= med * 0.9:
        verdict = "great deal"
    elif item_price <= med * 1.1:
        verdict = "fair"
    else:
        verdict = "overpriced"

    return {
        "verdict": verdict,
        "item_price": item_price,
        "median": round(med, 2),
        "count": len(comps),
        "reasoning": (
            f"${item_price:g} vs a median of ${med:g} across {len(comps)} "
            f"comparable {category} listings -- {verdict}."
        ),
    }


# -- Tool 5: get_trends (stretch) ----------------------------------------------

def get_trends(size: str | None = None, top_n: int = 5) -> list[str]:
    """
    Surface 'currently popular' style tags for a size range by ranking the most
    common style_tags among listings that fit that size.

    NOTE: This is a MOCK trend feed derived from the local listings dataset's
    tag frequency, standing in for a public fashion-platform trends API.

    Args:
        size:  Size to scope to (bidirectional case-insensitive match), or None
               for all listings.
        top_n: Number of trending tags to return.

    Returns:
        A list of trending style tags, most popular first. Empty list if the
        size range matches no listings.
    """
    try:
        listings = load_listings()
    except Exception:
        return []

    size_norm = size.strip().lower() if size else None
    counter: Counter = Counter()
    for l in listings:
        if size_norm:
            item_size = str(l.get("size", "")).lower()
            if size_norm not in item_size:
                continue
        counter.update(l.get("style_tags", []))

    return [tag for tag, _count in counter.most_common(top_n)]
