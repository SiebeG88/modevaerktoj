"""GUI-tests: ad-hoc mødetype + referatniveau-vælger i guidens trin 2.

Kræver et display (Tk). Springes over hvis ingen findes eller Tk >= 9
(samme guard som test_meeting_types_refresh)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

ctk = pytest.importorskip("customtkinter")
import tkinter

try:
    _probe_root = tkinter.Tk()
    _tk_version = tuple(
        int(x) for x in _probe_root.tk.call("info", "patchlevel").split(".")[:2]
    )
    _probe_root.destroy()
    if _tk_version >= (9, 0):
        pytest.skip(
            "Tk 9.x er inkompatibel med customtkinter CTkScrollableFrame — spring over",
            allow_module_level=True,
        )
except Exception:  # pragma: no cover - kun på headless miljøer
    pytest.skip("Intet Tk-display tilgængeligt", allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_app
import meeting_tool


@pytest.fixture
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture
def fake_app(root, tmp_path, monkeypatch):
    """Minimal app-attrap til MeetingWizard: rigtig _save_adhoc_type,
    resten SimpleNamespace. Typefil isoleret til tmp_path."""
    monkeypatch.setattr(
        meeting_app.MeetingTypesTab, "TYPES_FILE", tmp_path / "meeting_types.json"
    )
    types = {
        "driftledelse": meeting_tool._normalize_meeting_type(
            {"navn": "Driftledelsesmøde", "detaljeniveau": "kortfattet"}
        ),
    }
    app = SimpleNamespace(
        root=root,
        _meeting_types=types,
        _type_keys=list(types),
        _type_key="driftledelse",
        type_var=ctk.StringVar(value="Driftledelsesmøde"),
        referat_level_override=None,
        _types_tab=SimpleNamespace(reload_from_disk=lambda: None),
        status_var=SimpleNamespace(set=lambda s: None),
        tmp_path=tmp_path,
    )

    # Stubbene skal spejle den RIGTIGE MeetingApp-adfærd, som produktions-
    # koden er afhængig af (attaches efter, da SimpleNamespace ikke kan
    # selv-referere under konstruktion):

    def _on_meeting_types_changed():
        # Som MeetingApp.refresh_meeting_types: genindlæs typerne fra disk,
        # så den nyoprettede ad-hoc type findes i app._meeting_types.
        app._meeting_types = meeting_tool.load_meeting_types(tmp_path)
        app._type_keys = list(app._meeting_types)

    def _on_type_selected(label):
        # Som MeetingApp._on_type_selected: nyt typevalg nulstiller
        # pr.-møde-overstyringen.
        app.referat_level_override = None

    app._on_meeting_types_changed = _on_meeting_types_changed
    app._on_type_selected = _on_type_selected
    app._save_adhoc_type = MethodType(meeting_app.MeetingApp._save_adhoc_type, app)
    return app


def _wizard_on_type_step(fake_app):
    wiz = meeting_app.MeetingWizard(fake_app, start_step=1)
    wiz.withdraw()
    return wiz


def test_commit_adhoc_creates_type_and_selects_it(fake_app):
    wiz = _wizard_on_type_step(fake_app)
    wiz._adhoc_var.set("Ansættelsessamtale")
    wiz._select_adhoc()
    wiz._commit_adhoc_type()
    assert fake_app._type_key == "ansaettelsessamtale"
    assert fake_app.type_var.get() == "Ansættelsessamtale"
    data = json.loads(
        (fake_app.tmp_path / "meeting_types.json").read_text(encoding="utf-8")
    )
    assert data["ansaettelsessamtale"]["detaljeniveau"] == "balanceret"
    wiz.destroy()


def test_commit_with_empty_name_keeps_existing_selection(fake_app):
    wiz = _wizard_on_type_step(fake_app)
    wiz._select_adhoc()          # aktivt, men feltet er tomt
    wiz._commit_adhoc_type()
    assert fake_app._type_key == "driftledelse"
    assert not (fake_app.tmp_path / "meeting_types.json").exists()
    wiz.destroy()


def test_level_selector_defaults_to_type_level(fake_app):
    wiz = _wizard_on_type_step(fake_app)
    assert wiz._level_seg.get() == "Kort"          # driftledelse = kortfattet
    assert fake_app.referat_level_override is None
    wiz.destroy()


def test_choosing_other_level_sets_override_only(fake_app):
    wiz = _wizard_on_type_step(fake_app)
    wiz._on_level_selected("Grundig")
    assert fake_app.referat_level_override == "grundig"
    # Typen selv er urørt
    assert fake_app._meeting_types["driftledelse"]["detaljeniveau"] == "kortfattet"
    # Vælges typens eget niveau igen → ingen overstyring
    wiz._on_level_selected("Kort")
    assert fake_app.referat_level_override is None
    wiz.destroy()


def test_type_click_resets_override_and_syncs_selector(fake_app):
    wiz = _wizard_on_type_step(fake_app)
    wiz._on_level_selected("Grundig")
    wiz._select_type("driftledelse")
    assert fake_app.referat_level_override is None
    assert wiz._level_seg.get() == "Kort"
    wiz.destroy()


def test_override_survives_adhoc_commit(fake_app):
    wiz = _wizard_on_type_step(fake_app)
    wiz._adhoc_var.set("Ansættelsessamtale")
    wiz._select_adhoc()
    wiz._on_level_selected("Grundig")   # overstyring for ad-hoc mødet
    wiz._commit_adhoc_type()
    assert fake_app.referat_level_override == "grundig"
    wiz.destroy()
