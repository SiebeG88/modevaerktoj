"""Tests for top-level optagelses-funktioner."""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_tool as mt


class TestRecordThenTranscribeGemini:
    def test_happy_path(self, mock_subprocess, fake_gemini_client, tmp_path,
                        mocker):
        """record_then_transcribe_gemini returnerer (wav_path, transcript) ved normal flow."""
        # ffmpeg-Popen-mock skal "skabe" master WAV.
        # Filnavn: "Driftledelsesmøde {date}.wav"  (med ø)
        # Cmd-struktur: [..., str(master_path), "-y", "-loglevel", "warning"]
        # => output-fil er ét slot *før* "-y"
        def popen_side_effect(cmd, **_):
            if isinstance(cmd, list) and "-y" in cmd:
                out_idx = cmd.index("-y") - 1
                Path(cmd[out_idx]).write_bytes(b"RIFF" * 1000)
            return mock_subprocess.proc

        mock_subprocess.Popen.side_effect = popen_side_effect

        mocker.patch("meeting_tool.transcribe_with_gemini",
                     return_value="[00:00 - 00:05] mocked transcript")

        wav_path, transcript = mt.record_then_transcribe_gemini(
            output_dir=tmp_path,
            date="22-05-2026",
            device_id=0,
            attendees=["A"],
        )
        assert wav_path.exists()
        assert "mocked transcript" in transcript

    def test_zero_size_wav_raises(self, mock_subprocess, fake_gemini_client,
                                  tmp_path, mocker):
        """RuntimeError når WAV-filen er 0 bytes (fejlbesked: 'ikke gemt')."""
        # Popen kører, men master_wav skabes med 0 bytes
        master_wav = tmp_path / "Driftledelsesmøde 22-05-2026.wav"
        master_wav.write_bytes(b"")  # 0 bytes — eksisterer men er tom

        mocker.patch("meeting_tool.transcribe_with_gemini",
                     return_value="ikke kaldt")

        with pytest.raises(RuntimeError, match="ikke gemt"):
            mt.record_then_transcribe_gemini(
                output_dir=tmp_path, date="22-05-2026", device_id=0,
            )

    def test_caffeinate_started_and_terminated(self, mock_subprocess,
                                               fake_gemini_client, tmp_path,
                                               mocker):
        """caffeinate startes og termineres uanset outcome."""
        caffeinate_proc = MagicMock()
        ffmpeg_proc = mock_subprocess.proc

        def popen_side(cmd, **_):
            if isinstance(cmd, list) and cmd[0] == "caffeinate":
                return caffeinate_proc
            # ffmpeg: skab WAV
            if isinstance(cmd, list) and "-y" in cmd:
                out_idx = cmd.index("-y") - 1
                Path(cmd[out_idx]).write_bytes(b"RIFF" * 1000)
            return ffmpeg_proc

        mock_subprocess.Popen.side_effect = popen_side
        mocker.patch("meeting_tool.transcribe_with_gemini", return_value="t")

        mt.record_then_transcribe_gemini(
            output_dir=tmp_path, date="22-05-2026", device_id=0,
        )
        assert caffeinate_proc.terminate.called

    def test_stop_event_clears_between_phases(self, mock_subprocess,
                                              fake_gemini_client, tmp_path,
                                              mocker):
        """stop_event cleares inden transcribe_with_gemini kaldes (kodekommentar linje 655-656)."""
        def popen_side(cmd, **_):
            if isinstance(cmd, list) and "-y" in cmd:
                out_idx = cmd.index("-y") - 1
                Path(cmd[out_idx]).write_bytes(b"RIFF" * 1000)
            return mock_subprocess.proc

        mock_subprocess.Popen.side_effect = popen_side

        stop_event = threading.Event()
        stop_event.set()  # Sat før kaldet — simulerer at brugeren stopper optagelse

        # transcribe_with_gemini må ikke modtage et set() stop_event:
        def assert_clear(*args, stop_event=None, **kwargs):
            assert stop_event is None or not stop_event.is_set(), (
                "stop_event bør være clearet inden transcribe_with_gemini"
            )
            return "t"

        mocker.patch("meeting_tool.transcribe_with_gemini",
                     side_effect=assert_clear)

        mt.record_then_transcribe_gemini(
            output_dir=tmp_path, date="22-05-2026", device_id=0,
            stop_event=stop_event,
        )
        # Hvis assertion i side_effect ikke kastede, er testen bestået.

    def test_dual_track_merges_both(self, mock_subprocess, fake_gemini_client,
                                    tmp_path, mocker):
        def popen_side_effect(cmd, **_):
            if isinstance(cmd, list) and "-y" in cmd:
                out_idx = cmd.index("-y") - 1
                Path(cmd[out_idx]).write_bytes(b"RIFF" * 1000)
            return mock_subprocess.proc

        mock_subprocess.Popen.side_effect = popen_side_effect
        fake_audiotee = tmp_path / ".audiotee"
        fake_audiotee.write_bytes(b"x")
        mocker.patch.object(mt, "AUDIOTEE_BIN", fake_audiotee)
        mocker.patch(
            "meeting_tool.transcribe_with_gemini",
            side_effect=[
                "[00:00 - 00:05] Hej fra mig.",
                "[00:05 - 00:10] Hej fra modpart.",
            ],
        )
        wav_path, transcript = mt.record_then_transcribe_gemini(
            output_dir=tmp_path, date="27-05-2026", device_id=1, system_device=0,
        )
        assert wav_path.exists()
        assert "Mig: Hej fra mig." in transcript
        assert "Modpart: Hej fra modpart." in transcript

    def test_single_track_unchanged_when_no_system_device(
        self, mock_subprocess, fake_gemini_client, tmp_path, mocker
    ):
        def popen_side_effect(cmd, **_):
            if isinstance(cmd, list) and "-y" in cmd:
                out_idx = cmd.index("-y") - 1
                Path(cmd[out_idx]).write_bytes(b"RIFF" * 1000)
            return mock_subprocess.proc

        mock_subprocess.Popen.side_effect = popen_side_effect
        mocker.patch("meeting_tool.transcribe_with_gemini",
                     return_value="[00:00 - 00:05] kun mig")
        _wav, transcript = mt.record_then_transcribe_gemini(
            output_dir=tmp_path, date="27-05-2026", device_id=1,
        )
        assert "Mig:" not in transcript
        assert "kun mig" in transcript


class TestRecordAndTranscribeLive:
    def test_happy_path(self, mock_subprocess, fake_whisper_model, tmp_path,
                        mocker):
        """record_and_transcribe_live returnerer (Path, str) når stop_event er sat fra start."""
        # Chunks-dir er output_dir / ".chunks" (hardkodet i koden)
        chunks_dir = tmp_path / ".chunks"

        # subprocess.run bruges til ffmpeg concat (master WAV-sammensætning).
        # Det skaber master WAV-filen; vi lader mock_subprocess.run gøre det.
        def run_side(cmd, **_):
            # ffmpeg concat: output er cmd[-4] (str(master_path), "-y", "-loglevel", "warning")
            # Rækkefølge: [..., str(master_path), "-y", "-loglevel", "warning"]
            if isinstance(cmd, list) and "concat" in cmd:
                # Find output-sti: én plads før "-y"
                if "-y" in cmd:
                    out_idx = cmd.index("-y") - 1
                    Path(cmd[out_idx]).write_bytes(b"RIFF" * 1000)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        mock_subprocess.run.side_effect = run_side

        # Popen (ffmpeg segment) skal skabe chunk-filer i chunks_dir
        # og returnere hurtigt (proc.wait() returnerer 0).
        def popen_side(cmd, **_):
            chunks_dir.mkdir(parents=True, exist_ok=True)
            # Skab én chunk-fil med gyldigt indhold
            (chunks_dir / "chunk_0000.wav").write_bytes(b"RIFF" * 100)
            mock_subprocess.proc.poll.return_value = 0
            return mock_subprocess.proc

        mock_subprocess.Popen.side_effect = popen_side

        # Sæt stop_event fra start — ffmpeg-stopper med det samme
        stop_event = threading.Event()
        stop_event.set()

        wav_path, transcript = mt.record_and_transcribe_live(
            output_dir=tmp_path,
            date="22-05-2026",
            device_id=0,
            model_size="large-v3",
            chunk_duration=300,
            stop_event=stop_event,
        )
        # Vi får i hvert fald en master path tilbage
        assert isinstance(wav_path, Path)
        assert isinstance(transcript, str)

    def test_no_chunks_raises(self, mock_subprocess, fake_whisper_model,
                              tmp_path, mocker):
        """RuntimeError kastes når ingen chunks blev optaget."""
        # Popen kører men skaber ingen chunk-filer
        # (chunks_dir forbliver tom)
        stop_event = threading.Event()
        stop_event.set()

        with pytest.raises(RuntimeError, match="Ingen chunks"):
            mt.record_and_transcribe_live(
                output_dir=tmp_path,
                date="22-05-2026",
                device_id=0,
                model_size="large-v3",
                chunk_duration=300,
                stop_event=stop_event,
            )


class TestRecordAndTranscribeLiveDual:
    def test_dual_track_merges(self, mock_subprocess, fake_whisper_model,
                               tmp_path, mocker):
        def popen_side_effect(cmd, **_):
            if isinstance(cmd, list) and "-y" in cmd:
                out_idx = cmd.index("-y") - 1
                Path(cmd[out_idx]).write_bytes(b"RIFF" * 1000)
            return mock_subprocess.proc

        mock_subprocess.Popen.side_effect = popen_side_effect
        fake_audiotee = tmp_path / ".audiotee"
        fake_audiotee.write_bytes(b"x")
        mocker.patch.object(mt, "AUDIOTEE_BIN", fake_audiotee)
        mocker.patch(
            "meeting_tool.transcribe_audio",
            side_effect=[
                "[00:00 - 00:05] Mig-tekst.",
                "[00:05 - 00:10] Modpart-tekst.",
            ],
        )
        stop = threading.Event()
        stop.set()  # stop straks — vi tester flettelogikken, ikke varighed

        wav_path, transcript = mt.record_and_transcribe_live(
            output_dir=tmp_path,
            date="27-05-2026",
            device_id=1,
            model_size="large-v3",
            system_device=0,
            stop_event=stop,
        )
        assert wav_path.exists()
        assert "Mig: Mig-tekst." in transcript
        assert "Modpart: Modpart-tekst." in transcript

    def test_degrades_to_mic_only_when_sys_track_fails(
        self, fake_whisper_model, tmp_path, mocker
    ):
        def fake_record(mic_path, sys_path, *a, **k):
            mic_path.write_bytes(b"RIFF" * 1000)  # kun mic-sporet skabes
            return False  # systemsporet fejlede

        mocker.patch("meeting_tool._record_dual_tracks", side_effect=fake_record)
        mocker.patch("subprocess.Popen")  # caffeinate
        mocker.patch("meeting_tool.transcribe_audio",
                     return_value="[00:00 - 00:05] kun mig")
        stop = threading.Event()
        stop.set()

        wav_path, transcript = mt.record_and_transcribe_live(
            output_dir=tmp_path, date="27-05-2026", device_id=1,
            model_size="large-v3", system_device=0, stop_event=stop,
        )
        assert wav_path.exists()
        assert "Mig: kun mig" in transcript
        assert "Modpart:" not in transcript
        # transcribe_audio kaldt netop én gang (kun mic-sporet)
        assert mt.transcribe_audio.call_count == 1


class TestClampChunkTimestamps:
    def test_clamps_hallucinated_timestamps(self):
        text = "[00:30 - 00:31] Hej.\n[50:10 - 1:10:10] Lang saetning."
        out = mt._clamp_chunk_timestamps(text, 720)   # 12-min chunk
        lines = out.splitlines()
        assert lines[0] == "[00:30 - 00:31] Hej."
        assert lines[1] == "[12:00 - 12:00] Lang saetning."

    def test_leaves_lines_without_timestamp(self):
        assert mt._clamp_chunk_timestamps("ingen tidsstempel her", 720) == "ingen tidsstempel her"
