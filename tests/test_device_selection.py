"""Tests for robust enhedsvalg: undgå at en forældet avfoundation-index
(fx efter et tidligere møde) eller en virtuel enhed (BlackHole/Teams/Zoom)
fører til en tavs optagelse.

Alle ffmpeg-kald mockes via conftest.mock_subprocess.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_tool as mt


# Det reelle scenarie fra den tomme Driftmøde-optagelse: iPhone-mikrofon tog
# index 0 (Continuity), BlackHole lå på index 1, MacBook-mikrofonen røg til 2.
SCENARIO_DEVICES = [
    (0, "Mikrofon i Siebe - iPhone"),
    (1, "BlackHole 2ch"),
    (2, "Mikrofon i MacBook Pro"),
    (3, "Microsoft Teams Audio"),
    (4, "ZoomAudioDevice"),
]


class TestIsVirtualInputDevice:
    @pytest.mark.parametrize("name", [
        "BlackHole 2ch",
        "Microsoft Teams Audio",
        "ZoomAudioDevice",
        "Multi-Output Device",
        "Aggregate Device",
        "Soundflower (2ch)",
        "VB-Cable",
    ])
    def test_virtual_devices_detected(self, name):
        assert mt.is_virtual_input_device(name) is True

    @pytest.mark.parametrize("name", [
        "Mikrofon i MacBook Pro",
        "Mikrofon i Siebe - iPhone",
        "MacBook Pro-mikrofon",
        "Ekstern USB-mikrofon",
    ])
    def test_real_devices_not_virtual(self, name):
        assert mt.is_virtual_input_device(name) is False


class TestSelectPreferredInputDevice:
    def test_prefers_macbook_mic_over_virtual_and_iphone(self):
        # Kernen i bug'en: må IKKE vælge BlackHole (1) eller iPhone (0).
        assert mt.select_preferred_input_device(SCENARIO_DEVICES) == 2

    def test_picks_macbook_when_first(self):
        devices = [(0, "BlackHole 2ch"), (1, "Mikrofon i MacBook Pro")]
        assert mt.select_preferred_input_device(devices) == 1

    def test_falls_back_to_first_non_virtual_when_no_builtin(self):
        devices = [(0, "BlackHole 2ch"), (1, "Ekstern USB-mikrofon")]
        assert mt.select_preferred_input_device(devices) == 1

    def test_returns_none_when_only_virtual(self):
        assert mt.select_preferred_input_device([(0, "BlackHole 2ch")]) is None

    def test_returns_none_for_empty(self):
        assert mt.select_preferred_input_device([]) is None


class TestResolveDeviceIndex:
    def test_resolves_name_to_current_index(self):
        # Selve fixet: navnet slås op til det AKTUELLE index, ikke et cachet.
        assert mt.resolve_device_index("Mikrofon i MacBook Pro", SCENARIO_DEVICES) == 2

    def test_case_insensitive(self):
        assert mt.resolve_device_index("mikrofon i macbook pro", SCENARIO_DEVICES) == 2

    def test_substring_match(self):
        assert mt.resolve_device_index("MacBook Pro", SCENARIO_DEVICES) == 2

    def test_returns_none_when_absent(self):
        assert mt.resolve_device_index("Findes Ikke", SCENARIO_DEVICES) is None

    def test_windows_name_id(self):
        devices = [("Mikrofon (Realtek)", "Mikrofon (Realtek)")]
        assert mt.resolve_device_index("Mikrofon (Realtek)", devices) == "Mikrofon (Realtek)"


class TestMeasureInputLevelDb:
    def test_parses_max_volume(self, mock_subprocess):
        mock_subprocess.run.return_value = SimpleNamespace(
            returncode=0, stdout="",
            stderr="[Parsed_volumedetect_0] max_volume: -30.0 dB\n",
        )
        assert mt.measure_input_level_db(2) == pytest.approx(-30.0)

    def test_silent_device_reads_floor(self, mock_subprocess):
        mock_subprocess.run.return_value = SimpleNamespace(
            returncode=0, stdout="",
            stderr="[Parsed_volumedetect_0] max_volume: -91.0 dB\n",
        )
        assert mt.measure_input_level_db(1) == pytest.approx(-91.0)

    def test_no_match_returns_none(self, mock_subprocess):
        mock_subprocess.run.return_value = SimpleNamespace(
            returncode=0, stdout="", stderr="some unrelated ffmpeg output\n",
        )
        assert mt.measure_input_level_db(2) is None

    def test_subprocess_error_returns_none(self, mock_subprocess):
        mock_subprocess.run.side_effect = OSError("ffmpeg blew up")
        assert mt.measure_input_level_db(2) is None


class TestInputAppearsSilent:
    def test_true_below_threshold(self, mocker):
        mocker.patch("meeting_tool.measure_input_level_db", return_value=-91.0)
        assert mt.input_appears_silent(1) is True

    def test_false_above_threshold(self, mocker):
        mocker.patch("meeting_tool.measure_input_level_db", return_value=-30.0)
        assert mt.input_appears_silent(2) is False

    def test_unmeasurable_is_not_treated_as_silent(self, mocker):
        # Måle-fejl må ikke blokere et rigtigt møde (ingen falske positiver).
        mocker.patch("meeting_tool.measure_input_level_db", return_value=None)
        assert mt.input_appears_silent(2) is False


class TestPrepareInputDevice:
    def test_resolves_name_and_returns_index_when_signal(self, mocker):
        mocker.patch("meeting_tool.list_audio_devices", return_value=SCENARIO_DEVICES)
        mocker.patch("meeting_tool.input_appears_silent", return_value=False)
        assert mt.prepare_input_device("Mikrofon i MacBook Pro") == 2

    def test_raises_when_selected_device_is_silent(self, mocker):
        mocker.patch("meeting_tool.list_audio_devices", return_value=SCENARIO_DEVICES)
        mocker.patch("meeting_tool.input_appears_silent", return_value=True)
        with pytest.raises(mt.SilentInputError):
            mt.prepare_input_device("BlackHole 2ch")

    def test_falls_back_to_preferred_when_name_missing(self, mocker):
        mocker.patch("meeting_tool.list_audio_devices", return_value=SCENARIO_DEVICES)
        mocker.patch("meeting_tool.input_appears_silent", return_value=False)
        # Gammelt/ukendt navn -> fald tilbage til den foretrukne (MacBook = 2).
        assert mt.prepare_input_device("Et gammelt enhedsnavn") == 2

    def test_raises_when_no_usable_device(self, mocker):
        mocker.patch("meeting_tool.list_audio_devices", return_value=[(0, "BlackHole 2ch")])
        mocker.patch("meeting_tool.input_appears_silent", return_value=False)
        with pytest.raises(mt.SilentInputError):
            mt.prepare_input_device("Findes ikke")

    def test_check_level_false_skips_silence_probe(self, mocker):
        mocker.patch("meeting_tool.list_audio_devices", return_value=SCENARIO_DEVICES)
        silent = mocker.patch("meeting_tool.input_appears_silent", return_value=True)
        # check_level=False -> spring stilheds-tjek over og returnér index uanset.
        assert mt.prepare_input_device("BlackHole 2ch", check_level=False) == 1
        silent.assert_not_called()

    def test_on_status_callback_is_optional(self, mocker):
        mocker.patch("meeting_tool.list_audio_devices", return_value=SCENARIO_DEVICES)
        mocker.patch("meeting_tool.input_appears_silent", return_value=False)
        # Må ikke kaste når on_status er None.
        assert mt.prepare_input_device("Mikrofon i MacBook Pro", on_status=None) == 2
