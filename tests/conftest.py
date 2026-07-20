"""Delte pytest-fixtures til meeting_tool-suiten.

Alle eksterne afhængigheder mockes her:
- google.genai.Client (Gemini upload + generate)
- faster_whisper.WhisperModel
- subprocess.run / subprocess.Popen (ffmpeg, ffprobe, caffeinate)
- time.sleep (auto-no-op for hurtige retry-tests)
"""
from __future__ import annotations

import struct
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ----- Auto-applied -----

@pytest.fixture(autouse=True)
def silent_sleep(mocker):
    """Erstat time.sleep med no-op i alle tests så retry-loops kører instant."""
    return mocker.patch("time.sleep", return_value=None)


@pytest.fixture(autouse=True)
def gemini_env(monkeypatch):
    """Sikr at GEMINI_API_KEY er sat for tests der instantierer klienten."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-tests")


# ----- WAV-fil -----

@pytest.fixture
def tmp_wav(tmp_path: Path) -> Path:
    """Skab en 1-sekund stilhed-WAV (16 kHz mono PCM16) i tmp_path."""
    wav_path = tmp_path / "sample.wav"
    sample_rate = 16000
    n_frames = sample_rate  # 1 sekund
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * n_frames)
    return wav_path


# ----- Gemini-klient -----

def _build_response(text: str, finish_reason: str = "FinishReason.STOP",
                    out_tokens: int = 100):
    """Byg en mock Gemini-response."""
    response = MagicMock()
    response.text = text
    candidate = SimpleNamespace(finish_reason=finish_reason)
    response.candidates = [candidate]
    response.usage_metadata = SimpleNamespace(
        prompt_token_count=50,
        candidates_token_count=out_tokens,
        thoughts_token_count=0,
    )
    return response


def _build_uploaded_file(state: str = "ACTIVE"):
    """Byg en mock uploaded-file ref."""
    return SimpleNamespace(
        name="files/abc123",
        state=SimpleNamespace(name=state),
    )


@pytest.fixture
def fake_gemini_client(mocker):
    """Mock google.genai.Client og dets metoder.

    Returns the mock client. Konfigurer responses via:
        fake_gemini_client.models.generate_content.return_value = ...
        fake_gemini_client.models.generate_content.side_effect = [...]
    """
    client = MagicMock()
    client.files.upload.return_value = _build_uploaded_file("ACTIVE")
    client.files.get.return_value = _build_uploaded_file("ACTIVE")
    client.files.delete.return_value = None
    client.models.generate_content.return_value = _build_response(
        "[00:00 - 00:05] Hej alle sammen.\n[00:05 - 00:10] Vi starter mødet."
    )

    genai_module = MagicMock()
    genai_module.Client.return_value = client
    # types_module skal være tilgængelig BÅDE via sys.modules OG som
    # attribute på genai_module, fordi meeting_tool gør "from google.genai
    # import types" (attribute-lookup, ikke sys.modules-lookup).
    types_module = MagicMock()
    genai_module.types = types_module
    mocker.patch.dict("sys.modules", {
        "google": MagicMock(genai=genai_module),
        "google.genai": genai_module,
        "google.genai.types": types_module,
    })

    # Eksponér helpers på client så tests kan bygge custom responses
    client._build_response = staticmethod(_build_response)
    client._build_uploaded_file = staticmethod(_build_uploaded_file)
    return client


# ----- WhisperModel -----

@pytest.fixture
def fake_whisper_model(mocker):
    """Mock faster_whisper.WhisperModel."""
    model = MagicMock()
    seg1 = SimpleNamespace(start=0.0, end=5.0, text=" Hej alle sammen.")
    seg2 = SimpleNamespace(start=5.0, end=10.0, text=" Vi starter mødet.")
    info = SimpleNamespace(language="da", duration=10.0)
    model.transcribe.return_value = (iter([seg1, seg2]), info)

    fw_module = MagicMock()
    fw_module.WhisperModel.return_value = model
    mocker.patch.dict("sys.modules", {"faster_whisper": fw_module})
    return model


# ----- Subprocess (ffmpeg/ffprobe/caffeinate) -----

@pytest.fixture
def mock_subprocess(mocker):
    """Mock subprocess.run og subprocess.Popen.

    Tests kan konfigurere returnvalues på .run / .Popen mocks direkte.
    Default: run returnerer CompletedProcess med duration "1800.0".
    """
    run_mock = mocker.patch("subprocess.run")
    run_mock.return_value = SimpleNamespace(
        returncode=0,
        stdout="1800.0\n",
        stderr="",
    )
    popen_mock = mocker.patch("subprocess.Popen")
    proc = MagicMock()
    proc.wait.return_value = 0
    proc.poll.return_value = 0
    proc.stdin = MagicMock()
    proc.stdin.closed = False
    popen_mock.return_value = proc
    return SimpleNamespace(run=run_mock, Popen=popen_mock, proc=proc)
