"""Tests for transcribe_with_gemini — single-shot + chunked + retry-logik."""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_tool as mt


def _collect_status():
    msgs = []
    return msgs, lambda m: msgs.append(m)


@pytest.fixture
def mock_ffprobe_short(mocker):
    """ffprobe rapporterer 10 minutter → single-shot path."""
    mocker.patch("meeting_tool._ffprobe_duration", return_value=10 * 60)


@pytest.fixture
def mock_ffprobe_long(mocker):
    """ffprobe rapporterer 44 minutter → chunked path (4 chunks á 12 min)."""
    mocker.patch("meeting_tool._ffprobe_duration", return_value=44 * 60)


@pytest.fixture
def mock_chunk_split(mocker, tmp_path):
    """_split_audio_for_gemini returnerer 4 fake chunk-paths."""
    paths = [tmp_path / f"chunk_{i}.wav" for i in range(4)]
    for p in paths:
        p.write_bytes(b"RIFF")
    mocker.patch("meeting_tool._split_audio_for_gemini", return_value=paths)
    return paths


class TestNoApiKey:
    def test_missing_api_key_raises(self, fake_gemini_client, tmp_wav,
                                     mock_ffprobe_short, monkeypatch):
        """RuntimeError når GEMINI_API_KEY og GOOGLE_API_KEY begge mangler."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            mt.transcribe_with_gemini(tmp_wav)


class TestOnStatusNone:
    def test_on_status_none_uses_print(self, fake_gemini_client, tmp_wav,
                                        mock_ffprobe_short, capsys):
        """on_status=None → print() bruges (linje 1154)."""
        result = mt.transcribe_with_gemini(tmp_wav, on_status=None)
        assert result  # ikke tom
        out = capsys.readouterr().out
        assert len(out) > 0  # noget er printet


class TestSingleShot:
    def test_happy_path(self, fake_gemini_client, tmp_wav, mock_ffprobe_short):
        _msgs, on_status = _collect_status()
        result = mt.transcribe_with_gemini(
            tmp_wav, attendees=["A", "B"], model="gemini-2.5-pro",
            on_status=on_status,
        )
        assert "Hej alle sammen" in result
        assert fake_gemini_client.models.generate_content.call_count == 1

    def test_recitation_triggers_retry(self, fake_gemini_client, tmp_wav,
                                       mock_ffprobe_short):
        # Første kald: RECITATION-empty. Andet kald: success.
        empty = fake_gemini_client._build_response(
            "", finish_reason="FinishReason.RECITATION", out_tokens=0)
        ok = fake_gemini_client._build_response(
            "[00:00 - 00:05] reddet")
        fake_gemini_client.models.generate_content.side_effect = [empty, ok]

        _msgs, on_status = _collect_status()
        result = mt.transcribe_with_gemini(
            tmp_wav, attendees=["A"], on_status=on_status,
        )
        assert "reddet" in result
        assert fake_gemini_client.models.generate_content.call_count == 2
        assert any("RECITATION" in m for m in _msgs)

    def test_non_recitation_error_not_retried(self, fake_gemini_client, tmp_wav,
                                              mock_ffprobe_short):
        # Empty med STOP (ikke RECITATION) → raiser umiddelbart, ingen retry
        empty_stop = fake_gemini_client._build_response(
            "", finish_reason="FinishReason.STOP", out_tokens=0)
        fake_gemini_client.models.generate_content.return_value = empty_stop

        _msgs, on_status = _collect_status()
        with pytest.raises(RuntimeError, match="Tom transkription"):
            mt.transcribe_with_gemini(tmp_wav, on_status=on_status)
        assert fake_gemini_client.models.generate_content.call_count == 1


class TestChunkedHappyPath:
    def test_all_chunks_succeed(self, fake_gemini_client, tmp_wav,
                                mock_ffprobe_long, mock_chunk_split):
        # 4 chunks, alle returnerer simpel transcript
        _msgs, on_status = _collect_status()
        result = mt.transcribe_with_gemini(
            tmp_wav, attendees=["A"], on_status=on_status, max_parallel=2,
        )
        # Vi forventer 4 chunk-calls
        assert fake_gemini_client.models.generate_content.call_count == 4
        # Output skal indeholde tekst og være flettet (ikke tom)
        assert len(result) > 0
        assert any("Alle 4 chunks færdige" in m for m in _msgs)


class TestChunkedRecitationRetry:
    def test_one_chunk_recitation_then_retry_succeeds(
        self, fake_gemini_client, tmp_wav, mock_ffprobe_long, mock_chunk_split,
    ):
        # Med max_parallel=1 er rækkefølgen deterministisk:
        #   Pass 1: 4 calls — chunk 0 (empty), 1-3 (good)
        #   Pass 2: 1 call — retry på chunk 0 (ok)
        empty = fake_gemini_client._build_response(
            "", finish_reason="FinishReason.RECITATION", out_tokens=0)
        good = fake_gemini_client._build_response("[00:00 - 00:30] normal")
        ok = fake_gemini_client._build_response("[00:00 - 00:30] reddet chunk")

        fake_gemini_client.models.generate_content.side_effect = [
            empty, good, good, good,  # pass 1: chunk 0 fejler
            ok,                        # pass 2: retry på chunk 0 lykkes
        ]

        _msgs, on_status = _collect_status()
        result = mt.transcribe_with_gemini(
            tmp_wav, on_status=on_status, max_parallel=1,
        )
        assert "reddet" in result or "normal" in result
        assert any("Genforsøger" in m for m in _msgs)
        # Ingen placeholder fordi retry'et lykkedes
        assert "MANGLER" not in result

    def test_chunk_still_fails_after_retry_placeholder_inserted(
        self, fake_gemini_client, tmp_wav, mock_ffprobe_long, mock_chunk_split,
    ):
        # Chunk 0 fejler både i pass 1 og pass 2 → placeholder forventes
        empty = fake_gemini_client._build_response(
            "", finish_reason="FinishReason.RECITATION", out_tokens=0)
        good = fake_gemini_client._build_response("[00:00 - 00:30] normal")
        # Pass 1: 4 chunks → empty + 3 good. Pass 2 (retry): empty igen.
        fake_gemini_client.models.generate_content.side_effect = [
            empty, good, good, good,  # pass 1
            empty,                     # retry på chunk 0
        ]
        _msgs, on_status = _collect_status()
        result = mt.transcribe_with_gemini(
            tmp_wav, on_status=on_status, max_parallel=1,
        )
        # Placeholder-linje skal indeholde "MANGLER" og chunk-nummer
        assert "MANGLER" in result
        assert "chunk 1/4" in result
        # Status: 3/4 OK, 1 placeholder
        assert any("med placeholder" in m for m in _msgs)


class TestStopEvent:
    def test_stop_event_propagates(self, fake_gemini_client, tmp_wav,
                                   mock_ffprobe_long, mock_chunk_split):
        stop_event = threading.Event()
        stop_event.set()

        _msgs, on_status = _collect_status()
        with pytest.raises(mt._StopRequested):
            mt.transcribe_with_gemini(
                tmp_wav, on_status=on_status, stop_event=stop_event,
                max_parallel=1,
            )

    def test_stop_without_stop_event_still_raises(
        self, fake_gemini_client, tmp_wav, mock_ffprobe_long, mock_chunk_split,
    ):
        """_StopRequested propagerer selv når stop_event=None (linje 1250->1252)."""
        # Sæt alle chunks til at raise _StopRequested
        fake_gemini_client.models.generate_content.side_effect = mt._StopRequested(
            "stop"
        )
        _msgs, on_status = _collect_status()
        with pytest.raises(mt._StopRequested):
            mt.transcribe_with_gemini(
                tmp_wav, on_status=on_status, stop_event=None, max_parallel=1,
            )


class TestStopDuringSerialRetry:
    def test_stop_event_during_retry_propagates(
        self, fake_gemini_client, tmp_wav, mock_ffprobe_long, mock_chunk_split,
    ):
        """_StopRequested under seriel genforsøg-fase (linje 1277-1278)."""
        # Pass 1: chunk 0 fejler (exception), chunks 1-3 ok
        good = fake_gemini_client._build_response("[00:00 - 00:30] normal")
        chunk_error = RuntimeError("transient fejl i chunk 0")

        # Definer: pass 1 → chunk 0 fejler, resten ok
        # Pass 2 (seriel retry) → raise _StopRequested
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            # Første kald (chunk 0 i pass 1): fail
            if call_count[0] == 1:
                raise RuntimeError("timeout: server disconnected")
            # Kald 2-4 (chunks 1-3): ok
            if call_count[0] <= 4:
                return good
            # Kald 5 (retry på chunk 0): stop
            raise mt._StopRequested("brugeren stoppede")

        fake_gemini_client.models.generate_content.side_effect = side_effect

        _msgs, on_status = _collect_status()
        with pytest.raises(mt._StopRequested):
            mt.transcribe_with_gemini(
                tmp_wav, on_status=on_status, max_parallel=1,
            )
