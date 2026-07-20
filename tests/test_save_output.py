"""save_output skriver Word-dokumenter (transkription + referat)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
docx = pytest.importorskip("docx")
import meeting_tool


def test_writes_docx_for_transcript_and_minutes(tmp_path):
    meeting_tool.save_output(
        output_dir=tmp_path, date="20-07-2026",
        transcript="Ole: hej", minutes="## Referat\n\n- punkt",
        name_base="Ugemøde",
        meeting_type={"navn": "Driftmøde"}, attendees=["Ole"])
    assert (tmp_path / "Transkription Ugemøde.docx").exists()
    assert (tmp_path / "Referat Ugemøde.docx").exists()
    assert not (tmp_path / "Referat Ugemøde.pdf").exists()
    assert not (tmp_path / "Referat Ugemøde.md").exists()
    doc = docx.Document(str(tmp_path / "Referat Ugemøde.docx"))
    assert any(p.text == "Mødetype: Driftmøde" for p in doc.paragraphs)


def test_no_minutes_writes_only_transcript(tmp_path):
    meeting_tool.save_output(
        output_dir=tmp_path, date="d", transcript="x", minutes=None,
        name_base="M", meeting_type=None, attendees=[])
    assert (tmp_path / "Transkription M.docx").exists()
    assert not (tmp_path / "Referat M.docx").exists()


def test_docx_failure_falls_back_to_md(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("docx nede")
    monkeypatch.setattr(meeting_tool.docx_export, "write_meeting_docx", boom)
    meeting_tool.save_output(
        output_dir=tmp_path, date="d", transcript="raw tekst", minutes="referat",
        name_base="M", meeting_type=None, attendees=[])
    assert (tmp_path / "Transkription M.md").exists()
    assert (tmp_path / "Referat M.md").exists()
    assert "referat" in (tmp_path / "Referat M.md").read_text(encoding="utf-8")
