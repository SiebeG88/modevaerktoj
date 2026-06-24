"""Tests for main() CLI-indgangspunkt i meeting_tool."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_tool as mt


# ---------------------------------------------------------------------------
# Hjælper: kør main() med givne argv uden at sys.exit() spreder sig
# ---------------------------------------------------------------------------

def run_main(argv: list[str], monkeypatch):
    """Sæt sys.argv og kør main(). Returnerer None ved succes, exit-kode ved SystemExit."""
    monkeypatch.setattr(sys, "argv", ["meeting_tool.py"] + argv)
    try:
        mt.main()
        return None
    except SystemExit as exc:
        return exc.code


# ---------------------------------------------------------------------------
# --list-devices
# ---------------------------------------------------------------------------

class TestListDevices:
    def test_list_devices_calls_list_and_exits(self, monkeypatch, mock_subprocess):
        called = []

        def fake_list():
            called.append(True)
            return []

        monkeypatch.setattr(mt, "list_audio_devices", fake_list)
        code = run_main(["--list-devices"], monkeypatch)
        assert called, "list_audio_devices skal kaldes"
        assert code == 0


# ---------------------------------------------------------------------------
# Ingen argumenter → parser.error
# ---------------------------------------------------------------------------

class TestNoArgs:
    def test_no_args_exits_nonzero(self, monkeypatch):
        code = run_main([], monkeypatch)
        assert code != 0


# ---------------------------------------------------------------------------
# --transcript-file path (spring transkription over)
# ---------------------------------------------------------------------------

class TestTranscriptFile:
    def test_uses_existing_transcript(self, monkeypatch, tmp_path,
                                      fake_anthropic_client):
        transcript_file = tmp_path / "transcript.txt"
        transcript_file.write_text("[00:00 - 00:05] Hej alle.", encoding="utf-8")

        output_dir = tmp_path / "output"
        code = run_main([
            "--transcript-file", str(transcript_file),
            "--output-dir", str(output_dir),
            "--date", "22-05-2026",
        ], monkeypatch)
        assert code is None  # ingen SystemExit
        # Referat- og transkriptfil skal være gemt
        assert any(output_dir.glob("Transkription*"))

    def test_transcript_file_skip_minutes(self, monkeypatch, tmp_path):
        transcript_file = tmp_path / "t.txt"
        transcript_file.write_text("[00:00 - 00:05] x.", encoding="utf-8")

        output_dir = tmp_path / "output"
        code = run_main([
            "--transcript-file", str(transcript_file),
            "--output-dir", str(output_dir),
            "--date", "22-05-2026",
            "--skip-minutes",
        ], monkeypatch)
        assert code is None
        assert not any(output_dir.glob("Referat*"))

    def test_minutes_error_does_not_abort(self, monkeypatch, tmp_path,
                                          fake_anthropic_client):
        """generate_minutes fejler → advarsel, men transkription gemmes."""
        fake_anthropic_client.messages.create.side_effect = RuntimeError("API nede")

        transcript_file = tmp_path / "t.txt"
        transcript_file.write_text("[00:00 - 00:05] x.", encoding="utf-8")
        output_dir = tmp_path / "output"

        code = run_main([
            "--transcript-file", str(transcript_file),
            "--output-dir", str(output_dir),
            "--date", "22-05-2026",
        ], monkeypatch)
        assert code is None
        assert any(output_dir.glob("Transkription*"))


# ---------------------------------------------------------------------------
# WAV-fil givet direkte (ingen konvertering nødvendig)
# ---------------------------------------------------------------------------

class TestWavFileInput:
    def test_wav_file_uses_whisper(self, monkeypatch, tmp_wav, tmp_path,
                                   fake_whisper_model, fake_anthropic_client):
        output_dir = tmp_wav.parent
        code = run_main([
            str(tmp_wav),
            "--output-dir", str(output_dir),
            "--date", "22-05-2026",
        ], monkeypatch)
        assert code is None
        assert fake_whisper_model.transcribe.called

    def test_wav_file_gemini_engine(self, monkeypatch, tmp_wav, tmp_path,
                                    fake_gemini_client, fake_anthropic_client,
                                    mock_subprocess):
        mock_subprocess.run.return_value = SimpleNamespace(
            returncode=0, stdout="600.0\n", stderr="",
        )
        output_dir = tmp_wav.parent
        code = run_main([
            str(tmp_wav),
            "--engine", "gemini",
            "--output-dir", str(output_dir),
            "--date", "22-05-2026",
        ], monkeypatch)
        assert code is None
        assert fake_gemini_client.models.generate_content.called

    def test_nonexistent_audio_file_exits_1(self, monkeypatch, tmp_path):
        code = run_main([
            str(tmp_path / "no_such_file.wav"),
            "--date", "22-05-2026",
        ], monkeypatch)
        assert code == 1

    def test_m4a_file_triggers_conversion(self, monkeypatch, tmp_path,
                                          mock_subprocess, fake_whisper_model,
                                          fake_anthropic_client):
        """Ikke-WAV fil → convert_to_wav → transkribér → gem."""
        m4a = tmp_path / "meeting.m4a"
        m4a.write_bytes(b"fake audio data")

        # convert_to_wav + ffmpeg: vi mocker convert_to_wav
        tmp_wav_path = tmp_path / "converted.wav"
        tmp_wav_path.write_bytes(b"\x00\x00" * 16000)

        convert_called = []

        def fake_convert(src, dst):
            # Skriv en falsk WAV til dst
            dst.write_bytes(b"\x00\x00" * 16000)
            convert_called.append(True)
            return dst

        monkeypatch.setattr(mt, "convert_to_wav", fake_convert)

        output_dir = tmp_path / "output"
        code = run_main([
            str(m4a),
            "--output-dir", str(output_dir),
            "--date", "22-05-2026",
        ], monkeypatch)
        assert code is None
        assert convert_called, "convert_to_wav skal kaldes for m4a"


# ---------------------------------------------------------------------------
# Default model: engine-afhængigt
# ---------------------------------------------------------------------------

class TestDefaultModel:
    def test_default_model_hviske(self, monkeypatch, tmp_wav,
                                  fake_whisper_model, fake_anthropic_client):
        transcribe_calls = []

        def fake_transcribe(path, model_size="large-v3"):
            transcribe_calls.append(model_size)
            return "[00:00 - 00:05] ok"

        monkeypatch.setattr(mt, "transcribe_audio", fake_transcribe)

        output_dir = tmp_wav.parent
        run_main([str(tmp_wav), "--output-dir", str(output_dir),
                  "--date", "22-05-2026", "--skip-minutes"], monkeypatch)
        assert transcribe_calls
        assert "hviske" in transcribe_calls[0].lower() or "v3" in transcribe_calls[0].lower()

    def test_default_model_gemini(self, monkeypatch, tmp_wav,
                                  fake_gemini_client, fake_anthropic_client,
                                  mock_subprocess):
        mock_subprocess.run.return_value = SimpleNamespace(
            returncode=0, stdout="600.0\n", stderr="",
        )
        transcribe_calls = []

        def fake_transcribe(path, attendees=None, model="gemini-2.5-pro", **kw):
            transcribe_calls.append(model)
            return "[00:00 - 00:05] ok"

        monkeypatch.setattr(mt, "transcribe_with_gemini", fake_transcribe)

        output_dir = tmp_wav.parent
        run_main([
            str(tmp_wav), "--engine", "gemini",
            "--output-dir", str(output_dir),
            "--date", "22-05-2026", "--skip-minutes",
        ], monkeypatch)
        assert transcribe_calls
        assert "gemini" in transcribe_calls[0].lower()


# ---------------------------------------------------------------------------
# Gemini + --live → ignorerer --live
# ---------------------------------------------------------------------------

class TestGeminiLiveIgnored:
    def test_gemini_live_ignored(self, monkeypatch, tmp_wav, tmp_path,
                                  mock_subprocess, fake_gemini_client,
                                  fake_anthropic_client, capsys):
        mock_subprocess.run.return_value = SimpleNamespace(
            returncode=0, stdout="600.0\n", stderr="",
        )

        transcribed = []

        def fake_gemini_transcribe(path, **kw):
            transcribed.append(True)
            return "[00:00 - 00:05] ok"

        def fake_record_and_live(**kw):
            raise AssertionError("record_and_transcribe_live skal IKKE kaldes ved gemini+live")

        monkeypatch.setattr(mt, "transcribe_with_gemini", fake_gemini_transcribe)
        monkeypatch.setattr(mt, "record_and_transcribe_live", fake_record_and_live)
        monkeypatch.setattr(mt, "record_meeting", lambda *a, **kw: tmp_wav)
        monkeypatch.setattr(mt, "record_then_transcribe_gemini",
                            lambda **kw: (tmp_wav, "[00:00 - 00:05] ok"))

        # Kald med en lydfil direkte (undgår optagelse), men med --engine gemini + --live
        # Fordi --record ikke er sat og vi angiver en lydfil, tager vi sti:
        #   args.record=False, args.audio_file=tmp_wav → ingen optagelse
        # --live uden --record har ingen effekt i nuværende kode
        code = run_main([
            str(tmp_wav), "--engine", "gemini", "--live",
            "--output-dir", str(tmp_wav.parent),
            "--date", "22-05-2026", "--skip-minutes",
        ], monkeypatch)
        assert code is None


# ---------------------------------------------------------------------------
# Dato udledt fra mappenavn
# ---------------------------------------------------------------------------

class TestDateFromFolder:
    def test_date_inferred_from_folder(self, monkeypatch, tmp_path,
                                       fake_whisper_model, fake_anthropic_client):
        """Hvis lydfil er i mappe der hedder DD-MM-YYYY bruges det som dato."""
        date_dir = tmp_path / "22-05-2026"
        date_dir.mkdir()
        wav = date_dir / "meeting.wav"
        wav.write_bytes(b"\x00\x00" * 16000)

        transcribe_calls = []

        def fake_transcribe(path, model_size="large-v3"):
            transcribe_calls.append(True)
            return "[00:00 - 00:05] ok"

        monkeypatch.setattr(mt, "transcribe_audio", fake_transcribe)

        output_dir = tmp_path / "output"
        code = run_main([
            str(wav),
            "--output-dir", str(output_dir),
            "--skip-minutes",
        ], monkeypatch)
        assert code is None
        assert transcribe_calls

    def test_output_dir_from_audio_file_parent(self, monkeypatch, tmp_path,
                                                fake_whisper_model,
                                                fake_anthropic_client):
        """Ingen --output-dir + lydfil → output_dir = lydfils mappe."""
        wav = tmp_path / "test.wav"
        wav.write_bytes(b"\x00\x00" * 16000)

        monkeypatch.setattr(mt, "transcribe_audio",
                            lambda *a, **kw: "[00:00 - 00:05] x")

        code = run_main([str(wav), "--skip-minutes", "--date", "22-05-2026"],
                        monkeypatch)
        assert code is None
        # Transkription gemmes i tmp_path (lydfils mappe)
        assert any(tmp_path.glob("Transkription*"))


# ---------------------------------------------------------------------------
# --attendees
# ---------------------------------------------------------------------------

class TestAttendees:
    def test_custom_attendees_in_minutes(self, monkeypatch, tmp_path,
                                          fake_anthropic_client,
                                          fake_whisper_model, tmp_wav):
        minutes_attendees = []

        orig_gen = mt.generate_minutes

        def fake_gen(transcript, attendees, date):
            minutes_attendees.extend(attendees)
            return orig_gen.__wrapped__(transcript, attendees, date) if hasattr(
                orig_gen, "__wrapped__") else "# Referat"

        fake_anthropic_client.messages.create.return_value = MagicMock(
            content=[SimpleNamespace(text="# Referat")]
        )

        output_dir = tmp_wav.parent
        run_main([
            str(tmp_wav),
            "--attendees", "Anna", "Bo", "Erik",
            "--output-dir", str(output_dir),
            "--date", "22-05-2026",
        ], monkeypatch)

        call = fake_anthropic_client.messages.create.call_args
        system = call.kwargs["system"]
        assert "Anna" in system
        assert "Bo" in system
        assert "Erik" in system


# ---------------------------------------------------------------------------
# --model eksplicit sat (1621->1629 branch: model IS None = False)
# ---------------------------------------------------------------------------

class TestExplicitModel:
    def test_explicit_model_is_used(self, monkeypatch, tmp_wav,
                                     fake_whisper_model, fake_anthropic_client):
        transcribe_calls = []

        def fake_transcribe(path, model_size="large-v3"):
            transcribe_calls.append(model_size)
            return "[00:00 - 00:05] ok"

        monkeypatch.setattr(mt, "transcribe_audio", fake_transcribe)
        output_dir = tmp_wav.parent
        run_main([
            str(tmp_wav),
            "--model", "custom-model-123",
            "--output-dir", str(output_dir),
            "--date", "22-05-2026",
            "--skip-minutes",
        ], monkeypatch)
        assert transcribe_calls == ["custom-model-123"]


# ---------------------------------------------------------------------------
# Gemini + --record + --live → advarsel + live ignoreres (1630-1635)
# ---------------------------------------------------------------------------

class TestGeminiRecordLiveWarning:
    def test_gemini_record_live_prints_warning(self, monkeypatch, tmp_path,
                                                mock_subprocess,
                                                fake_gemini_client,
                                                fake_anthropic_client,
                                                capsys):
        """--engine gemini + --record + --live → 'Ignorerer --live' advarsel."""
        # Mocker optagelse + transkription for at undgå rigtig ffmpeg
        wav = tmp_path / "Driftledelsesmoede 22-05-2026.wav"
        wav.write_bytes(b"RIFF" * 1000)

        monkeypatch.setattr(mt, "record_meeting", lambda path, **kw: path)
        monkeypatch.setattr(
            mt, "transcribe_with_gemini",
            lambda *a, **kw: "[00:00 - 00:05] ok",
        )
        monkeypatch.setattr(
            mt, "generate_minutes",
            lambda *a, **kw: "# Referat",
        )
        monkeypatch.setattr(mt, "save_output", lambda *a, **kw: None)

        code = run_main([
            "--record",
            "--engine", "gemini",
            "--live",
            "--output-dir", str(tmp_path),
            "--date", "22-05-2026",
            "--skip-minutes",
        ], monkeypatch)
        assert code is None
        err = capsys.readouterr().err
        assert "gemini" in err.lower() or "ignorerer" in err.lower()


# ---------------------------------------------------------------------------
# --record (klassisk, uden --live) → record_meeting kaldt (1681-1684)
# ---------------------------------------------------------------------------

class TestRecordClassic:
    def test_record_classic_calls_record_meeting(self, monkeypatch, tmp_path,
                                                   fake_whisper_model,
                                                   fake_anthropic_client):
        """--record uden --live bruger record_meeting + transcribe_audio."""
        wav = tmp_path / "Driftledelsesmoede 22-05-2026.wav"

        record_called = []

        def fake_record(path, **kw):
            path.write_bytes(b"RIFF" * 1000)
            record_called.append(True)
            return path

        monkeypatch.setattr(mt, "record_meeting", fake_record)
        monkeypatch.setattr(
            mt, "transcribe_audio",
            lambda *a, **kw: "[00:00 - 00:05] ok",
        )

        code = run_main([
            "--record",
            "--output-dir", str(tmp_path),
            "--date", "22-05-2026",
            "--skip-minutes",
        ], monkeypatch)
        assert code is None
        assert record_called, "record_meeting skal kaldes"

    def test_record_prints_status_line(self, monkeypatch, tmp_path,
                                        fake_whisper_model,
                                        fake_anthropic_client,
                                        capsys):
        """Når --record er sat printes 'Tilstand: Optagelse' (linje 1658)."""
        monkeypatch.setattr(mt, "record_meeting",
                            lambda path, **kw: path.write_bytes(b"RIFF") or path)
        monkeypatch.setattr(mt, "transcribe_audio",
                            lambda *a, **kw: "[00:00 - 00:05] ok")

        run_main([
            "--record",
            "--output-dir", str(tmp_path),
            "--date", "22-05-2026",
            "--skip-minutes",
        ], monkeypatch)
        out = capsys.readouterr().out
        assert "Optagelse" in out or "optagelse" in out.lower()


# ---------------------------------------------------------------------------
# --record + --live → record_and_transcribe_live kaldt (1671)
# ---------------------------------------------------------------------------

class TestRecordLive:
    def test_record_live_calls_live_function(self, monkeypatch, tmp_path,
                                              fake_whisper_model,
                                              fake_anthropic_client):
        """--record + --live kalder record_and_transcribe_live."""
        live_called = []
        wav = tmp_path / "moede.wav"

        def fake_live(**kw):
            wav.write_bytes(b"RIFF" * 1000)
            live_called.append(True)
            return (wav, "[00:00 - 00:05] ok")

        monkeypatch.setattr(mt, "record_and_transcribe_live", fake_live)

        code = run_main([
            "--record", "--live",
            "--output-dir", str(tmp_path),
            "--date", "22-05-2026",
            "--skip-minutes",
        ], monkeypatch)
        assert code is None
        assert live_called, "record_and_transcribe_live skal kaldes"


# ---------------------------------------------------------------------------
# output_dir = MEETINGS_DIR / date (linje 1651): ingen --output-dir, ingen lydfil
# ---------------------------------------------------------------------------

class TestOutputDirDefault:
    def test_default_output_dir_is_meetings_dir(self, monkeypatch, tmp_path,
                                                  fake_anthropic_client):
        """Ingen --output-dir og ingen lydfil → MEETINGS_DIR / date."""
        meetings_dir = tmp_path / "moeder"
        monkeypatch.setattr(mt, "MEETINGS_DIR", meetings_dir)

        transcript_file = tmp_path / "t.txt"
        transcript_file.write_text("[00:00 - 00:05] x.", encoding="utf-8")

        code = run_main([
            "--transcript-file", str(transcript_file),
            "--date", "22-05-2026",
            "--skip-minutes",
        ], monkeypatch)
        assert code is None
        expected_dir = meetings_dir / "22-05-2026"
        assert expected_dir.exists()


# ---------------------------------------------------------------------------
# Date inferred from folder (1642->1646): mappe er ikke DD-MM-YYYY
# ---------------------------------------------------------------------------

class TestDateFolderMismatch:
    def test_non_date_folder_uses_today(self, monkeypatch, tmp_path,
                                         fake_whisper_model,
                                         fake_anthropic_client):
        """Mappe hedder ikke DD-MM-YYYY → brug args.date (1642->1646 branch)."""
        folder = tmp_path / "ikke-en-dato"
        folder.mkdir()
        wav = folder / "meeting.wav"
        wav.write_bytes(b"\x00\x00" * 16000)

        monkeypatch.setattr(mt, "transcribe_audio",
                            lambda *a, **kw: "[00:00 - 00:05] ok")

        code = run_main([
            str(wav),
            "--date", "01-01-2026",
            "--skip-minutes",
        ], monkeypatch)
        assert code is None


# ---------------------------------------------------------------------------
# --setup-audio
# ---------------------------------------------------------------------------

class TestSetupAudioFlag:
    def test_setup_audio_runs_setup_and_exits(self, monkeypatch, mocker):
        mocker.patch.object(
            mt.audio_routing, "setup_system_output",
            return_value=mt.audio_routing.SetupResult(status="ok", system_device=0),
        )
        code = run_main(["--setup-audio"], monkeypatch)
        assert code == 0

    def test_setup_audio_prints_guidance_on_manual(self, monkeypatch, mocker, capsys):
        mocker.patch.object(
            mt.audio_routing, "setup_system_output",
            return_value=mt.audio_routing.SetupResult(
                status="needs_manual", guidance="GOER SAADAN HER"),
        )
        code = run_main(["--setup-audio"], monkeypatch)
        assert code == 1
        assert "GOER SAADAN HER" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# --record --system-audio
# ---------------------------------------------------------------------------

class TestSystemAudioFlag:
    def test_system_audio_forwards_device_to_live_engine(self, monkeypatch, mocker, tmp_path):
        mocker.patch.object(mt.audio_routing, "ensure_routing_active",
            return_value=mt.audio_routing.SetupResult(status="ok", system_device=3))
        rec = mocker.patch.object(
            mt, "record_and_transcribe_live",
            return_value=(tmp_path / "x.wav", "[00:00 - 00:05] t"),
        )
        run_main([
            "--record", "--system-audio",
            "--engine", "hviske",
            "--output-dir", str(tmp_path),
            "--date", "22-05-2026",
            "--skip-minutes",
        ], monkeypatch)
        assert rec.call_args.kwargs["system_device"] == 3

    def test_system_audio_gemini_forwards_device(self, monkeypatch, mocker, tmp_path):
        mocker.patch.object(mt.audio_routing, "ensure_routing_active",
            return_value=mt.audio_routing.SetupResult(status="ok", system_device=5))
        rec = mocker.patch.object(
            mt, "record_then_transcribe_gemini",
            return_value=(tmp_path / "x.wav", "[00:00 - 00:05] t"),
        )
        run_main([
            "--record", "--system-audio",
            "--engine", "gemini",
            "--output-dir", str(tmp_path),
            "--date", "22-05-2026",
            "--skip-minutes",
        ], monkeypatch)
        assert rec.call_args.kwargs["system_device"] == 5

    def test_system_audio_errors_when_blackhole_missing(self, monkeypatch, mocker):
        mocker.patch.object(mt.audio_routing, "ensure_routing_active",
            return_value=mt.audio_routing.SetupResult(status="needs_manual", guidance="x"))
        code = run_main(["--record", "--system-audio"], monkeypatch)
        assert code == 1
