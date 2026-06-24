"""Tests for save_output."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_tool as mt


class TestSaveOutput:
    def test_writes_both_files(self, tmp_path):
        mt.save_output(
            output_dir=tmp_path,
            date="22-05-2026",
            transcript="[00:00 - 00:05] Hej.",
            minutes="# Referat\n\nDet gik godt.",
            name_base="Driftledelsesmøde 22-05-2026",
        )
        transcript_file = tmp_path / "Transkription Driftledelsesmøde 22-05-2026.md"
        referat_file = tmp_path / "Referat Driftledelsesmøde 22-05-2026.md"
        assert transcript_file.exists()
        assert referat_file.exists()

    def test_minutes_none_skips_referat(self, tmp_path):
        mt.save_output(
            output_dir=tmp_path,
            date="22-05-2026",
            transcript="[00:00 - 00:05] Hej.",
            minutes=None,
            name_base="møde1",
        )
        assert (tmp_path / "Transkription møde1.md").exists()
        assert not (tmp_path / "Referat møde1.md").exists()

    def test_transcript_content_format(self, tmp_path):
        mt.save_output(
            output_dir=tmp_path,
            date="22-05-2026",
            transcript="[00:00 - 00:05] Linje 1.",
            minutes=None,
            name_base="m",
        )
        content = (tmp_path / "Transkription m.md").read_text(encoding="utf-8")
        assert content.startswith("# Transkription - m")
        assert "Dato: 22-05-2026" in content
        assert "[00:00 - 00:05] Linje 1." in content

    def test_referat_content_is_minutes_verbatim(self, tmp_path):
        mt.save_output(
            output_dir=tmp_path,
            date="22-05-2026",
            transcript="x",
            minutes="# Referat\n\nDet gik godt.",
            name_base="m",
        )
        content = (tmp_path / "Referat m.md").read_text(encoding="utf-8")
        assert content == "# Referat\n\nDet gik godt.\n"

    def test_name_base_defaults_to_date(self, tmp_path):
        mt.save_output(
            output_dir=tmp_path,
            date="22-05-2026",
            transcript="x",
            minutes=None,
        )
        # Når name_base ikke er sat, bruges date
        assert (tmp_path / "Transkription 22-05-2026.md").exists()

    def test_creates_output_dir_if_missing(self, tmp_path):
        target = tmp_path / "ny" / "undermappe"
        # target findes ikke
        mt.save_output(
            output_dir=target,
            date="22-05-2026",
            transcript="x",
            minutes=None,
            name_base="m",
        )
        assert target.exists()
        assert (target / "Transkription m.md").exists()
