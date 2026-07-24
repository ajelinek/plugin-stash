"""Reads/writes the single local `student_profile.json` — the structured,
known-shape half of this plugin's memory (see preferences_store.py for the
freeform half). Single-user, single-file by design: multi-student support is
an explicit Phase-1 non-goal, so there is no student_id to key on.
"""

from __future__ import annotations

import json
import os
from typing import Any

from shadetree_ai_plugins_common import state_dir

from .onet_data import RIASEC_CODES

CONFIDENCE_LEVELS = ("low", "medium", "high")


def profile_path() -> str:
    override = os.environ.get("SHADETREE_AI_PLUGINS_CAREER_NAVIGATOR_PROFILE_PATH")
    if override:
        return override
    return str(state_dir("career-navigator") / "student_profile.json")


def default_profile() -> dict[str, Any]:
    return {
        "riasec": {
            "scores": {},
            "top_codes": [],
            "confidence": "low",
            "last_updated": None,
        },
        "academics": {
            "gpa": None,
            "act_score": None,
            "sat_score": None,
            "favorite_subjects": [],
            "grade_level": None,
        },
        "activities": {
            "clubs": [],
            "sports": [],
            "jobs_or_internships": [],
            # Not part of the original schema sketch: tracks "asked about
            # activities and the student had none to report" separately from
            # "never asked yet", since both otherwise look like empty lists.
            "reviewed": False,
        },
        "profile_completeness": {
            "riasec": False,
            "academics": False,
            "activities": False,
        },
    }


def load_profile() -> dict[str, Any]:
    path = profile_path()
    if not os.path.exists(path):
        return default_profile()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return default_profile()
    profile = default_profile()
    for section in ("riasec", "academics", "activities"):
        if isinstance(data.get(section), dict):
            profile[section].update(data[section])
    return profile


def interest_terms(profile: dict[str, Any]) -> list[str]:
    """Free-text signal derived from the stored profile's favorite subjects
    and activities (clubs/sports/jobs_or_internships) -- fed into matching.
    rank_occupations as an additive nudge (matching.py's interest_terms
    param), never as an exclusionary filter. GPA/act_score/sat_score are
    deliberately excluded from this: they're academic-achievement numbers,
    not a validated signal for which occupations to filter out, and this is
    an exploration tool for students who haven't decided anything yet --
    they stay in the profile as conversational context only.
    """
    academics = profile.get("academics") or {}
    activities = profile.get("activities") or {}
    terms = list(academics.get("favorite_subjects") or [])
    for key in ("clubs", "sports", "jobs_or_internships"):
        terms.extend(activities.get(key) or [])
    return terms


def compute_top_codes(scores: dict[str, float]) -> list[str]:
    scored = [c for c in RIASEC_CODES if c in scores]
    scored.sort(key=lambda c: (-scores[c], RIASEC_CODES.index(c)))
    return scored[:3]


def compute_completeness(profile: dict[str, Any]) -> dict[str, bool]:
    riasec = profile["riasec"]
    riasec_complete = (
        all(c in riasec["scores"] for c in RIASEC_CODES)
        and riasec["confidence"] in ("medium", "high")
    )
    academics = profile["academics"]
    academics_complete = (
        academics.get("grade_level") is not None and bool(academics.get("favorite_subjects"))
    )
    activities = profile["activities"]
    activities_complete = bool(
        activities.get("clubs")
        or activities.get("sports")
        or activities.get("jobs_or_internships")
        or activities.get("reviewed")
    )
    return {
        "riasec": riasec_complete,
        "academics": academics_complete,
        "activities": activities_complete,
    }


def save_profile(profile: dict[str, Any]) -> dict[str, Any]:
    # profile_completeness is always recomputed here, never trusted from the
    # caller — it's a derived view of the rest of the profile, not real state.
    profile["profile_completeness"] = compute_completeness(profile)
    path = profile_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return profile


def next_step_hint(completeness: dict[str, bool]) -> str:
    if not completeness["riasec"]:
        return (
            "RIASEC profile not complete yet — continue the conversational interview "
            "(see the career-navigator skill for question themes and tone) and call "
            "career_update_profile with your current best-estimate scores as they firm up."
        )
    if not completeness["academics"]:
        return (
            "Ask about grade level and favorite subjects (GPA/test scores are optional "
            "extras, not required) and record them with career_update_profile."
        )
    if not completeness["activities"]:
        return (
            "Ask about clubs, sports, or jobs/internships. If the student has none, "
            "call career_update_profile(activities_reviewed=true) rather than leaving "
            "this unasked."
        )
    return (
        "Profile complete — call career_rank_matches to get a shortlist, or career_search "
        "for a specific question."
    )
