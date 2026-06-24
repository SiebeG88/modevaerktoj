"""Tests for audio-helpers: ffprobe, splitting, conversion, faster-whisper."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_tool as mt


class TestFfprobeDuration:
    def test_parses_float(self, mock_subprocess, tmp_wav):
        mock_subprocess.run.return_value = SimpleNamespace(
            returncode=0, stdout="1234.56\n", stderr="",
        )
        assert mt._ffprobe_duration(tmp_wav) == pytest.approx(1234.56)

    def test_invalid_output_raises(self, mock_subprocess, tmp_wav):
        mock_subprocess.run.return_value = SimpleNamespace(
            returncode=0, stdout="not-a-number\n", stderr="",
        )
        with pytest.raises(ValueError):
            mt._ffprobe_duration(tmp_wav)

    def test_subprocess_failure_propagates(self, mock_subprocess, tmp_wav):
        import subprocess
        mock_subprocess.run.side_effect = subprocess.CalledProcessError(
            1, ["ffprobe"], output="", stderr="bad",
        )
        with pytest.raises(subprocess.CalledProcessError):
            mt._ffprobe_duration(tmp_wav)


class TestSplitAudioForGemini:
    def test_creates_n_chunks(self, mock_subprocess, tmp_wav, tmp_path, mocker):
        # Mock _ffprobe_duration so we get 4 chunks of 12 min from 44 min
        mocker.patch("meeting_tool._ffprobe_duration", return_value=44 * 60)
        out_dir = tmp_path / "chunks"
        out_dir.mkdir()

        def fake_run(cmd, **_kwargs):
            # ffmpeg segment mode creates chunk_0000.wav … chunk_0003.wav
            # The pattern argument is the last element: out_dir/chunk_%04d.wav
            # We find out_dir from the pattern and create all 4 expected files.
            pattern = None
            for arg in cmd:
                if "chunk_%04d.wav" in str(arg):
                    pattern = Path(str(arg))
                    break
            if pattern is not None:
                chunk_dir = pattern.parent
                # ceil(44 * 60 / (12 * 60)) = ceil(3.67) = 4 chunks
                for i in range(4):
                    (chunk_dir / f"chunk_{i:04d}.wav").write_bytes(b"RIFF")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        mock_subprocess.run.side_effect = fake_run

        paths = mt._split_audio_for_gemini(tmp_wav, 12 * 60, out_dir)
        assert len(paths) == 4
        for p in paths:
            assert p.exists()


class TestConvertToWav:
    def test_runs_ffmpeg(self, mock_subprocess, tmp_path):
        input_path = tmp_path / "src.m4a"
        input_path.write_bytes(b"")
        output_path = tmp_path / "dst.wav"
        result = mt.convert_to_wav(input_path, output_path)
        assert result == output_path
        assert mock_subprocess.run.called
        # Check that the ffmpeg command contains the expected flags
        cmd = mock_subprocess.run.call_args.args[0]
        assert "ffmpeg" in cmd[0]
        assert "16000" in cmd
        assert str(output_path) in cmd


class TestTranscribeAudio:
    def test_returns_formatted_segments(self, fake_whisper_model, tmp_wav):
        result = mt.transcribe_audio(tmp_wav, model_size="large-v3")
        assert "[00:00 - 00:05] Hej alle sammen." in result
        assert "[00:05 - 00:10] Vi starter mødet." in result

    def test_loads_model_with_correct_size(self, fake_whisper_model, tmp_wav, mocker):
        mt.transcribe_audio(tmp_wav, model_size="custom-model")
        # WhisperModel constructor called with "custom-model"
        # Access via sys.modules since the production code uses
        # "from faster_whisper import WhisperModel" (inline import)
        whisper_cls = sys.modules["faster_whisper"].WhisperModel
        whisper_cls.assert_called()
        call = whisper_cls.call_args
        assert call.args[0] == "custom-model"
