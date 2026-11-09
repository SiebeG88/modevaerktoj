"""Gemini-modellen skal kun stå ét sted, og kaldet skal passe til Gemini 3."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import meeting_tool as mt

PRODUCTION_FILES = ["meeting_tool.py", "meeting_app.py", "resume_transcribe.py"]


def test_default_model_is_3_8_flash():
    assert mt.GEMINI_MODEL == "gemini-3.8-flash"
    assert mt.GEMINI_MINUTES_MODEL == mt.GEMINI_MODEL


@pytest.mark.parametrize("filename", PRODUCTION_FILES)
def test_no_retired_model_left_in_source(filename):
    source = (ROOT / filename).read_text(encoding="utf-8")
    assert not re.search(r"gemini[- ]2\.5", source, re.IGNORECASE)


@pytest.mark.parametrize("filename", PRODUCTION_FILES[1:])
def test_callers_use_the_shared_constant(filename):
    source = (ROOT / filename).read_text(encoding="utf-8")
    assert "meeting_tool.GEMINI_MODEL" in source


def test_transcription_uses_thinking_level_not_budget(fake_gemini_client, tmp_wav):
    mt._gemini_transcribe_single(
        fake_gemini_client, tmp_wav, "instr", mt.GEMINI_MODEL, "01/1", lambda _m: None,
    )
    types_module = sys.modules["google.genai"].types
    types_module.ThinkingConfig.assert_called_once_with(thinking_level="LOW")


def test_real_sdk_accepts_the_thinking_level():
    types = pytest.importorskip("google.genai.types")
    config = types.ThinkingConfig(thinking_level="LOW")
    assert config.thinking_level == types.ThinkingLevel.LOW
