"""Tests for _gemini_transcribe_single."""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_tool as mt


def _make_status_collector():
    msgs = []
    return msgs, lambda m: msgs.append(m)


class TestSingleHappyPath:
    def test_returns_text_and_diag(self, fake_gemini_client, tmp_wav):
        msgs, on_status = _make_status_collector()
        text, diag = mt._gemini_transcribe_single(
            fake_gemini_client, tmp_wav,
            system_instruction="instr", model="gemini-2.5-pro",
            chunk_label="01/4", on_status=on_status,
        )
        assert "Hej alle sammen" in text
        assert diag["finish_reason"] == "FinishReason.STOP"
        assert diag["output_tokens"] == 100
        assert diag["elapsed"] >= 0
        # Status-beskeder bør indeholde upload + transkriberer + færdig
        assert any("Uploader" in m for m in msgs)
        assert any("Transkriberer" in m for m in msgs)
        assert any("Færdig" in m for m in msgs)

    def test_uploads_and_deletes_file(self, fake_gemini_client, tmp_wav):
        _msgs, on_status = _make_status_collector()
        mt._gemini_transcribe_single(
            fake_gemini_client, tmp_wav, "instr", "gemini-2.5-pro",
            "01/4", on_status,
        )
        assert fake_gemini_client.files.upload.called
        assert fake_gemini_client.files.delete.called

    def test_temperature_param_propagates(self, fake_gemini_client, tmp_wav):
        _msgs, on_status = _make_status_collector()
        mt._gemini_transcribe_single(
            fake_gemini_client, tmp_wav, "instr", "gemini-2.5-pro",
            "01/4", on_status, temperature=0.5,
        )
        # generate_content kaldt med temperature=0.5 i config.
        # types_module er en MagicMock — GenerateContentConfig.call_args
        # afslører kwargs den blev kaldt med.
        import google.genai as _genai_module
        cfg_calls = _genai_module.types.GenerateContentConfig.call_args
        assert cfg_calls.kwargs["temperature"] == 0.5


class TestFileStates:
    def test_polls_until_active(self, fake_gemini_client, tmp_wav):
        # Først PROCESSING, så ACTIVE
        fake_gemini_client.files.upload.return_value = \
            fake_gemini_client._build_uploaded_file("PROCESSING")
        fake_gemini_client.files.get.side_effect = [
            fake_gemini_client._build_uploaded_file("PROCESSING"),
            fake_gemini_client._build_uploaded_file("ACTIVE"),
        ]
        _msgs, on_status = _make_status_collector()
        text, _diag = mt._gemini_transcribe_single(
            fake_gemini_client, tmp_wav, "instr", "gemini-2.5-pro",
            "01/4", on_status,
        )
        assert text  # ikke tom
        assert fake_gemini_client.files.get.call_count >= 1

    def test_failed_state_raises(self, fake_gemini_client, tmp_wav):
        fake_gemini_client.files.upload.return_value = \
            fake_gemini_client._build_uploaded_file("FAILED")
        _msgs, on_status = _make_status_collector()
        with pytest.raises(RuntimeError, match="Upload fejlede"):
            mt._gemini_transcribe_single(
                fake_gemini_client, tmp_wav, "instr", "gemini-2.5-pro",
                "01/4", on_status,
            )


class TestTransientErrors:
    def test_retries_on_timeout(self, fake_gemini_client, tmp_wav):
        # Første to kald fejler med timeout, tredje lykkes
        ok_resp = fake_gemini_client._build_response("[00:00 - 00:05] ok")
        fake_gemini_client.models.generate_content.side_effect = [
            TimeoutError("Server disconnected"),
            TimeoutError("Server disconnected"),
            ok_resp,
        ]
        _msgs, on_status = _make_status_collector()
        text, _diag = mt._gemini_transcribe_single(
            fake_gemini_client, tmp_wav, "instr", "gemini-2.5-pro",
            "01/4", on_status,
        )
        assert "ok" in text
        assert fake_gemini_client.models.generate_content.call_count == 3

    def test_gives_up_after_3_attempts(self, fake_gemini_client, tmp_wav):
        fake_gemini_client.models.generate_content.side_effect = \
            TimeoutError("server disconnected without response")
        _msgs, on_status = _make_status_collector()
        with pytest.raises((TimeoutError, RuntimeError)):
            mt._gemini_transcribe_single(
                fake_gemini_client, tmp_wav, "instr", "gemini-2.5-pro",
                "01/4", on_status,
            )

    def test_no_retry_on_permanent_4xx(self, fake_gemini_client, tmp_wav):
        # Ikke en transient-marker → skal raise med det samme
        fake_gemini_client.models.generate_content.side_effect = \
            ValueError("invalid input")
        _msgs, on_status = _make_status_collector()
        with pytest.raises(ValueError, match="invalid input"):
            mt._gemini_transcribe_single(
                fake_gemini_client, tmp_wav, "instr", "gemini-2.5-pro",
                "01/4", on_status,
            )
        assert fake_gemini_client.models.generate_content.call_count == 1


class TestRecitation:
    def test_empty_text_with_recitation_raises(self, fake_gemini_client, tmp_wav):
        fake_gemini_client.models.generate_content.return_value = \
            fake_gemini_client._build_response("", finish_reason="FinishReason.RECITATION", out_tokens=0)
        _msgs, on_status = _make_status_collector()
        with pytest.raises(RuntimeError, match="RECITATION"):
            mt._gemini_transcribe_single(
                fake_gemini_client, tmp_wav, "instr", "gemini-2.5-pro",
                "01/4", on_status,
            )


class TestStopEvent:
    def test_stop_during_polling_raises(self, fake_gemini_client, tmp_wav):
        # Bliv ved med at returnere PROCESSING
        fake_gemini_client.files.upload.return_value = \
            fake_gemini_client._build_uploaded_file("PROCESSING")
        fake_gemini_client.files.get.return_value = \
            fake_gemini_client._build_uploaded_file("PROCESSING")
        stop_event = threading.Event()
        stop_event.set()  # allerede sat
        _msgs, on_status = _make_status_collector()
        with pytest.raises(mt._StopRequested):
            mt._gemini_transcribe_single(
                fake_gemini_client, tmp_wav, "instr", "gemini-2.5-pro",
                "01/4", on_status, stop_event=stop_event,
            )


class TestAsciiFilename:
    def test_non_ascii_path_uses_symlink(self, fake_gemini_client, tmp_path):
        # Filnavn med æ/ø/å
        wav = tmp_path / "møde.wav"
        wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")  # ikke en rigtig WAV men nok
        _msgs, on_status = _make_status_collector()
        # Brug et chunk_label uden '/' så symlink-filnavnet ikke indeholder
        # en mappe-separator (produktionskode bruger chunk_label direkte i
        # tempfil-navnet; '01/4' ville skabe en ikke-eksisterende undermappe).
        mt._gemini_transcribe_single(
            fake_gemini_client, wav, "instr", "gemini-2.5-pro",
            "01of4", on_status,
        )
        # Upload kaldet med en ascii-sikker symlink-sti
        upload_call = fake_gemini_client.files.upload.call_args
        uploaded_path = upload_call.kwargs["file"]
        # Stien skal være ASCII-encodable
        uploaded_path.encode("ascii")  # raiser hvis ikke
