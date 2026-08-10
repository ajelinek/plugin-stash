#!/usr/bin/env python3
"""Tests for normalize_transcript.py's format detection, parsing, timing,
and stats.

Pure string-in/JSON-out logic -- no filesystem or network access needed, so
this suite runs offline and instantly.
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "skills" / "meeting-recap" / "scripts" / "normalize_transcript.py"
)

spec = importlib.util.spec_from_file_location("normalize_transcript", SCRIPT_PATH)
normalize_transcript = importlib.util.module_from_spec(spec)
sys.modules["normalize_transcript"] = normalize_transcript
spec.loader.exec_module(normalize_transcript)

VTT_SAMPLE = """WEBVTT

1
00:00:00.000 --> 00:00:03.500
Jane: Welcome everyone, thanks for joining.

2
00:00:03.500 --> 00:00:07.000
<v John>Happy to be here.</v>
"""

VTT_WITH_CUE_SETTINGS = """WEBVTT

1
00:00:00.000 --> 00:00:03.500 align:start position:0%
Jane: Welcome everyone.
"""

SRT_SAMPLE = """1
00:00:00,000 --> 00:00:03,500
Jane: Welcome everyone, thanks for joining.

2
00:00:03,500 --> 00:00:07,000
John: Happy to be here.
"""

LABELED_WITH_TIMESTAMPS = """0:00 Jane: Welcome everyone, thanks for joining today.
0:12 John: Happy to be here, glad we could sync up.
0:20 John: Let's get started on the agenda.
"""

LABELED_NO_TIMESTAMPS = """Jane: Welcome everyone, thanks for joining today.
John: Happy to be here.
John: Let's get started.
"""

PLAIN_TEXT = """This meeting covered a lot of ground without any clear speaker
labels, just a continuous block of prose describing what happened.

A second paragraph continues the same unstructured recounting of events.
"""


class DetectFormatTests(unittest.TestCase):
    def test_detects_vtt(self):
        self.assertEqual(normalize_transcript.detect_format(VTT_SAMPLE), "vtt")

    def test_detects_srt(self):
        self.assertEqual(normalize_transcript.detect_format(SRT_SAMPLE), "srt")

    def test_detects_labeled_with_timestamps(self):
        self.assertEqual(normalize_transcript.detect_format(LABELED_WITH_TIMESTAMPS), "labeled")

    def test_detects_labeled_without_timestamps(self):
        self.assertEqual(normalize_transcript.detect_format(LABELED_NO_TIMESTAMPS), "labeled")

    def test_detects_plain(self):
        self.assertEqual(normalize_transcript.detect_format(PLAIN_TEXT), "plain")


class ParseVttTests(unittest.TestCase):
    def test_extracts_colon_prefixed_speaker(self):
        segments = normalize_transcript.parse_vtt(VTT_SAMPLE)
        self.assertEqual(segments[0]["speaker"], "Jane")
        self.assertEqual(segments[0]["text"], "Welcome everyone, thanks for joining.")
        self.assertEqual(segments[0]["timestamp_sec"], 0.0)
        self.assertEqual(segments[0]["end_sec"], 3.5)

    def test_extracts_voice_tag_speaker(self):
        segments = normalize_transcript.parse_vtt(VTT_SAMPLE)
        self.assertEqual(segments[1]["speaker"], "John")
        self.assertEqual(segments[1]["text"], "Happy to be here.")
        self.assertEqual(segments[1]["timestamp_sec"], 3.5)
        self.assertEqual(segments[1]["end_sec"], 7.0)

    def test_ignores_trailing_cue_settings(self):
        segments = normalize_transcript.parse_vtt(VTT_WITH_CUE_SETTINGS)
        self.assertEqual(segments[0]["timestamp_sec"], 0.0)
        self.assertEqual(segments[0]["end_sec"], 3.5)


class ParseSrtTests(unittest.TestCase):
    def test_parses_two_cues(self):
        segments = normalize_transcript.parse_srt(SRT_SAMPLE)
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["speaker"], "Jane")
        self.assertEqual(segments[1]["speaker"], "John")
        self.assertEqual(segments[1]["timestamp_sec"], 3.5)
        self.assertEqual(segments[1]["end_sec"], 7.0)


class ParseLabeledTests(unittest.TestCase):
    def test_parses_leading_timestamp(self):
        segments = normalize_transcript.parse_labeled(LABELED_WITH_TIMESTAMPS)
        self.assertEqual(len(segments), 3)
        self.assertEqual(segments[0]["speaker"], "Jane")
        self.assertEqual(segments[0]["timestamp_sec"], 0.0)
        self.assertIsNone(segments[0]["end_sec"])
        self.assertEqual(segments[1]["timestamp_sec"], 12.0)

    def test_parses_without_timestamp(self):
        segments = normalize_transcript.parse_labeled(LABELED_NO_TIMESTAMPS)
        self.assertEqual(len(segments), 3)
        self.assertIsNone(segments[0]["timestamp_sec"])
        self.assertEqual(segments[0]["speaker"], "Jane")

    def test_wrapped_line_continues_previous_segment(self):
        text = "Jane: This is a long point that\nkeeps going on the next line.\nJohn: My turn now."
        segments = normalize_transcript.parse_labeled(text)
        self.assertEqual(len(segments), 2)
        self.assertEqual(
            segments[0]["text"], "This is a long point that keeps going on the next line."
        )


class ParsePlainTests(unittest.TestCase):
    def test_splits_into_unattributed_paragraphs(self):
        segments = normalize_transcript.parse_plain(PLAIN_TEXT)
        self.assertEqual(len(segments), 2)
        self.assertIsNone(segments[0]["speaker"])
        self.assertIsNone(segments[0]["timestamp_sec"])
        self.assertIsNone(segments[0]["end_sec"])


class ComputeStatsTests(unittest.TestCase):
    def test_flags_short_transcript(self):
        stats = normalize_transcript.compute_stats(
            [{"speaker": "Jane", "timestamp_sec": None, "text": "Hi there"}]
        )
        self.assertTrue(any("Very short transcript" in w for w in stats["warnings"]))

    def test_flags_missing_speakers(self):
        stats = normalize_transcript.compute_stats(normalize_transcript.parse_plain(PLAIN_TEXT))
        self.assertFalse(stats["has_speakers"])
        self.assertTrue(any("No speaker labels" in w for w in stats["warnings"]))

    def test_no_warnings_for_healthy_transcript(self):
        segments = normalize_transcript.parse_labeled(LABELED_WITH_TIMESTAMPS * 10)
        stats = normalize_transcript.compute_stats(segments)
        self.assertEqual(stats["warnings"], [])
        self.assertTrue(stats["has_speakers"])
        self.assertTrue(stats["has_timestamps"])

    def test_duration_falls_back_to_span_of_start_times_when_no_end_times_recorded(self):
        stats = normalize_transcript.compute_stats(
            normalize_transcript.parse_labeled(LABELED_WITH_TIMESTAMPS)
        )
        self.assertEqual(stats["duration_sec"], 20.0)
        self.assertEqual(stats["duration_basis"], "start_times_only")


class DurationCalculationTests(unittest.TestCase):
    """The crux of the sliding-ruler feature: duration must span the
    transcript's first recorded start time through its *last recorded end
    time*, not just its last recorded start time."""

    def test_vtt_duration_uses_last_cues_recorded_end_not_its_start(self):
        stats = normalize_transcript.compute_stats(normalize_transcript.parse_vtt(VTT_SAMPLE))
        # Last cue starts at 3.5s and ends at 7.0s. Using its start (the old,
        # wrong behavior) would give 3.5; the correct span is 7.0.
        self.assertEqual(stats["start_sec"], 0.0)
        self.assertEqual(stats["end_sec"], 7.0)
        self.assertEqual(stats["duration_sec"], 7.0)
        self.assertEqual(stats["duration_basis"], "recorded_end_times")

    def test_srt_duration_uses_recorded_end_times(self):
        stats = normalize_transcript.compute_stats(normalize_transcript.parse_srt(SRT_SAMPLE))
        self.assertEqual(stats["duration_sec"], 7.0)
        self.assertEqual(stats["duration_basis"], "recorded_end_times")

    def test_estimates_duration_from_word_count_when_no_timestamps(self):
        segments = normalize_transcript.parse_plain(PLAIN_TEXT * 3)
        stats = normalize_transcript.compute_stats(segments)
        self.assertEqual(stats["duration_basis"], "estimated_from_word_count")
        expected = (stats["word_count"] / normalize_transcript.ESTIMATED_WORDS_PER_MINUTE) * 60
        self.assertAlmostEqual(stats["duration_sec"], expected)
        self.assertTrue(any("rough estimate" in w for w in stats["warnings"]))

    def test_unknown_duration_when_no_timestamps_and_no_words(self):
        stats = normalize_transcript.compute_stats([])
        self.assertIsNone(stats["duration_sec"])
        self.assertEqual(stats["duration_basis"], "unknown")


class SessionBreakDetectionTests(unittest.TestCase):
    def test_flags_a_large_gap_between_segments(self):
        segments = [
            {"speaker": "A", "timestamp_sec": 0.0, "end_sec": 10.0,
             "text": "Morning session starts."},
            {"speaker": "B", "timestamp_sec": 10.0 + normalize_transcript.SESSION_GAP_THRESHOLD_SEC,
             "end_sec": None, "text": "Welcome back from the break."},
        ]
        breaks = normalize_transcript.detect_session_breaks(segments)
        self.assertEqual(len(breaks), 1)
        self.assertEqual(breaks[0]["after_segment_index"], 0)
        self.assertEqual(breaks[0]["before_segment_index"], 1)

    def test_no_breaks_for_a_continuous_conversation(self):
        segments = normalize_transcript.parse_labeled(LABELED_WITH_TIMESTAMPS)
        self.assertEqual(normalize_transcript.detect_session_breaks(segments), [])


class DayMarkerDetectionTests(unittest.TestCase):
    def test_detects_numeric_day_marker(self):
        segments = [{"speaker": "A", "timestamp_sec": None, "end_sec": None,
                     "text": "Welcome back to Day 2 of the offsite."}]
        hints = normalize_transcript.detect_day_markers(segments)
        self.assertEqual(len(hints), 1)
        self.assertEqual(hints[0]["matched_text"].lower(), "day 2")

    def test_detects_spelled_out_day_marker(self):
        segments = [{"speaker": "A", "timestamp_sec": None, "end_sec": None,
                     "text": "This is Day Three, let's continue."}]
        hints = normalize_transcript.detect_day_markers(segments)
        self.assertEqual(len(hints), 1)

    def test_no_false_positive_on_unrelated_mention(self):
        segments = [{"speaker": "A", "timestamp_sec": None, "end_sec": None,
                     "text": "Let's circle back in a day or two."}]
        self.assertEqual(normalize_transcript.detect_day_markers(segments), [])


class RecommendTierTests(unittest.TestCase):
    def test_ten_minutes_is_micro(self):
        tier = normalize_transcript.recommend_tier(600, "recorded_end_times", [])
        self.assertEqual(tier["name"], "micro")
        self.assertEqual(tier["structure"], "flat")

    def test_exactly_thirty_minutes_is_short_not_standard(self):
        tier = normalize_transcript.recommend_tier(30 * 60, "recorded_end_times", [])
        self.assertEqual(tier["name"], "short")

    def test_two_hours_is_extended(self):
        tier = normalize_transcript.recommend_tier(2 * 3600, "recorded_end_times", [])
        self.assertEqual(tier["name"], "extended")

    def test_four_hours_is_half_day_and_hierarchical(self):
        tier = normalize_transcript.recommend_tier(4 * 3600, "recorded_end_times", [])
        self.assertEqual(tier["name"], "half_day")
        self.assertEqual(tier["structure"], "hierarchical")
        self.assertEqual(tier["cluster_unit"], "sessions")

    def test_ten_hours_is_multi_day(self):
        tier = normalize_transcript.recommend_tier(10 * 3600, "recorded_end_times", [])
        self.assertEqual(tier["name"], "multi_day")

    def test_day_marker_hints_force_multi_day_even_if_short(self):
        tier = normalize_transcript.recommend_tier(
            600, "recorded_end_times", [{"segment_index": 0}]
        )
        self.assertEqual(tier["name"], "multi_day")

    def test_unknown_when_no_duration_available(self):
        tier = normalize_transcript.recommend_tier(None, "unknown", [])
        self.assertEqual(tier["name"], "unknown")

    def test_basis_is_passed_through(self):
        tier = normalize_transcript.recommend_tier(600, "estimated_from_word_count", [])
        self.assertEqual(tier["basis"], "estimated_from_word_count")


class NormalizeTests(unittest.TestCase):
    def test_end_to_end_vtt(self):
        result = normalize_transcript.normalize(VTT_SAMPLE)
        self.assertEqual(result["format_detected"], "vtt")
        self.assertEqual(len(result["segments"]), 2)
        self.assertIn("stats", result)
        self.assertEqual(result["stats"]["recommended_tier"]["name"], "micro")

    def test_format_override_skips_detection(self):
        result = normalize_transcript.normalize(LABELED_NO_TIMESTAMPS, format_override="plain")
        self.assertEqual(result["format_detected"], "plain")
        self.assertEqual(len(result["segments"]), 1)

    def test_every_transcript_segment_is_tagged_as_such(self):
        for sample in (VTT_SAMPLE, SRT_SAMPLE, LABELED_WITH_TIMESTAMPS, PLAIN_TEXT):
            for seg in normalize_transcript.normalize(sample)["segments"]:
                self.assertEqual(seg["source"], "transcript")


class ParseIsoDatetimeTests(unittest.TestCase):
    def test_parses_zulu_time(self):
        parsed = normalize_transcript.parse_iso_datetime("2026-08-07T14:00:00Z")
        self.assertEqual(parsed.hour, 14)
        self.assertEqual(parsed.utcoffset().total_seconds(), 0)

    def test_parses_fractional_seconds_and_offset(self):
        parsed = normalize_transcript.parse_iso_datetime("2026-08-07T14:00:00.123-05:00")
        self.assertEqual(parsed.microsecond, 123000)
        self.assertEqual(parsed.utcoffset().total_seconds(), -5 * 3600)

    def test_offset_without_colon(self):
        parsed = normalize_transcript.parse_iso_datetime("2026-08-07T14:00:00+0200")
        self.assertEqual(parsed.utcoffset().total_seconds(), 2 * 3600)

    def test_returns_none_for_junk(self):
        self.assertIsNone(normalize_transcript.parse_iso_datetime("last Tuesday"))
        self.assertIsNone(normalize_transcript.parse_iso_datetime("2026-13-45T99:00:00Z"))
        self.assertIsNone(normalize_transcript.parse_iso_datetime(None))


CHAT_JSON = json.dumps([
    {"sender": "Priya", "timestamp": "2026-08-07T14:00:05Z", "text": "Here's the deck link"},
    {"sender": "Dev", "timestamp": "2026-08-07T14:00:02Z", "text": "joining now"},
])

# Microsoft Graph's shape: nested body/from, HTML content, createdDateTime.
CHAT_GRAPH_JSON = json.dumps({
    "messages": [
        {
            "createdDateTime": "2026-08-07T14:00:04Z",
            "from": {"user": {"displayName": "Marcus"}},
            "body": {"contentType": "html", "content": "<p>I'll own the <b>rollout</b></p>"},
        }
    ]
})


class ParseChatTests(unittest.TestCase):
    def test_tags_source_and_reads_flat_json(self):
        segments = normalize_transcript.parse_chat(CHAT_JSON)
        self.assertEqual(len(segments), 2)
        self.assertTrue(all(s["source"] == "chat" for s in segments))
        self.assertEqual(segments[0]["speaker"], "Priya")

    def test_strips_html_and_unwraps_graph_shape(self):
        segments = normalize_transcript.parse_chat(CHAT_GRAPH_JSON)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["speaker"], "Marcus")
        self.assertEqual(segments[0]["text"], "I'll own the rollout")

    def test_anchor_converts_wall_clock_to_transcript_seconds(self):
        anchor = normalize_transcript.parse_iso_datetime("2026-08-07T14:00:00Z")
        segments = normalize_transcript.parse_chat(CHAT_JSON, anchor=anchor)
        self.assertEqual(segments[0]["timestamp_sec"], 5.0)
        self.assertEqual(segments[1]["timestamp_sec"], 2.0)

    def test_message_before_the_anchor_gets_a_negative_offset(self):
        anchor = normalize_transcript.parse_iso_datetime("2026-08-07T14:00:10Z")
        segments = normalize_transcript.parse_chat(CHAT_JSON, anchor=anchor)
        self.assertEqual(segments[0]["timestamp_sec"], -5.0)

    def test_without_anchor_timestamps_are_null_but_wall_clock_is_kept(self):
        segments = normalize_transcript.parse_chat(CHAT_JSON)
        self.assertIsNone(segments[0]["timestamp_sec"])
        self.assertIn("2026-08-07T14:00:05", segments[0]["sent_at"])

    def test_skips_teams_system_event_messages(self):
        """Shapes taken verbatim from a real Teams meeting chat listing."""
        raw = json.dumps({"messages": [
            {"messageType": "message", "from": "Jared Hall",
             "createdDateTime": "2026-07-11T13:24:38.917Z",
             "bodyPreview": "How do I download the transcript link?"},
            {"messageType": "unknownFutureValue", "from": "Unknown",
             "createdDateTime": "2026-07-09T21:24:59.602Z",
             "eventDetail": {"@odata.type": "#microsoft.graph.membersJoinedEventMessageDetail"},
             "bodyPreview": "<systemEventMessage/>"},
        ]})
        segments = normalize_transcript.parse_chat(raw)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["speaker"], "Jared Hall")

    def test_reads_flat_from_and_bodypreview_from_a_listing(self):
        raw = json.dumps([{"from": "Bill Walter", "bodyPreview": "sounds good"}])
        segments = normalize_transcript.parse_chat(raw)
        self.assertEqual(segments[0]["speaker"], "Bill Walter")
        self.assertEqual(segments[0]["text"], "sounds good")

    def test_falls_back_to_labeled_lines_for_non_json(self):
        segments = normalize_transcript.parse_chat("Priya: here's the link\nDev: thanks")
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["speaker"], "Priya")
        self.assertTrue(all(s["source"] == "chat" for s in segments))


class MergeTests(unittest.TestCase):
    """The merge is the one place chat and transcript touch, so these cover
    both that it interleaves correctly and that it refuses to guess when it
    can't."""

    def _merged(self, **kwargs):
        return normalize_transcript.normalize(VTT_SAMPLE, chat_text=CHAT_JSON, **kwargs)

    def test_chat_interleaves_chronologically_when_anchored(self):
        result = self._merged(chat_anchor="2026-08-07T14:00:00Z")
        self.assertEqual(result["stats"]["chat_alignment"], "anchored")
        # Transcript cues at 0.0 and 3.5; chat at 2.0 and 5.0.
        self.assertEqual(
            [(s["source"], s["timestamp_sec"]) for s in result["segments"]],
            [("transcript", 0.0), ("chat", 2.0), ("transcript", 3.5), ("chat", 5.0)],
        )

    def test_unanchored_chat_is_appended_not_interleaved(self):
        result = self._merged()
        self.assertEqual(result["stats"]["chat_alignment"], "unanchored")
        self.assertEqual(
            [s["source"] for s in result["segments"]],
            ["transcript", "transcript", "chat", "chat"],
        )

    def test_unanchored_chat_warns_that_order_is_unknown(self):
        warnings = " ".join(self._merged()["stats"]["warnings"])
        self.assertIn("could not be placed", warnings)

    def test_anchored_chat_warns_that_adjacency_is_approximate(self):
        warnings = " ".join(self._merged(chat_anchor="2026-08-07T14:00:00Z")["stats"]["warnings"])
        self.assertIn("approximate", warnings)

    def test_unparseable_anchor_is_reported_not_silently_ignored(self):
        result = self._merged(chat_anchor="last Tuesday")
        self.assertEqual(result["stats"]["chat_alignment"], "unanchored")
        self.assertIn("not a parseable ISO 8601", result["stats"]["warnings"][0])

    def test_transcript_without_timestamps_cannot_interleave(self):
        result = normalize_transcript.normalize(
            LABELED_NO_TIMESTAMPS, chat_text=CHAT_JSON, chat_anchor="2026-08-07T14:00:00Z"
        )
        self.assertEqual(result["stats"]["chat_alignment"], "unanchored")

    def test_no_chat_leaves_alignment_and_segments_untouched(self):
        result = normalize_transcript.normalize(VTT_SAMPLE)
        self.assertEqual(result["stats"]["chat_alignment"], "none")
        self.assertEqual(result["stats"]["chat_message_count"], 0)


class StatsPurityTests(unittest.TestCase):
    """Chat is merged for context only. If it ever leaks into the stats, a
    chatty meeting silently gets a longer recap tier than it earned -- these
    are the tests that stop that."""

    def _with_chat(self, chat_text, anchor="2026-08-07T14:00:00Z"):
        return normalize_transcript.normalize(VTT_SAMPLE, chat_text=chat_text, chat_anchor=anchor)

    def test_chat_does_not_change_duration_or_tier(self):
        baseline = normalize_transcript.normalize(VTT_SAMPLE)["stats"]
        merged = self._with_chat(CHAT_JSON)["stats"]
        self.assertEqual(merged["duration_sec"], baseline["duration_sec"])
        self.assertEqual(merged["recommended_tier"]["name"], baseline["recommended_tier"]["name"])
        self.assertEqual(merged["start_sec"], baseline["start_sec"])
        self.assertEqual(merged["end_sec"], baseline["end_sec"])

    def test_a_chat_message_hours_later_does_not_stretch_the_meeting(self):
        late = json.dumps([
            {"sender": "Priya", "timestamp": "2026-08-07T20:00:00Z", "text": "one more thing"}
        ])
        merged = self._with_chat(late)["stats"]
        self.assertEqual(merged["duration_sec"], 7.0)
        self.assertEqual(merged["recommended_tier"]["name"], "micro")
        self.assertEqual(merged["possible_session_breaks"], [])

    def test_chat_word_and_segment_counts_stay_separate(self):
        baseline = normalize_transcript.normalize(VTT_SAMPLE)["stats"]
        merged = self._with_chat(CHAT_JSON)["stats"]
        self.assertEqual(merged["word_count"], baseline["word_count"])
        self.assertEqual(merged["segment_count"], baseline["segment_count"])
        self.assertEqual(merged["chat_message_count"], 2)
        self.assertGreater(merged["chat_word_count"], 0)

    def test_chat_senders_are_listed_apart_from_transcript_speakers(self):
        merged = self._with_chat(CHAT_JSON)["stats"]
        self.assertEqual(merged["speakers"], ["Jane", "John"])
        self.assertEqual(merged["chat_senders"], ["Dev", "Priya"])

    def test_day_marker_typed_in_chat_does_not_force_multi_day(self):
        chat = json.dumps([
            {"sender": "Dev", "timestamp": "2026-08-07T14:00:02Z", "text": "see you on day 2"}
        ])
        merged = self._with_chat(chat)["stats"]
        self.assertEqual(merged["day_marker_hints"], [])
        self.assertEqual(merged["recommended_tier"]["name"], "micro")

    def test_reported_indices_point_into_the_merged_segments_list(self):
        transcript = "\n\n".join(
            f"{i}\n00:0{i}:00.000 --> 00:0{i}:01.000\nJane: day 2 planning" for i in range(3)
        )
        result = normalize_transcript.normalize(
            "WEBVTT\n\n" + transcript,
            chat_text=json.dumps([
                {"sender": "Dev", "timestamp": "2026-08-07T14:00:30Z", "text": "ok"}
            ]),
            chat_anchor="2026-08-07T14:00:00Z",
        )
        segments = result["segments"]
        self.assertEqual(segments[1]["source"], "chat")  # chat at 30s lands between cues
        for hint in result["stats"]["day_marker_hints"]:
            self.assertEqual(segments[hint["segment_index"]]["source"], "transcript")
            self.assertIn("day 2", segments[hint["segment_index"]]["text"])


if __name__ == "__main__":
    unittest.main()
