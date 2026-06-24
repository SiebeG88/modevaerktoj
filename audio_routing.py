"""Lyd-enhedsdetektion og systemlyd-tilgængelighed (macOS).

Tidligere håndterede dette modul også BlackHole-installation og Multi-Output-
routing. Det er fjernet — systemlyd-capture kører nu via AudioTee (Core Audio
Tap API, macOS 14.2+) i meeting_tool, der ikke kræver virtuelle drivere eller
output-omdirigering.

Tilbage er kun:
- Parsing af ffmpeg avfoundation-enhedsliste (bruges af GUI's mikrofon-dropdown).
- `system_audio_available()` der tjekker om AudioTee-binæren er bygget.
- Stub-funktioner med samme navne som de gamle (setup_system_output,
  ensure_routing_active, restore_system_output, load_previous_output) så
  meeting_app/CLI fortsat virker uden ændringer; de er no-ops eller returnerer
  blot om AudioTee findes.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
AUDIOTEE_BIN = TOOL_DIR / ".audiotee"

_AUDIOTEE_MISSING_GUIDANCE = (
    "AudioTee-binæren mangler. Byg den én gang:\n"
    "  1. git clone https://github.com/makeusabrew/audiotee.git /tmp/audiotee\n"
    "  2. cd /tmp/audiotee && swift build -c release\n"
    f"  3. cp /tmp/audiotee/.build/release/audiotee {AUDIOTEE_BIN}\n"
    "Første gang appen optager systemlyd vil macOS bede om "
    "'Screen & System Audio Recording'-tilladelse."
)


@dataclass
class SetupResult:
    """Resultat af systemlyd-opsætning.

    `system_device` og `previous_output` er ubrugt med AudioTee — beholdt for
    bagudkompatibilitet med kaldekoden i meeting_app/CLI.
    """
    status: str  # "ok" | "needs_manual"
    system_device: int | None = None
    previous_output: str | None = None
    guidance: str = ""


# ---------------------------------------------------------------------------
# Enhedsliste-parsing (mikrofon-dropdown i GUI)
# ---------------------------------------------------------------------------

def parse_audio_devices(stderr: str) -> list[tuple[int, str]]:
    """Udtræk (index, navn) for avfoundation audio-enheder fra ffmpeg-stderr."""
    devices: list[tuple[int, str]] = []
    in_audio = False
    for line in stderr.splitlines():
        if "audio devices" in line.lower():
            in_audio = True
            continue
        if in_audio and "devices" in line.lower() and "audio devices" not in line.lower():
            in_audio = False
            continue
        if in_audio and "[AVFoundation" in line:
            part = line.split("]", 2)
            if len(part) >= 3:
                idx = part[1].strip().strip("[]")
                name = part[2].strip()
                try:
                    devices.append((int(idx), name))
                except ValueError:
                    continue
    return devices


def _list_devices_stderr() -> str:
    """Kør ffmpeg device-listning og returnér stderr (tom streng ved fejl)."""
    try:
        import app_paths
        result = subprocess.run(
            [app_paths.resolve_binary("ffmpeg"), "-f", "avfoundation",
             "-list_devices", "true", "-i", ""],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return ""
    return result.stderr


def detect_blackhole(devices_stderr: str | None = None) -> int | None:
    """Bagudkompatibel: BlackHole bruges ikke længere. Returnerer altid None.

    Beholdt fordi CLI og GUI kalder den; vi ignorerer parameteret.
    """
    del devices_stderr
    return None


# ---------------------------------------------------------------------------
# Systemlyd-tilgængelighed (AudioTee)
# ---------------------------------------------------------------------------

def system_audio_available() -> bool:
    """True hvis AudioTee-binæren er bygget og klar."""
    return AUDIOTEE_BIN.exists()


def setup_system_output() -> SetupResult:
    """Tjekker om systemlyd-capture er klar (AudioTee bygget).

    Hed tidligere setup_system_output fordi den konfigurerede Multi-Output;
    den simple check her erstatter alt det gamle setup-flow.
    """
    if system_audio_available():
        # system_device=0 fungerer som et "ja, optag dual-track"-flag i GUI/CLI.
        # AudioTee tap'per systemlyd direkte uden enheds-indeks, så værdien
        # bruges kun til not-None-tjekket i meeting_tool.py — selve tallet
        # ignoreres (del sys_device i _record_dual_tracks).
        return SetupResult(status="ok", system_device=0)
    return SetupResult(status="needs_manual", guidance=_AUDIOTEE_MISSING_GUIDANCE)


def ensure_routing_active() -> SetupResult:
    """Bagudkompatibel: kaldes af GUI/CLI før hver optagelse.

    AudioTee kræver ingen forhåndsrouting — bare tjek at binæren findes.
    """
    return setup_system_output()


def restore_system_output(previous_output: str) -> None:
    """Bagudkompatibel no-op: AudioTee ændrer ikke brugerens lyd-output."""
    del previous_output


def load_previous_output() -> str | None:
    """Bagudkompatibel no-op: ingen output-routing at gendanne."""
    return None
