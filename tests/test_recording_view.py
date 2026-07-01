"""Aktiv optage-skærm: undertekst må KUN være mødenavnet (aldrig motor).

Testen instantierer app'en (display-guarded), men selve reglen er ren logik."""
from __future__ import annotations
import json, sys
from pathlib import Path
import pytest

ctk = pytest.importorskip("customtkinter")
import tkinter
try:
    _r = tkinter.Tk()
    _v = tuple(int(x) for x in _r.tk.call("info", "patchlevel").split(".")[:2])
    _r.destroy()
    if _v >= (9, 0):
        pytest.skip("Tk 9.x inkompatibel med CTk", allow_module_level=True)
except Exception:
    pytest.skip("Intet Tk-display", allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    (tmp_path / "meeting_types.json").write_text(
        json.dumps({"a": {"navn": "Møde A"}}), encoding="utf-8")
    monkeypatch.setattr(meeting_app, "TOOL_DIR", tmp_path)
    monkeypatch.setattr(meeting_app, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(meeting_app, "load_state", lambda: {})
    monkeypatch.setattr(meeting_app.MeetingApp, "_open_wizard", lambda self, s=0: None)
    root = ctk.CTk(); root.withdraw()
    a = meeting_app.MeetingApp(root)
    yield a
    root.destroy()


def test_active_subtitle_is_only_meeting_name(app):
    app.name_var.set("Bestyrelsesmøde Q3")
    app.engine_var.set("gemini")
    sub = app._active_subtitle_text()
    assert sub == "Bestyrelsesmøde Q3"
    assert "gemini" not in sub.lower()
    assert "hviske" not in sub.lower()


def test_active_subtitle_falls_back_to_type(app):
    app.name_var.set("   ")
    app.type_var.set("Møde A")
    assert app._active_subtitle_text() == "Møde A"
