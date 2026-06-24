"""Tests for write_pdf_from_markdown — best-effort PDF via pandoc.

subprocess.run mockes (jf. conftest.mock_subprocess), så ingen rigtig pandoc
kører under test.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_tool as mt


@pytest.fixture
def md_file(tmp_path: Path) -> Path:
    p = tmp_path / "Referat møde.md"
    p.write_text("# Referat\n\nÆøå-indhold.\n", encoding="utf-8")
    return p


class TestWritePdfFromMarkdown:
    def test_calls_pandoc_with_weasyprint_and_returns_pdf_path(
        self, mock_subprocess, md_file, tmp_path
    ):
        pdf_path = tmp_path / "Referat møde.pdf"
        result = mt.write_pdf_from_markdown(md_file, pdf_path)

        assert result == pdf_path
        assert mock_subprocess.run.called
        cmd = mock_subprocess.run.call_args[0][0]
        assert cmd[0] == "pandoc"
        assert str(md_file) in cmd
        assert str(pdf_path) in cmd
        # Engine kan være "weasyprint" (fallback) eller en absolut sti til
        # weasyprint (når _find_weasyprint lokaliserer den uden for PATH).
        assert any(
            c.startswith("--pdf-engine=") and c.endswith("weasyprint")
            for c in cmd
        )

    def test_pandoc_missing_returns_none(self, mock_subprocess, md_file, tmp_path):
        mock_subprocess.run.side_effect = FileNotFoundError("pandoc not found")
        result = mt.write_pdf_from_markdown(md_file, tmp_path / "out.pdf")
        assert result is None

    def test_render_failure_returns_none(self, mock_subprocess, md_file, tmp_path):
        mock_subprocess.run.side_effect = subprocess.CalledProcessError(
            returncode=1, cmd=["pandoc"], stderr=b"render failed"
        )
        result = mt.write_pdf_from_markdown(md_file, tmp_path / "out.pdf")
        assert result is None


class TestSaveOutputPdf:
    def test_minutes_triggers_pdf_of_referat(self, mocker, tmp_path):
        pdf = mocker.patch("meeting_tool.write_pdf_from_markdown")
        mt.save_output(
            output_dir=tmp_path,
            date="03-06-2026",
            transcript="[00:00 - 00:05] Hej.",
            minutes="# Referat\n\nIndhold.",
            name_base="møde",
        )
        pdf.assert_called_once_with(
            tmp_path / "Referat møde.md",
            tmp_path / "Referat møde.pdf",
        )

    def test_no_minutes_no_pdf(self, mocker, tmp_path):
        pdf = mocker.patch("meeting_tool.write_pdf_from_markdown")
        mt.save_output(
            output_dir=tmp_path,
            date="03-06-2026",
            transcript="[00:00 - 00:05] Hej.",
            minutes=None,
            name_base="møde",
        )
        assert not pdf.called
