"""GUI-tests: mødetype-dropdowns i optage- og transkribér-fanen skal opdateres
når der oprettes/redigeres/slettes en type i Mødetyper-fanen.

Kræver et display (Tk). Springes over hvis ingen findes (fx headless CI).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ctk = pytest.importorskip("customtkinter")
import tkinter

# Spring hele modulet over hvis der ikke er et display eller Tk-versionen
# er inkompatibel med customtkinter (Tk 9.x crash i CTkScrollableFrame).
try:
    _probe_root = tkinter.Tk()
    _tk_version = tuple(int(x) for x in _probe_root.tk.call("info", "patchlevel").split(".")[:2])
    _probe_root.destroy()
    if _tk_version >= (9, 0):
        pytest.skip(
            f"Tk {'.'.join(str(x) for x in _tk_version)} er inkompatibel med "
            "customtkinter CTkScrollableFrame — spring over",
            allow_module_level=True,
        )
except Exception:  # pragma: no cover - kun på headless miljøer
    pytest.skip("Intet Tk-display tilgængeligt", allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_app


@pytest.fixture
def tool_dir(tmp_path, monkeypatch):
    """Isolér mødetype-/state-filer til tmp_path, så testen ikke rører repoet."""
    (tmp_path / "meeting_types.json").write_text(
        json.dumps(
            {
                "driftledelse": {"navn": "Driftledelsesmøde"},
                "ledergruppe": {"navn": "Ledergruppemøde"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(meeting_app, "TOOL_DIR", tmp_path)
    monkeypatch.setattr(meeting_app, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(
        meeting_app.MeetingTypesTab, "TYPES_FILE", tmp_path / "meeting_types.json"
    )
    monkeypatch.setattr(meeting_app, "load_state", lambda: {})
    return tmp_path


@pytest.fixture
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


def _combo_values(combo):
    return list(combo.cget("values"))


def test_refresh_meeting_types_picks_up_new_type_from_disk(tool_dir, root):
    tab = meeting_app.TranscribeFileTab(ctk.CTkFrame(root))
    assert "Jobsamtaler" not in _combo_values(tab.type_combo)

    # En ny type skrives til disk udefra (svarer til en anden fane der gemmer).
    data = json.loads((tool_dir / "meeting_types.json").read_text(encoding="utf-8"))
    data["type1"] = {"navn": "Jobsamtaler"}
    (tool_dir / "meeting_types.json").write_text(
        json.dumps(data), encoding="utf-8"
    )

    tab.refresh_meeting_types()
    assert "Jobsamtaler" in _combo_values(tab.type_combo)


def test_creating_type_in_types_tab_updates_transcribe_combo(tool_dir, root):
    transcribe = meeting_app.TranscribeFileTab(ctk.CTkFrame(root))
    types = meeting_app.MeetingTypesTab(
        ctk.CTkFrame(root), on_change=transcribe.refresh_meeting_types
    )

    assert "Jobsamtaler" not in _combo_values(transcribe.type_combo)

    types._new()  # opretter "Ny mødetype 1"
    types.navn_var.set("Jobsamtaler")
    types._save()

    assert "Jobsamtaler" in _combo_values(transcribe.type_combo)


def test_deleting_selected_type_falls_back_without_crash(tool_dir, root):
    transcribe = meeting_app.TranscribeFileTab(ctk.CTkFrame(root))
    types = meeting_app.MeetingTypesTab(
        ctk.CTkFrame(root), on_change=transcribe.refresh_meeting_types
    )

    # Vælg "ledergruppe" i transkribér-fanen og slet den i Mødetyper.
    transcribe._on_type_selected("Ledergruppemøde")
    types._on_select("Ledergruppemøde")
    types._delete()

    values = _combo_values(transcribe.type_combo)
    assert "Ledergruppemøde" not in values
    # Markeringen skal pege på en type der stadig findes.
    assert transcribe._meeting_types[transcribe._type_key]["navn"] in values
