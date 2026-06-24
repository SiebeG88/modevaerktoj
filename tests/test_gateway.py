# tests/test_gateway.py
import os

import pytest

import meeting_tool
import meeting_app


def test_anthropic_client_direct_when_no_base_url(monkeypatch, mocker):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    fake = mocker.patch("anthropic.Anthropic")
    meeting_tool._anthropic_client()
    fake.assert_called_once_with()


def test_anthropic_client_gateway_when_base_url(monkeypatch, mocker):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://ai-gateway.vercel.sh")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "gw-key-123")
    fake = mocker.patch("anthropic.Anthropic")
    meeting_tool._anthropic_client()
    fake.assert_called_once_with(
        base_url="https://ai-gateway.vercel.sh", api_key="gw-key-123"
    )


def test_friendly_error_402():
    msg = meeting_tool._friendly_anthropic_error(402)
    assert msg is not None and "budget" in msg.lower()


def test_friendly_error_429():
    msg = meeting_tool._friendly_anthropic_error(429)
    assert msg is not None and "forespørgsler" in msg.lower()


def test_friendly_error_other_returns_none():
    assert meeting_tool._friendly_anthropic_error(500) is None


def test_claude_model_direct_uses_default(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setenv("CLAUDE_GATEWAY_MODEL", "anthropic/claude-opus-4.7")
    # Uden Gateway ignoreres override'en — direkte Anthropic bruger standard.
    assert meeting_tool._claude_model() == meeting_tool.CLAUDE_MINUTES_MODEL


def test_claude_model_gateway_uses_override(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://ai-gateway.vercel.sh")
    monkeypatch.setenv("CLAUDE_GATEWAY_MODEL", "anthropic/claude-opus-4.7")
    assert meeting_tool._claude_model() == "anthropic/claude-opus-4.7"


def test_claude_model_gateway_without_override_uses_gateway_default(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://ai-gateway.vercel.sh")
    monkeypatch.delenv("CLAUDE_GATEWAY_MODEL", raising=False)
    # Gateway kræver anthropic/-præfikset slug — ikke det direkte slug.
    assert meeting_tool._claude_model() == meeting_tool.CLAUDE_GATEWAY_MODEL_DEFAULT
    assert meeting_tool._claude_model().startswith("anthropic/")


def test_request_extra_includes_user_when_mt_user_set(monkeypatch):
    monkeypatch.setenv("MT_USER", "Dorte")
    assert meeting_tool._claude_request_extra() == {"metadata": {"user_id": "Dorte"}}


def test_request_extra_empty_when_no_mt_user(monkeypatch):
    monkeypatch.delenv("MT_USER", raising=False)
    assert meeting_tool._claude_request_extra() == {}


def test_generate_minutes_passes_user_metadata(monkeypatch, fake_anthropic_client):
    # Brugerstyring: MT_USER skal nå messages.create som metadata.user_id.
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setenv("MT_USER", "Mads")
    meeting_tool.generate_minutes("et transkript", ["Mads"], "2026-01-01")
    _, kwargs = fake_anthropic_client.messages.create.call_args
    assert kwargs.get("metadata") == {"user_id": "Mads"}


def test_generate_minutes_402_raises_friendly_runtimeerror(
    monkeypatch, fake_anthropic_client
):
    """Integration: en 402 fra messages.create skal blive til en RuntimeError
    med den danske budget-besked, som GUI'en viser i statuslinjen."""
    import anthropic

    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    fake_anthropic_client.messages.create.side_effect = anthropic.APIStatusError(
        "payment required", status_code=402
    )
    with pytest.raises(RuntimeError, match="budget"):
        meeting_tool.generate_minutes("et transkript", ["Mads"], "2026-01-01")


def test_generate_minutes_unmapped_status_reraises(
    monkeypatch, fake_anthropic_client
):
    """Integration: en ukendt statuskode (fx 500) skal boble videre uændret,
    ikke maskeres som en venlig besked."""
    import anthropic

    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    fake_anthropic_client.messages.create.side_effect = anthropic.APIStatusError(
        "server error", status_code=500
    )
    with pytest.raises(anthropic.APIStatusError):
        meeting_tool.generate_minutes("et transkript", ["Mads"], "2026-01-01")


def test_needs_setup_true_when_no_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert meeting_app._needs_setup() is True


def test_needs_setup_false_with_gateway_token(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "x")
    assert meeting_app._needs_setup() is False


def test_needs_setup_false_with_direct_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert meeting_app._needs_setup() is False


def test_write_env_persists_and_sets_environ(tmp_path, monkeypatch):
    # Peg ENV_FILE på en temp-fil så vi ikke rører den rigtige .env
    env_file = tmp_path / ".env"
    monkeypatch.setattr(meeting_app, "ENV_FILE", env_file)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    meeting_app._write_env({"GEMINI_API_KEY": "g-123", "MT_USER": "Dorte", "EMPTY": ""})
    # Filen er skrevet og indeholder de ikke-tomme nøgler:
    text = env_file.read_text(encoding="utf-8")
    assert "GEMINI_API_KEY=g-123" in text
    assert "MT_USER=Dorte" in text
    assert "EMPTY=" not in text           # tomme værdier springes over
    # os.environ er opdateret:
    assert os.environ["GEMINI_API_KEY"] == "g-123"


def test_write_env_preserves_existing_keys(tmp_path, monkeypatch):
    # En eksisterende .env med fx MEETINGS_DIR må ikke slettes af setup-dialogen.
    env_file = tmp_path / ".env"
    env_file.write_text("MEETINGS_DIR=/data/moeder\n", encoding="utf-8")
    monkeypatch.setattr(meeting_app, "ENV_FILE", env_file)
    meeting_app._write_env({"ANTHROPIC_AUTH_TOKEN": "gw-1"})
    text = env_file.read_text(encoding="utf-8")
    assert "MEETINGS_DIR=/data/moeder" in text   # bevaret
    assert "ANTHROPIC_AUTH_TOKEN=gw-1" in text    # tilføjet


class _FakeRoot:
    def __init__(self):
        self.after_calls = []

    def after(self, ms, fn):
        self.after_calls.append((ms, fn))


def test_maybe_apply_update_defers_when_recording(mocker):
    apply = mocker.patch("updater.apply_and_restart")
    root = _FakeRoot()
    app = type("App", (), {"recording": True, "transcribing": False})()
    meeting_app._maybe_apply_update(root, app, "staging")
    apply.assert_not_called()                       # ikke genstart midt i optagelse
    assert root.after_calls and root.after_calls[0][0] == 30_000


def test_maybe_apply_update_defers_when_transcribing(mocker):
    apply = mocker.patch("updater.apply_and_restart")
    root = _FakeRoot()
    app = type("App", (), {"recording": False, "transcribing": True})()
    meeting_app._maybe_apply_update(root, app, "staging")
    apply.assert_not_called()
    assert root.after_calls and root.after_calls[0][0] == 30_000


def test_maybe_apply_update_defers_when_file_transcribing(mocker):
    # C1: en fil-transkription (TranscribeFileTab.processing) må også udsætte.
    apply = mocker.patch("updater.apply_and_restart")
    root = _FakeRoot()
    tab = type("Tab", (), {"processing": True})()
    app = type("App", (), {"recording": False, "transcribing": False,
                           "_transcribe_tab": tab})()
    meeting_app._maybe_apply_update(root, app, "staging")
    apply.assert_not_called()
    assert root.after_calls and root.after_calls[0][0] == 30_000


def test_maybe_apply_update_applies_when_idle(mocker):
    apply = mocker.patch("updater.apply_and_restart")
    root = _FakeRoot()
    tab = type("Tab", (), {"processing": False})()
    app = type("App", (), {"recording": False, "transcribing": False,
                           "_transcribe_tab": tab})()
    meeting_app._maybe_apply_update(root, app, "staging")
    apply.assert_called_once_with("staging")
    assert root.after_calls == []                   # ingen udsættelse
