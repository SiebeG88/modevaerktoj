"""Tests for audio_routing efter AudioTee-omskrivning.

audio_routing eksponerer nu kun:
- parse_audio_devices / _list_devices_stderr / detect_blackhole (deprecated, returnerer None)
- system_audio_available
- setup_system_output / ensure_routing_active (no-op stubs der tjekker AudioTee)
- restore_system_output / load_previous_output (no-ops)
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import audio_routing as ar

FFMPEG_DEVICE_LIST = """\
[AVFoundation indev @ 0x7f] AVFoundation video devices:
[AVFoundation indev @ 0x7f] [0] FaceTime HD Camera
[AVFoundation indev @ 0x7f] AVFoundation audio devices:
[AVFoundation indev @ 0x7f] [0] BlackHole 2ch
[AVFoundation indev @ 0x7f] [1] MacBook Pro Microphone
[AVFoundation indev @ 0x7f] [2] External Mic
"""

FFMPEG_NO_AUDIO = """\
[AVFoundation indev @ 0x7f] AVFoundation video devices:
[AVFoundation indev @ 0x7f] [0] FaceTime HD Camera
"""


class TestParseAudioDevices:
    def test_parses_index_and_name(self):
        devices = ar.parse_audio_devices(FFMPEG_DEVICE_LIST)
        assert (0, "BlackHole 2ch") in devices
        assert (1, "MacBook Pro Microphone") in devices
        assert (2, "External Mic") in devices

    def test_ignores_video_devices(self):
        devices = ar.parse_audio_devices(FFMPEG_DEVICE_LIST)
        names = [n for _, n in devices]
        assert "FaceTime HD Camera" not in names

    def test_no_audio_section(self):
        assert ar.parse_audio_devices(FFMPEG_NO_AUDIO) == []


class TestListDevicesStderr:
    def test_returns_stderr_on_success(self, mocker):
        mocker.patch("subprocess.run", return_value=SimpleNamespace(
            returncode=0, stdout="", stderr=FFMPEG_DEVICE_LIST,
        ))
        assert "BlackHole" in ar._list_devices_stderr()

    def test_returns_empty_when_ffmpeg_missing(self, mocker):
        mocker.patch("subprocess.run", side_effect=FileNotFoundError("no ffmpeg"))
        assert ar._list_devices_stderr() == ""


class TestDetectBlackholeDeprecated:
    """detect_blackhole er nu en deprecated no-op der altid returnerer None."""

    def test_returns_none(self):
        assert ar.detect_blackhole() is None

    def test_ignores_argument(self):
        assert ar.detect_blackhole(FFMPEG_DEVICE_LIST) is None


class TestSystemAudioAvailable:
    def test_true_when_binary_exists(self, mocker, tmp_path):
        fake_bin = tmp_path / ".audiotee"
        fake_bin.write_bytes(b"")
        mocker.patch.object(ar, "AUDIOTEE_BIN", fake_bin)
        assert ar.system_audio_available() is True

    def test_false_when_binary_missing(self, mocker, tmp_path):
        mocker.patch.object(ar, "AUDIOTEE_BIN", tmp_path / "nonexistent")
        assert ar.system_audio_available() is False


class TestSetupSystemOutput:
    def test_ok_when_available(self, mocker):
        mocker.patch("audio_routing.system_audio_available", return_value=True)
        r = ar.setup_system_output()
        assert r.status == "ok"

    def test_needs_manual_when_unavailable(self, mocker):
        mocker.patch("audio_routing.system_audio_available", return_value=False)
        r = ar.setup_system_output()
        assert r.status == "needs_manual"
        assert "audiotee" in r.guidance.lower()

    def test_system_device_not_none_when_available(self, mocker):
        """Regression: setup_system_output() skal returnere system_device != None
        naar AudioTee er tilgaengelig, saa kaldekoden (gater paa 'if system_device
        is not None') aktiverer dual-track-optagelse.

        Bug: foer fix returnerede setup_system_output() SetupResult(status='ok')
        uden system_device, saa system_device var None og dual-track udloestes
        aldrig fra GUI/CLI.
        """
        mocker.patch("audio_routing.system_audio_available", return_value=True)
        r = ar.setup_system_output()
        assert r.system_device is not None, (
            "system_device maa ikke vaere None naar AudioTee er tilgaengelig — "
            "kaldekoden bruger 'if system_device is not None' til at gate dual-track"
        )

    def test_ensure_routing_active_system_device_not_none_when_available(self, mocker):
        """Regression (via ensure_routing_active): system_device skal vaere ikke-None
        naar AudioTee er tilgaengelig, saa GUI/CLI-koden aktiverer dual-track.
        """
        mocker.patch("audio_routing.system_audio_available", return_value=True)
        r = ar.ensure_routing_active()
        assert r.system_device is not None


class TestEnsureRoutingActive:
    def test_delegates_to_setup_system_output(self, mocker):
        mocker.patch("audio_routing.system_audio_available", return_value=True)
        assert ar.ensure_routing_active().status == "ok"


class TestNoOps:
    def test_restore_system_output_does_nothing(self):
        assert ar.restore_system_output("Speakers") is None

    def test_load_previous_output_returns_none(self):
        assert ar.load_previous_output() is None


class TestSetupResult:
    def test_defaults(self):
        r = ar.SetupResult(status="ok")
        assert r.status == "ok"
        assert r.system_device is None
        assert r.previous_output is None
        assert r.guidance == ""
