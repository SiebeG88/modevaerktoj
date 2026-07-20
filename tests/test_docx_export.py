"""Word-renderer: metadata-hoved + markdown→docx uden GUI."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
docx = pytest.importorskip("docx")
import docx_export


def _para_texts(doc):
    return [p.text for p in doc.paragraphs]


def _style_of(doc, text):
    for p in doc.paragraphs:
        if p.text == text:
            return p.style.name
    return None


def test_header_has_all_metadata(tmp_path):
    out = docx_export.write_meeting_docx(
        tmp_path / "r.docx", title="Ugemøde", date="20-07-2026",
        meeting_type_name="Driftmøde", attendees=["Ole", "Jacob"],
        body="Noget indhold.", is_transcript=False)
    doc = docx.Document(str(out))
    texts = _para_texts(doc)
    assert any(p.text == "Ugemøde" for p in doc.paragraphs)
    assert "Dato: 20-07-2026" in texts
    assert "Mødetype: Driftmøde" in texts
    assert "Deltagere: Ole, Jacob" in texts


def test_empty_attendees_omits_line(tmp_path):
    out = docx_export.write_meeting_docx(
        tmp_path / "r.docx", title="M", date="20-07-2026",
        meeting_type_name="Møde", attendees=[], body="x", is_transcript=False)
    doc = docx.Document(str(out))
    assert not any(p.text.startswith("Deltagere:") for p in doc.paragraphs)


def test_markdown_headings_and_lists(tmp_path):
    body = "## Beslutninger\n\n- Punkt et\n- Punkt to\n\n1. Først\n"
    out = docx_export.write_meeting_docx(
        tmp_path / "r.docx", title="M", date="d",
        meeting_type_name=None, attendees=[], body=body, is_transcript=False)
    doc = docx.Document(str(out))
    assert _style_of(doc, "Beslutninger") == "Heading 2"
    assert _style_of(doc, "Punkt et") == "List Bullet"
    assert _style_of(doc, "Først") == "List Number"


def test_bold_becomes_bold_run(tmp_path):
    out = docx_export.write_meeting_docx(
        tmp_path / "r.docx", title="M", date="d",
        meeting_type_name=None, attendees=[], body="Dette er **vigtigt** nu",
        is_transcript=False)
    doc = docx.Document(str(out))
    p = next(p for p in doc.paragraphs if "vigtigt" in p.text)
    bold_runs = [r.text for r in p.runs if r.bold]
    assert "vigtigt" in bold_runs


def test_transcript_keeps_speaker_lines_as_paragraphs(tmp_path):
    body = "Ole: Hej alle\nJacob: Godmorgen\n"
    out = docx_export.write_meeting_docx(
        tmp_path / "t.docx", title="T", date="d",
        meeting_type_name=None, attendees=[], body=body, is_transcript=True)
    doc = docx.Document(str(out))
    texts = _para_texts(doc)
    assert "Ole: Hej alle" in texts
    assert "Jacob: Godmorgen" in texts
