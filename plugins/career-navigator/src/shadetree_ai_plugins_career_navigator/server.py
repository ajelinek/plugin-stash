"""FastMCP server bundled with the shadetree-ai-plugins 'career-navigator' plugin.

A local, single-student career-exploration tool for high schoolers. Claude
runs the actual conversation (a conversational RIASEC/Holland Code interview,
then academic/activity questions — see skills/career-navigator/SKILL.md); this
server only validates and persists what Claude has already inferred, and
ranks/searches the bundled local O*NET occupation dataset. No network calls,
no external services, no accounts: everything lives in two local JSON files
under ~/.shadetree-ai-plugins/career-navigator/ plus the read-only bundled dataset.

- `career_status` — session-start preflight: profile/preferences state + what
  to ask about next.
- `career_update_profile` — merge new RIASEC/academic/activity signal onto the
  stored profile.
- `career_update_preferences` — append a freeform note.
- `career_search` — ad-hoc O*NET search by RIASEC codes and/or free text.
- `career_record_feedback` — log a reaction to one presented career.
- `career_rank_matches` — the primary shortlist, combining the stored profile
  and preferences.

See skills/career-navigator/references/onet-data.md for the dataset's
provenance/license and skills/career-navigator/references/riasec-interview.md
for the conversational technique and question bank.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from fastmcp import FastMCP
from shadetree_ai_plugins_common import get_logger

from .matching import rank_occupations, riasec_labels, validate_riasec_codes
from .onet_data import RIASEC_CODES, find_by_soc_code, load_occupations
from .preferences_store import (
    REACTIONS,
    append_career_exploration,
    append_general_note,
    disliked_top_code_pairs,
    load_preferences,
)
from .profile_store import (
    CONFIDENCE_LEVELS,
    compute_completeness,
    compute_top_codes,
    interest_terms,
    load_profile,
    next_step_hint,
    profile_path,
    save_profile,
)

logger = get_logger("shadetree-ai-plugins-career-navigator")

mcp = FastMCP(
    name="shadetree-ai-plugins-career-navigator",
    instructions=(
        "Conversational career exploration for a high school student, fully local: no "
        "accounts, no hosting, no network calls. Two local JSON files hold the student's "
        "state (RIASEC/academic/activity profile, and freeform preferences/career "
        "reactions); a bundled O*NET 30.3-derived dataset (923 occupations) is searched "
        "and ranked in-process. Call career_status first each session. The RIASEC "
        "profile itself is Claude's own conversational inference, not a scored "
        "psychometric instrument — see the career-navigator skill for the interview "
        "technique before asking questions."
    ),
    mask_error_details=False,
)


_READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
_WRITE_LOCAL = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
}

_ACADEMICS_KEYS = {"gpa", "act_score", "sat_score", "favorite_subjects", "grade_level"}
_ACTIVITIES_KEYS = {"clubs", "sports", "jobs_or_internships"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@mcp.tool(annotations=_READ_ONLY)
def career_status() -> dict:
    """Call this first in a session. Loads (or reports as not-yet-started) the
    student's profile and preferences, reports the local O*NET dataset size,
    and returns a next_step hint for what to ask about next — so a returning
    student is never re-asked what's already known."""
    profile = load_profile()
    profile["profile_completeness"] = compute_completeness(profile)
    prefs = load_preferences()
    return {
        "profile": profile,
        "profile_exists_on_disk": os.path.exists(profile_path()),
        "career_explorations_count": len(prefs["career_explorations"]),
        "general_notes_count": len(prefs["general_notes"]),
        "occupation_dataset_count": len(load_occupations()),
        "next_step": next_step_hint(profile["profile_completeness"]),
    }


@mcp.tool(annotations=_WRITE_LOCAL)
def career_update_profile(
    riasec_scores: dict[str, float] | None = None,
    riasec_confidence: str | None = None,
    academics: dict[str, Any] | None = None,
    activities: dict[str, list[str]] | None = None,
    activities_reviewed: bool | None = None,
) -> dict:
    """Record what you've learned about the student so far. Call this
    repeatedly across the conversation as your understanding firms up, not
    just once at the end — each call merges onto the existing profile; omit
    anything you don't have new information on.

    riasec_scores: your own best-estimate intensity per Holland dimension, on
    a 0-100 scale (not O*NET's internal 1-7 scale used for occupations) — e.g.
    {"I": 80, "A": 65}. Pass only the letters you have signal on; letters
    aren't mutually exclusive percentages, a student can score high on more
    than one. Later calls overwrite only the letters you include.

    riasec_confidence: "low" | "medium" | "high" — your own confidence in the
    scores so far. The profile isn't RIASEC-complete until this is "medium" or
    "high" and all six letters have a score.

    academics: any of gpa, act_score, sat_score, favorite_subjects (list of
    str), grade_level.

    activities: any of clubs, sports, jobs_or_internships, each a list of str.

    activities_reviewed: pass true once you've explicitly asked about
    activities and the student reports having none — otherwise an empty
    activities section looks identical to "never asked"."""
    if not any(
        v is not None
        for v in (riasec_scores, riasec_confidence, academics, activities, activities_reviewed)
    ):
        raise ValueError(
            "Pass at least one of riasec_scores, riasec_confidence, academics, activities, "
            "or activities_reviewed."
        )

    profile = load_profile()

    if riasec_scores:
        for code, value in riasec_scores.items():
            if code not in RIASEC_CODES:
                raise ValueError(f"riasec_scores key {code!r} must be one of {list(RIASEC_CODES)}.")
            score = float(value)
            if not 0 <= score <= 100:
                raise ValueError(f"riasec_scores[{code!r}]={value} must be between 0 and 100.")
            profile["riasec"]["scores"][code] = score
        profile["riasec"]["top_codes"] = compute_top_codes(profile["riasec"]["scores"])
        profile["riasec"]["last_updated"] = _now_iso()

    if riasec_confidence is not None:
        if riasec_confidence not in CONFIDENCE_LEVELS:
            raise ValueError(f"riasec_confidence must be one of {list(CONFIDENCE_LEVELS)}.")
        profile["riasec"]["confidence"] = riasec_confidence

    if academics:
        unknown = set(academics) - _ACADEMICS_KEYS
        if unknown:
            raise ValueError(
                f"Unknown academics field(s): {sorted(unknown)}. "
                f"Allowed: {sorted(_ACADEMICS_KEYS)}."
            )
        profile["academics"].update(academics)

    if activities:
        unknown = set(activities) - _ACTIVITIES_KEYS
        if unknown:
            raise ValueError(
                f"Unknown activities field(s): {sorted(unknown)}. "
                f"Allowed: {sorted(_ACTIVITIES_KEYS)}."
            )
        for key, value in activities.items():
            if not isinstance(value, list):
                raise ValueError(f"activities[{key!r}] must be a list of strings.")
        profile["activities"].update(activities)

    if activities_reviewed is not None:
        profile["activities"]["reviewed"] = activities_reviewed

    profile = save_profile(profile)
    return {"profile": profile, "next_step": next_step_hint(profile["profile_completeness"])}


@mcp.tool(annotations=_WRITE_LOCAL)
def career_update_preferences(note: str) -> dict:
    """Append a freeform observation that doesn't fit the structured profile —
    e.g. 'wants to stay near home for college', 'seemed unsure about a 4-year
    commitment'. Appended and timestamped, never overwritten."""
    if not note or not note.strip():
        raise ValueError("note must not be empty.")
    prefs = append_general_note(f"[{_now_iso()}] {note.strip()}")
    return {
        "general_notes_count": len(prefs["general_notes"]),
        "general_notes": prefs["general_notes"],
    }


@mcp.tool(annotations=_READ_ONLY)
def career_search(
    query: str | None = None,
    riasec_codes: list[str] | None = None,
    job_zone_max: int | None = None,
    limit: int = 10,
) -> dict:
    """Ad-hoc search over the local O*NET occupation dataset — for a specific
    question mid-conversation ("what about jobs that mix music and
    computers"), separate from career_rank_matches's use of the student's own
    stored profile. Pass at least one of query (free text matched against
    title/description/skills/knowledge/tasks/work styles) or riasec_codes (up
    to 3 Holland codes, most-preferred first). job_zone_max optionally caps
    results to occupations at or below that O*NET Job Zone (1=least, 5=most
    preparation needed).

    match_score is literal keyword overlap, not semantic understanding — it
    will miss a real match phrased differently than O*NET's own wording (e.g.
    "helping animals" won't surface "Veterinarians" on words alone). If a
    query that should plausibly have hits comes back empty or thin, re-call
    with a notably higher limit (20-30) and no riasec_codes filter, then read
    the returned description/tasks/top_skills yourself and judge fit — you
    have far better semantic understanding of what the student means than
    this token-overlap score does. Treat the returned order as a recall net,
    not the final ranking."""
    if not query and not riasec_codes:
        raise ValueError("Pass at least one of query or riasec_codes.")
    if riasec_codes:
        validate_riasec_codes(riasec_codes)

    results = rank_occupations(
        load_occupations(),
        riasec_codes=riasec_codes,
        query=query,
        job_zone_max=job_zone_max,
        limit=limit,
    )
    return {"results": results, "count": len(results)}


@mcp.tool(annotations=_WRITE_LOCAL)
def career_record_feedback(
    soc_code: str,
    reaction: str,
    notes: str | None = None,
    title: str | None = None,
) -> dict:
    """Log the student's reaction to one specific presented career. Call this
    right after presenting a career and hearing the student's response — it
    feeds career_rank_matches's personalization (similar career categories the
    student has already disliked get deprioritized, even if RIASEC alone would
    suggest a fit). reaction must be one of liked/disliked/neutral/unspecified.
    title is only needed if soc_code isn't in the local dataset (e.g. a career
    discussed outside career_search/career_rank_matches results)."""
    if reaction not in REACTIONS:
        raise ValueError(f"reaction must be one of {list(REACTIONS)}.")
    occ = find_by_soc_code(soc_code)
    if occ is None and not title:
        raise ValueError(
            f"{soc_code!r} isn't in the local O*NET dataset and no title was given — pass "
            f"the title shown in the career_search/career_rank_matches result you're "
            f"reacting to."
        )
    entry = {
        "onet_soc_code": soc_code,
        "title": occ["title"] if occ else title,
        "presented_at": _now_iso(),
        "student_reaction": reaction,
        "notes": notes or "",
    }
    prefs = append_career_exploration(entry)
    return {"recorded": entry, "career_explorations_count": len(prefs["career_explorations"])}


@mcp.tool(annotations=_READ_ONLY)
def career_rank_matches(
    limit: int = 5,
    job_zone_max: int | None = None,
    include_previously_shown: bool = False,
) -> dict:
    """The primary shortlist tool: combines the student's stored RIASEC top
    codes, their favorite subjects/clubs/sports/jobs (as a lower-weighted
    keyword nudge, not a filter), and their career_explorations history
    (deprioritizing/excluding what they've already reacted to) to rank the
    local O*NET dataset. Returns a soft error (not a raised exception) with a
    next_step hint if the RIASEC profile isn't complete yet — finish the
    conversational interview first. By default excludes soc_codes already
    logged via career_record_feedback so the same career isn't re-presented;
    pass include_previously_shown=true to allow repeats.

    match_score is rank-order RIASEC overlap plus keyword overlap — a coarse
    recall signal, not a semantic judgment. GPA/test scores are intentionally
    not applied as a filter here (see the career-navigator skill for why);
    use job_zone_max instead if the conversation has surfaced a real
    preparation-level constraint."""
    profile = load_profile()
    completeness = compute_completeness(profile)
    if not completeness["riasec"]:
        return {
            "ranked": [],
            "count": 0,
            "error": "RIASEC profile isn't complete yet.",
            "next_step": next_step_hint(completeness),
        }

    prefs = load_preferences()
    occupations = load_occupations()
    occupations_by_soc = {occ["soc_code"]: occ for occ in occupations}

    exclude_soc_codes = (
        set()
        if include_previously_shown
        else {e["onet_soc_code"] for e in prefs["career_explorations"]}
    )
    disliked_pairs = disliked_top_code_pairs(prefs, occupations_by_soc)

    top_codes = profile["riasec"]["top_codes"]
    ranked = rank_occupations(
        occupations,
        riasec_codes=top_codes,
        interest_terms=interest_terms(profile),
        job_zone_max=job_zone_max,
        exclude_soc_codes=exclude_soc_codes,
        disliked_category_pairs=disliked_pairs,
        limit=limit,
    )
    return {
        "ranked": ranked,
        "count": len(ranked),
        "based_on_top_codes": top_codes,
        "based_on_top_codes_labels": riasec_labels(top_codes),
    }
