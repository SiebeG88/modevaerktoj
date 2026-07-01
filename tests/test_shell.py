"""Sidebar + view-switch. Display-guarded (samme mønster som test_meeting_types_refresh)."""
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


def test_views_exist(app):
    for name in ("optag", "transkriber", "historik", "indstillinger"):
        assert name in app._views


def test_show_view_switches_visibility(app):
    app._show_view("historik")
    assert app._current_view == "historik"
    assert app._views["historik"].winfo_manager() != ""      # pakket
    assert app._views["optag"].winfo_manager() == ""          # skjult


def test_no_tabview_attribute(app):
    # CTkTabview er fjernet til fordel for sidebar.
    assert not hasattr(app, "_tabview")
