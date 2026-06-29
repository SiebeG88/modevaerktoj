# tests/test_setup.py
import os

import pytest

import meeting_tool
import meeting_app


def test_needs_setup_true_when_no_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert meeting_app._needs_setup() is True


def test_needs_setup_false_with_gemini_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-123")
    assert meeting_app._needs_setup() is False


def test_needs_setup_false_with_google_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "g-456")
    assert meeting_app._needs_setup() is False


def test_write_env_persists_and_sets_environ(tmp_path, monkeypatch):
    # Peg ENV_FILE på en temp-fil så vi ikke rører den rigtige .env
    env_file = tmp_path / ".env"
    monkeypatch.setattr(meeting_app, "ENV_FILE", env_file)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    meeting_app._write_env({"GEMINI_API_KEY": "g-123", "MEETINGS_DIR": "/data/m", "EMPTY": ""})
    # Filen er skrevet og indeholder de ikke-tomme nøgler:
    text = env_file.read_text(encoding="utf-8")
    assert "GEMINI_API_KEY=g-123" in text
    assert "MEETINGS_DIR=/data/m" in text
    assert "EMPTY=" not in text           # tomme værdier springes over
    # os.environ er opdateret:
    assert os.environ["GEMINI_API_KEY"] == "g-123"


def test_write_env_preserves_existing_keys(tmp_path, monkeypatch):
    # En eksisterende .env med fx MEETINGS_DIR må ikke slettes af setup-dialogen.
    env_file = tmp_path / ".env"
    env_file.write_text("MEETINGS_DIR=/data/moeder\n", encoding="utf-8")
    monkeypatch.setattr(meeting_app, "ENV_FILE", env_file)
    meeting_app._write_env({"GEMINI_API_KEY": "g-1"})
    text = env_file.read_text(encoding="utf-8")
    assert "MEETINGS_DIR=/data/moeder" in text   # bevaret
    assert "GEMINI_API_KEY=g-1" in text           # tilføjet


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
