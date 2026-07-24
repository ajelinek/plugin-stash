"""Scoring and ranking of O*NET occupations against a student's RIASEC top
codes, stored interests, and/or a free-text query.

Deliberately not a vector/embedding search: the dataset is ~900 occupations
with an already-discrete RIASEC axis, so rank-order overlap on the top 2-3
Holland codes (Stage 1 in the PRD) plus a plain keyword-overlap score (for
free-text queries and stored interests) covers the matching need without a
vector DB or an embedding model to download and run locally. The keyword
corpus now also includes each occupation's top tasks and top work styles
(see onet_data.py), and free-text queries have real recall limits from
being literal-word matching, not semantic — see SKILL.md's "Free-text
search" section for the widen-then-judge-yourself technique that
compensates for that in conversation, using Claude's own reading of the
returned tasks/description rather than a second matching pipeline.

interest_terms (favorite subjects, clubs, sports, jobs/internships pulled
from the stored profile) are folded in as an additive nudge at a lower
weight than an explicit query -- never as an exclusionary filter.
GPA/ACT/SAT are deliberately NOT turned into a match filter or score
component here: there's no research-backed mapping from a high schooler's
current grades to which occupations they should be shown, and silently
excluding "reach" careers based on GPA would undercut the point of an
exploration tool aimed at students who haven't decided anything yet. They
stay in the profile as conversational context for Claude to use with
judgment (e.g. discussing what a Job Zone 5 path actually requires), not as
an automatic gate.
"""

from __future__ import annotations

import re
from typing import Any

from .onet_data import RIASEC_CODES, RIASEC_LABELS

_RANK_WEIGHTS = (3, 2, 1)

# Weight given to the RIASEC-overlap component relative to the keyword-overlap
# component when both are present, chosen so neither one component can be
# fully drowned out by the other (RIASEC overlap maxes out at 9, keyword
# overlap is roughly 0-2) rather than tuned against a labeled dataset.
_RIASEC_WEIGHT = 3.0
_DISLIKED_CATEGORY_PENALTY = 1.5

# Interest terms (favorite subjects/activities) are a weaker, noisier signal
# than an explicit query -- a club name or subject overlapping an
# occupation's skills/knowledge is suggestive, not a stated intent -- so it's
# weighted below keyword_score's usual ~0-2 range rather than on par with it.
_INTEREST_WEIGHT = 0.5

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def validate_riasec_codes(codes: list[str]) -> None:
    invalid = [c for c in codes if c not in RIASEC_CODES]
    if invalid:
        raise ValueError(
            f"riasec_codes contains invalid code(s) {invalid} — must each be one of "
            f"{list(RIASEC_CODES)}."
        )


def riasec_labels(codes: list[str]) -> list[str]:
    return [RIASEC_LABELS[c] for c in codes]


def riasec_overlap_score(student_top: list[str], occ_top: list[str]) -> float:
    score = 0.0
    for i, student_code in enumerate(student_top[:3]):
        for j, occ_code in enumerate(occ_top[:3]):
            if student_code == occ_code:
                score += _RANK_WEIGHTS[i] * _RANK_WEIGHTS[j]
    return score


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def keyword_score(query: str, occ: dict[str, Any]) -> float:
    query_tokens = _tokenize(query)
    if not query_tokens:
        return 0.0
    haystack = " ".join(
        [
            occ["title"],
            occ["description"],
            *occ.get("top_skills", []),
            *occ.get("top_knowledge", []),
            *occ.get("tasks", []),
            *occ.get("top_work_styles", []),
        ]
    )
    overlap = query_tokens & _tokenize(haystack)
    score = len(overlap) / len(query_tokens)
    if query.strip().lower() in occ["title"].lower():
        score += 1.0
    return score


def score_occupation(
    occ: dict[str, Any],
    *,
    riasec_codes: list[str] | None = None,
    query: str | None = None,
    interest_terms: list[str] | None = None,
) -> float:
    score = 0.0
    if riasec_codes:
        score += _RIASEC_WEIGHT * (riasec_overlap_score(riasec_codes, occ["top_codes"]) / 9.0)
    if query:
        score += keyword_score(query, occ)
    if interest_terms:
        score += _INTEREST_WEIGHT * keyword_score(" ".join(interest_terms), occ)
    return score


def summarize(occ: dict[str, Any], *, match_score: float | None = None) -> dict[str, Any]:
    summary = {
        "soc_code": occ["soc_code"],
        "title": occ["title"],
        "description": occ["description"],
        "riasec": occ["riasec"],
        "top_codes": occ["top_codes"],
        "top_codes_labels": riasec_labels(occ["top_codes"]),
        "job_zone": occ["job_zone"],
        "job_zone_label": occ["job_zone_label"],
        "typical_education": occ["typical_education"],
        "top_skills": occ["top_skills"],
        "top_knowledge": occ["top_knowledge"],
        "tasks": occ.get("tasks", []),
        "top_work_styles": occ.get("top_work_styles", []),
    }
    if match_score is not None:
        summary["match_score"] = round(match_score, 3)
    return summary


def rank_occupations(
    occupations: list[dict[str, Any]],
    *,
    riasec_codes: list[str] | None = None,
    query: str | None = None,
    interest_terms: list[str] | None = None,
    job_zone_max: int | None = None,
    exclude_soc_codes: set[str] | None = None,
    disliked_category_pairs: set[frozenset] | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    exclude_soc_codes = exclude_soc_codes or set()
    disliked_category_pairs = disliked_category_pairs or set()

    scored: list[tuple[float, dict[str, Any]]] = []
    for occ in occupations:
        if occ["soc_code"] in exclude_soc_codes:
            continue
        zone = occ["job_zone"]
        if job_zone_max is not None and zone is not None and zone > job_zone_max:
            continue
        score = score_occupation(
            occ, riasec_codes=riasec_codes, query=query, interest_terms=interest_terms
        )
        category = frozenset(occ["top_codes"][:2]) if len(occ["top_codes"]) >= 2 else None
        if category is not None and category in disliked_category_pairs:
            score -= _DISLIKED_CATEGORY_PENALTY
        scored.append((score, occ))

    scored.sort(key=lambda pair: (-pair[0], pair[1]["title"]))
    return [summarize(occ, match_score=score) for score, occ in scored[:limit]]
