"""Regressionstests for tre konkrete fixes.

Fix #1 er daekket i test_audio_routing.py (TestSetupSystemOutput).

Fix #2 — _record_dual_tracks stop_event-raekkefoelge:
    stop_event.wait() skal kaldes FOER _drain/terminate, saa optagelsen
    ikke afslutter sig selv efter ~12s (drain-timeout) uanset om brugeren
    har trykket Stop.

Fix #3 — _gemini_transcribe_single thinking_budget:
    GenerateContentConfig skal sendes med thinking_config der har
    thinking_budget=128, saa 2.5-pro ikke braender hele output-budgettet
    paa thinking og returnerer tom tekst.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_tool as mt


# ---------------------------------------------------------------------------
# Fix #2 — _record_dual_tracks: stop_event.wait() FOER drain/terminate
# ---------------------------------------------------------------------------

class TestRecordDualTracksStopOrder:
    """Verificerer at _record_dual_tracks ikke drainer/terminerer processerne
    foer stop_event er sat.

    Tilgang: vi instrumenterer stop_event.wait og proc.wait via side_effect
    der logger kald-raekkefoelgen i en delt liste. Saa asserterer vi at
    stop_event.wait optaeder foerst.
    """

    def _make_fake_proc(self, call_log, label):
        """Returnerer et fake Popen-objekt hvis .wait() logger til call_log."""
        proc = MagicMock()
        proc.poll.return_value = None  # korer stadig
        proc.stdin = MagicMock()
        proc.stdin.closed = False
        proc.stdout = MagicMock()

        def _wait_side(timeout=None):
            call_log.append(f"proc.wait:{label}")
            return 0

        proc.wait.side_effect = _wait_side
        return proc

    def test_stop_event_wait_before_drain(self, mocker, tmp_path):
        """stop_event.wait() skal logges FOER proc.wait (drain) kaldes."""
        call_log = []

        stop_event = threading.Event()

        # Wrap stop_event.wait saa vi kan logge hvornaar den kaldes.
        original_wait = stop_event.wait

        def tracked_wait(timeout=None):
            call_log.append("stop_event.wait")
            stop_event.set()          # ssat nu saa _drain ikke blokerer
            return original_wait(0)   # returnerer straks

        stop_event.wait = tracked_wait

        mic_proc = self._make_fake_proc(call_log, "mic")
        # AudioTee-binaren skal IKKE eksistere saa vi undgaar at starte
        # audiotee_proc (simplerer testen til mic-only grenen).
        mocker.patch.object(mt, "AUDIOTEE_BIN", tmp_path / "nonexistent_audiotee")

        popen_calls = iter([mic_proc])
        mocker.patch("subprocess.Popen", side_effect=lambda *a, **k: next(popen_calls))

        mic_path = tmp_path / "mic.wav"
        sys_path = tmp_path / "sys.wav"

        mt._record_dual_tracks(
            mic_path, sys_path,
            mic_device=0,
            sys_device=0,
            stop_event=stop_event,
            on_status=lambda m: None,
        )

        assert "stop_event.wait" in call_log, "stop_event.wait blev aldrig kaldt"
        stop_wait_idx = call_log.index("stop_event.wait")
        drain_entries = [i for i, e in enumerate(call_log) if e.startswith("proc.wait:")]
        assert drain_entries, "proc.wait (drain) blev aldrig kaldt"
        first_drain_idx = drain_entries[0]
        assert stop_wait_idx < first_drain_idx, (
            f"stop_event.wait (pos {stop_wait_idx}) skal komme FOER "
            f"proc.wait/drain (pos {first_drain_idx}). Raekkefoelge: {call_log}"
        )

    def test_function_blocks_until_stop_event_set(self, mocker, tmp_path):
        """_record_dual_tracks maa ikke returnere foer stop_event saettes.

        Vi starter funktionen i en baggrundstraad med et stop_event der
        IKKE er sat. Funktionen skal stadig koere (blokere) efter en kort
        ventetid. Dernaest saetter vi stop_event og verificerer at
        funktionen returnerer.
        """
        stop_event = threading.Event()
        done_event = threading.Event()

        mic_proc = MagicMock()
        mic_proc.poll.return_value = None
        mic_proc.stdin = MagicMock()
        mic_proc.stdin.closed = False
        mic_proc.stdout = MagicMock()
        mic_proc.wait.return_value = 0

        mocker.patch.object(mt, "AUDIOTEE_BIN", tmp_path / "nonexistent_audiotee")
        mocker.patch("subprocess.Popen", return_value=mic_proc)

        mic_path = tmp_path / "mic.wav"
        sys_path = tmp_path / "sys.wav"

        def _run():
            mt._record_dual_tracks(
                mic_path, sys_path,
                mic_device=0,
                sys_device=0,
                stop_event=stop_event,
                on_status=lambda m: None,
            )
            done_event.set()

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        # Funktionen maa IKKE vaere faerdig efter 0.1s (stop_event er ikke sat).
        finished_early = done_event.wait(timeout=0.10)
        assert not finished_early, (
            "_record_dual_tracks returnerede uden at stop_event var sat — "
            "drainer straks (den gamle bug)"
        )
        assert t.is_alive(), "Traaden er ikke laengere i live — funktion returnerede for tidligt"

        # Saet stop_event — funktionen skal nu afslutte hurtigt.
        stop_event.set()
        finished = done_event.wait(timeout=2.0)
        assert finished, "_record_dual_tracks returnerede ikke inden for 2s efter stop_event blev sat"


# ---------------------------------------------------------------------------
# Fix #3 — _gemini_transcribe_single: thinking_config med thinking_budget=128
# ---------------------------------------------------------------------------

class TestGeminiTranscribeSingleThinkingBudget:
    """Verificerer at GenerateContentConfig sendes med
    thinking_config=ThinkingConfig(thinking_budget=128).

    Foer fix: ingen ThinkingConfig → 2.5-pro kunne bruge hele output-
    budgettet paa thinking og returnere tom tekst + MAX_TOKENS.
    """

    def test_thinking_budget_128_in_generate_content_config(
        self, fake_gemini_client, tmp_wav
    ):
        """thinking_config skal vaere sat med thinking_budget=128 i den
        GenerateContentConfig der sendes til generate_content."""
        import google.genai as _genai_module

        _msgs, on_status = [], lambda m: _msgs.append(m)
        mt._gemini_transcribe_single(
            fake_gemini_client, tmp_wav,
            system_instruction="Transkriber venligst.",
            model="gemini-2.5-pro",
            chunk_label="01/1",
            on_status=on_status,
        )

        cfg_call = _genai_module.types.GenerateContentConfig.call_args
        assert cfg_call is not None, "GenerateContentConfig blev aldrig kaldt"

        # ThinkingConfig skal vaere instantieret med thinking_budget=128
        thinking_config_calls = _genai_module.types.ThinkingConfig.call_args
        assert thinking_config_calls is not None, (
            "types.ThinkingConfig blev aldrig instantieret — "
            "thinking_config mangler i GenerateContentConfig-kaldet"
        )
        assert thinking_config_calls.kwargs.get("thinking_budget") == 128, (
            f"thinking_budget skal vaere 128, fik: "
            f"{thinking_config_calls.kwargs.get('thinking_budget')!r}"
        )

    def test_thinking_config_passed_to_generate_content_config(
        self, fake_gemini_client, tmp_wav
    ):
        """thinking_config-argumentet i GenerateContentConfig-kaldet skal vaere
        det ThinkingConfig-objekt der er bygget med thinking_budget=128.

        Denne test verificerer at ThinkingConfig-resultatet faktisk videregives
        (ikke kun at ThinkingConfig instantieres et sted).
        """
        import google.genai as _genai_module

        _msgs, on_status = [], lambda m: _msgs.append(m)
        mt._gemini_transcribe_single(
            fake_gemini_client, tmp_wav,
            system_instruction="Transkriber venligst.",
            model="gemini-2.5-pro",
            chunk_label="01/1",
            on_status=on_status,
        )

        cfg_call = _genai_module.types.GenerateContentConfig.call_args
        assert cfg_call is not None

        # ThinkingConfig() returnerer et MagicMock (da types er en MagicMock).
        # Verificer at kwarg 'thinking_config' er sat i GenerateContentConfig-kaldet.
        thinking_config_kwarg = cfg_call.kwargs.get("thinking_config")
        assert thinking_config_kwarg is not None, (
            "'thinking_config' kwarg mangler i GenerateContentConfig-kaldet — "
            "grenen er ikke naet eller ThinkingConfig sendes ikke videre"
        )
