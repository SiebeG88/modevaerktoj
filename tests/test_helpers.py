"""Tests for pure funktioner i meeting_tool — ingen mocks nødvendige."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Vi importerer modulet for at få adgang til private helpers
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_tool as mt


# ----- format_timestamp -----

class TestFormatTimestamp:
    def test_zero(self):
        assert mt.format_timestamp(0) == "00:00"

    def test_under_minute(self):
        assert mt.format_timestamp(45) == "00:45"

    def test_exact_minute(self):
        assert mt.format_timestamp(60) == "01:00"

    def test_minutes_and_seconds(self):
        assert mt.format_timestamp(125) == "02:05"

    def test_exact_hour_uses_h_format(self):
        assert mt.format_timestamp(3600) == "1:00:00"

    def test_hour_minute_second(self):
        assert mt.format_timestamp(3661) == "1:01:01"

    def test_float_input_truncates(self):
        assert mt.format_timestamp(59.9) == "00:59"


# ----- _offset_transcript -----

class TestOffsetTranscript:
    def test_empty_input(self):
        assert mt._offset_transcript("", 60) == ""

    def test_zero_offset_unchanged(self):
        text = "[00:10 - 00:20] Hej"
        assert mt._offset_transcript(text, 0) == text

    def test_mm_ss_offset(self):
        text = "[00:10 - 00:20] Hej"
        assert mt._offset_transcript(text, 60) == "[01:10 - 01:20] Hej"

    def test_offset_crosses_hour_boundary(self):
        text = "[55:00 - 56:00] tale"
        # 55:00 + 6 min = 1:01:00; 56:00 + 6 min = 1:02:00
        result = mt._offset_transcript(text, 6 * 60)
        assert result == "[1:01:00 - 1:02:00] tale"

    def test_line_without_timestamp_passes_through(self):
        text = "Bare en linje uden timestamp"
        assert mt._offset_transcript(text, 60) == text

    def test_multiple_lines(self):
        text = "[00:00 - 00:05] a\n[00:05 - 00:10] b"
        expected = "[01:00 - 01:05] a\n[01:05 - 01:10] b"
        assert mt._offset_transcript(text, 60) == expected


# ----- _postprocess_transcript -----

class TestPostprocessTranscript:
    def test_mats_to_mads(self):
        assert mt._postprocess_transcript("Mats kom forbi") == "Mads kom forbi"

    def test_mats_substring_unchanged(self):
        assert mt._postprocess_transcript("Matsen området") == "Matsen området"

    def test_mats_at_end_of_sentence(self):
        # Mats i slutningen af en sætning (efterfulgt af punktum)
        assert mt._postprocess_transcript("Vi ventede på Mats.") == "Vi ventede på Mads."

    def test_mats_unchanged_when_no_match(self):
        # Tekst uden Mats forbliver uændret
        assert mt._postprocess_transcript("Dorte kom forbi") == "Dorte kom forbi"

    def test_empty_input(self):
        assert mt._postprocess_transcript("") == ""

    def test_multiple_replacements(self):
        text = "Mats mødte Mats bror"
        assert mt._postprocess_transcript(text) == "Mads mødte Mads bror"


# ----- _load_env_file -----

class TestLoadEnvFile:
    def test_loads_simple_keys(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("FOO=bar\nBAZ=qux\n", encoding="utf-8")
        monkeypatch.setattr(mt, "CONFIG_DIR", tmp_path)
        monkeypatch.delenv("FOO", raising=False)
        monkeypatch.delenv("BAZ", raising=False)

        mt._load_env_file()

        assert os.environ["FOO"] == "bar"
        assert os.environ["BAZ"] == "qux"

    def test_skips_comments_and_blanks(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("# kommentar\n\nKEY=value\n", encoding="utf-8")
        monkeypatch.setattr(mt, "CONFIG_DIR", tmp_path)
        monkeypatch.delenv("KEY", raising=False)

        mt._load_env_file()

        assert os.environ["KEY"] == "value"

    def test_strips_quotes(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text('QUOTED="hello world"\n', encoding="utf-8")
        monkeypatch.setattr(mt, "CONFIG_DIR", tmp_path)
        monkeypatch.delenv("QUOTED", raising=False)

        mt._load_env_file()

        assert os.environ["QUOTED"] == "hello world"

    def test_does_not_override_existing(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("EXISTING=from_file\n", encoding="utf-8")
        monkeypatch.setattr(mt, "CONFIG_DIR", tmp_path)
        monkeypatch.setenv("EXISTING", "from_env")

        mt._load_env_file()

        assert os.environ["EXISTING"] == "from_env"

    def test_missing_file_no_op(self, tmp_path, monkeypatch):
        # Ingen .env-fil i tmp_path
        monkeypatch.setattr(mt, "CONFIG_DIR", tmp_path)
        # Skal ikke raise
        mt._load_env_file()


# ----- _parse_timestamp -----

class TestParseTimestamp:
    def test_mm_ss(self):
        assert mt._parse_timestamp("02:30") == 150

    def test_h_mm_ss(self):
        assert mt._parse_timestamp("1:05:30") == 3930

    def test_invalid_returns_zero(self):
        assert mt._parse_timestamp("nope") == 0


# ----- is_repetitive_hallucination -----

class TestIsRepetitiveHallucination:
    def test_short_text_returns_false(self):
        # Færre end 6 ord → altid False
        assert not mt.is_repetitive_hallucination("hej ja")

    def test_normal_text_returns_false(self):
        text = "Vi starter mødet nu og gennemgår dagsordenen for i dag."
        assert not mt.is_repetitive_hallucination(text)

    def test_word_domination_returns_true(self):
        # "ja" udgør > 45% af ordene
        text = "ja ja ja ja ja ja ja ja ja andet"
        assert mt.is_repetitive_hallucination(text)

    def test_bigram_repetition_returns_true(self):
        # Konstruér tekst hvor ingen enkelt ord dominerer > 45%,
        # men et bigram gentages > 35%:
        # "ab cd ab cd ab cd ab cd ef gh ij kl" - "ab" og "cd" = 2/12 = 17% hver
        # bigram "ab cd" gentages 4 gange ud af 11 bigrams = 36% > 0.35
        text = "ab cd ab cd ab cd ab cd ef gh ij kl"
        assert mt.is_repetitive_hallucination(text)

    def test_six_to_eight_words_skips_bigram_check(self):
        # 7 ord: bigram-check springes over (len(words) < 8), men unique-check
        # kræver >= 12 → returnerer False for normal tekst
        text = "det er seks syv ord her nu"
        assert not mt.is_repetitive_hallucination(text)

    def test_low_unique_ratio_returns_true(self):
        # 12+ ord men < 50% unikke
        text = "a b c a b c a b c a b c x"  # 13 ord, 4 unikke = 30%
        assert mt.is_repetitive_hallucination(text)

    def test_high_unique_ratio_returns_false(self):
        # 12+ ord med > 50% unikke
        text = "vi begynder med at gennemgå de vigtige punkter på dagsordenen for mødet"
        assert not mt.is_repetitive_hallucination(text)


# ----- list_audio_devices -----

class TestListAudioDevices:
    def test_calls_ffmpeg_and_returns_list(self, mock_subprocess):
        mock_subprocess.run.return_value = SimpleNamespace(
            returncode=0,
            stdout="",
            stderr=(
                "AVFoundation audio devices:\n"
                "[AVFoundation indev @ 0x1] [0] Built-in Microphone\n"
                "[AVFoundation indev @ 0x2] [1] Loopback\n"
            ),
        )
        devices = mt.list_audio_devices()
        assert isinstance(devices, list)
        assert len(devices) == 2
        assert devices[0] == (0, "Built-in Microphone")

    def test_no_audio_section_no_crash(self, mock_subprocess):
        mock_subprocess.run.return_value = SimpleNamespace(
            returncode=0, stdout="", stderr="ingen lyd her",
        )
        result = mt.list_audio_devices()  # må ikke kaste
        assert result == []


# ----- record_meeting -----

class TestRecordMeeting:
    def test_returns_wav_path_when_file_exists(self, mock_subprocess, tmp_path):
        out_wav = tmp_path / "moede.wav"
        # Popen "skaber" filen
        def popen_side(cmd, **kw):
            out_wav.write_bytes(b"RIFF" * 1000)
            return mock_subprocess.proc

        mock_subprocess.Popen.side_effect = popen_side

        result = mt.record_meeting(out_wav, device_id=0)
        assert result == out_wav

    def test_exits_when_file_missing(self, mock_subprocess, tmp_path):
        out_wav = tmp_path / "moede.wav"
        # Popen kører men skaber ingen fil

        with pytest.raises(SystemExit):
            mt.record_meeting(out_wav, device_id=0)


# ----- _parse_transcript_segments -----

class TestParseTranscriptSegments:
    def test_parses_mmss_lines(self):
        text = "[00:00 - 00:05] Hej.\n[01:30 - 01:35] Farvel."
        segs = mt._parse_transcript_segments(text)
        assert segs[0] == (0.0, 5.0, "Hej.")
        assert segs[1] == (90.0, 95.0, "Farvel.")

    def test_parses_hmmss_lines(self):
        text = "[1:00:00 - 1:00:05] Lang."
        segs = mt._parse_transcript_segments(text)
        assert segs[0] == (3600.0, 3605.0, "Lang.")

    def test_skips_unparseable_lines(self):
        text = "ingen tidsstempel her\n[00:00 - 00:05] OK."
        segs = mt._parse_transcript_segments(text)
        assert segs == [(0.0, 5.0, "OK.")]
