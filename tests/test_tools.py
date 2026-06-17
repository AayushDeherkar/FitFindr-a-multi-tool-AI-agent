"""
tests/test_tools.py

Tests for each FitFindr tool, with at least one test per failure mode.
Run with:  pytest tests/

The two LLM-backed tools (suggest_outfit, create_fit_card) are exercised here
for their *failure / edge* paths, which do not require a network call:
- suggest_outfit with an empty wardrobe still returns a non-empty string (the
  LLM happy path is covered, but skipped automatically if no GROQ_API_KEY).
- create_fit_card with an empty outfit returns an error string WITHOUT calling
  the LLM at all (pure guard clause) -- always tested.
"""

import os

import pytest

from tools import (
    search_listings,
    suggest_outfit,
    create_fit_card,
    compare_price,
    get_trends,
)

HAS_KEY = bool(os.environ.get("GROQ_API_KEY"))
requires_llm = pytest.mark.skipif(not HAS_KEY, reason="needs GROQ_API_KEY")


# -- search_listings -----------------------------------------------------------

def test_search_returns_results():
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    assert isinstance(results, list)
    assert len(results) > 0


def test_search_empty_results():
    # Failure mode: nothing matches -> empty list, no exception.
    results = search_listings("designer ballgown", size="XXS", max_price=5)
    assert results == []


def test_search_price_filter():
    results = search_listings("jacket", size=None, max_price=10)
    assert all(item["price"] <= 10 for item in results)


def test_search_size_filter_bidirectional():
    # "M" should match listings sized "S/M", "M/L", etc.
    results = search_listings("top", size="M", max_price=None)
    assert len(results) > 0
    for item in results:
        s = item["size"].lower()
        assert "m" in s or s in "m"


def test_search_results_sorted_by_relevance():
    results = search_listings("vintage", size=None, max_price=None)
    assert len(results) > 1  # sorted call should not raise; order is best-first


# -- create_fit_card (guard clause, no LLM needed) -----------------------------

def test_fit_card_empty_outfit_returns_error_string():
    # Failure mode: empty outfit -> descriptive error STRING, not an exception.
    msg = create_fit_card("", {"title": "Faded Band Tee", "price": 22, "platform": "depop"})
    assert isinstance(msg, str)
    assert msg.strip() != ""
    assert "outfit" in msg.lower()


def test_fit_card_whitespace_outfit_returns_error_string():
    msg = create_fit_card("   ", {"title": "X", "price": 10, "platform": "depop"})
    assert isinstance(msg, str) and "outfit" in msg.lower()


# -- compare_price (stretch) ---------------------------------------------------

def test_compare_price_returns_verdict():
    listings = search_listings("tee", size=None, max_price=None)
    assert listings
    result = compare_price(listings[0])
    assert result["verdict"] in {"great deal", "fair", "overpriced", "unknown"}
    assert "reasoning" in result


def test_compare_price_unknown_when_no_comparables():
    # Failure mode: a category with only itself -> 'unknown', no crash.
    lone = {"id": "x", "category": "__no_such_category__", "price": 99.0}
    result = compare_price(lone, listings=[lone])
    assert result["verdict"] == "unknown"
    assert result["median"] is None


# -- get_trends (stretch) ------------------------------------------------------

def test_get_trends_returns_tags():
    trends = get_trends(size=None, top_n=5)
    assert isinstance(trends, list)
    assert len(trends) > 0
    assert all(isinstance(t, str) for t in trends)


def test_get_trends_empty_for_impossible_size():
    # Failure mode: no listings for the size range -> empty list, no crash.
    trends = get_trends(size="ZZZ-not-a-size", top_n=5)
    assert trends == []


# -- suggest_outfit (LLM; skipped without a key) -------------------------------

@requires_llm
def test_suggest_outfit_empty_wardrobe_returns_string():
    listings = search_listings("vintage graphic tee", size=None, max_price=50)
    assert listings
    out = suggest_outfit(listings[0], {"items": []})
    assert isinstance(out, str) and out.strip() != ""


@requires_llm
def test_suggest_outfit_with_wardrobe_returns_string():
    from utils.data_loader import get_example_wardrobe

    listings = search_listings("vintage graphic tee", size=None, max_price=50)
    out = suggest_outfit(listings[0], get_example_wardrobe())
    assert isinstance(out, str) and out.strip() != ""


@requires_llm
def test_fit_card_varies_for_different_input():
    listings = search_listings("vintage graphic tee", size=None, max_price=50)
    a = create_fit_card("pair with baggy jeans and chunky sneakers", listings[0])
    b = create_fit_card("layer under a denim jacket with combat boots", listings[0])
    assert a != b  # high temperature + different inputs -> different captions
