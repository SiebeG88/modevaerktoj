"""Tests for fletning af to spors segmenter til ét mærket transkript."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from transcript_merge import (merge_tracks, sanitize_segments, shift_segments, remove_bleed, build_transcript)


class TestMergeTracks:
    def test_interleaves_by_start_time(self):
        mic = [(0.0, 5.0, "Hej, godmorgen."), (10.0, 14.0, "Lad os starte.")]
        sys_ = [(5.0, 9.0, "Godmorgen, kan I høre mig?")]
        result = merge_tracks(mic, sys_)
        lines = result.splitlines()
        assert lines[0] == "[00:00 - 00:05] Mig: Hej, godmorgen."
        assert lines[1] == "[00:05 - 00:09] Modpart: Godmorgen, kan I høre mig?"
        assert lines[2] == "[00:10 - 00:14] Mig: Lad os starte."

    def test_only_mic_track(self):
        mic = [(0.0, 5.0, "Kun mig her.")]
        result = merge_tracks(mic, [])
        assert result == "[00:00 - 00:05] Mig: Kun mig her."

    def test_only_sys_track(self):
        sys_ = [(0.0, 5.0, "Kun modpart.")]
        result = merge_tracks([], sys_)
        assert result == "[00:00 - 00:05] Modpart: Kun modpart."

    def test_both_empty_returns_empty_string(self):
        assert merge_tracks([], []) == ""

    def test_stable_order_when_same_start(self):
        # Ved samme start-tid kommer mic før sys (deterministisk).
        mic = [(3.0, 4.0, "A")]
        sys_ = [(3.0, 4.0, "B")]
        result = merge_tracks(mic, sys_).splitlines()
        assert result[0] == "[00:03 - 00:04] Mig: A"
        assert result[1] == "[00:03 - 00:04] Modpart: B"


class TestSanitizeSegments:
    def test_clamps_to_duration(self):
        out = sanitize_segments([(3010.0, 4210.0, "langt fremme")], 2853.0)
        assert out == [(2853.0, 2853.0, "langt fremme")]

    def test_enforces_non_decreasing_start(self):
        out = sanitize_segments([(10.0, 12.0, "a"), (5.0, 7.0, "b")], 100.0)
        assert out[0][0] == 10.0
        assert out[1][0] == 10.0          # skubbet op til forrige start
        assert out[1][1] >= out[1][0]

    def test_repairs_overlong_span(self):
        out = sanitize_segments([(10.0, 400.0, "spaend")], 1000.0, max_segment_seconds=120.0)
        assert out == [(10.0, 130.0, "spaend")]

    def test_end_not_before_start(self):
        out = sanitize_segments([(50.0, 30.0, "omvendt")], 100.0)
        assert out[0][1] >= out[0][0]

    def test_empty(self):
        assert sanitize_segments([], 100.0) == []


class TestRemoveBleed:
    def test_drops_long_overlapping_copy(self):
        phrase = "det her er en lang saetning der er helt ens i begge spor"
        assert remove_bleed([(10.0, 16.0, phrase)], [(11.0, 17.0, phrase)]) == []

    def test_keeps_short_segment(self):
        assert remove_bleed([(10.0, 11.0, "Ja.")], [(10.0, 11.0, "Ja.")]) == [(10.0, 11.0, "Ja.")]

    def test_keeps_genuine_double_talk(self):
        mic = [(10.0, 16.0, "jeg synes vi skal starte med oekonomien nu")]
        sys = [(10.0, 16.0, "kan I overhovedet hoere mig derinde paa kontoret")]
        assert remove_bleed(mic, sys) == mic

    def test_keeps_when_no_time_overlap(self):
        phrase = "det her er en lang saetning der er helt ens i begge spor"
        assert remove_bleed([(10.0, 16.0, phrase)], [(100.0, 106.0, phrase)]) == [(10.0, 16.0, phrase)]

    def test_empty_sys_keeps_all(self):
        mic = [(10.0, 16.0, "uanset hvad skal det her blive staaende her")]
        assert remove_bleed(mic, []) == mic


class TestShiftSegments:
    def test_positive_offset(self):
        assert shift_segments([(1.0, 2.0, "x")], 5.0) == [(6.0, 7.0, "x")]

    def test_zero_offset(self):
        assert shift_segments([(1.0, 2.0, "x")], 0.0) == [(1.0, 2.0, "x")]

    def test_empty(self):
        assert shift_segments([], 5.0) == []


class TestBuildTranscript:
    def test_aligns_and_dedups_bleed(self):
        # sys (Modpart) er ren reference. mic startede 225 s senere end sys,
        # og samme ytring optræder i mic (bleed) ved 50 s og i sys ved 275 s.
        phrase = "vi er landbrug og digitalisering med tres medarbejdere i aarhus"
        mic = [(50.0, 60.0, phrase)]
        sys = [(275.0, 285.0, phrase)]
        out = build_transcript(
            mic, sys, mic_dur=300.0, sys_dur=600.0,
            mic_start=1225.0, sys_start=1000.0,
        )
        lines = out.splitlines()
        assert len(lines) == 1
        assert lines[0] == "[04:35 - 04:45] Modpart: " + phrase

    def test_mic_only(self):
        out = build_transcript(
            [(0.0, 5.0, "kun mig her")], [],
            mic_dur=10.0, sys_dur=0.0, mic_start=1000.0, sys_start=1000.0,
        )
        assert out == "[00:00 - 00:05] Mig: kun mig her"
