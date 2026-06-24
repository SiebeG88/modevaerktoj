"""Tests for fletning af to spors segmenter til ét mærket transkript."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from transcript_merge import merge_tracks


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
