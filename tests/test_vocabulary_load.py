"""Tests for load_vocabulary - læser og normaliserer vocabulary.json."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import meeting_tool


def _write(path: Path, data) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def test_load_complete_file(tmp_path: Path):
    _write(tmp_path / "vocabulary.json", {
        "personer": ["Mads — driftsleder"],
        "steder": ["Nordmarken"],
        "fagtermer": ["ramsning"],
    })
    vocab = meeting_tool.load_vocabulary(tmp_path)
    assert vocab == {
        "personer": ["Mads — driftsleder"],
        "steder": ["Nordmarken"],
        "fagtermer": ["ramsning"],
    }


def test_missing_keys_default_to_empty(tmp_path: Path):
    _write(tmp_path / "vocabulary.json", {"personer": ["Mads"]})
    vocab = meeting_tool.load_vocabulary(tmp_path)
    assert vocab == {"personer": ["Mads"], "steder": [], "fagtermer": []}


def test_non_string_entries_filtered(tmp_path: Path):
    _write(tmp_path / "vocabulary.json", {
        "personer": ["Mads", 42, None, {"x": 1}],
        "steder": [],
        "fagtermer": [],
    })
    vocab = meeting_tool.load_vocabulary(tmp_path)
    assert vocab["personer"] == ["Mads"]


def test_whitespace_only_entries_filtered(tmp_path: Path):
    _write(tmp_path / "vocabulary.json", {
        "personer": ["Mads", "  ", "", "\t\n"],
        "steder": [],
        "fagtermer": [],
    })
    vocab = meeting_tool.load_vocabulary(tmp_path)
    assert vocab["personer"] == ["Mads"]


def test_entries_are_stripped(tmp_path: Path):
    _write(tmp_path / "vocabulary.json", {
        "personer": ["  Mads — driftsleder  "],
        "steder": [],
        "fagtermer": [],
    })
    vocab = meeting_tool.load_vocabulary(tmp_path)
    assert vocab["personer"] == ["Mads — driftsleder"]


def test_invalid_json_returns_empty_vocab(tmp_path: Path, capsys):
    (tmp_path / "vocabulary.json").write_text("{ not valid json", encoding="utf-8")
    vocab = meeting_tool.load_vocabulary(tmp_path)
    assert vocab == {"personer": [], "steder": [], "fagtermer": []}
    captured = capsys.readouterr()
    assert "vocabulary.json" in captured.err.lower() or "vocabulary.json" in captured.out.lower()


def test_missing_file_seeds_from_default(tmp_path: Path, monkeypatch):
    # Læg en default-fil i en simuleret "tool_dir"
    default = tmp_path / "vocabulary.default.json"
    _write(default, {
        "personer": ["Mads — default"],
        "steder": ["Nordmarken — default"],
        "fagtermer": [],
    })
    vocab_file = tmp_path / "vocabulary.json"
    assert not vocab_file.exists()

    vocab = meeting_tool.load_vocabulary(tmp_path)
    # Returnerer default-indhold
    assert vocab["personer"] == ["Mads — default"]
    assert vocab["steder"] == ["Nordmarken — default"]
    # Og skriver vocabulary.json så brugeren kan redigere
    assert vocab_file.exists()
    saved = json.loads(vocab_file.read_text(encoding="utf-8"))
    assert saved["personer"] == ["Mads — default"]


def test_missing_file_no_default_returns_empty(tmp_path: Path):
    # Ingen default-fil, ingen vocabulary.json
    vocab = meeting_tool.load_vocabulary(tmp_path)
    assert vocab == {"personer": [], "steder": [], "fagtermer": []}
