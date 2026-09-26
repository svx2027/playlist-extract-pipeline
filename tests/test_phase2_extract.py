"""Tests for the pure, credential-free logic in src/phase2_extract.py: the
classification rule, safe filename derivation, status mapping, transcript
language/format selection, garbage-payload detection, and the two caption
parsers. None of this touches the network, yt-dlp, or ffmpeg.

Run:
    .venv/bin/python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import phase2_extract as p2  # noqa: E402


class SafeNameTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(p2.safe_name("My Study Playlist", 1), "01_My_Study_Playlist")

    def test_strips_punctuation(self):
        self.assertEqual(p2.safe_name("Recipes to try!!", 3), "03_Recipes_to_try")

    def test_none_title_falls_back_to_playlist(self):
        self.assertEqual(p2.safe_name(None, 4), "04_playlist")

    def test_only_punctuation_falls_back_to_playlist(self):
        self.assertEqual(p2.safe_name("!!!@@@", 2), "02_playlist")

    def test_truncates_to_50_chars(self):
        title = "a" * 80
        self.assertEqual(p2.safe_name(title, 5), "05_" + "a" * 50)

    def test_pads_number_to_two_digits(self):
        self.assertTrue(p2.safe_name("x", 7).startswith("07_"))


class ClassifyTests(unittest.TestCase):
    def test_vertical_short_duration_is_short(self):
        fmt, dur, w, h = p2.classify({"duration": 90, "width": 720, "height": 1280})
        self.assertEqual((fmt, dur, w, h), ("short", 90, 720, 1280))

    def test_vertical_long_duration_is_long(self):
        fmt, _, _, _ = p2.classify({"duration": 300, "width": 720, "height": 1280})
        self.assertEqual(fmt, "long")

    def test_landscape_short_duration_is_long(self):
        fmt, _, _, _ = p2.classify({"duration": 90, "width": 1280, "height": 720})
        self.assertEqual(fmt, "long")

    def test_uses_aspect_ratio_when_no_dimensions(self):
        fmt, _, _, _ = p2.classify({"duration": 60, "aspect_ratio": 0.5625})
        self.assertEqual(fmt, "short")

    def test_aspect_ratio_landscape_is_long(self):
        fmt, _, _, _ = p2.classify({"duration": 60, "aspect_ratio": 1.78})
        self.assertEqual(fmt, "long")

    def test_missing_duration_is_long_even_if_vertical(self):
        fmt, _, _, _ = p2.classify({"width": 720, "height": 1280})
        self.assertEqual(fmt, "long")

    def test_no_dimensions_or_ratio_is_long(self):
        fmt, _, _, _ = p2.classify({"duration": 30})
        self.assertEqual(fmt, "long")


class StatusFromMetaTests(unittest.TestCase):
    def test_public_passthrough(self):
        self.assertEqual(p2.status_from_meta({"availability": "public"}), "public")

    def test_premium_only_maps_to_members_only(self):
        self.assertEqual(p2.status_from_meta({"availability": "premium_only"}), "members_only")

    def test_subscriber_only_maps_to_members_only(self):
        self.assertEqual(p2.status_from_meta({"availability": "subscriber_only"}), "members_only")

    def test_needs_auth_maps_to_private(self):
        self.assertEqual(p2.status_from_meta({"availability": "needs_auth"}), "private")

    def test_needs_subscription_maps_to_members_only(self):
        self.assertEqual(p2.status_from_meta({"availability": "needs_subscription"}), "members_only")

    def test_missing_availability_defaults_public(self):
        self.assertEqual(p2.status_from_meta({}), "public")

    def test_unmapped_value_passes_through_raw(self):
        self.assertEqual(p2.status_from_meta({"availability": "some_new_youtube_status"}),
                          "some_new_youtube_status")


class StatusFromErrorTests(unittest.TestCase):
    def test_private(self):
        self.assertEqual(p2.status_from_error("Video is private"), "private")

    def test_members_only_hyphenated(self):
        self.assertEqual(p2.status_from_error("This video is members-only content"), "members_only")

    def test_join_this_channel(self):
        self.assertEqual(
            p2.status_from_error("Join this channel to get access to members-only perks"),
            "members_only")

    def test_removed(self):
        self.assertEqual(p2.status_from_error("This video has been removed"), "unavailable")

    def test_no_longer_available(self):
        self.assertEqual(p2.status_from_error("Video is no longer available"), "unavailable")

    def test_empty_defaults_unavailable(self):
        self.assertEqual(p2.status_from_error(""), "unavailable")
        self.assertEqual(p2.status_from_error(None), "unavailable")

    def test_unrecognized_text_defaults_unavailable(self):
        self.assertEqual(p2.status_from_error("some transient network hiccup"), "unavailable")


class ChooseSubTests(unittest.TestCase):
    def test_prefers_original_language_manual(self):
        meta = {"language": "hi", "subtitles": {"hi": ["fmtA"], "en": ["fmtB"]}}
        self.assertEqual(p2.choose_sub(meta), ("hi", "manual", ["fmtA"]))

    def test_falls_back_to_english_manual(self):
        meta = {"language": None, "subtitles": {"en": ["fmtA"], "fr": ["fmtB"]}}
        self.assertEqual(p2.choose_sub(meta), ("en", "manual", ["fmtA"]))

    def test_falls_back_to_automatic_when_no_manual(self):
        meta = {"language": "es", "subtitles": {}, "automatic_captions": {"es": ["a"], "en": ["b"]}}
        self.assertEqual(p2.choose_sub(meta), ("es", "auto", ["a"]))

    def test_falls_back_to_sorted_first_key(self):
        meta = {"language": None, "subtitles": {"zh": ["x"], "fr": ["y"]}}
        self.assertEqual(p2.choose_sub(meta), ("fr", "manual", ["y"]))

    def test_filters_pseudo_tracks(self):
        meta = {"language": None, "subtitles": {"live_chat": ["x"]}, "automatic_captions": {}}
        self.assertEqual(p2.choose_sub(meta), (None, "none", None))

    def test_no_subs_at_all(self):
        self.assertEqual(p2.choose_sub({}), (None, "none", None))


class PickFmtTests(unittest.TestCase):
    def test_prefers_json3_over_vtt(self):
        formats = [{"ext": "vtt", "url": "u1"}, {"ext": "json3", "url": "u2"}]
        self.assertEqual(p2.pick_fmt(formats), ("u2", "json3"))

    def test_falls_back_through_priority_order(self):
        formats = [{"ext": "srv1", "url": "u1"}]
        self.assertEqual(p2.pick_fmt(formats), ("u1", "srv1"))

    def test_falls_back_to_first_format_when_no_known_ext(self):
        formats = [{"ext": "xyz", "url": "u1"}]
        self.assertEqual(p2.pick_fmt(formats), ("u1", "xyz"))

    def test_empty_list(self):
        self.assertEqual(p2.pick_fmt([]), (None, None))

    def test_no_urls_returns_none(self):
        formats = [{"ext": "json3", "url": None}]
        self.assertEqual(p2.pick_fmt(formats), (None, None))


class LooksGarbageTests(unittest.TestCase):
    def test_empty_is_false(self):
        self.assertFalse(p2._looks_garbage(""))
        self.assertFalse(p2._looks_garbage(None))

    def test_single_marker_is_false(self):
        self.assertFalse(p2._looks_garbage("some function(x) call"))

    def test_two_markers_is_true(self):
        self.assertTrue(p2._looks_garbage("ytcfg.set(...) Polymer widget"))

    def test_doctype_alone_is_true(self):
        self.assertTrue(p2._looks_garbage("<!DOCTYPE html><html></html>"))

    def test_clean_transcript_is_false(self):
        self.assertFalse(
            p2._looks_garbage("Welcome back to the channel, today we talk about numbers."))


class ParseJson3Tests(unittest.TestCase):
    def test_basic_concat(self):
        data = {"events": [{"segs": [{"utf8": "Hello "}]}, {"segs": [{"utf8": "world"}]}]}
        self.assertEqual(p2.parse_json3(json.dumps(data)), "Hello\nworld")

    def test_dedups_consecutive_duplicates(self):
        data = {"events": [{"segs": [{"utf8": "same"}]},
                            {"segs": [{"utf8": "same"}]},
                            {"segs": [{"utf8": "diff"}]}]}
        self.assertEqual(p2.parse_json3(json.dumps(data)), "same\ndiff")

    def test_skips_events_without_segs(self):
        data = {"events": [{"id": 1}, {"segs": [{"utf8": "text"}]}]}
        self.assertEqual(p2.parse_json3(json.dumps(data)), "text")

    def test_normalizes_whitespace_across_segs(self):
        data = {"events": [{"segs": [{"utf8": "Hello\n"}, {"utf8": "  world"}]}]}
        self.assertEqual(p2.parse_json3(json.dumps(data)), "Hello world")


class ParseVttTests(unittest.TestCase):
    def test_strips_header_timestamps_and_tags(self):
        text = (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:02.000\n"
            "Hello <b>world</b>\n\n"
            "00:00:02.000 --> 00:00:03.000\n"
            "Hello <b>world</b>\n\n"
            "1\n"
            "00:00:03.000 --> 00:00:04.000\n"
            "Goodbye\n"
        )
        self.assertEqual(p2.parse_vtt(text), "Hello world\nGoodbye")

    def test_skips_metadata_lines(self):
        text = (
            "WEBVTT\n"
            "Kind: captions\n"
            "Language: en\n\n"
            "NOTE this is a note\n"
            "STYLE\n"
            "::cue { color: red; }\n\n"
            "00:00:01.000 --> 00:00:02.000\n"
            "Real caption line\n"
        )
        self.assertEqual(p2.parse_vtt(text), "Real caption line")


if __name__ == "__main__":
    unittest.main()
