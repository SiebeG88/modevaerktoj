"""Nul mødetyper tolereres; neutral default; engangs-oprydning af seed."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_tool

_PRISTINE = {
    "driftledelse": {
        "navn": "Driftledelsesmøde", "detaljeniveau": "kortfattet",
        "citater": False, "opgaveliste": True,
        "fokus": "Konkrete opgaver, aftaler og beslutninger for den daglige drift.",
        "ekstra_instruktioner": "", "deltagere": [],
    },
    "ledergruppe": {
        "navn": "Ledergruppemøde", "detaljeniveau": "grundig",
        "citater": True, "opgaveliste": False,
        "fokus": "Strategi, langsigtet retning og principielle drøftelser.",
        "ekstra_instruktioner": "Skriv i sammenhængende prosa. Bevar nuancer og uenigheder.",
        "deltagere": [],
    },
}


def test_normalize_empty_stays_empty():
    assert meeting_tool._normalize_meeting_types({}) == {}


def test_neutral_meeting_type_is_valid():
    mt = meeting_tool.neutral_meeting_type()
    assert mt["navn"] == "Møde"
    assert mt["detaljeniveau"] == "balanceret"


def test_none_type_normalizes_to_neutral_without_crash():
    # generate_minutes' None-gren bruger _normalize_meeting_type(meeting_type or {}):
    # tom typeliste må aldrig ramme next(iter({})). Verificér guard-udtrykket.
    mt = meeting_tool._normalize_meeting_type(None or {})
    assert mt["navn"] == "Møde"


def test_cleanup_clears_pristine_seed(tmp_path):
    (tmp_path / "meeting_types.json").write_text(
        json.dumps(_PRISTINE, ensure_ascii=False), encoding="utf-8")
    meeting_tool.cleanup_seed_meeting_types(tmp_path)
    assert json.loads((tmp_path / "meeting_types.json").read_text(encoding="utf-8")) == {}


def test_cleanup_preserves_edited(tmp_path):
    edited = json.loads(json.dumps(_PRISTINE))
    edited["driftledelse"]["navn"] = "Mit møde"
    (tmp_path / "meeting_types.json").write_text(
        json.dumps(edited, ensure_ascii=False), encoding="utf-8")
    meeting_tool.cleanup_seed_meeting_types(tmp_path)
    data = json.loads((tmp_path / "meeting_types.json").read_text(encoding="utf-8"))
    assert data["driftledelse"]["navn"] == "Mit møde"


def test_cleanup_preserves_extra_type(tmp_path):
    extra = json.loads(json.dumps(_PRISTINE))
    extra["egen"] = {"navn": "Egen type"}
    (tmp_path / "meeting_types.json").write_text(
        json.dumps(extra, ensure_ascii=False), encoding="utf-8")
    meeting_tool.cleanup_seed_meeting_types(tmp_path)
    data = json.loads((tmp_path / "meeting_types.json").read_text(encoding="utf-8"))
    assert "egen" in data


from types import MethodType, SimpleNamespace


def test_file_tab_resolve_uses_neutral_when_no_type(tmp_path, monkeypatch):
    import meeting_app
    monkeypatch.setattr(
        meeting_app.MeetingTypesTab, "TYPES_FILE", tmp_path / "meeting_types.json")
    tab = SimpleNamespace(
        _meeting_types={}, _type_keys=[], _type_key=None,
        type_var=SimpleNamespace(get=lambda: "", set=lambda v: None),
        _level_seg=SimpleNamespace(get=lambda: "Mellem"),
        _on_types_changed=lambda: None,
        status_var=SimpleNamespace(set=lambda s: None),
    )
    tab._resolve_meeting_type = MethodType(
        meeting_app.TranscribeFileTab._resolve_meeting_type, tab)
    mt = tab._resolve_meeting_type()
    assert mt["navn"] == "Møde"  # neutral default
