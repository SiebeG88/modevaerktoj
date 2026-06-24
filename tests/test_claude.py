"""Tests for generate_minutes (Claude API)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_tool as mt


class TestGenerateMinutes:
    def test_returns_text(self, fake_anthropic_client):
        result = mt.generate_minutes(
            transcript="[00:00 - 00:05] Hej alle.",
            attendees=["Anna", "Dorte"],
            date="22-05-2026",
        )
        assert "Referat" in result

    def test_uses_claude_opus_model(self, fake_anthropic_client):
        mt.generate_minutes("[00:00 - 00:05] test", ["A"], "22-05-2026")
        call = fake_anthropic_client.messages.create.call_args
        assert call.kwargs["model"] == mt.CLAUDE_MINUTES_MODEL
        assert "claude-opus" in call.kwargs["model"]

    def test_system_prompt_includes_attendees(self, fake_anthropic_client):
        mt.generate_minutes("[00:00 - 00:05] x", ["Anna", "Bo"], "22-05-2026")
        call = fake_anthropic_client.messages.create.call_args
        system = call.kwargs["system"]
        assert "Anna" in system
        assert "Bo" in system

    def test_user_prompt_includes_date_and_transcript(self, fake_anthropic_client):
        mt.generate_minutes("[00:00 - 00:05] xyz-123", ["A"], "22-05-2026")
        call = fake_anthropic_client.messages.create.call_args
        user_content = call.kwargs["messages"][0]["content"]
        assert "22-05-2026" in user_content
        assert "xyz-123" in user_content

    def test_api_error_propagates(self, fake_anthropic_client):
        fake_anthropic_client.messages.create.side_effect = RuntimeError("API down")
        with pytest.raises(RuntimeError, match="API down"):
            mt.generate_minutes("transcript", ["A"], "22-05-2026")
