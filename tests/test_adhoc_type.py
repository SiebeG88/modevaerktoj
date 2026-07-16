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
