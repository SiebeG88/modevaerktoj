"""Ad-hoc mødetyper fra guiden: slug, navneopslag, niveau-overstyring, persistering."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_app
import meeting_tool


def _types() -> dict:
    return {
        "driftledelse": meeting_tool._normalize_meeting_type(
            {"navn": "Driftledelsesmøde", "detaljeniveau": "kortfattet"}
        ),
    }


class TestSlugifyTypeName:
    def test_translittererer_og_binder(self):
        assert meeting_app._slugify_type_name("Ansættelsessamtale") == "ansaettelsessamtale"
        assert meeting_app._slugify_type_name("Møde: Økonomi & Løn") == "moede-oekonomi-loen"

    def test_kun_specialtegn_giver_fallback(self):
        assert meeting_app._slugify_type_name("!!!") == "type"


class TestEnsureMeetingType:
    def test_creates_new_type_with_defaults(self):
        types, key, created = meeting_app.ensure_meeting_type(_types(), "Ansættelsessamtale")
        assert created is True
        assert key == "ansaettelsessamtale"
        t = types[key]
        assert t["navn"] == "Ansættelsessamtale"
        assert t["detaljeniveau"] == "balanceret"
        assert t["opgaveliste"] is True
        assert t["citater"] is False
        assert t["deltagere"] == []

    def test_reuses_existing_on_case_insensitive_name_match(self):
        base = _types()
        types, key, created = meeting_app.ensure_meeting_type(base, "  driftledelsesmøde ")
        assert created is False
        assert key == "driftledelse"
        assert types is base  # ingen kopi, ingen ændring

    def test_does_not_mutate_input_dict(self):
        base = _types()
        types, key, _ = meeting_app.ensure_meeting_type(base, "Ny type")
        assert key not in base
        assert key in types

    def test_slug_collision_gets_numeric_suffix(self):
        base = _types()
        base["ny-type"] = meeting_tool._normalize_meeting_type({"navn": "Noget andet"})
        _, key, created = meeting_app.ensure_meeting_type(base, "Ny type")
        assert created is True
        assert key == "ny-type-2"

    def test_empty_name_raises(self):
        with pytest.raises(ValueError):
            meeting_app.ensure_meeting_type(_types(), "   ")


class TestOverrideDetaljeniveau:
    def test_returns_copy_with_new_level(self):
        t = _types()["driftledelse"]
        out = meeting_app.override_detaljeniveau(t, "grundig")
        assert out["detaljeniveau"] == "grundig"
        assert out is not t
        assert t["detaljeniveau"] == "kortfattet"  # original uændret

    def test_same_level_returns_original(self):
        t = _types()["driftledelse"]
        assert meeting_app.override_detaljeniveau(t, "kortfattet") is t

    def test_invalid_or_none_level_returns_original(self):
        t = _types()["driftledelse"]
        assert meeting_app.override_detaljeniveau(t, "episk") is t
        assert meeting_app.override_detaljeniveau(t, None) is t


class TestPersistMeetingTypes:
    def test_writes_json_and_returns_none(self, tmp_path):
        path = tmp_path / "meeting_types.json"
        assert meeting_app.persist_meeting_types(_types(), path) is None
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["driftledelse"]["navn"] == "Driftledelsesmøde"

    def test_oserror_returns_message(self, tmp_path):
        # tmp_path er en mappe → write_text fejler med OSError
        msg = meeting_app.persist_meeting_types(_types(), tmp_path)
        assert msg is not None and "Kunne ikke gemme" in msg


from types import MethodType, SimpleNamespace


def _fake_app(tmp_path, monkeypatch, types=None):
    """Minimal MeetingApp-attrap: kun det _save_adhoc_type rører."""
    monkeypatch.setattr(
        meeting_app.MeetingTypesTab, "TYPES_FILE", tmp_path / "meeting_types.json"
    )
    calls = []
    app = SimpleNamespace(
        _meeting_types=types if types is not None else _types(),
        _type_keys=["driftledelse"],
        _on_meeting_types_changed=lambda: calls.append("changed"),
        _types_tab=SimpleNamespace(reload_from_disk=lambda: calls.append("tab-reload")),
        status_var=SimpleNamespace(set=lambda s: calls.append(("status", s))),
    )
    app.calls = calls
    app._save_adhoc_type = MethodType(meeting_app.MeetingApp._save_adhoc_type, app)
    return app


class TestSaveAdhocType:
    def test_creates_persists_and_refreshes(self, tmp_path, monkeypatch):
        app = _fake_app(tmp_path, monkeypatch)
        key = app._save_adhoc_type("Ansættelsessamtale")
        assert key == "ansaettelsessamtale"
        data = json.loads(
            (tmp_path / "meeting_types.json").read_text(encoding="utf-8")
        )
        assert data[key]["navn"] == "Ansættelsessamtale"
        assert "changed" in app.calls and "tab-reload" in app.calls

    def test_existing_name_reuses_without_writing(self, tmp_path, monkeypatch):
        app = _fake_app(tmp_path, monkeypatch)
        key = app._save_adhoc_type("Driftledelsesmøde")
        assert key == "driftledelse"
        assert not (tmp_path / "meeting_types.json").exists()
        assert app.calls == []

    def test_write_failure_keeps_type_in_memory(self, tmp_path, monkeypatch):
        app = _fake_app(tmp_path, monkeypatch)
        # Peg TYPES_FILE på en mappe → persist fejler med OSError
        monkeypatch.setattr(meeting_app.MeetingTypesTab, "TYPES_FILE", tmp_path)
        key = app._save_adhoc_type("Ansættelsessamtale")
        assert key == "ansaettelsessamtale"
        assert key in app._meeting_types          # brugbar for DETTE møde
        assert key in app._type_keys
        assert "changed" not in app.calls          # intet disk-refresh
        assert any(c[0] == "status" for c in app.calls if isinstance(c, tuple))


class TestClearMeetingFields:
    def test_clear_meeting_fields_resets_level_override(self):
        """Nulstiller referatniveau-overstyring når mødet skabes på ny."""
        # Opret en fake app med de attributter som _clear_meeting_fields rører
        app = SimpleNamespace(
            meeting_form_var=SimpleNamespace(set=lambda s: None),
            type_var=SimpleNamespace(set=lambda s: None),
            name_var=SimpleNamespace(set=lambda s: None),
            attendees_var=SimpleNamespace(set=lambda s: None),
            _refresh_summary=lambda: None,
            referat_level_override="grundig",  # Sæt til en ikke-None værdi først
        )
        # Bind metoden til fake app'en
        app._clear_meeting_fields = MethodType(meeting_app.MeetingApp._clear_meeting_fields, app)
        # Kald metoden
        app._clear_meeting_fields()
        # Bekræft at overstyringen blev nulstillet
        assert app.referat_level_override is None


def _fake_tab(tmp_path, monkeypatch, seg_label="Kort", types=None):
    """Minimal TranscribeFileTab-attrap: kun det _resolve_meeting_type rører."""
    monkeypatch.setattr(
        meeting_app.MeetingTypesTab, "TYPES_FILE", tmp_path / "meeting_types.json"
    )
    calls = []
    t = types if types is not None else _types()
    first = next(iter(t))
    tab = SimpleNamespace(
        _meeting_types=t,
        _type_keys=list(t),
        _type_key=first,
        type_var=SimpleNamespace(_v=t[first]["navn"]),
        _level_seg=SimpleNamespace(get=lambda: seg_label),
        status_var=SimpleNamespace(set=lambda s: calls.append(("status", s))),
    )

    def _on_types_changed():
        calls.append("changed")
        # Spejl den virkelige kæde: refresh_meeting_types → _sync_level_seg
        # nulstiller vælgeren til typens eget niveau.
        tab._level_seg.get = lambda: meeting_app._NIVEAU_LABELS[
            tab._meeting_types[tab._type_key]["detaljeniveau"]
        ]
    tab._on_types_changed = _on_types_changed
    tab.type_var.get = lambda: tab.type_var._v
    tab.type_var.set = lambda v: setattr(tab.type_var, "_v", v)
    tab.calls = calls
    tab._resolve_meeting_type = MethodType(
        meeting_app.TranscribeFileTab._resolve_meeting_type, tab
    )
    return tab


class TestResolveMeetingType:
    def test_same_type_same_level_returns_original(self, tmp_path, monkeypatch):
        tab = _fake_tab(tmp_path, monkeypatch, seg_label="Kort")  # driftledelse = kortfattet
        result = tab._resolve_meeting_type()
        assert result is tab._meeting_types["driftledelse"]
        assert tab.calls == []
        assert not (tmp_path / "meeting_types.json").exists()

    def test_level_differs_returns_copy(self, tmp_path, monkeypatch):
        tab = _fake_tab(tmp_path, monkeypatch, seg_label="Grundig")
        result = tab._resolve_meeting_type()
        assert result["detaljeniveau"] == "grundig"
        assert tab._meeting_types["driftledelse"]["detaljeniveau"] == "kortfattet"
        assert result is not tab._meeting_types["driftledelse"]

    def test_typed_new_name_creates_persists_and_overrides(self, tmp_path, monkeypatch):
        tab = _fake_tab(tmp_path, monkeypatch, seg_label="Grundig")
        tab.type_var.set("Ansættelsessamtale")
        result = tab._resolve_meeting_type()
        assert tab._type_key == "ansaettelsessamtale"
        assert tab.type_var.get() == "Ansættelsessamtale"
        assert "changed" in tab.calls
        data = json.loads((tmp_path / "meeting_types.json").read_text(encoding="utf-8"))
        assert data["ansaettelsessamtale"]["detaljeniveau"] == "balanceret"  # typen selv
        assert result["detaljeniveau"] == "grundig"  # kørslens kopi

    def test_typed_existing_name_reuses_without_writing(self, tmp_path, monkeypatch):
        tab = _fake_tab(tmp_path, monkeypatch, seg_label="Kort")
        tab.type_var.set("  driftledelsesmøde ")
        result = tab._resolve_meeting_type()
        assert tab._type_key == "driftledelse"
        assert not (tmp_path / "meeting_types.json").exists()
        assert "changed" not in tab.calls
        assert result is tab._meeting_types["driftledelse"]

    def test_write_failure_keeps_type_in_memory(self, tmp_path, monkeypatch):
        tab = _fake_tab(tmp_path, monkeypatch, seg_label="Mellem")
        monkeypatch.setattr(meeting_app.MeetingTypesTab, "TYPES_FILE", tmp_path)  # mappe → OSError
        tab.type_var.set("Ansættelsessamtale")
        result = tab._resolve_meeting_type()
        assert tab._type_key == "ansaettelsessamtale"
        assert "ansaettelsessamtale" in tab._meeting_types
        assert "changed" not in tab.calls
        assert any(isinstance(c, tuple) and c[0] == "status" for c in tab.calls)
        assert result["detaljeniveau"] == "balanceret"
