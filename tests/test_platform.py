"""Tests for platform-laget i meeting_tool (mac/Windows/andet)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_tool as mt


class TestAudioInputFormat:
    def test_macos(self, mocker):
        mocker.patch("meeting_tool._IS_MACOS", True)
        mocker.patch("meeting_tool._IS_WINDOWS", False)
        assert mt.audio_input_format() == "avfoundation"

    def test_windows(self, mocker):
        mocker.patch("meeting_tool._IS_MACOS", False)
        mocker.patch("meeting_tool._IS_WINDOWS", True)
        assert mt.audio_input_format() == "dshow"

    def test_other_raises(self, mocker):
        mocker.patch("meeting_tool._IS_MACOS", False)
        mocker.patch("meeting_tool._IS_WINDOWS", False)
        with pytest.raises(RuntimeError, match="understøttes ikke"):
            mt.audio_input_format()


class TestBuildFfmpegInput:
    def test_macos_index(self, mocker):
        mocker.patch("meeting_tool._IS_MACOS", True)
        mocker.patch("meeting_tool._IS_WINDOWS", False)
        assert mt.build_ffmpeg_input(1) == ["-f", "avfoundation", "-i", ":1"]

    def test_windows_name(self, mocker):
        mocker.patch("meeting_tool._IS_MACOS", False)
        mocker.patch("meeting_tool._IS_WINDOWS", True)
        assert mt.build_ffmpeg_input("Mikrofon (Realtek)") == [
            "-f", "dshow", "-i", "audio=Mikrofon (Realtek)",
        ]


_MAC_STDERR = """\
[AVFoundation indev @ 0x1] AVFoundation video devices:
[AVFoundation indev @ 0x1] [0] FaceTime HD Camera
[AVFoundation indev @ 0x1] AVFoundation audio devices:
[AVFoundation indev @ 0x1] [0] MacBook Pro Microphone
[AVFoundation indev @ 0x1] [1] External Microphone
"""

_WIN_STDERR = """\
[dshow @ 0x1] DirectShow video devices
[dshow @ 0x1]  "HD WebCam"
[dshow @ 0x1] DirectShow audio devices
[dshow @ 0x1]  "Microphone (Realtek High Definition Audio)"
[dshow @ 0x1]    Alternative name "@device_cm_{ABC}\\wave_{DEF}"
"""

# Verbosere header-variant fra nyere ffmpeg-builds (indeholder "audio devices"
# som delstreng i video-linjen — regressionstest for sorteringsbugs)
_WIN_VERBOSE_STDERR = """\
[dshow @ 0x1] DirectShow video devices (some may be both video and audio devices)
[dshow @ 0x1]  "Integrated Camera"
[dshow @ 0x1] DirectShow audio devices (some may be both video and audio devices)
[dshow @ 0x1]  "Microphone Array (Realtek)"
"""


class TestListAudioDevices:
    def test_macos_parses_index_and_name(self, mock_subprocess, mocker):
        mocker.patch("meeting_tool._IS_MACOS", True)
        mocker.patch("meeting_tool._IS_WINDOWS", False)
        mock_subprocess.run.return_value.stderr = _MAC_STDERR
        devices = mt.list_audio_devices()
        assert devices == [(0, "MacBook Pro Microphone"),
                           (1, "External Microphone")]

    def test_windows_parses_name_only(self, mock_subprocess, mocker):
        mocker.patch("meeting_tool._IS_MACOS", False)
        mocker.patch("meeting_tool._IS_WINDOWS", True)
        mock_subprocess.run.return_value.stderr = _WIN_STDERR
        devices = mt.list_audio_devices()
        name = "Microphone (Realtek High Definition Audio)"
        assert devices == [(name, name)]

    def test_windows_verbose_headers_camera_not_in_audio(self, mock_subprocess, mocker):
        """Regression: verbose video-header indeholder 'audio devices' som delstreng.
        Med forkert rækkefølge ville kameraet fejlagtigt havne som lydenhed."""
        mocker.patch("meeting_tool._IS_MACOS", False)
        mocker.patch("meeting_tool._IS_WINDOWS", True)
        mock_subprocess.run.return_value.stderr = _WIN_VERBOSE_STDERR
        devices = mt.list_audio_devices()
        name = "Microphone Array (Realtek)"
        assert devices == [(name, name)]

    def test_failure_returns_empty(self, mock_subprocess, mocker):
        mocker.patch("meeting_tool._IS_MACOS", True)
        mocker.patch("meeting_tool._IS_WINDOWS", False)
        mock_subprocess.run.side_effect = OSError("ffmpeg ikke fundet")
        assert mt.list_audio_devices() == []


class TestKeepAwake:
    def test_macos_starts_and_stops_caffeinate(self, mock_subprocess, mocker):
        mocker.patch("meeting_tool._IS_MACOS", True)
        mocker.patch("meeting_tool._IS_WINDOWS", False)
        handle = mt.start_keep_awake()
        assert mock_subprocess.Popen.call_args.args[0][0] == "caffeinate"
        assert handle is mock_subprocess.proc
        mt.stop_keep_awake(handle)
        assert mock_subprocess.proc.terminate.called

    def test_windows_uses_setthreadexecutionstate(self, mocker):
        mocker.patch("meeting_tool._IS_MACOS", False)
        mocker.patch("meeting_tool._IS_WINDOWS", True)
        import ctypes
        fake_windll = mocker.MagicMock()
        mocker.patch.object(ctypes, "windll", fake_windll, create=True)
        handle = mt.start_keep_awake()
        assert handle == "windows"
        assert fake_windll.kernel32.SetThreadExecutionState.called
        mt.stop_keep_awake(handle)
        # Kaldt igen ved stop (reset til ES_CONTINUOUS)
        assert fake_windll.kernel32.SetThreadExecutionState.call_count == 2

    def test_other_platform_is_noop(self, mocker):
        mocker.patch("meeting_tool._IS_MACOS", False)
        mocker.patch("meeting_tool._IS_WINDOWS", False)
        assert mt.start_keep_awake() is None
        mt.stop_keep_awake(None)  # må ikke kaste


class TestDefaultMeetingsDir:
    def test_macos_default_path(self, mocker):
        mocker.patch("meeting_tool._IS_MACOS", True)
        mocker.patch("meeting_tool._IS_WINDOWS", False)
        p = mt.default_meetings_dir()
        assert p == Path.home() / "Møder"

    def test_windows_uses_onedrive_commercial(self, mocker, monkeypatch):
        mocker.patch("meeting_tool._IS_MACOS", False)
        mocker.patch("meeting_tool._IS_WINDOWS", True)
        monkeypatch.setenv("OneDriveCommercial", r"C:\Users\x\OneDrive - Eksempel")
        monkeypatch.delenv("OneDrive", raising=False)
        assert str(mt.default_meetings_dir()) == r"C:\Users\x\OneDrive - Eksempel"

    def test_windows_falls_back_to_onedrive_then_home(self, mocker, monkeypatch):
        mocker.patch("meeting_tool._IS_MACOS", False)
        mocker.patch("meeting_tool._IS_WINDOWS", True)
        monkeypatch.delenv("OneDriveCommercial", raising=False)
        monkeypatch.setenv("OneDrive", r"C:\Users\x\OneDrive")
        assert str(mt.default_meetings_dir()) == r"C:\Users\x\OneDrive"

    def test_windows_both_onedrive_unset_falls_back_to_home(self, mocker, monkeypatch):
        mocker.patch("meeting_tool._IS_MACOS", False)
        mocker.patch("meeting_tool._IS_WINDOWS", True)
        monkeypatch.delenv("OneDriveCommercial", raising=False)
        monkeypatch.delenv("OneDrive", raising=False)
        assert mt.default_meetings_dir() == Path.home()

    def test_other_platform_uses_home(self, mocker, monkeypatch):
        mocker.patch("meeting_tool._IS_MACOS", False)
        mocker.patch("meeting_tool._IS_WINDOWS", False)
        assert mt.default_meetings_dir() == Path.home()
