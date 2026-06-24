"""Tests for transcribe_file — pipeline for en eksisterende lydfil.

Alle interne pipeline-trin (convert_to_wav, transcribe_audio,
transcribe_with_gemini, generate_minutes, save_output) mockes, så testene
kun verificerer orkestreringen.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_tool as mt


@pytest.fixture
def patched_pipeline(mocker):
    """Patch alle pipeline-trin og returnér mocks som namespace."""
    convert = mocker.patch(
        "meeting_tool.convert_to_wav",
        side_effect=lambda inp, out: out,
    )
    hviske = mocker.patch(
        "meeting_tool.transcribe_audio",
        return_value="[00:00 - 00:05] Hviske-tekst.",
    )
    gemini = mocker.patch(
        "meeting_tool.transcribe_with_gemini",
        return_value="[00:00 - 00:05] Gemini-tekst.",
    )
    minutes = mocker.patch(
        "meeting_tool.generate_minutes",
        return_value="# Referat\n\nIndhold.",
    )
    save = mocker.patch("meeting_tool.save_output")
    return SimpleNamespace(
        convert=convert,
        hviske=hviske,
        gemini=gemini,
        minutes=minutes,
        save=save,
    )


@pytest.fixture
def input_audio(tmp_path: Path) -> Path:
    """En dummy iPhone-memo (.m4a) — indhold er ligegyldigt da convert mockes."""
    p = tmp_path / "New Recording 5.m4a"
    p.write_bytes(b"\x00\x00")
    return p


class TestTranscribeFile:
    def test_hviske_engine_calls_transcribe_audio_not_gemini(
        self, patched_pipeline, input_audio, tmp_path
    ):
        out_dir = tmp_path / "Møde"
        mt.transcribe_file(
            input_path=input_audio,
            output_dir=out_dir,
            date="03-06-2026",
            name_base="Møde",
            attendees=["Mads"],
            engine="hviske",
            make_minutes=False,
        )
        assert patched_pipeline.hviske.called
        assert not patched_pipeline.gemini.called

    def test_gemini_engine_calls_transcribe_with_gemini_not_hviske(
        self, patched_pipeline, input_audio, tmp_path
    ):
        mt.transcribe_file(
            input_path=input_audio,
            output_dir=tmp_path / "Møde",
            date="03-06-2026",
            name_base="Møde",
            attendees=["Mads", "Dorte"],
            engine="gemini",
            make_minutes=False,
            gemini_model="gemini-2.5-pro",
        )
        assert patched_pipeline.gemini.called
        assert not patched_pipeline.hviske.called
        _, kwargs = patched_pipeline.gemini.call_args
        assert kwargs["attendees"] == ["Mads", "Dorte"]
        assert kwargs["model"] == "gemini-2.5-pro"

    def test_make_minutes_true_generates_and_returns_minutes(
        self, patched_pipeline, input_audio, tmp_path
    ):
        _, transcript, minutes = mt.transcribe_file(
            input_path=input_audio,
            output_dir=tmp_path / "Møde",
            date="03-06-2026",
            name_base="Møde",
            attendees=["Mads"],
            engine="hviske",
            make_minutes=True,
        )
        patched_pipeline.minutes.assert_called_once_with(
            transcript, ["Mads"], "03-06-2026", meeting_type=None
        )
        assert minutes == "# Referat\n\nIndhold."

    def test_make_minutes_false_skips_generate_minutes(
        self, patched_pipeline, input_audio, tmp_path
    ):
        mt.transcribe_file(
            input_path=input_audio,
            output_dir=tmp_path / "Møde",
            date="03-06-2026",
            name_base="Møde",
            attendees=["Mads"],
            engine="hviske",
            make_minutes=False,
        )
        assert not patched_pipeline.minutes.called

    def test_empty_transcript_skips_minutes_and_saves_none(
        self, patched_pipeline, input_audio, tmp_path
    ):
        patched_pipeline.hviske.return_value = "   \n  "
        _, _, minutes = mt.transcribe_file(
            input_path=input_audio,
            output_dir=tmp_path / "Møde",
            date="03-06-2026",
            name_base="Møde",
            attendees=["Mads"],
            engine="hviske",
            make_minutes=True,
        )
        assert not patched_pipeline.minutes.called
        assert minutes is None
        _, save_kwargs = patched_pipeline.save.call_args
        assert save_kwargs.get("minutes", "MISSING") is None

    def test_wav_written_into_output_dir_as_name_base(
        self, patched_pipeline, input_audio, tmp_path
    ):
        out_dir = tmp_path / "Driftsmøde"
        wav_path, _, _ = mt.transcribe_file(
            input_path=input_audio,
            output_dir=out_dir,
            date="03-06-2026",
            name_base="Driftsmøde",
            attendees=["Mads"],
            engine="hviske",
            make_minutes=False,
        )
        expected_wav = out_dir / "Driftsmøde.wav"
        assert wav_path == expected_wav
        convert_args, _ = patched_pipeline.convert.call_args
        assert convert_args[0] == input_audio
        assert convert_args[1] == expected_wav
        assert out_dir.exists()
