"""
profile.py  (stretch feature: style profile memory)

Persists a small style profile across sessions so a returning user does not have
to re-describe their taste. Stored as JSON at data/style_profile.json (gitignored).

Functions:
    load_profile()           -> dict   {"style_prefs": [...], "seen_items": int}
    save_profile(profile)    -> None
    update_profile(tags)     -> dict    merge new style tags, persist, return it

Failure mode: a missing or corrupt file yields an empty default profile -- this
module never raises on load.
"""

import json
import os

_PROFILE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "style_profile.json")

_DEFAULT = {"style_prefs": [], "seen_items": 0}


def load_profile() -> dict:
    """Return the saved profile, or an empty default if missing/corrupt."""
    try:
        with open(_PROFILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # normalize shape
        return {
            "style_prefs": list(data.get("style_prefs", [])),
            "seen_items": int(data.get("seen_items", 0)),
        }
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
        return dict(_DEFAULT)


def save_profile(profile: dict) -> None:
    """Persist the profile to disk. Silently no-ops on write failure."""
    try:
        with open(_PROFILE_PATH, "w", encoding="utf-8") as f:
            json.dump(profile, f, indent=2)
    except OSError:
        pass


def update_profile(tags: list[str], max_prefs: int = 12) -> dict:
    """
    Merge `tags` into the saved style_prefs (most-recent-first, de-duplicated,
    capped), increment the seen counter, persist, and return the updated profile.
    """
    profile = load_profile()
    prefs = list(profile.get("style_prefs", []))
    for tag in tags:
        t = (tag or "").strip().lower()
        if not t:
            continue
        if t in prefs:
            prefs.remove(t)
        prefs.insert(0, t)
    profile["style_prefs"] = prefs[:max_prefs]
    profile["seen_items"] = int(profile.get("seen_items", 0)) + 1
    save_profile(profile)
    return profile
