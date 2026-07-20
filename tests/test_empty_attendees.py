"""Deltager-defaults er tomme; huskede per-type deltagere bevares."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_app
import meeting_tool


def test_default_attendees_empty():
    assert meeting_app.DEFAULT_ATTENDEES == []
    assert meeting_tool.DEFAULT_ATTENDEES == []


def test_resolve_empty_when_nothing_remembered():
    mt = meeting_tool._normalize_meeting_type({"navn": "M"})  # deltagere=[]
    got = meeting_tool.resolve_type_attendees("m", mt, {}, meeting_tool.DEFAULT_ATTENDEES)
    assert got == []


def test_resolve_prefers_remembered():
    mt = meeting_tool._normalize_meeting_type({"navn": "M"})
    got = meeting_tool.resolve_type_attendees(
        "m", mt, {"m": ["Ole"]}, meeting_tool.DEFAULT_ATTENDEES)
    assert got == ["Ole"]
