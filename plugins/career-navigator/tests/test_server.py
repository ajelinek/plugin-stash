"""Tests for the shadetree-ai-plugins-career-navigator FastMCP server, against a small
synthetic O*NET-shaped dataset — no dependency on the real bundled dataset's
exact contents, so these stay stable across future dataset rebuilds.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastmcp import Client
from shadetree_ai_plugins_career_navigator import server as srv
from shadetree_ai_plugins_career_navigator.matching import (
    keyword_score,
    riasec_overlap_score,
    score_occupation,
    validate_riasec_codes,
)
from shadetree_ai_plugins_career_navigator.profile_store import (
    compute_completeness,
    compute_top_codes,
    default_profile,
    interest_terms,
)

# --------------------------------------------------------------------------------
# Pure-function tests (no fixtures needed)
# --------------------------------------------------------------------------------


class TestComputeTopCodes:
    def test_ranks_by_score_descending(self):
        assert compute_top_codes({"R": 10, "I": 80, "A": 50, "S": 40, "E": 30, "C": 70}) == [
            "I",
            "C",
            "A",
        ]

    def test_ties_broken_by_riasec_order(self):
        assert compute_top_codes({"R": 50, "I": 50}) == ["R", "I"]

    def test_fewer_than_three_scored_letters(self):
        assert compute_top_codes({"S": 40, "E": 90}) == ["E", "S"]

    def test_empty_scores(self):
        assert compute_top_codes({}) == []


class TestComputeCompleteness:
    def _profile(self, **overrides):
        from shadetree_ai_plugins_career_navigator.profile_store import default_profile

        profile = default_profile()
        for section, values in overrides.items():
            profile[section].update(values)
        return profile

    def test_all_incomplete_by_default(self):
        completeness = compute_completeness(self._profile())
        assert completeness == {"riasec": False, "academics": False, "activities": False}

    def test_riasec_incomplete_with_low_confidence_even_if_all_scored(self):
        profile = self._profile(
            riasec={"scores": dict.fromkeys("RIASEC", 50), "confidence": "low"}
        )
        assert compute_completeness(profile)["riasec"] is False

    def test_riasec_complete_with_all_six_and_medium_confidence(self):
        profile = self._profile(
            riasec={"scores": dict.fromkeys("RIASEC", 50), "confidence": "medium"}
        )
        assert compute_completeness(profile)["riasec"] is True

    def test_academics_requires_grade_level_and_a_favorite_subject(self):
        profile = self._profile(academics={"grade_level": "10th", "favorite_subjects": ["Art"]})
        assert compute_completeness(profile)["academics"] is True
        profile2 = self._profile(academics={"grade_level": "10th"})
        assert compute_completeness(profile2)["academics"] is False

    def test_activities_complete_via_reviewed_flag_with_no_lists(self):
        profile = self._profile(activities={"reviewed": True})
        assert compute_completeness(profile)["activities"] is True

    def test_activities_complete_via_any_populated_list(self):
        profile = self._profile(activities={"clubs": ["Robotics"]})
        assert compute_completeness(profile)["activities"] is True


class TestRiasecOverlapScore:
    def test_top_rank_match_scores_highest(self):
        assert riasec_overlap_score(["I", "A", "S"], ["I", "C", "E"]) == 9

    def test_no_overlap_scores_zero(self):
        assert riasec_overlap_score(["R", "C", "E"], ["I", "A", "S"]) == 0

    def test_partial_overlap_at_different_ranks(self):
        # student's #1 (I, weight 3) matches occ's #2 (weight 2) -> 6
        assert riasec_overlap_score(["I", "A", "S"], ["C", "I", "E"]) == 6


class TestKeywordScore:
    def _occ(self):
        return {
            "title": "Electricians",
            "description": "Install, maintain, and repair electrical wiring and equipment.",
            "top_skills": ["Troubleshooting"],
            "top_knowledge": ["Building and Construction"],
        }

    def test_title_substring_gives_bonus(self):
        # "electrician" hits the title-substring bonus with no token overlap
        # (tokenizer treats "electrician" and "electricians" as distinct words);
        # "electrical systems" gets partial token overlap but no title bonus.
        assert keyword_score("electrician", self._occ()) > keyword_score(
            "electrical systems", self._occ()
        )

    def test_no_overlap_scores_zero(self):
        assert keyword_score("underwater basket weaving", self._occ()) == 0.0

    def test_empty_query_scores_zero(self):
        assert keyword_score("", self._occ()) == 0.0

    def test_missing_tasks_and_work_styles_keys_dont_raise(self):
        # Real records post-enrichment always have these keys, but older/
        # synthetic occupations (like this fixture and the test dataset
        # below) don't -- keyword_score must tolerate that.
        assert keyword_score("electrician", self._occ()) >= 0.0

    def test_tasks_and_work_styles_are_part_of_the_searchable_corpus(self):
        occ = self._occ()
        occ["tasks"] = ["Splice wires together using knives, pliers, and tape."]
        occ["top_work_styles"] = ["Attention to Detail"]
        assert keyword_score("splice wires", occ) > 0
        assert keyword_score("attention to detail", occ) > 0


class TestScoreOccupationInterestTerms:
    def _occ(self):
        return {
            "title": "Actuaries",
            "description": "Analyze statistical data to estimate probability and cost of an event.",
            "top_codes": ["C", "I", "E"],
            "top_skills": ["Mathematics", "Critical Thinking"],
            "top_knowledge": ["Mathematics", "Economics and Accounting"],
        }

    def test_interest_terms_add_a_smaller_boost_than_an_equivalent_query(self):
        occ = self._occ()
        boosted = score_occupation(occ, interest_terms=["Mathematics"])
        queried = score_occupation(occ, query="Mathematics")
        assert 0 < boosted < queried

    def test_unrelated_interest_terms_add_nothing(self):
        occ = self._occ()
        assert score_occupation(occ, interest_terms=["underwater basket weaving"]) == 0.0

    def test_interest_terms_never_exclude_when_no_riasec_or_query(self):
        # No riasec_codes/query passed at all -- interest_terms alone should
        # never raise or force a zero/negative score for a real overlap.
        occ = self._occ()
        assert score_occupation(occ, interest_terms=["Mathematics", "Economics"]) > 0


class TestInterestTermsHelper:
    def test_empty_profile_returns_empty_list(self):
        assert interest_terms(default_profile()) == []

    def test_combines_favorite_subjects_and_all_activity_kinds(self):
        profile = default_profile()
        profile["academics"]["favorite_subjects"] = ["Chemistry"]
        profile["activities"]["clubs"] = ["Robotics Club"]
        profile["activities"]["sports"] = ["Track"]
        profile["activities"]["jobs_or_internships"] = ["Lab intern"]
        assert interest_terms(profile) == ["Chemistry", "Robotics Club", "Track", "Lab intern"]

    def test_gpa_and_test_scores_are_never_included(self):
        # Deliberate: GPA/ACT/SAT aren't a validated signal for which
        # occupations to surface, so they must never leak into the
        # keyword-matching interest terms.
        profile = default_profile()
        profile["academics"]["gpa"] = 2.1
        profile["academics"]["act_score"] = 15
        assert interest_terms(profile) == []


class TestValidateRiasecCodes:
    def test_valid_codes_pass(self):
        validate_riasec_codes(["R", "I", "A"])

    def test_invalid_code_raises(self):
        with pytest.raises(ValueError):
            validate_riasec_codes(["R", "X"])


# --------------------------------------------------------------------------------
# Fixture: synthetic O*NET dataset + isolated profile/preferences files
# --------------------------------------------------------------------------------

SYNTHETIC_OCCUPATIONS = [
    {
        "soc_code": "15-1252.00",
        "title": "Software Developers",
        "description": "Develop, create, and modify general computer applications software.",
        "riasec": {"R": 2, "I": 7, "A": 4, "S": 2, "E": 3, "C": 5},
        "top_codes": ["I", "C", "A"],
        "job_zone": 4,
        "job_zone_label": "Job Zone Four: Considerable Preparation Needed",
        "typical_education": "Bachelor's Degree",
        "top_skills": ["Programming", "Critical Thinking"],
        "top_knowledge": ["Computers and Electronics", "Mathematics"],
    },
    {
        "soc_code": "27-1024.00",
        "title": "Graphic Designers",
        "description": "Design or create graphics for product illustrations, logos, and websites.",
        "riasec": {"R": 1, "I": 2, "A": 7, "S": 3, "E": 4, "C": 2},
        "top_codes": ["A", "E", "S"],
        "job_zone": 3,
        "job_zone_label": "Job Zone Three: Medium Preparation Needed",
        "typical_education": "Associate's Degree",
        "top_skills": ["Design", "Active Listening"],
        "top_knowledge": ["Design", "Fine Arts"],
    },
    {
        "soc_code": "21-1021.00",
        "title": "Social Workers",
        "description": "Provide social services to help clients resolve personal problems.",
        "riasec": {"R": 1, "I": 3, "A": 2, "S": 7, "E": 3, "C": 2},
        "top_codes": ["S", "I", "E"],
        "job_zone": 5,
        "job_zone_label": "Job Zone Five: Extensive Preparation Needed",
        "typical_education": "Master's Degree",
        "top_skills": ["Active Listening", "Service Orientation"],
        "top_knowledge": ["Psychology", "Therapy and Counseling"],
    },
    {
        "soc_code": "47-2111.00",
        "title": "Electricians",
        "description": "Install, maintain, and repair electrical wiring and equipment.",
        "riasec": {"R": 7, "I": 3, "A": 1, "S": 2, "E": 2, "C": 4},
        "top_codes": ["R", "C", "I"],
        "job_zone": 3,
        "job_zone_label": "Job Zone Three: Medium Preparation Needed",
        "typical_education": "High School Diploma - or the equivalent",
        "top_skills": ["Installation", "Troubleshooting"],
        "top_knowledge": ["Building and Construction", "Engineering and Technology"],
    },
    {
        "soc_code": "15-2011.00",
        "title": "Actuaries",
        "description": "Analyze statistical data to estimate probability and cost of an event.",
        "riasec": {"R": 1, "I": 6, "A": 1, "S": 2, "E": 3, "C": 7},
        "top_codes": ["C", "I", "E"],
        "job_zone": 4,
        "job_zone_label": "Job Zone Four: Considerable Preparation Needed",
        "typical_education": "Bachelor's Degree",
        "top_skills": ["Mathematics", "Critical Thinking"],
        "top_knowledge": ["Mathematics", "Economics and Accounting"],
    },
]


@pytest.fixture
def fixtures(tmp_path, monkeypatch):
    dataset_path = tmp_path / "onet_occupations.json"
    dataset_path.write_text(json.dumps(SYNTHETIC_OCCUPATIONS))

    monkeypatch.setenv("SHADETREE_AI_PLUGINS_CAREER_NAVIGATOR_ONET_PATH", str(dataset_path))
    monkeypatch.setenv(
        "SHADETREE_AI_PLUGINS_CAREER_NAVIGATOR_PROFILE_PATH", str(tmp_path / "student_profile.json")
    )
    monkeypatch.setenv(
        "SHADETREE_AI_PLUGINS_CAREER_NAVIGATOR_PREFERENCES_PATH",
        str(tmp_path / "student_preferences.json"),
    )
    return {"tmp_path": tmp_path}


def _riasec_complete_scores() -> dict:
    return {"I": 80, "C": 70, "A": 50, "S": 40, "E": 30, "R": 20}


# --------------------------------------------------------------------------------
# career_status
# --------------------------------------------------------------------------------


async def test_career_status_before_any_profile_exists(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("career_status", {})
        assert r.data["profile_exists_on_disk"] is False
        assert r.data["occupation_dataset_count"] == len(SYNTHETIC_OCCUPATIONS)
        assert "riasec" in r.data["next_step"].lower()


async def test_career_status_after_profile_update(fixtures):
    async with Client(srv.mcp) as client:
        await client.call_tool(
            "career_update_profile",
            {"riasec_scores": _riasec_complete_scores(), "riasec_confidence": "medium"},
        )
        r = await client.call_tool("career_status", {})
        assert r.data["profile_exists_on_disk"] is True
        assert r.data["profile"]["profile_completeness"]["riasec"] is True


# --------------------------------------------------------------------------------
# career_update_profile
# --------------------------------------------------------------------------------


async def test_update_profile_requires_at_least_one_field(fixtures):
    async with Client(srv.mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool("career_update_profile", {})


async def test_update_profile_merges_across_calls(fixtures):
    async with Client(srv.mcp) as client:
        await client.call_tool("career_update_profile", {"riasec_scores": {"I": 80}})
        r = await client.call_tool("career_update_profile", {"riasec_scores": {"A": 60}})
        scores = r.data["profile"]["riasec"]["scores"]
        assert scores == {"I": 80, "A": 60}


async def test_update_profile_invalid_riasec_key_raises(fixtures):
    async with Client(srv.mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool("career_update_profile", {"riasec_scores": {"X": 50}})


async def test_update_profile_out_of_range_score_raises(fixtures):
    async with Client(srv.mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool("career_update_profile", {"riasec_scores": {"I": 150}})


async def test_update_profile_invalid_confidence_raises(fixtures):
    async with Client(srv.mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool("career_update_profile", {"riasec_confidence": "extreme"})


async def test_update_profile_unknown_academics_field_raises(fixtures):
    async with Client(srv.mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool(
                "career_update_profile", {"academics": {"favorite_color": "blue"}}
            )


async def test_update_profile_activities_must_be_lists(fixtures):
    async with Client(srv.mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool("career_update_profile", {"activities": {"clubs": "Robotics"}})


async def test_update_profile_activities_reviewed_flag(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("career_update_profile", {"activities_reviewed": True})
        assert r.data["profile"]["activities"]["reviewed"] is True
        assert r.data["profile"]["profile_completeness"]["activities"] is True


# --------------------------------------------------------------------------------
# career_update_preferences
# --------------------------------------------------------------------------------


async def test_update_preferences_appends_notes(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool(
            "career_update_preferences", {"note": "wants to stay near home for college"}
        )
        assert r.data["general_notes_count"] == 1
        assert "wants to stay near home" in r.data["general_notes"][0]


async def test_update_preferences_empty_note_raises(fixtures):
    async with Client(srv.mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool("career_update_preferences", {"note": "   "})


# --------------------------------------------------------------------------------
# career_search
# --------------------------------------------------------------------------------


async def test_search_requires_query_or_riasec_codes(fixtures):
    async with Client(srv.mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool("career_search", {})


async def test_search_by_riasec_codes_ranks_matching_occupation_first(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("career_search", {"riasec_codes": ["A", "S", "E"]})
        assert r.data["results"][0]["soc_code"] == "27-1024.00"  # Graphic Designers


async def test_search_by_keyword_finds_title_match(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("career_search", {"query": "electrician"})
        assert r.data["results"][0]["soc_code"] == "47-2111.00"


async def test_search_job_zone_max_excludes_higher_zones(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool(
            "career_search", {"riasec_codes": ["S", "I", "E"], "job_zone_max": 4}
        )
        socs = [x["soc_code"] for x in r.data["results"]]
        assert "21-1021.00" not in socs  # Social Workers is job_zone 5


async def test_search_invalid_riasec_code_raises(fixtures):
    async with Client(srv.mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool("career_search", {"riasec_codes": ["Z"]})


async def test_search_results_include_tasks_and_work_styles_fields(fixtures):
    # Synthetic dataset has no tasks/top_work_styles keys at all -- results
    # must still include the fields (defaulting to []), same as real,
    # enriched occupations do.
    async with Client(srv.mcp) as client:
        r = await client.call_tool("career_search", {"riasec_codes": ["A", "S", "E"]})
        top = r.data["results"][0]
        assert top["tasks"] == []
        assert top["top_work_styles"] == []


# --------------------------------------------------------------------------------
# career_record_feedback
# --------------------------------------------------------------------------------


async def test_record_feedback_known_soc_code_fills_in_title(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool(
            "career_record_feedback", {"soc_code": "15-1252.00", "reaction": "liked"}
        )
        assert r.data["recorded"]["title"] == "Software Developers"


async def test_record_feedback_unknown_soc_code_without_title_raises(fixtures):
    async with Client(srv.mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool(
                "career_record_feedback", {"soc_code": "99-9999.00", "reaction": "liked"}
            )


async def test_record_feedback_unknown_soc_code_with_title_succeeds(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool(
            "career_record_feedback",
            {"soc_code": "99-9999.00", "reaction": "neutral", "title": "Some Future Job"},
        )
        assert r.data["recorded"]["title"] == "Some Future Job"


async def test_record_feedback_invalid_reaction_raises(fixtures):
    async with Client(srv.mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool(
                "career_record_feedback", {"soc_code": "15-1252.00", "reaction": "meh"}
            )


# --------------------------------------------------------------------------------
# career_rank_matches
# --------------------------------------------------------------------------------


async def test_rank_matches_before_riasec_complete_returns_soft_error(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("career_rank_matches", {})
        assert r.data["ranked"] == []
        assert "error" in r.data


async def test_rank_matches_ranks_by_stored_top_codes(fixtures):
    async with Client(srv.mcp) as client:
        await client.call_tool(
            "career_update_profile",
            {"riasec_scores": _riasec_complete_scores(), "riasec_confidence": "high"},
        )
        r = await client.call_tool("career_rank_matches", {})
        assert r.data["based_on_top_codes"] == ["I", "C", "A"]
        assert r.data["ranked"][0]["soc_code"] == "15-1252.00"  # Software Developers


async def test_rank_matches_excludes_previously_shown_by_default(fixtures):
    async with Client(srv.mcp) as client:
        await client.call_tool(
            "career_update_profile",
            {"riasec_scores": _riasec_complete_scores(), "riasec_confidence": "high"},
        )
        await client.call_tool(
            "career_record_feedback", {"soc_code": "15-1252.00", "reaction": "disliked"}
        )
        r = await client.call_tool("career_rank_matches", {})
        socs = [x["soc_code"] for x in r.data["ranked"]]
        assert "15-1252.00" not in socs


async def test_rank_matches_include_previously_shown_allows_repeat(fixtures):
    async with Client(srv.mcp) as client:
        await client.call_tool(
            "career_update_profile",
            {"riasec_scores": _riasec_complete_scores(), "riasec_confidence": "high"},
        )
        await client.call_tool(
            "career_record_feedback", {"soc_code": "15-1252.00", "reaction": "disliked"}
        )
        r = await client.call_tool(
            "career_rank_matches", {"include_previously_shown": True}
        )
        socs = [x["soc_code"] for x in r.data["ranked"]]
        assert "15-1252.00" in socs


async def test_rank_matches_uses_favorite_subjects_as_interest_signal(fixtures):
    async with Client(srv.mcp) as client:
        await client.call_tool(
            "career_update_profile",
            {"riasec_scores": _riasec_complete_scores(), "riasec_confidence": "high"},
        )
        without = await client.call_tool("career_rank_matches", {"limit": 10})
        score_without = next(
            x["match_score"] for x in without.data["ranked"] if x["soc_code"] == "15-2011.00"
        )

        await client.call_tool(
            "career_update_profile", {"academics": {"favorite_subjects": ["Mathematics"]}}
        )
        with_interest = await client.call_tool("career_rank_matches", {"limit": 10})
        score_with = next(
            x["match_score"] for x in with_interest.data["ranked"] if x["soc_code"] == "15-2011.00"
        )
        # Actuaries' top_skills/top_knowledge both list Mathematics.
        assert score_with > score_without


async def test_rank_matches_deprioritizes_similar_category_after_dislike(fixtures):
    async with Client(srv.mcp) as client:
        await client.call_tool(
            "career_update_profile",
            {"riasec_scores": _riasec_complete_scores(), "riasec_confidence": "high"},
        )
        before = await client.call_tool(
            "career_rank_matches", {"include_previously_shown": True, "limit": 10}
        )
        before_score = next(
            x["match_score"] for x in before.data["ranked"] if x["soc_code"] == "15-2011.00"
        )

        # Software Developers (top_codes I, C, A -> category {I, C}) disliked;
        # Actuaries (top_codes C, I, E -> category {C, I}) shares that category.
        await client.call_tool(
            "career_record_feedback", {"soc_code": "15-1252.00", "reaction": "disliked"}
        )
        after = await client.call_tool(
            "career_rank_matches", {"include_previously_shown": True, "limit": 10}
        )
        after_score = next(
            x["match_score"] for x in after.data["ranked"] if x["soc_code"] == "15-2011.00"
        )
        assert after_score < before_score


# --------------------------------------------------------------------------------
# No-network guard
# --------------------------------------------------------------------------------


def test_server_has_no_network_calls():
    src_dir = (
        Path(__file__).resolve().parent.parent / "src" / "shadetree_ai_plugins_career_navigator"
    )
    banned_imports = re.compile(
        r"^\s*(import|from)\s+(requests|socket|urllib\d*|http\.client|httplib)\b", re.MULTILINE
    )
    for path in src_dir.glob("*.py"):
        source = path.read_text()
        assert not banned_imports.search(source), f"found a banned network-related import in {path}"
