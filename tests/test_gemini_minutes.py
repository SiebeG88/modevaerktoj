"""Tests for generate_minutes (Gemini API)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_tool as mt


class TestGenerateMinutes:
    def test_returns_text(self, fake_gemini_client):
        fake_gemini_client.models.generate_content.return_value = \
            fake_gemini_client._build_response("# Referat\n\nMødet handlede om ...")
        result = mt.generate_minutes(
            transcript="[00:00 - 00:05] Hej alle.",
            attendees=["Anna", "Dorte"],
            date="22-05-2026",
        )
        assert "Referat" in result

    def test_uses_gemini_pro_model(self, fake_gemini_client):
        mt.generate_minutes("[00:00 - 00:05] test", ["A"], "22-05-2026")
        call = fake_gemini_client.models.generate_content.call_args
        assert call.kwargs["model"] == mt.GEMINI_MINUTES_MODEL
        assert "gemini" in call.kwargs["model"]

    def test_model_env_override(self, monkeypatch, fake_gemini_client):
        monkeypatch.setenv("GEMINI_MINUTES_MODEL", "gemini-2.5-flash")
        mt.generate_minutes("[00:00 - 00:05] test", ["A"], "22-05-2026")
        call = fake_gemini_client.models.generate_content.call_args
        assert call.kwargs["model"] == "gemini-2.5-flash"

    def test_system_instruction_includes_attendees(self, fake_gemini_client):
        mt.generate_minutes("[00:00 - 00:05] x", ["Anna", "Bo"], "22-05-2026")
        import google.genai as _genai
        cfg = _genai.types.GenerateContentConfig.call_args
        system = cfg.kwargs["system_instruction"]
        assert "Anna" in system
        assert "Bo" in system

    def test_contents_include_date_and_transcript(self, fake_gemini_client):
        mt.generate_minutes("[00:00 - 00:05] xyz-123", ["A"], "22-05-2026")
        call = fake_gemini_client.models.generate_content.call_args
        joined = "".join(call.kwargs["contents"])
        assert "22-05-2026" in joined
        assert "xyz-123" in joined

    def test_missing_key_raises(self, monkeypatch, fake_gemini_client):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            mt.generate_minutes("transcript", ["A"], "22-05-2026")
