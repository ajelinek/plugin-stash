"""Reads/writes `student_preferences.json` — append-only freeform memory:
reactions to specific presented careers, plus general notes that don't fit
`student_profile.json`'s fixed schema. Never overwritten wholesale; only
ever appended to.
"""

from __future__ import annotations

import json
import os
from typing import Any

from shadetree_ai_plugins_common import state_dir

REACTIONS = ("liked", "disliked", "neutral", "unspecified")


def preferences_path() -> str:
    override = os.environ.get("SHADETREE_AI_PLUGINS_CAREER_NAVIGATOR_PREFERENCES_PATH")
    if override:
        return override
    return str(state_dir("career-navigator") / "student_preferences.json")


def default_preferences() -> dict[str, Any]:
    return {"career_explorations": [], "general_notes": []}


def load_preferences() -> dict[str, Any]:
    path = preferences_path()
    if not os.path.exists(path):
        return default_preferences()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return default_preferences()
    prefs = default_preferences()
    if isinstance(data.get("career_explorations"), list):
        prefs["career_explorations"] = data["career_explorations"]
    if isinstance(data.get("general_notes"), list):
        prefs["general_notes"] = data["general_notes"]
    return prefs


def save_preferences(prefs: dict[str, Any]) -> dict[str, Any]:
    path = preferences_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(prefs, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return prefs


def append_general_note(note: str) -> dict[str, Any]:
    prefs = load_preferences()
    prefs["general_notes"].append(note)
    return save_preferences(prefs)


def append_career_exploration(entry: dict[str, Any]) -> dict[str, Any]:
    prefs = load_preferences()
    prefs["career_explorations"].append(entry)
    return save_preferences(prefs)


def disliked_top_code_pairs(
    prefs: dict[str, Any], occupations_by_soc: dict[str, Any]
) -> set[frozenset]:
    """Two-code 'category' signatures (e.g. {I, A}) of occupations the student
    has already disliked — used to deprioritize similar categories even when
    RIASEC alone would suggest a fit (PRD Stage 3 personalization)."""
    pairs: set[frozenset] = set()
    for entry in prefs["career_explorations"]:
        if entry.get("student_reaction") != "disliked":
            continue
        occ = occupations_by_soc.get(entry.get("onet_soc_code"))
        if occ and len(occ.get("top_codes", [])) >= 2:
            pairs.add(frozenset(occ["top_codes"][:2]))
    return pairs
