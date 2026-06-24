# tests/test_app_paths.py
import os
import sys
from pathlib import Path

import app_paths


def test_is_frozen_false_in_source(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert app_paths.is_frozen() is False


def test_is_frozen_true_when_sys_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert app_paths.is_frozen() is True


def test_user_config_dir_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    d = app_paths.user_config_dir()
    assert d == tmp_path / "Mødeværktøj"
    assert d.is_dir()  # skal være oprettet


def test_user_config_dir_macos_is_app_dir(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert app_paths.user_config_dir() == app_paths.app_dir()


def test_resolve_binary_prefers_bundled(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(app_paths, "app_dir", lambda: tmp_path)
    (tmp_path / "ffmpeg.exe").write_text("x")
    assert app_paths.resolve_binary("ffmpeg") == str(tmp_path / "ffmpeg.exe")


def test_resolve_binary_falls_back_to_name(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(app_paths, "app_dir", lambda: tmp_path)
    assert app_paths.resolve_binary("ffmpeg") == "ffmpeg"


def test_ensure_user_config_copies_when_dirs_differ(monkeypatch, tmp_path):
    src = tmp_path / "app"
    dst = tmp_path / "cfg"
    src.mkdir(); dst.mkdir()
    (src / "vocabulary.default.json").write_text("{}")
    monkeypatch.setattr(app_paths, "app_dir", lambda: src)
    monkeypatch.setattr(app_paths, "user_config_dir", lambda: dst)
    app_paths.ensure_user_config(["vocabulary.default.json"])
    assert (dst / "vocabulary.default.json").read_text() == "{}"


def test_ensure_user_config_noop_when_same_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(app_paths, "app_dir", lambda: tmp_path)
    monkeypatch.setattr(app_paths, "user_config_dir", lambda: tmp_path)
    # Må ikke rejse selv om filen ikke findes
    app_paths.ensure_user_config(["vocabulary.default.json"])


def test_meeting_tool_loads_env_from_config_dir(tmp_path, monkeypatch):
    import meeting_tool
    (tmp_path / ".env").write_text("MT_TEST_KEY=hej123\n", encoding="utf-8")
    monkeypatch.delenv("MT_TEST_KEY", raising=False)
    meeting_tool._load_env_file(config_dir=tmp_path)
    assert os.environ["MT_TEST_KEY"] == "hej123"
