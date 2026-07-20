#!/opt/local/bin/python3.12
"""
Moedvaerktoej for moeder (generisk eksempel-organisation)
Optager, transkriberer, genererer referat og opgaver per person.

Brug:
    # Optag og transkriber direkte
    python meeting_tool.py --record

    # Optag med specifik enhed
    python meeting_tool.py --record --list-devices
    python meeting_tool.py --record --device 1

    # Transkriber eksisterende fil
    python meeting_tool.py path/til/lydfil.m4a

Kraever:
    - ffmpeg installeret
    - GEMINI_API_KEY miljoevariabel sat (til Gemini-transkription og referat)
    - Python-pakker: faster-whisper, google-genai
"""

import argparse
import atexit
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime

import audio_routing
import app_paths
import docx_export
from transcript_merge import build_transcript


@atexit.register
def _restore_audio_output_on_exit():  # pragma: no cover
    """Sikkerhedsnet: gendan brugerens lyd-output hvis en dual-track-optagelse
    crashede før den nåede at gendanne det selv."""
    prev = audio_routing.load_previous_output()
    if prev:
        audio_routing.restore_system_output(prev)

TOOL_DIR = Path(__file__).resolve().parent
MEETINGS_DIR = TOOL_DIR.parent

# AudioTee-binær (Core Audio Tap-baseret systemlyd-capture, macOS 14.2+).
# Bygges fra https://github.com/makeusabrew/audiotee og kopieres til TOOL_DIR.
AUDIOTEE_BIN = TOOL_DIR / ".audiotee"

# Bruger-config (env, state, ordliste, mødetyper) ligger et skrivbart sted, der
# overlever OTA-opdateringer. macOS: == TOOL_DIR (uændret). Windows: %APPDATA%.
CONFIG_DIR = app_paths.user_config_dir()


def seed_user_config():
    """Kopiér read-only default-templates til CONFIG_DIR ved behov (no-op på
    macOS). Kaldes ved app-opstart — IKKE ved import, så unit-tests og CLI ikke
    får utilsigtede filsystem-side-effekter ved blot at importere modulet."""
    app_paths.ensure_user_config(
        ["vocabulary.default.json", "meeting_types.default.json"]
    )

# ffmpeg/ffprobe: bundlet binær på frosset Windows, ellers PATH-opslag.
FFMPEG = app_paths.resolve_binary("ffmpeg")
FFPROBE = app_paths.resolve_binary("ffprobe")

# På det frosne Windows-GUI-bygge (console=False) har processen intet
# konsolvindue; hver ffmpeg/ffprobe-underproces ville ellers blinke sit eget
# sorte konsolvindue op. CREATE_NO_WINDOW undertrykker det. 0 (ingen effekt) på
# macOS/Linux, hvor flaget ikke findes (getattr giver 0 = ingen effekt).
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ---------------------------------------------------------------------------
# Platform-detektion
# ---------------------------------------------------------------------------
_IS_MACOS = sys.platform == "darwin"
_IS_WINDOWS = sys.platform == "win32"


def _load_env_file(config_dir: Path | None = None):
    """Læser .env fra config-mappen (samme format som meeting_app.py).
    Eksisterende miljøvariabler overskrives IKKE.
    """
    env_file = (config_dir or CONFIG_DIR) / ".env"
    if not env_file.exists():
        return
    try:
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value:
                os.environ.setdefault(key, value)
    except Exception as e:  # pragma: no cover
        print(f"Kunne ikke læse {env_file}: {e}", file=sys.stderr)


_load_env_file()


# ---------------------------------------------------------------------------
# 1. Optagelse via ffmpeg (avfoundation paa macOS)
# ---------------------------------------------------------------------------

def audio_input_format() -> str:
    """ffmpeg input-format til live-optagelse på denne platform."""
    if _IS_MACOS:
        return "avfoundation"
    if _IS_WINDOWS:
        return "dshow"
    raise RuntimeError(
        f"Live-optagelse understøttes ikke på platformen '{sys.platform}'. "
        "Brug fanen 'Transkribér fil' i stedet."
    )


def build_ffmpeg_input(device_id) -> list:
    """['-f', <fmt>, '-i', <input>]-fragment til ffmpeg-optagekommandoen.

    macOS:   device_id er et heltals-index -> ':<id>'
    Windows: device_id er et device-navn   -> 'audio=<id>'
    """
    fmt = audio_input_format()
    if _IS_WINDOWS:
        return ["-f", fmt, "-i", f"audio={device_id}"]
    return ["-f", fmt, "-i", f":{device_id}"]


# Windows power-API konstanter (SetThreadExecutionState)
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002


def start_keep_awake():
    """Forhindrer maskinen i at sove under optagelse.

    Returnerer et handle til stop_keep_awake(). Fejler aldrig hårdt.
    macOS -> caffeinate-proces; Windows -> 'windows'-sentinel; andet -> None.
    """
    if _IS_MACOS:
        try:
            return subprocess.Popen(
                ["caffeinate", "-d", "-i", "-m", "-s"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:  # pragma: no cover
            return None
    if _IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(
                _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED
            )
            return "windows"
        except Exception:  # pragma: no cover
            return None
    return None


def stop_keep_awake(handle) -> None:
    """Ophæver keep-awake. Idempotent; tåler None."""
    if handle is None:
        return
    if handle == "windows":
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
        except Exception:  # pragma: no cover
            pass
        return
    try:
        handle.terminate()
        handle.wait(timeout=2)
    except Exception:  # pragma: no cover
        try:
            handle.kill()
        except Exception:
            pass


def default_meetings_dir() -> Path:
    """Standard-mappe til møde-output pr. platform.

    macOS/andet: ~/Møder.
    Windows:     %OneDriveCommercial% -> %OneDrive% -> hjemmemappe.
    """
    if _IS_MACOS:
        return Path.home() / "Møder"
    if _IS_WINDOWS:
        for var in ("OneDriveCommercial", "OneDrive"):
            val = os.environ.get(var)
            if val:
                return Path(val)
        return Path.home()
    return Path.home()


def list_audio_devices() -> list:
    """Liste af (id, navn) for tilgængelige lyd-input-enheder.

    macOS:   id = int (avfoundation-index)
    Windows: id = str (dshow device-navn)
    Fejl/ukendt platform -> [].
    """
    fmt = "dshow" if _IS_WINDOWS else "avfoundation"
    dummy = "dummy" if _IS_WINDOWS else ""
    try:
        result = subprocess.run(
            [FFMPEG, "-f", fmt, "-list_devices", "true", "-i", dummy],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return []

    stderr = result.stderr or ""
    if _IS_WINDOWS:
        return _parse_dshow_devices(stderr)
    return _parse_avfoundation_devices(stderr)


def _parse_avfoundation_devices(stderr: str) -> list:
    """Parse 'ffmpeg -f avfoundation -list_devices' stderr -> [(int, navn)]."""
    devices = []
    in_audio = False
    for line in stderr.splitlines():
        if "audio devices" in line.lower():
            in_audio = True
            continue
        if in_audio and "[AVFoundation" in line:
            part = line.split("]", 2)
            if len(part) >= 3:
                idx_s = part[1].strip().strip("[]")
                name = part[2].strip()
                try:
                    devices.append((int(idx_s), name))
                except ValueError:
                    pass
    return devices


def _parse_dshow_devices(stderr: str) -> list:
    """Parse 'ffmpeg -f dshow -list_devices' stderr -> [(navn, navn)].

    Robust over for begge ffmpeg-stilarter: sektions-header
    ('DirectShow audio devices') og '(audio)'-markør pr. linje.
    """
    devices = []
    in_audio = False
    for line in stderr.splitlines():
        low = line.lower()
        if "video devices" in low:
            in_audio = False
            continue
        if "audio devices" in low:
            in_audio = True
            continue
        if "alternative name" in low or '"' not in line:
            continue
        name = line.split('"')[1].strip()
        if not name:
            continue
        if low.rstrip().endswith("(video)"):
            continue
        if in_audio or low.rstrip().endswith("(audio)"):
            devices.append((name, name))
    return devices


# Virtuelle/loopback/konference-enheder — IKKE rigtige mikrofoner. At optage
# fra en af disse uden noget routet ind giver en helt tavs (nul) optagelse.
_VIRTUAL_DEVICE_KEYWORDS = (
    "blackhole", "multi-output", "aggregate", "soundflower",
    "loopback", "vb-cable", "vb-audio", "teams", "zoom",
)

# Navne-fragmenter der peger på en indbygget Mac-mikrofon (foretrækkes).
_BUILTIN_MIC_KEYWORDS = ("macbook", "built-in", "indbygget", "intern")


class SilentInputError(RuntimeError):
    """Rejst når den valgte lydenhed ikke leverer signal (tavs optagelse)."""


def is_virtual_input_device(name: str) -> bool:
    """True hvis enhedsnavnet ligner en virtuel/loopback/konference-enhed
    (BlackHole, Multi-Output, Aggregate, Teams, Zoom, …) frem for en mikrofon."""
    low = (name or "").lower()
    return any(kw in low for kw in _VIRTUAL_DEVICE_KEYWORDS)


def select_preferred_input_device(devices):
    """Vælg den bedste rigtige mikrofon fra [(id, navn), …].

    Udelukker virtuelle enheder; foretrækker den indbyggede Mac-mikrofon;
    ellers første ikke-virtuelle enhed. Returnerer enhedens id eller None.
    """
    real = [(dev_id, name) for dev_id, name in devices
            if not is_virtual_input_device(name)]
    if not real:
        return None
    for dev_id, name in real:
        low = name.lower()
        if any(kw in low for kw in _BUILTIN_MIC_KEYWORDS):
            return dev_id
    return real[0][0]


def resolve_device_index(name, devices):
    """Slå et enhedsnavn op til dets AKTUELLE id i [(id, navn), …].

    avfoundation-indekser flytter sig når enheder kobles til/fra (iPhone via
    Continuity, BlackHole, Teams/Zoom), så et navn skal genoversættes lige før
    optagelse i stedet for at genbruge et cachet index. Matcher eksakt, derefter
    case-insensitivt, derefter som delstreng. Returnerer id eller None.
    """
    if not name:
        return None
    for dev_id, dev_name in devices:
        if dev_name == name:
            return dev_id
    low = name.lower()
    for dev_id, dev_name in devices:
        if dev_name.lower() == low:
            return dev_id
    for dev_id, dev_name in devices:
        if low in dev_name.lower():
            return dev_id
    return None


def measure_input_level_db(device_id, duration: float = 1.0):
    """Optag ~duration sek. fra enheden og returnér max_volume i dB.

    Pre-flight-tjek: digital stilhed giver ~-91 dB. Returnerer en float, eller
    None hvis niveauet ikke kunne måles (fx ffmpeg-fejl).
    """
    cmd = [
        FFMPEG, "-hide_banner",
        *build_ffmpeg_input(device_id),
        "-t", str(duration),
        "-af", "volumedetect",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                                creationflags=_NO_WINDOW)
    except Exception:
        return None
    m = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", result.stderr or "")
    if not m:
        return None
    return float(m.group(1))


def input_appears_silent(device_id, threshold_db: float = -60.0,
                         duration: float = 1.0) -> bool:
    """True hvis enheden måler under threshold_db (reelt tavs).

    Kan niveauet ikke måles (None), returneres False — en måle-fejl må ikke
    blokere et rigtigt møde.
    """
    level = measure_input_level_db(device_id, duration=duration)
    if level is None:
        return False
    return level <= threshold_db


def prepare_input_device(device_name, *, check_level: bool = True,
                         threshold_db: float = -60.0, duration: float = 1.0,
                         on_status=None):
    """Find det aktuelle id for den valgte mikrofon og verificér signal.

    Slår device_name op til et aktuelt index (fixer forældede indekser efter fx
    et tidligere møde). Findes navnet ikke, vælges den foretrukne mikrofon.
    Kører et pre-flight stilheds-tjek og rejser SilentInputError hvis enheden er
    tavs. Returnerer det brugbare device_id.
    """
    def _say(msg):
        if on_status:
            on_status(msg)

    devices = list_audio_devices()
    device_id = resolve_device_index(device_name, devices)
    if device_id is None:
        device_id = select_preferred_input_device(devices)
        if device_id is None:
            raise SilentInputError(
                "Ingen brugbar mikrofon fundet. Tjek at en mikrofon er "
                "tilsluttet og valgt."
            )
        _say(f"Enheden '{device_name}' blev ikke fundet — bruger den "
             "foretrukne mikrofon i stedet.")

    if check_level and input_appears_silent(
        device_id, threshold_db=threshold_db, duration=duration
    ):
        raise SilentInputError(
            f"Den valgte lydenhed ('{device_name}') giver intet signal — "
            "optagelsen ville blive tavs. Tjek at den rigtige mikrofon er valgt "
            "og ikke er muted (undgå virtuelle enheder som BlackHole)."
        )
    return device_id


def record_meeting(output_path: Path, device_id: int | str = 1) -> Path:
    """
    Optager lyd fra mikrofon via ffmpeg.
    Gemmer direkte i 16 kHz mono WAV (optimalt for Whisper).
    Tryk Ctrl+C for at stoppe optagelsen.

    device_id: avfoundation-index (macOS, int) eller dshow device-navn (Windows, str)
    """
    print(f"\n{'='*60}")
    print(f"  OPTAGELSE STARTET")
    print(f"  Enhed: {device_id}")
    print(f"  Format: 16 kHz mono WAV")
    print(f"  Fil: {output_path.name}")
    print(f"  Tryk Ctrl+C for at stoppe")
    print(f"{'='*60}\n")

    cmd = [
        FFMPEG,
        *build_ffmpeg_input(device_id),
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(output_path),
        "-y",
        "-loglevel", "warning",
    ]

    # stdin=PIPE: send 'q' for paent stop (SIGTERM ignoreres af avfoundation)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=_NO_WINDOW)

    def stop_recording(signum, frame):  # pragma: no cover
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.write(b"q")
                proc.stdin.flush()
                proc.stdin.close()
        except Exception:
            proc.terminate()

    original_handler = signal.signal(signal.SIGINT, stop_recording)

    try:
        proc.wait()
    finally:
        signal.signal(signal.SIGINT, original_handler)

    if output_path.exists() and output_path.stat().st_size > 0:
        size_mb = output_path.stat().st_size / (1024 * 1024)
        print(f"\nOptagelse gemt: {output_path} ({size_mb:.1f} MB)")
        return output_path
    else:
        print("Fejl: Optagelsen blev ikke gemt.")
        sys.exit(1)


@dataclass
class DualTrackResult:
    """Resultat af to-spors-optagelse: status, faktiske starttider (epoch) og varigheder."""
    sys_ok: bool
    mic_start: float
    sys_start: float
    mic_duration: float
    sys_duration: float


def _resolve_sync(stamps, mic_path: Path, sys_path: Path, sys_ok: bool,
                  *, now: float, duration_fn) -> "DualTrackResult":
    """Udled DualTrackResult fra start-stempler med sikre fallbacks.

    `stamps` er {"mic": float|None, "sys": float|None}. Manglende mic-stempel →
    `now`; manglende sys-stempel → mic_start. `duration_fn(path)->float` er
    injicerbar (i produktion `_ffprobe_duration`)."""
    mic_start = stamps.get("mic") if stamps.get("mic") is not None else now
    sys_start = stamps.get("sys") if stamps.get("sys") is not None else mic_start
    mic_duration = duration_fn(mic_path) if mic_path.exists() else 0.0
    sys_final = bool(sys_ok and sys_path.exists() and sys_path.stat().st_size > 0)
    sys_duration = duration_fn(sys_path) if sys_final else 0.0
    return DualTrackResult(sys_final, mic_start, sys_start, mic_duration, sys_duration)


def _write_sync_sidecar(path: Path, result: "DualTrackResult") -> None:
    """Skriv sync-metadata som JSON ved siden af WAV-filerne."""
    import json
    path.write_text(json.dumps({
        "version": 1,
        "mic_start": result.mic_start,
        "sys_start": result.sys_start,
        "mic_duration": result.mic_duration,
        "sys_duration": result.sys_duration,
    }), encoding="utf-8")


def _record_dual_tracks(
    mic_path: Path,
    sys_path: Path,
    mic_device: int,
    sys_device: int,  # historisk parameter (BlackHole-index); ignoreret nu — AudioTee bruges
    stop_event: threading.Event | None,
    on_status,
) -> "DualTrackResult":
    """Optag mikrofon + systemlyd til hver sin WAV.

    Mikrofon: ffmpeg/avfoundation som før.
    Systemlyd: AudioTee (Core Audio Tap API, macOS 14.2+) piper rå s16le PCM
    til en pump-tråd der videresender til en parallel ffmpeg-proces der pakker
    det som WAV. Ingen BlackHole eller Multi-Output-routing nødvendig.

    Returnerer et DualTrackResult med synkroniseringsinformation og skriver
    desuden et <navn>.sync.json-sidecar ved siden af mic_path.

    Ved fejl på systemsporet degraderes til mic-kun (DualTrackResult.sys_ok=False).

    De to spor kan starte med få ms forskel; det håndteres via tidsstempler i
    DualTrackResult, så sample-præcis sync er ikke nødvendig her.
    """
    del sys_device  # bagudkompatibilitet — AudioTee tap'per systemlyd direkte

    def _status(msg):
        (on_status or print)(msg)

    stamps: dict[str, float | None] = {"mic": None, "sys": None}
    capture_stop = threading.Event()

    # --- Mikrofon-spor: ffmpeg/avfoundation ---
    mic_cmd = [
        FFMPEG, "-f", "avfoundation", "-i", f":{mic_device}",
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        str(mic_path), "-y", "-loglevel", "warning",
    ]
    mic_proc = subprocess.Popen(mic_cmd, stdin=subprocess.PIPE)

    # Poll-tråd: stempl mic-start når filen vokser forbi WAV-headeren (>1 KB).
    def _poll_mic_start():
        while not capture_stop.is_set():
            try:
                if mic_path.stat().st_size > 1024:
                    stamps["mic"] = time.time()
                    return
            except OSError:
                pass
            capture_stop.wait(0.05)
    mic_poll_thread = threading.Thread(target=_poll_mic_start, daemon=True)
    mic_poll_thread.start()

    # --- Systemlyd-spor: AudioTee → (pump-tråd) → ffmpeg pipe → WAV ---
    sys_ok = True
    audiotee_proc: subprocess.Popen | None = None
    sys_ffmpeg_proc: subprocess.Popen | None = None
    sys_pump_thread: threading.Thread | None = None
    if not AUDIOTEE_BIN.exists():
        _status(
            f"AudioTee-binær mangler ({AUDIOTEE_BIN.name}) — "
            f"optager kun mikrofon. Byg med: cd /tmp/audiotee && swift build -c release"
        )
        sys_ok = False
    else:
        try:
            audiotee_proc = subprocess.Popen(
                [str(AUDIOTEE_BIN), "--sample-rate", "16000"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            sys_ffmpeg_proc = subprocess.Popen(
                [
                    FFMPEG,
                    "-f", "s16le", "-ar", "16000", "-ac", "1",
                    "-i", "pipe:0",
                    "-c:a", "copy",
                    str(sys_path), "-y", "-loglevel", "warning",
                ],
                stdin=subprocess.PIPE,
            )

            def _pump_sys():
                first = True
                try:
                    while True:
                        data = audiotee_proc.stdout.read(4096)
                        if not data:
                            break
                        if first:
                            stamps["sys"] = time.time()
                            first = False
                        try:
                            sys_ffmpeg_proc.stdin.write(data)
                        except (BrokenPipeError, ValueError):
                            break
                finally:
                    try:
                        sys_ffmpeg_proc.stdin.close()
                    except Exception:
                        pass
            sys_pump_thread = threading.Thread(target=_pump_sys, daemon=True)
            sys_pump_thread.start()
        except Exception as e:  # pragma: no cover
            _status(f"Kunne ikke starte systemlyd-optagelse: {e} — fortsætter mic-kun.")
            sys_ok = False

    def stop_all():
        try:
            if mic_proc.stdin and not mic_proc.stdin.closed:
                mic_proc.stdin.write(b"q")
                mic_proc.stdin.flush()
                mic_proc.stdin.close()
        except Exception:  # pragma: no cover
            mic_proc.terminate()
        if audiotee_proc is not None and audiotee_proc.poll() is None:
            try:
                audiotee_proc.send_signal(signal.SIGINT)
            except Exception:  # pragma: no cover
                try:
                    audiotee_proc.terminate()
                except Exception:
                    pass

    def _drain(p, label):
        try:
            p.wait(timeout=12)
        except subprocess.TimeoutExpired:  # pragma: no cover
            _status(f"{label} svarer ikke — terminerer ...")
            try:
                p.terminate()
                p.wait(timeout=3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    if stop_event is not None:
        stop_event.wait()
        stop_all()
        _drain(mic_proc, "ffmpeg-mic")
        if audiotee_proc is not None:
            _drain(audiotee_proc, "AudioTee")
        if sys_pump_thread is not None:
            sys_pump_thread.join(timeout=5)
        if sys_ffmpeg_proc is not None:
            _drain(sys_ffmpeg_proc, "ffmpeg-sys")
    else:  # pragma: no cover
        original = signal.signal(signal.SIGINT, lambda *_: stop_all())
        try:
            _drain(mic_proc, "ffmpeg-mic")
            if audiotee_proc is not None:
                _drain(audiotee_proc, "AudioTee")
            if sys_pump_thread is not None:
                sys_pump_thread.join(timeout=5)
            if sys_ffmpeg_proc is not None:
                _drain(sys_ffmpeg_proc, "ffmpeg-sys")
        finally:
            signal.signal(signal.SIGINT, original)

    capture_stop.set()
    mic_poll_thread.join(timeout=1)

    result = _resolve_sync(
        stamps, mic_path, sys_path, sys_ok,
        now=time.time(), duration_fn=_ffprobe_duration,
    )
    name = mic_path.name
    suffix = ".mic.wav"
    base = name[:-len(suffix)] if name.endswith(suffix) else mic_path.stem
    sidecar_path = mic_path.with_name(base + ".sync.json")
    try:
        _write_sync_sidecar(sidecar_path, result)
    except Exception as e:  # pragma: no cover
        _status(f"Kunne ikke skrive {sidecar_path.name}: {e}")
    return result


# ---------------------------------------------------------------------------
# 1b. Live optagelse + parallel transkription (chunk-baseret)
# ---------------------------------------------------------------------------

# Traersler for at afvise hallucinerede/lav-kvalitets segmenter.
# Whisper-modeller har tendens til at "droemme" tekst paa lydsvage passager.
MIN_AVG_LOGPROB = -1.0        # segment skal have gennemsnits-logsandsynlighed over denne
MAX_NO_SPEECH_PROB = 0.7      # segment skal have under denne sandsynlighed for stilhed
MAX_COMPRESSION_RATIO = 2.4   # segment skal have komprimerings-ratio under denne (hoej = repetitivt)


def is_repetitive_hallucination(text: str) -> bool:
    """Detekterer repetitive fraser som 'åh ja, ja, ja' eller 'det kan det kan det kan'."""
    import re
    # Normaliser: fjern tegnsaetning, lowercase
    words = re.findall(r"[\wæøåÆØÅ]+", text.lower())
    if len(words) < 6:
        return False
    # Tael hyppigste ord
    counts: dict[str, int] = {}
    for w in words:
        counts[w] = counts.get(w, 0) + 1
    top_count = max(counts.values())
    # Hvis et ord udgoer over 45% af teksten er det hallucinatorisk gentagelse
    if top_count / len(words) > 0.45:
        return True
    # Check for gentagne 2-grams (fx "åh ja åh ja åh ja")
    if len(words) >= 8:
        bigrams: dict[str, int] = {}
        for i in range(len(words) - 1):
            bg = f"{words[i]} {words[i+1]}"
            bigrams[bg] = bigrams.get(bg, 0) + 1
        top_bg = max(bigrams.values())
        if top_bg / (len(words) - 1) > 0.35:
            return True
    # Lav unique-word-ratio paa laengere tekster = drift-hallucination
    # Normal dansk tale har typisk > 55% unikke ord i en laengere sentens.
    if len(words) >= 12:
        unique_ratio = len(set(words)) / len(words)
        if unique_ratio < 0.5:
            return True
    return False

def record_and_transcribe_live(
    output_dir: Path,
    date: str,
    device_id: int | str,  # avfoundation-index (macOS, int) eller dshow device-navn (Windows, str)
    system_device: int | None = None,
    model_size: str = "large-v3",  # default så system_device kan stå før i keyword-API'et
    chunk_duration: int = 300,
    stop_event: threading.Event | None = None,
    on_status=None,
    on_chunk_done=None,
    recording_name: str | None = None,
) -> tuple[Path, str]:
    """
    Optager lyd og transkriberer i baggrunden mens optagelsen koerer.

    - ffmpeg splitter optagelsen i chunks af `chunk_duration` sekunder
    - En baggrundstraad transkriberer chunks efterhaanden som de bliver faerdige
    - Modellen loades kun een gang
    - Stop enten via Ctrl+C (CLI) eller ved at saette `stop_event` (GUI)
    - `on_status(msg)` og `on_chunk_done(idx, segments)` callbacks til live-updates

    Returnerer (master_wav_path, full_transcript).
    """
    from faster_whisper import WhisperModel

    def _status(msg: str):
        if on_status is not None:
            on_status(msg)
        else:
            print(msg, flush=True)

    output_dir.mkdir(parents=True, exist_ok=True)

    if system_device is not None:
        base_name = recording_name or f"Driftledelsesmoede {date}"
        mic_path = output_dir / f"{base_name}.mic.wav"
        sys_path = output_dir / f"{base_name}.sys.wav"

        caffeinate_proc = None
        try:
            caffeinate_proc = subprocess.Popen(
                ["caffeinate", "-d", "-i", "-m", "-s"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:  # pragma: no cover
            pass

        try:
            _status("Optager to spor (mikrofon + systemlyd) ...")
            dual = _record_dual_tracks(
                mic_path, sys_path, device_id, system_device, stop_event, _status,
            )
        finally:
            if caffeinate_proc is not None:
                try:
                    caffeinate_proc.terminate()
                    caffeinate_proc.wait(timeout=2)
                except Exception:  # pragma: no cover
                    try:
                        caffeinate_proc.kill()
                    except Exception:
                        pass

        if stop_event is not None:
            stop_event.clear()  # samme grund som Gemini/single-track: undgå at afbryde transkription

        if not (mic_path.exists() and mic_path.stat().st_size > 0):
            raise RuntimeError("Optagelsen blev ikke gemt (mic-spor mangler).")

        _status("Transkriberer mikrofon-spor lokalt ...")
        mic_segs = _parse_transcript_segments(
            transcribe_audio(mic_path, model_size=model_size)
        )
        if dual.sys_ok:
            _status("Transkriberer systemlyd-spor lokalt ...")
            sys_segs = _parse_transcript_segments(
                transcribe_audio(sys_path, model_size=model_size)
            )
        else:
            sys_segs = []

        return mic_path, build_transcript(
            mic_segs, sys_segs,
            mic_dur=dual.mic_duration, sys_dur=dual.sys_duration,
            mic_start=dual.mic_start, sys_start=dual.sys_start,
        )

    chunks_dir = output_dir / ".chunks"
    if chunks_dir.exists():
        shutil.rmtree(chunks_dir)
    chunks_dir.mkdir(parents=True)

    if on_status is None:
        print(f"\n{'='*60}")
        print(f"  LIVE OPTAGELSE + TRANSKRIPTION")
        print(f"  Enhed: {device_id}")
        print(f"  Chunk-varighed: {chunk_duration}s ({chunk_duration//60} min)")
        print(f"  Model: {model_size}")
        print(f"  Chunks: {chunks_dir}")
        print(f"  Tryk Ctrl+C for at stoppe")
        print(f"{'='*60}\n")

    # --- Hold maskinen vågen mens vi optager ---
    _awake = start_keep_awake()
    if _awake is not None:
        _status("Holder maskinen vågen under optagelse.")

    # --- Load model een gang op foran ---
    _status("Indlaeser model ... (foerste gang downloades den)")
    model = WhisperModel(model_size, device="cpu", compute_type="auto")
    _status("Model klar.")

    # Vocabulary til bias af lokal sprogmodel (egennavne + steder).
    _vocab = load_vocabulary(CONFIG_DIR)
    _hviske_initial_prompt = format_vocabulary_for_hviske(_vocab) or None

    # --- Delt tilstand ---
    all_segments: dict[int, list[tuple[float, float, str]]] = {}
    segments_lock = threading.Lock()
    submitted_chunks: set[int] = set()
    submit_lock = threading.Lock()
    transcribe_queue: queue.Queue = queue.Queue()
    stop_watcher = threading.Event()

    def submit_chunk(idx: int, path: Path):
        with submit_lock:
            if idx in submitted_chunks:
                return
            submitted_chunks.add(idx)
        transcribe_queue.put((idx, path))

    # --- Worker: transkriberer chunks fra koeen ---
    def transcribe_worker():
        while True:
            item = transcribe_queue.get()
            if item is None:
                break
            chunk_idx, chunk_path = item
            try:
                t0 = time.time()
                _status(f"Transkriberer chunk {chunk_idx:04d} ...")
                segments, _ = model.transcribe(
                    str(chunk_path),
                    language="da",
                    beam_size=5,
                    vad_filter=True,
                    vad_parameters=dict(
                        min_silence_duration_ms=500,
                        speech_pad_ms=300,
                    ),
                    initial_prompt=_hviske_initial_prompt,
                    # Forhindrer hallucinationer i at sprede sig mellem 30-sek vinduer.
                    # Kan give lidt mindre konsistens i navne/termer indenfor chunk,
                    # men undgaar "åh ja, ja, ja" drift-hallucinationer.
                    condition_on_previous_text=False,
                    # Indbygget filtrering af segmenter under disse traerskler.
                    no_speech_threshold=MAX_NO_SPEECH_PROB,
                    log_prob_threshold=MIN_AVG_LOGPROB,
                    compression_ratio_threshold=MAX_COMPRESSION_RATIO,
                )
                offset = chunk_idx * chunk_duration
                result = []
                dropped = 0
                for seg in segments:
                    text = seg.text.strip()
                    if not text:
                        continue
                    # Post-filter: afvis segmenter med daarlige metrikker
                    if seg.avg_logprob < MIN_AVG_LOGPROB:
                        dropped += 1
                        continue
                    if seg.no_speech_prob > MAX_NO_SPEECH_PROB:
                        dropped += 1
                        continue
                    if seg.compression_ratio > MAX_COMPRESSION_RATIO:
                        dropped += 1
                        continue
                    # Post-filter: afvis repetitive hallucinationer
                    if is_repetitive_hallucination(text):
                        dropped += 1
                        continue
                    result.append((
                        seg.start + offset,
                        seg.end + offset,
                        text,
                    ))
                with segments_lock:
                    all_segments[chunk_idx] = result
                elapsed = time.time() - t0
                drop_info = f", {dropped} filtreret" if dropped else ""
                _status(
                    f"Chunk {chunk_idx:04d} faerdig "
                    f"({len(result)} segmenter{drop_info}, {elapsed:.1f}s)"
                )
                if on_chunk_done is not None:
                    try:
                        on_chunk_done(chunk_idx, result)
                    except Exception:  # pragma: no cover
                        pass
            except Exception as e:
                _status(f"FEJL i chunk {chunk_idx}: {e}")

    worker = threading.Thread(target=transcribe_worker, daemon=True)
    worker.start()

    # --- Watcher: indsender faerdige chunks til transkription ---
    def watch_chunks():
        while not stop_watcher.is_set():
            files = sorted(chunks_dir.glob("chunk_*.wav"))
            # Alle chunks undtagen den sidste er garanteret faerdigskrevet
            if len(files) >= 2:
                for f in files[:-1]:
                    try:
                        idx = int(f.stem.split("_")[1])
                    except (ValueError, IndexError):
                        continue
                    submit_chunk(idx, f)
            time.sleep(3)

    watcher = threading.Thread(target=watch_chunks, daemon=True)
    watcher.start()

    # --- Start ffmpeg i segment-mode ---
    chunk_pattern = str(chunks_dir / "chunk_%04d.wav")
    cmd = [
        FFMPEG,
        *build_ffmpeg_input(device_id),
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        "-f", "segment",
        "-segment_time", str(chunk_duration),
        "-reset_timestamps", "1",
        chunk_pattern,
        "-y",
        "-loglevel", "warning",
    ]

    # stdin=PIPE saa vi kan sende 'q' for at stoppe ffmpeg paent.
    # SIGTERM/SIGINT ignoreres ofte af avfoundation-input paa macOS,
    # hvilket laser hele optagelsen. 'q' til stdin er ffmpegs kanoniske
    # quit-signal og flusher den igangvaerende segment-fil korrekt.
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=_NO_WINDOW)

    def graceful_stop():
        """Stop ffmpeg paent via stdin 'q', med fallback til terminate/kill."""
        # 1. Proev 'q' (ffmpegs graceful quit - flusher igangvaerende segment)
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.write(b"q")
                proc.stdin.flush()
                proc.stdin.close()
        except Exception:  # pragma: no cover
            pass
        # 2. Vent paa at ffmpeg flusher og exiter
        try:
            proc.wait(timeout=10)
            return
        except subprocess.TimeoutExpired:  # pragma: no cover
            pass
        # 3. Fallback: SIGTERM
        _status("ffmpeg svarer ikke paa 'q' - sender SIGTERM ...")  # pragma: no cover
        try:  # pragma: no cover
            proc.terminate()
            proc.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:  # pragma: no cover
            pass
        except Exception:  # pragma: no cover
            pass
        # 4. Sidste udvej: SIGKILL (risiko for tab af igangvaerende chunk)
        _status("ffmpeg svarer stadig ikke - draeber med SIGKILL ...")  # pragma: no cover
        try:  # pragma: no cover
            proc.kill()
        except Exception:  # pragma: no cover
            pass

    # Stop-mekanisme: enten stop_event (GUI) eller SIGINT (CLI)
    original_handler = None
    stop_watcher_thread = None

    if stop_event is not None:
        def watch_stop_event():
            stop_event.wait()
            _status("Stopper optagelse paent ...")
            graceful_stop()

        stop_watcher_thread = threading.Thread(
            target=watch_stop_event, daemon=True
        )
        stop_watcher_thread.start()
    else:
        def stop_recording_signal(signum, frame):  # pragma: no cover
            _status(
                "\nStopper optagelse ... "
                "(afventer sidste chunk + evt. resterende transkription)"
            )
            # Koer i separat traad saa signal-handler returnerer hurtigt
            threading.Thread(target=graceful_stop, daemon=True).start()

        original_handler = signal.signal(signal.SIGINT, stop_recording_signal)

    try:
        proc.wait()
    finally:
        if original_handler is not None:
            signal.signal(signal.SIGINT, original_handler)

    # --- ffmpeg faerdig: stop watcher, indsend resterende chunks ---
    stop_watcher.set()
    watcher.join(timeout=5)

    final_files = sorted(chunks_dir.glob("chunk_*.wav"))
    if not final_files:
        _status("Fejl: Ingen chunks blev optaget.")
        transcribe_queue.put(None)
        worker.join(timeout=5)
        stop_keep_awake(_awake)
        raise RuntimeError("Ingen chunks blev optaget")

    for f in final_files:
        try:
            idx = int(f.stem.split("_")[1])
        except (ValueError, IndexError):
            continue
        submit_chunk(idx, f)

    # --- Byg master WAV FOERST (saa audio er sikret inden lang transkription) ---
    base_name = recording_name or f"Driftledelsesmoede {date}"
    master_path = output_dir / f"{base_name}.wav"
    _status(f"Samler {len(final_files)} chunks til master WAV ...")
    concat_list = chunks_dir / "concat.txt"
    with open(concat_list, "w") as fh:
        for chunk in final_files:
            # ffmpeg concat demuxer kraever 'file'-linjer med escapede stier
            safe_path = str(chunk.resolve()).replace("'", r"'\''")
            fh.write(f"file '{safe_path}'\n")

    subprocess.run(
        [
            FFMPEG,
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            str(master_path),
            "-y",
            "-loglevel", "warning",
        ],
        check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=_NO_WINDOW,
    )
    size_mb = master_path.stat().st_size / (1024 * 1024)
    _status(f"Master WAV gemt: {master_path.name} ({size_mb:.1f} MB)")

    # --- Vent paa at worker faerdiggoer alle chunks ---
    _status("Venter paa sidste transkriptioner ...")
    transcribe_queue.put(None)
    worker.join()

    # --- Byg samlet transkript i korrekt raekkefoelge ---
    with segments_lock:
        ordered = sorted(all_segments.items())

    lines = []
    for _idx, segs in ordered:
        for start, end, text in segs:
            lines.append(f"[{format_timestamp(start)} - {format_timestamp(end)}] {text}")
    transcript = "\n".join(lines)

    # --- Ryd op ---
    shutil.rmtree(chunks_dir, ignore_errors=True)
    stop_keep_awake(_awake)

    total_segs = sum(len(s) for s in all_segments.values())
    _status(
        f"Live-transkription faerdig: "
        f"{len(all_segments)} chunks, {total_segs} segmenter."
    )
    return master_path, transcript


# ---------------------------------------------------------------------------
# 1c. Optag først, transkribér bagefter (bruges af Gemini-motoren)
# ---------------------------------------------------------------------------

def record_then_transcribe_gemini(
    output_dir: Path,
    date: str,
    device_id: int | str,  # avfoundation-index (macOS, int) eller dshow device-navn (Windows, str)
    system_device: int | None = None,
    attendees: list[str] | None = None,
    model: str = "gemini-2.5-pro",
    stop_event: threading.Event | None = None,
    on_status=None,
    on_chunk_done=None,  # API-kompatibilitet med live; bruges ikke her
    recording_name: str | None = None,
    meeting_type_name: str = "driftsledelsesmøde",
) -> tuple[Path, str]:
    """
    Flow til Gemini-motoren: optag til én master WAV, og transkribér bagefter
    via Gemini Files API (med automatisk chunking for lang lyd).

    Samme interface som record_and_transcribe_live: returnerer (wav_path, transcript).
    Stop-mekanisme: stop_event (GUI) eller SIGINT (CLI).
    """
    del on_chunk_done  # ubrugt — Gemini kører efter optagelse

    def _status(msg: str):
        if on_status is not None:
            on_status(msg)
        else:
            print(msg, flush=True)

    output_dir.mkdir(parents=True, exist_ok=True)
    base_name = recording_name or f"Driftledelsesmøde {date}"
    master_path = output_dir / f"{base_name}.wav"

    # --- Hold maskinen vågen ---
    _awake = start_keep_awake()
    if _awake is not None:
        _status("Holder maskinen vågen under optagelse.")

    if system_device is not None:
        mic_path = output_dir / f"{base_name}.mic.wav"
        sys_path = output_dir / f"{base_name}.sys.wav"
        try:
            _status("Optager to spor (mikrofon + systemlyd) ...")
            dual = _record_dual_tracks(
                mic_path, sys_path, device_id, system_device, stop_event, _status,
            )
            sys_ok = dual.sys_ok
        finally:
            stop_keep_awake(_awake)
        if stop_event is not None:
            stop_event.clear()  # samme grund som single-track: undgå at afbryde transkription
        if not (mic_path.exists() and mic_path.stat().st_size > 0):
            raise RuntimeError("Optagelsen blev ikke gemt (mic-spor mangler).")
        _status("Transkriberer mikrofon-spor via Gemini ...")
        mic_segs = _parse_transcript_segments(
            transcribe_with_gemini(mic_path, attendees=attendees, model=model,
                                   on_status=_status, stop_event=stop_event,
                                   meeting_type_name=meeting_type_name)
        )
        if sys_ok:
            _status("Transkriberer systemlyd-spor via Gemini ...")
            sys_segs = _parse_transcript_segments(
                transcribe_with_gemini(sys_path, attendees=attendees, model=model,
                                       on_status=_status, stop_event=stop_event,
                                       meeting_type_name=meeting_type_name)
            )
        else:
            sys_segs = []
        return mic_path, build_transcript(
            mic_segs, sys_segs,
            mic_dur=dual.mic_duration, sys_dur=dual.sys_duration,
            mic_start=dual.mic_start, sys_start=dual.sys_start,
        )

    # --- Start ffmpeg ---
    _status(f"Starter optagelse: {master_path.name}")
    cmd = [
        FFMPEG,
        *build_ffmpeg_input(device_id),
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        str(master_path),
        "-y", "-loglevel", "warning",
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=_NO_WINDOW)

    def graceful_stop():
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.write(b"q")
                proc.stdin.flush()
                proc.stdin.close()
        except Exception:  # pragma: no cover
            pass
        try:
            proc.wait(timeout=10)
            return
        except subprocess.TimeoutExpired:  # pragma: no cover
            pass
        _status("ffmpeg svarer ikke på 'q' – sender SIGTERM ...")  # pragma: no cover
        try:  # pragma: no cover
            proc.terminate()
            proc.wait(timeout=3)
            return
        except Exception:  # pragma: no cover
            pass
        _status("ffmpeg svarer stadig ikke – dræber med SIGKILL ...")  # pragma: no cover
        try:  # pragma: no cover
            proc.kill()
        except Exception:  # pragma: no cover
            pass

    original_handler = None
    if stop_event is not None:
        def watch_stop_event():
            stop_event.wait()
            _status("Stopper optagelse pænt ...")
            graceful_stop()
        threading.Thread(target=watch_stop_event, daemon=True).start()
    else:
        def stop_recording_signal(signum, frame):  # pragma: no cover
            _status("\nStopper optagelse ...")
            threading.Thread(target=graceful_stop, daemon=True).start()
        original_handler = signal.signal(signal.SIGINT, stop_recording_signal)

    try:
        proc.wait()
    finally:
        if original_handler is not None:
            signal.signal(signal.SIGINT, original_handler)

    if not master_path.exists() or master_path.stat().st_size == 0:
        stop_keep_awake(_awake)
        raise RuntimeError("Optagelsen blev ikke gemt.")

    size_mb = master_path.stat().st_size / (1024 * 1024)
    _status(f"Optagelse færdig: {master_path.name} ({size_mb:.1f} MB)")

    # --- Transkribér via Gemini ---
    # Nulstil stop_event mellem optagelsesfase og transkriberingsfase, så
    # ffmpeg-stop ikke automatisk afbryder transkriberingen. UI kan sætte
    # det igen hvis brugeren ønsker at afbryde transkriberingen.
    if stop_event is not None:
        stop_event.clear()

    try:
        transcript = transcribe_with_gemini(
            master_path,
            attendees=attendees,
            model=model,
            on_status=_status,
            stop_event=stop_event,
            meeting_type_name=meeting_type_name,
        )
    finally:
        stop_keep_awake(_awake)

    return master_path, transcript


# ---------------------------------------------------------------------------
# 2. Audio-konvertering
# ---------------------------------------------------------------------------

def convert_to_wav(input_path: Path, output_path: Path) -> Path:
    """Konverterer lydfil til 16 kHz mono WAV (kraevet af Whisper-modeller)."""
    print(f"Konverterer {input_path.name} til WAV ...", flush=True)
    cmd = [
        FFMPEG, "-i", str(input_path),
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        str(output_path), "-y", "-loglevel", "warning"
    ]
    subprocess.run(cmd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   creationflags=_NO_WINDOW)
    print("Konvertering faerdig.", flush=True)
    return output_path


# ---------------------------------------------------------------------------
# 3. Transkription med faster-whisper
# ---------------------------------------------------------------------------

def transcribe_audio(wav_path: Path, model_size: str = "large-v3") -> str:
    """
    Transkriberer WAV-fil med faster-whisper.

    model_size kan vaere:
      - "large-v3"  (OpenAI Whisper - god til dansk)
      - Et HuggingFace model-id, f.eks. "syvai/faster-hviske-v3-conversation"
    """
    from faster_whisper import WhisperModel

    print(f"Indlaeser model: {model_size} ...", flush=True)
    print("(Foerste gang downloades modellen - det kan tage et par minutter)", flush=True)

    model = WhisperModel(
        model_size,
        device="cpu",
        compute_type="auto",
    )

    # Vocabulary til bias af lokal sprogmodel (egennavne + steder).
    vocab = load_vocabulary(CONFIG_DIR)
    initial_prompt = format_vocabulary_for_hviske(vocab) or None

    print("Transkriberer ... (dette kan tage et stykke tid for lange optagelser)", flush=True)
    segments, info = model.transcribe(
        str(wav_path),
        language="da",
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(
            min_silence_duration_ms=500,
            speech_pad_ms=300,
        ),
        initial_prompt=initial_prompt,
    )

    lines = []
    for segment in segments:
        start = format_timestamp(segment.start)
        end = format_timestamp(segment.end)
        lines.append(f"[{start} - {end}] {segment.text.strip()}")

    transcript = "\n".join(lines)
    print(f"Transkription faerdig: {len(lines)} segmenter.", flush=True)
    return transcript


def format_timestamp(seconds: float) -> str:
    """Formaterer sekunder til MM:SS."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


_TS_LINE = re.compile(
    r"^\[(?P<start>[\d:]+)\s*-\s*(?P<end>[\d:]+)\]\s*(?P<text>.*)$"
)


def _ts_to_seconds(ts: str) -> float:
    """'MM:SS' eller 'H:MM:SS' → sekunder."""
    parts = [int(p) for p in ts.split(":")]
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h, m, s = 0, parts[0], parts[1]
    else:  # pragma: no cover
        return 0.0
    return float(h * 3600 + m * 60 + s)


def _parse_transcript_segments(text: str) -> list[tuple[float, float, str]]:
    """Parse '[MM:SS - MM:SS] tekst'-linjer til (start, end, text)-tuples."""
    segments = []
    for line in text.splitlines():
        m = _TS_LINE.match(line.strip())
        if not m:
            continue
        segments.append((
            _ts_to_seconds(m.group("start")),
            _ts_to_seconds(m.group("end")),
            m.group("text").strip(),
        ))
    return segments


# ---------------------------------------------------------------------------
# 3b. Transkription med Gemini 2.5 Pro (cloud, bedre paa danske navne/termer)
# ---------------------------------------------------------------------------

GEMINI_SYSTEM_INSTRUCTION = """\
Du er en professionel dansk transskribent for et {meeting_type_name} hos \
Eksempelgården – en dansk grøntsagsproducent med ca. 2.000 ha.

**Deltagere i dette møde:** {attendees}

---

## VIGTIGSTE REGLER OM EGENNAVNE

**Du må IKKE gætte navne. Hvis du er i tvivl, skriv ordet du hører efterfulgt \
af [?] – fx "Nord-... [?]", "Mats[?]". Det er bedre at flagge tvivl end at \
indsætte et forkert kendt navn.**

Listen nedenfor er navne der ER kendte i organisationen. Brug dem KUN når du \
faktisk hører dem – ikke som "default-gæt" når lyden er utydelig. To navne på \
listen kan godt lyde akustisk ens; brug konteksten til at afgøre, eller flag \
med [?].

{vocabulary_section}

{extra_context}

---

## OPGAVE

Transskribér lydfilen **ordret** på dansk – så tæt på det faktisk sagte som \
muligt. Bevar talesprog, pauseord, gentagelser og tøven ("ja ja", "altså", \
"øhm", "jo jo"). Lad være med at polere sproget.

Bevar dansk stavning med æ, ø, å – brug altid de ægte tegn, aldrig ae/oe/aa.

**Tvivl om navne eller fagtermer:** marker altid med [?] efter ordet. Det er \
bedre at flagge end at gætte forkert.

**Tiltale og selvintroduktion:** når en deltager bliver tiltalt med navn \
("Mads, hvad siger du?" eller "Lars her –"), skriv det præcist – det er \
afgørende for at kunne tilskrive citater til den rigtige person bagefter.

Brug punktum og komma hvor det giver mening. Tankestreg for afbrydelser.
"""


GEMINI_USER_PROMPT = """\
Transskribér den vedhæftede lydfil ordret på dansk.

Output ét segment pr. linje, i formatet:

[MM:SS - MM:SS] tekst

For lydfiler længere end én time brug H:MM:SS i stedet.

VIGTIGT:
- Ingen overskrift, ingen indledning, ingen sammendrag – KUN segmenter.
- Transskribér så ordret som muligt. Behold talesprog, pauseord og gentagelser.
- Segmenter følger naturlige pauser og taleskift – ikke en fast længde. Korte \
  replikker (et "ja", "nej", "mm") får deres egen linje. Længere udsagn må gerne \
  være 20+ sekunder hvis det er sammenhængende.
- Bevar alle danske bogstaver korrekt: brug altid æ, ø, å – aldrig ae, oe, aa.
- Bevar egennavne og fagtermer præcist som de står i system-instruktionen.
- Hvis du er i tvivl om et ord, skriv det du hører efterfulgt af [?].
"""


def _ffprobe_duration(wav_path: Path) -> float:
    """Returnerer lydens varighed i sekunder via ffprobe."""
    result = subprocess.run(
        [
            FFPROBE, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(wav_path),
        ],
        capture_output=True, text=True, check=True,
        creationflags=_NO_WINDOW,
    )
    return float(result.stdout.strip())


def _split_audio_for_gemini(
    wav_path: Path, chunk_seconds: int, out_dir: Path
) -> list[Path]:
    """Splitter lyd i ens-lange chunks med ffmpeg segment-mode."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(out_dir / "chunk_%04d.wav")
    subprocess.run(
        [
            FFMPEG, "-i", str(wav_path),
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            "-f", "segment",
            "-segment_time", str(chunk_seconds),
            "-reset_timestamps", "1",
            pattern, "-y", "-loglevel", "warning",
        ],
        check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=_NO_WINDOW,
    )
    return sorted(out_dir.glob("chunk_*.wav"))


def _parse_timestamp(s: str) -> int:
    """Parser 'MM:SS' eller 'H:MM:SS' til sekunder."""
    parts = s.strip().split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return 0


# Matcher [MM:SS - MM:SS] og [H:MM:SS - H:MM:SS] i starten af en linje.
_TIMESTAMP_LINE_RE = re.compile(
    r"^\[\s*((?:\d+:)?\d+:\d+)\s*-\s*((?:\d+:)?\d+:\d+)\s*\]"
)


def _offset_transcript(text: str, offset_seconds: int) -> str:
    """Skyder alle [timestamp - timestamp] præfikser med offset_seconds."""
    out_lines = []
    for line in text.splitlines():
        m = _TIMESTAMP_LINE_RE.match(line)
        if m:
            t1 = _parse_timestamp(m.group(1)) + offset_seconds
            t2 = _parse_timestamp(m.group(2)) + offset_seconds
            rest = line[m.end():]
            out_lines.append(f"[{format_timestamp(t1)} - {format_timestamp(t2)}]{rest}")
        else:
            out_lines.append(line)
    return "\n".join(out_lines)


def _clamp_chunk_timestamps(text: str, chunk_seconds: float) -> str:
    """Klem hvert segments [start - end]-præfiks til [0, chunk_seconds].

    Modvirker Gemini-hallucinerede tidsstempler, FØR chunk-offset lægges på, så
    urealistiske tal (fx 12 timer på et 12-min chunk) ikke forplanter sig.
    """
    out_lines = []
    for line in text.splitlines():
        m = _TIMESTAMP_LINE_RE.match(line)
        if m:
            t1 = max(0.0, min(float(_parse_timestamp(m.group(1))), chunk_seconds))
            t2 = max(0.0, min(float(_parse_timestamp(m.group(2))), chunk_seconds))
            if t2 < t1:
                t2 = t1
            rest = line[m.end():]
            out_lines.append(f"[{format_timestamp(int(t1))} - {format_timestamp(int(t2))}]{rest}")
        else:
            out_lines.append(line)
    return "\n".join(out_lines)


class _StopRequested(Exception):
    """Brugeren har trykket Stop under transkription."""


def _gemini_transcribe_single(
    client,
    wav_path: Path,
    system_instruction: str,
    model: str,
    chunk_label: str,
    on_status,
    stop_event: threading.Event | None = None,
    temperature: float = 0.1,
) -> tuple[str, dict]:
    """Upload + transkribér én lydfil. Returnerer (text, diagnostics)."""
    from google.genai import types

    def _check_stop():
        if stop_event is not None and stop_event.is_set():
            raise _StopRequested(f"[{chunk_label}] afbrudt af bruger")

    _check_stop()
    size_mb = wav_path.stat().st_size / (1024 * 1024)
    on_status(f"[{chunk_label}] Uploader ({size_mb:.1f} MB) ...")

    # Gemini SDK sender filnavnet som HTTP-header (kun ASCII).
    upload_path = wav_path
    tmp_link: Path | None = None
    try:
        str(wav_path).encode("ascii")
    except UnicodeEncodeError:
        tmp_link = Path(tempfile.gettempdir()) / (
            f"gemini_upload_{os.getpid()}_{chunk_label}.wav"
        )
        if tmp_link.exists() or tmp_link.is_symlink():
            tmp_link.unlink()
        try:
            tmp_link.symlink_to(wav_path.resolve())
        except OSError:
            # Windows uden symlink-privilegie (WinError 1314: "A required
            # privilege is not held") — fald tilbage til en almindelig kopi.
            # Danske mødenavne (fx "Driftledelsesmøde") er ikke-ASCII, så denne
            # gren rammes ved helt normal brug på Windows.
            shutil.copy2(wav_path, tmp_link)
        upload_path = tmp_link

    try:
        uploaded = client.files.upload(
            file=str(upload_path),
            config=types.UploadFileConfig(
                display_name=f"chunk_{chunk_label}"[:120],
                mime_type="audio/wav",
            ),
        )
    finally:
        if tmp_link is not None:
            try:
                tmp_link.unlink()
            except Exception:  # pragma: no cover
                pass

    while uploaded.state.name == "PROCESSING":
        # Vent i korte intervaller så stop_event kan reagere hurtigt.
        for _ in range(10):
            _check_stop()
            time.sleep(0.2)
        uploaded = client.files.get(name=uploaded.name)

    if uploaded.state.name != "ACTIVE":
        raise RuntimeError(
            f"[{chunk_label}] Upload fejlede: state={uploaded.state.name}"
        )

    _check_stop()
    on_status(f"[{chunk_label}] Transkriberer ...")
    t0 = time.time()

    # Retry på transient netværksfejl. Gemini SDK retrier på HTTP-statuskoder
    # men ikke på connection errors som "Server disconnected without sending
    # a response" — så vi pakker selv generate_content i retry-loop.
    # Overbelastning (503/UNAVAILABLE "high demand") får et tålmodigt forløb:
    # en demand-spike varer typisk minutter, så 2s/4s-backoff rider den ikke
    # af. Op til 5 forsøg med 15-120s ventetid (~3,7 min i alt).
    last_exc: Exception | None = None
    response = None
    quick_backoff = (2, 4)        # netværksblip: max 3 forsøg
    overload_backoff = (15, 30, 60, 120)  # 503-overbelastning: max 5 forsøg
    attempt = 0
    while True:
        attempt += 1
        try:
            _check_stop()
            response = client.models.generate_content(
                model=model,
                contents=[uploaded, GEMINI_USER_PROMPT],
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=temperature,
                    max_output_tokens=65000,
                    # Transkription kræver ingen reasoning. Uden cap kan 2.5-pro
                    # brænde hele output-budgettet på thinking → tom tekst +
                    # finish_reason=MAX_TOKENS (set på sys-sporet). Cap lavt så
                    # hele budgettet er til selve transkriptet. (Min for pro = 128.)
                    thinking_config=types.ThinkingConfig(thinking_budget=128),
                ),
            )
            break
        except _StopRequested:
            raise
        except Exception as e:
            # Retry kun på transient fejl (netværk/timeout/5xx). 4xx-fejl
            # som invalid input, kvote etc. skal ikke retries.
            msg = str(e).lower()
            transient_markers = (
                "remoteprotocolerror",
                "server disconnected",
                "connection",
                "timeout",
                "timed out",
                "503",
                "504",
                "500",
                "502",
            )
            overload_markers = ("503", "unavailable", "overloaded", "high demand")
            is_transient = any(m in msg for m in transient_markers) or \
                any(m in type(e).__name__.lower() for m in ("timeout", "connection", "remoteprotocol"))
            is_overload = any(m in msg for m in overload_markers)
            schedule = overload_backoff if is_overload else quick_backoff
            max_attempts = len(schedule) + 1
            if not is_transient or attempt >= max_attempts:
                raise
            backoff = schedule[attempt - 1]
            on_status(
                f"[{chunk_label}] Transient fejl ({type(e).__name__}: {e}); "
                f"genforsøg {attempt+1}/{max_attempts} om {backoff}s ..."
            )
            last_exc = e
            if stop_event is not None:
                # Afbrydeligt: vækkes straks hvis brugeren stopper.
                stop_event.wait(backoff)
            else:
                time.sleep(backoff)
    if response is None:
        raise RuntimeError(
            f"[{chunk_label}] Gemini fejlede efter {attempt} forsøg: {last_exc}"
        )
    elapsed = time.time() - t0

    try:
        client.files.delete(name=uploaded.name)
    except Exception:  # pragma: no cover
        pass

    finish_reason = None
    try:
        finish_reason = str(response.candidates[0].finish_reason)
    except Exception:  # pragma: no cover
        pass

    usage = getattr(response, "usage_metadata", None)
    diag = {
        "finish_reason": finish_reason,
        "input_tokens": getattr(usage, "prompt_token_count", None) if usage else None,
        "output_tokens": getattr(usage, "candidates_token_count", None) if usage else None,
        "thinking_tokens": getattr(usage, "thoughts_token_count", None) if usage else None,
        "elapsed": elapsed,
    }

    text = (response.text or "").strip()
    if not text:
        raise RuntimeError(
            f"[{chunk_label}] Tom transkription (finish_reason={finish_reason})."
        )

    on_status(
        f"[{chunk_label}] Færdig: {text.count(chr(10)) + 1} linjer, "
        f"{len(text)} tegn, {elapsed:.1f}s "
        f"(finish={finish_reason}, out_tokens={diag['output_tokens']})."
    )
    return text, diag


# Kendte konsekvente fejl modellen laver, som er entydige at rette automatisk.
# Reglen: kun ord der ALDRIG er korrekte i denne organisations kontekst.
# Brug \b ord-grænse så ord der indeholder "Mats" (fx "Matsen") ikke rammes.
_TRANSCRIPT_AUTO_FIXES = [
    (r"\bMats\b", "Mads"),  # Mads er driftsleder; "Mats" er en hyppig fejlstavning
]


def _postprocess_transcript(text: str) -> str:
    """Anvend kendte sikre rettelser på Gemini-output.

    Kun rettelser hvor det målte ord ALDRIG er korrekt i organisationen.
    Tvetydige rettelser (fx Nordmarken vs Nordgården) håndteres af prompten,
    ikke her – fordi begge ord kan være korrekte afhængigt af kontekst.
    """
    import re as _re
    for pattern, replacement in _TRANSCRIPT_AUTO_FIXES:
        text = _re.sub(pattern, replacement, text)
    return text


# Chunk-varighed for lang lyd. 12 min giver os god margen under Geminis
# "stopper frivilligt på lang lyd"-tærskel (set empirisk omkring 6-10 min
# output-ækvivalent). Over denne tærskel splittes lyd før transkription.
GEMINI_CHUNK_SECONDS = 12 * 60
GEMINI_CHUNK_THRESHOLD_SECONDS = 15 * 60  # splitter kun hvis > dette
GEMINI_MAX_PARALLEL = 3


def transcribe_with_gemini(
    wav_path: Path,
    attendees: list[str] | None = None,
    model: str = "gemini-2.5-pro",
    extra_context: str = "",
    on_status=None,
    chunk_seconds: int = GEMINI_CHUNK_SECONDS,
    chunk_threshold_seconds: int = GEMINI_CHUNK_THRESHOLD_SECONDS,
    max_parallel: int = GEMINI_MAX_PARALLEL,
    stop_event: threading.Event | None = None,
    meeting_type_name: str = "driftsledelsesmøde",
) -> str:
    """
    Transkribér lydfil med Gemini 2.5 Pro via Files API.

    For lyd over `chunk_threshold_seconds` (default 15 min) splittes den
    automatisk i `chunk_seconds`-stykker (default 12 min), der transkriberes
    parallelt og flettes med offset-korrigerede timestamps. Grunden:
    Gemini stopper frivilligt før fuld transkription på lang lyd.

    Output-format matcher faster-whisper: "[MM:SS - MM:SS] tekst".

    Kræver GEMINI_API_KEY (eller GOOGLE_API_KEY) i miljøet.
    """
    from google import genai
    from google.genai import types as genai_types

    def _status(msg: str):
        if on_status is not None:
            on_status(msg)
        else:
            print(msg, flush=True)

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY ikke sat. Tilføj den i .env eller som miljøvariabel."
        )

    # HTTP-timeout (ms): hver request må højst tage 10 min. Uden timeout kan
    # SDK'et hænge i timevis ved netværks-/server-side problemer.
    client = genai.Client(
        api_key=api_key,
        http_options=genai_types.HttpOptions(timeout=600_000),
    )

    attendees_str = ", ".join(attendees) if attendees else "ukendt"
    vocab = load_vocabulary(CONFIG_DIR)
    system_instruction = GEMINI_SYSTEM_INSTRUCTION.format(
        meeting_type_name=meeting_type_name,
        attendees=attendees_str,
        vocabulary_section=format_vocabulary_for_gemini(vocab),
        extra_context=extra_context or "(ingen ekstra kontekst for dette møde)",
    )

    duration = _ffprobe_duration(wav_path)
    _status(f"Lydens varighed: {duration/60:.1f} min.")

    # --- Kort lyd: single-shot ---
    if duration <= chunk_threshold_seconds:
        try:
            text, _diag = _gemini_transcribe_single(
                client, wav_path, system_instruction, model, "single", _status,
                stop_event=stop_event,
            )
        except RuntimeError as e:
            # RECITATION-filteret er ikke-deterministisk; et nyt forsøg med
            # højere temperatur omgår det ofte.
            if "RECITATION" not in str(e):
                raise
            _status("RECITATION-filter ramte; genforsøger med temperatur=0.5 ...")
            text, _diag = _gemini_transcribe_single(
                client, wav_path, system_instruction, model, "single-retry", _status,
                stop_event=stop_event,
                temperature=0.5,
            )
        return _postprocess_transcript(text)

    # --- Lang lyd: split og transkribér parallelt ---
    n_chunks = int(duration // chunk_seconds) + (1 if duration % chunk_seconds else 0)
    _status(
        f"Splitter i {n_chunks} chunks af ~{chunk_seconds//60} min "
        f"(parallelt med {max_parallel} workers) ..."
    )

    chunks_dir = Path(tempfile.mkdtemp(prefix="gemini_chunks_"))
    try:
        chunk_paths = _split_audio_for_gemini(wav_path, chunk_seconds, chunks_dir)
        _status(f"Opdelt i {len(chunk_paths)} chunks. Starter transkription.")

        from concurrent.futures import ThreadPoolExecutor, as_completed

        results: dict[int, str] = {}
        diags: dict[int, dict] = {}

        def run_one(idx_path):
            idx, path = idx_path
            label = f"{idx+1:02d}/{len(chunk_paths)}"
            text, diag = _gemini_transcribe_single(
                client, path, system_instruction, model, label, _status,
                stop_event=stop_event,
            )
            text = _clamp_chunk_timestamps(text, chunk_seconds)
            offset = idx * chunk_seconds
            return idx, _offset_transcript(text, offset), diag

        failures: dict[int, Exception] = {}
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=max_parallel) as pool:
            future_to_idx = {
                pool.submit(run_one, (i, p)): i
                for i, p in enumerate(chunk_paths)
            }
            try:
                for fut in as_completed(future_to_idx):
                    idx_orig = future_to_idx[fut]
                    try:
                        idx, text, diag = fut.result()
                        results[idx] = text
                        diags[idx] = diag
                    except _StopRequested:
                        raise
                    except Exception as e:
                        # Per-chunk fejl må ikke smide de andre væk. Saml
                        # dem op og forsøg igen serielt nedenfor.
                        failures[idx_orig] = e
                        _status(
                            f"[{idx_orig+1:02d}/{len(chunk_paths)}] "
                            f"Fejl i første forsøg: {e}"
                        )
            except _StopRequested:
                if stop_event is not None:
                    stop_event.set()
                for fut in future_to_idx:
                    fut.cancel()
                raise

        # Genforsøg fejlede chunks serielt med højere temperatur. RECITATION-
        # filteret er ikke-deterministisk og rammer sjældent samme chunk to
        # gange i træk — især ikke ved ændret sampling-temperatur.
        if failures:
            _status(
                f"{len(failures)} chunk(s) fejlede. Genforsøger serielt "
                f"(temperatur=0.5) ..."
            )
            still_failed: dict[int, Exception] = {}
            for idx in sorted(failures):
                path = chunk_paths[idx]
                label = f"{idx+1:02d}/{len(chunk_paths)}-retry"
                try:
                    text, diag = _gemini_transcribe_single(
                        client, path, system_instruction, model, label, _status,
                        stop_event=stop_event,
                        temperature=0.5,
                    )
                    text = _clamp_chunk_timestamps(text, chunk_seconds)
                    offset = idx * chunk_seconds
                    results[idx] = _offset_transcript(text, offset)
                    diags[idx] = diag
                except _StopRequested:
                    raise
                except Exception as e:
                    _status(f"[{label}] Genforsøg fejlede også: {e}")
                    still_failed[idx] = e
            failures = still_failed

        # Indsæt placeholder-linjer for chunks der ikke kunne reddes — bedre
        # at gemme delvis transkription end at miste alt.
        for idx in sorted(failures):
            start_sec = idx * chunk_seconds
            end_sec = min((idx + 1) * chunk_seconds, int(duration))
            results[idx] = (
                f"[{format_timestamp(start_sec)} - {format_timestamp(end_sec)}] "
                f"(MANGLER: chunk {idx+1}/{len(chunk_paths)} kunne ikke "
                f"transkriberes — {failures[idx]})"
            )

        total_elapsed = time.time() - t0
        ordered = [results[i] for i in sorted(results)]
        merged = _postprocess_transcript("\n".join(ordered))

        total_out = sum(
            (d.get("output_tokens") or 0) for d in diags.values()
        )
        n_ok = len(chunk_paths) - len(failures)
        summary_prefix = (
            f"Alle {len(chunk_paths)} chunks færdige"
            if not failures
            else f"{n_ok}/{len(chunk_paths)} chunks OK, "
                 f"{len(failures)} med placeholder"
        )
        _status(
            f"{summary_prefix}: "
            f"{merged.count(chr(10)) + 1} linjer, {len(merged)} tegn, "
            f"{total_elapsed:.1f}s total, ~{total_out} output-tokens."
        )
        return merged
    finally:
        shutil.rmtree(chunks_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 4. Referat og opgaver via Gemini API
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
Du er en erfaren mødesekretær for et {meeting_type_name} hos \
Eksempelgården – en dansk grøntsagsproducent med ca. 2.000 ha.

Deltagere i dette møde: {attendees}.
Sproget er dansk.

Du modtager en ordret transkription af et møde (med timestamps) og skal \
lave et referat. Hvordan referatet skal være, står i afsnittet om denne \
mødetype nedenfor.

---

## ABSOLUT REGEL OM PERSONNAVNE OG TILSKRIVNING

Transkriptionen indeholder **INGEN speaker-labels** – det er bare ord. \
Du kan derfor IKKE i almindelighed vide hvem der har sagt hvad.

**Tilskriv aldrig en konkret deltager en udtalelse, mening eller handling \
medmindre transkriptionen entydigt viser det.** Det er værre at citere \
forkert person end ikke at citere personen overhovedet.

**Hvad tæller som entydig tilskrivning** (kun disse tilfælde):
1. En anden tiltaler personen umiddelbart før udtalelsen \
   ("Lars, hvad siger du?" → den næste replik tilskrives Lars).
2. Personen introducerer sig selv eller refererer til sig selv i 1. person \
   ("Jeg, Anna, har snakket med ...", "Lars her –").
3. Konteksten gør tilskrivningen utvetydig (fx "som Anna lige fortalte" \
   sagt af tredjepart bekræfter at Anna talte tidligere).
4. Et navn med [?] efter er **ikke** entydigt – behandl det som tvivl.

**Hvis ikke entydigt:** brug i stedet
- *"en deltager"*, *"flere i gruppen"*, *"det blev rejst"*, *"der blev udtrykt"*,
- passiv form (*"det blev besluttet at ..."*),
- eller udelad personen helt og beskriv kun indholdet.

**Direkte citater i kursiv:** kun hvis personen entydigt kan tilskrives. \
Ellers parafraser uden navn.

**Opgaveliste pr. person**: medtag kun en opgave under et navn hvis det er \
klart at vedkommende selv har påtaget sig den, eller blev konkret tildelt den \
af andre. Hvis det er uklart hvem der står for opgaven, læg den under en \
**"Uafklaret ansvar"**-sektion i stedet.

---

## EMNEPRIORITERING (eksempel-organisationens vægtning — tilpas til din egen, brug ved pladskonflikt)

1. Arbejdsforhold og medarbejdertrivsel
2. Økonomi og effektivitet
3. Fødevaresikkerhed og sporbarhed (inkl. GLOBALG.A.P. / GRASP når relevant)
4. Digitalisering
5. Miljø og grundvand

---

## STEDER OG NAVNE DER ER KENDTE I ORGANISATIONEN

{vocabulary_section}

Hvis transkriptionen indeholder ord markeret med [?] (fx "Nord-...[?]"), \
**løs ikke gætningen i referatet** – skriv enten det generelle ("en mark", \
"en samarbejdsgård") eller noter eksplicit at navnet var uklart.

---

{meeting_type_section}
"""

USER_PROMPT = """\
Her er transkriptionen fra et {meeting_type_name} den {date}:

---
{transcript}
---

Generer venligst et komplet referat og opgaveliste.
"""


GEMINI_MINUTES_MODEL = "gemini-2.5-pro"


def generate_minutes(
    transcript: str,
    attendees: list[str],
    date: str,
    meeting_type: dict | None = None,
) -> str:
    """Genererer referat og opgaver via Gemini API.

    meeting_type: en (normaliseret) mødetype-dict. None → neutral standardtype
    (se neutral_meeting_type).
    """
    from google import genai
    from google.genai import types

    meeting_type = _normalize_meeting_type(meeting_type or {})

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY ikke sat. Indsæt din Gemini-nøgle i indstillingerne "
            "eller i .env."
        )

    model = os.environ.get("GEMINI_MINUTES_MODEL") or GEMINI_MINUTES_MODEL
    print(f"Genererer referat og opgaver med Gemini ({model}) ...", flush=True)

    client = genai.Client(api_key=api_key)

    attendees_str = ", ".join(attendees)
    vocab = load_vocabulary(CONFIG_DIR)
    system = SYSTEM_PROMPT.format(
        meeting_type_name=meeting_type["navn"],
        attendees=attendees_str,
        vocabulary_section=format_vocabulary_for_minutes(vocab),
        meeting_type_section=compose_type_section(meeting_type),
    )
    user_msg = USER_PROMPT.format(
        date=date, transcript=transcript, meeting_type_name=meeting_type["navn"]
    )

    response = client.models.generate_content(
        model=model,
        contents=[user_msg],
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.2,
            max_output_tokens=32000,
        ),
    )
    return response.text


# ---------------------------------------------------------------------------
# 5. Output
# ---------------------------------------------------------------------------

def save_output(
    output_dir: Path,
    date: str,
    transcript: str,
    minutes: str | None,
    name_base: str | None = None,
    meeting_type: dict | None = None,
    attendees: list[str] | None = None,
):
    """Gemmer transkription og (evt.) referat som Word-dokumenter med
    metadata-hoved. Fejler .docx-skrivning, falder vi tilbage til .md så
    intet indhold går tabt."""
    output_dir.mkdir(parents=True, exist_ok=True)
    base = name_base or date
    mt_name = meeting_type.get("navn") if isinstance(meeting_type, dict) else None
    att = attendees or []

    def _write(kind_title: str, filename_stem: str, body: str, is_transcript: bool):
        try:
            path = docx_export.write_meeting_docx(
                output_dir / f"{filename_stem}.docx",
                title=f"{kind_title} – {base}", date=date,
                meeting_type_name=mt_name, attendees=att,
                body=body, is_transcript=is_transcript)
        except Exception as e:  # nød-fallback: bevar indholdet som .md
            md = output_dir / f"{filename_stem}.md"
            md.write_text(f"# {kind_title} - {base}\n\nDato: {date}\n\n{body}\n",
                          encoding="utf-8")
            print(f"Advarsel: Word-eksport fejlede ({e}); gemte {md}", file=sys.stderr)
            return
        print(f"{kind_title} gemt: {path}", flush=True)

    _write("Transkription", f"Transkription {base}", transcript, True)
    if minutes:
        _write("Referat", f"Referat {base}", minutes, False)


# ---------------------------------------------------------------------------
# 5b. Transkribér eksisterende lydfil (upload-flow)
# ---------------------------------------------------------------------------

DEFAULT_HVISKE_MODEL = "syvai/faster-hviske-v3-conversation"


def transcribe_file(
    input_path: Path,
    output_dir: Path,
    date: str,
    name_base: str,
    attendees: list[str],
    engine: str = "hviske",
    make_minutes: bool = True,
    model_size: str = DEFAULT_HVISKE_MODEL,
    gemini_model: str = "gemini-2.5-pro",
    on_status=None,
    stop_event: threading.Event | None = None,
    meeting_type: dict | None = None,
) -> tuple[Path, str, str | None]:
    """Transkribér en eksisterende lydfil og gem transkription (+ evt. referat).

    Konverterer `input_path` (fx en iPhone Voice Memo .m4a) til WAV i
    `output_dir`, transkriberer med valgt motor ("hviske" lokalt eller
    "gemini" i skyen), genererer valgfrit referat og gemmer output.

    Returnerer (wav_path, transcript, minutes).
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    meeting_type_name = meeting_type["navn"] if meeting_type else "driftsledelsesmøde"

    wav_path = convert_to_wav(input_path, output_dir / f"{name_base}.wav")

    if engine == "gemini":
        transcript = transcribe_with_gemini(
            wav_path,
            attendees=attendees,
            model=gemini_model,
            on_status=on_status,
            stop_event=stop_event,
            meeting_type_name=meeting_type_name,
        )
    else:
        transcript = transcribe_audio(wav_path, model_size=model_size)

    minutes = None
    if make_minutes and transcript.strip():
        minutes = generate_minutes(transcript, attendees, date, meeting_type=meeting_type)

    save_output(
        output_dir=output_dir,
        date=date,
        transcript=transcript,
        minutes=minutes,
        name_base=name_base,
        meeting_type=meeting_type,
        attendees=attendees,
    )

    return wav_path, transcript, minutes


# ---------------------------------------------------------------------------
# 6. Main
# ---------------------------------------------------------------------------

DEFAULT_ATTENDEES = []


def main():
    parser = argparse.ArgumentParser(
        description="Moedvaerktoej - optag, transkriber, referat og opgaver"
    )
    parser.add_argument(
        "audio_file",
        type=Path,
        nargs="?",
        default=None,
        help="Sti til lydfilen (M4A, WAV, MP3, etc.)",
    )

    # Optagelse
    rec_group = parser.add_argument_group("Optagelse")
    rec_group.add_argument(
        "--record", "-r",
        action="store_true",
        help="Start optagelse fra mikrofon",
    )
    rec_group.add_argument(
        "--device", "-d",
        default="1",
        help="Lyd-enhed. macOS: index (default 1). Windows: device-navn. Brug --list-devices.",
    )
    rec_group.add_argument(
        "--list-devices",
        action="store_true",
        help="Vis tilgaengelige lyd-enheder og afslut",
    )
    rec_group.add_argument(
        "--live",
        action="store_true",
        help=(
            "Live-tilstand: transkriber parallelt mens optagelsen koerer "
            "(chunk-baseret). Ellers transkriberes foerst efter optagelse."
        ),
    )
    rec_group.add_argument(
        "--chunk-duration",
        type=int,
        default=300,
        help="Chunk-laengde i sekunder for --live (default: 300 = 5 min)",
    )
    rec_group.add_argument(
        "--system-audio",
        action="store_true",
        help="Optag ogsaa systemlyd (Meet/Teams/telefon) som separat spor via BlackHole.",
    )
    rec_group.add_argument(
        "--setup-audio",
        action="store_true",
        help="Opsaet systemlyd-routing (BlackHole + Multi-Output) og afslut.",
    )

    # Transkription
    trans_group = parser.add_argument_group("Transkription")
    trans_group.add_argument(
        "--engine",
        choices=["hviske", "gemini"],
        default="hviske",
        help=(
            "Transkriptions-motor: 'hviske' (lokalt, faster-whisper, default) "
            "eller 'gemini' (cloud, Gemini 2.5 Pro - bedst til navne og fagtermer). "
            "Gemini koerer ALTID efter optagelsen (ikke live)."
        ),
    )
    trans_group.add_argument(
        "--model",
        default=None,
        help=(
            "Model-ID. Default afhaenger af --engine: "
            "hviske=syvai/faster-hviske-v3-conversation, gemini=gemini-2.5-pro"
        ),
    )
    trans_group.add_argument(
        "--transcript-file",
        type=Path,
        default=None,
        help="Brug eksisterende transkription (spring transkription over)",
    )

    # Referat
    ref_group = parser.add_argument_group("Referat")
    ref_group.add_argument(
        "--skip-minutes",
        action="store_true",
        help="Spring referat-generering over (kun transkription)",
    )
    ref_group.add_argument(
        "--attendees",
        nargs="+",
        default=DEFAULT_ATTENDEES,
        help="Deltagere (default: Mads Lars Dorte)",
    )
    ref_group.add_argument(
        "--meeting-type",
        default="driftledelse",
        help="Mødetype-nøgle fra meeting_types.json (default: driftledelse)",
    )

    # Output
    out_group = parser.add_argument_group("Output")
    out_group.add_argument(
        "--date",
        default=None,
        help="Moedets dato (default: i dag eller udledt fra filnavn)",
    )
    out_group.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Mappe til output-filer (default: dato-mappe under moeder/)",
    )

    args = parser.parse_args()

    # Vis enheder
    if args.list_devices:
        print("\nTilgængelige lyd-enheder:")
        print("-" * 40)
        for dev_id, name in list_audio_devices():
            print(f"  [{dev_id}] {name}")
        print()
        sys.exit(0)

    # Opsaet systemlyd-routing
    if args.setup_audio:
        result = audio_routing.setup_system_output()
        if result.status == "ok":
            print(f"Systemlyd klar. BlackHole paa enhed [{result.system_device}].")
            sys.exit(0)
        print(result.guidance)
        sys.exit(1)

    # Valider input
    if not args.record and not args.audio_file and not args.transcript_file:
        parser.error("Angiv enten --record, en lydfil, eller --transcript-file")

    # Default model afhaengig af engine
    if args.model is None:
        args.model = (
            "gemini-2.5-pro"
            if args.engine == "gemini"
            else "syvai/faster-hviske-v3-conversation"
        )

    # Gemini + --live giver ikke mening: tving klassisk optag-foerst-flow
    if args.engine == "gemini" and args.record and args.live:
        print(
            "Bemaerk: --engine gemini koerer efter optagelsen. "
            "Ignorerer --live.",
            file=sys.stderr,
        )
        args.live = False

    # Mødetype: resolve med fallback. Nul mødetyper er en gyldig tilstand —
    # next(iter(...)) ville da rejse StopIteration, så vi falder tilbage til
    # den neutrale standardtype i stedet for at ramme et tomt dict.
    _types = load_meeting_types(CONFIG_DIR)
    _meeting_type = (
        _types.get(args.meeting_type) or _types.get("driftledelse")
        or (next(iter(_types.values())) if _types else neutral_meeting_type())
    )

    # Dato
    date = args.date or datetime.now().strftime("%d-%m-%Y")
    if not args.date and args.audio_file:
        parent_name = args.audio_file.resolve().parent.name
        parts = parent_name.split("-")
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            date = parent_name

    # Output-mappe
    if args.output_dir:
        output_dir = args.output_dir.resolve()
    elif args.audio_file:
        output_dir = args.audio_file.resolve().parent
    else:
        output_dir = MEETINGS_DIR / date

    print(f"\n{'='*60}")
    print(f"  Moedvaerktoej - Driftsledelsesmoede")
    print(f"  Dato: {date}")
    print(f"  Deltagere: {', '.join(args.attendees)}")
    if args.record:
        print(f"  Tilstand: Optagelse + transkription")
    print(f"  Output: {output_dir}")
    print(f"{'='*60}\n")

    # --- Trin 1: Faa lydkilden ---
    wav_path = None
    needs_cleanup = False

    if args.transcript_file:
        print(f"Bruger eksisterende transkription: {args.transcript_file}")
        transcript = args.transcript_file.read_text(encoding="utf-8")
    elif args.record and args.system_audio:
        routing = audio_routing.ensure_routing_active()
        if routing.status != "ok":
            print(routing.guidance)
            sys.exit(1)
        system_device = routing.system_device
        # systemlyd-optagelse bruger altid live-style dual-track; args.live er uden betydning her
        if args.engine == "gemini":
            wav_path, transcript = record_then_transcribe_gemini(
                output_dir=output_dir,
                date=date,
                device_id=args.device,
                system_device=system_device,
                attendees=args.attendees,
                model=args.model,
                meeting_type_name=_meeting_type["navn"],
            )
        else:
            wav_path, transcript = record_and_transcribe_live(
                output_dir=output_dir,
                date=date,
                device_id=args.device,
                model_size=args.model,
                system_device=system_device,
                chunk_duration=args.chunk_duration,
            )
    elif args.record and args.live:
        # Live-tilstand: optag + transkriber parallelt i chunks
        wav_path, transcript = record_and_transcribe_live(
            output_dir=output_dir,
            date=date,
            device_id=args.device,
            model_size=args.model,
            chunk_duration=args.chunk_duration,
        )
    else:
        if args.record:
            # Klassisk optag-foerst-transkriber-bagefter
            output_dir.mkdir(parents=True, exist_ok=True)
            recording_name = f"Driftledelsesmoede {date}.wav"
            wav_path = output_dir / recording_name
            record_meeting(wav_path, device_id=args.device)
        else:
            # Konverter eksisterende fil til WAV
            audio_file = args.audio_file.resolve()
            if not audio_file.exists():
                print(f"Fejl: Filen '{audio_file}' findes ikke.")
                sys.exit(1)

            if audio_file.suffix.lower() == ".wav":
                wav_path = audio_file
            else:
                tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                wav_path = Path(tmp.name)
                tmp.close()
                needs_cleanup = True
                convert_to_wav(audio_file, wav_path)

        # --- Trin 2: Transkriber ---
        try:
            if args.engine == "gemini":
                transcript = transcribe_with_gemini(
                    wav_path,
                    attendees=args.attendees,
                    model=args.model,
                    meeting_type_name=_meeting_type["navn"],
                )
            else:
                transcript = transcribe_audio(wav_path, model_size=args.model)
        finally:
            if needs_cleanup and wav_path:
                wav_path.unlink(missing_ok=True)

    # --- Trin 3: Referat ---
    minutes = None
    if not args.skip_minutes:
        try:
            minutes = generate_minutes(transcript, args.attendees, date, meeting_type=_meeting_type)
        except Exception as e:
            print(f"\nAdvarsel: Kunne ikke generere referat: {e}")
            print("Transkriptionen gemmes stadig.")

    # --- Trin 4: Gem ---
    save_output(output_dir, date, transcript, minutes,
                meeting_type=_meeting_type, attendees=args.attendees)

    print(f"\nFaerdig!")


# ---------------------------------------------------------------------------
# Mødetyper
# ---------------------------------------------------------------------------

DETALJENIVEAUER = ("kortfattet", "balanceret", "grundig")

MEETING_TYPE_DEFAULTS = {
    "navn": "Møde",
    "detaljeniveau": "balanceret",
    "citater": False,
    "opgaveliste": True,
    "fokus": "",
    "ekstra_instruktioner": "",
    "deltagere": [],
}


def _normalize_meeting_type(raw: dict) -> dict:
    """Returnerer en fuld, gyldig mødetype. Manglende felter fyldes med
    defaults; ugyldigt detaljeniveau → 'balanceret'; bools tvinges; deltagere
    filtreres for ikke-strings og tomme strings. Brækker aldrig prompten."""
    if not isinstance(raw, dict):
        raw = {}
    out = dict(MEETING_TYPE_DEFAULTS)
    out["deltagere"] = []

    navn = raw.get("navn", MEETING_TYPE_DEFAULTS["navn"])
    out["navn"] = navn.strip() if isinstance(navn, str) and navn.strip() else MEETING_TYPE_DEFAULTS["navn"]

    niveau = raw.get("detaljeniveau")
    out["detaljeniveau"] = niveau if niveau in DETALJENIVEAUER else "balanceret"

    out["citater"] = bool(raw.get("citater", MEETING_TYPE_DEFAULTS["citater"]))
    out["opgaveliste"] = bool(raw.get("opgaveliste", MEETING_TYPE_DEFAULTS["opgaveliste"]))

    fokus = raw.get("fokus", "")
    out["fokus"] = fokus.strip() if isinstance(fokus, str) else ""

    ekstra = raw.get("ekstra_instruktioner", "")
    out["ekstra_instruktioner"] = ekstra.strip() if isinstance(ekstra, str) else ""

    deltagere = raw.get("deltagere", [])
    if isinstance(deltagere, list):
        for d in deltagere:
            if isinstance(d, str) and d.strip():
                out["deltagere"].append(d.strip())

    return out


def _normalize_meeting_types(raw: dict) -> dict:
    """Normaliserer alle typer. Ikke-dict → {}; tom dict bevares tom
    (nul mødetyper er en gyldig tilstand)."""
    if not isinstance(raw, dict):
        return {}
    return {str(k): _normalize_meeting_type(v) for k, v in raw.items()}


def neutral_meeting_type() -> dict:
    """En gyldig, ikke-gemt standardtype til brug når ingen er valgt
    (navn 'Møde', balanceret niveau). Vises ikke og persisteres ikke."""
    return _normalize_meeting_type({})


# De to oprindelige seed-typer, som gamle installationer stadig har liggende.
# Bruges KUN til engangs-oprydning så de urørte danske eksempler kan fjernes.
_SEED_MEETING_TYPES = {
    "driftledelse": {
        "navn": "Driftledelsesmøde", "detaljeniveau": "kortfattet",
        "citater": False, "opgaveliste": True,
        "fokus": "Konkrete opgaver, aftaler og beslutninger for den daglige drift.",
        "ekstra_instruktioner": "", "deltagere": [],
    },
    "ledergruppe": {
        "navn": "Ledergruppemøde", "detaljeniveau": "grundig",
        "citater": True, "opgaveliste": False,
        "fokus": "Strategi, langsigtet retning og principielle drøftelser.",
        "ekstra_instruktioner": "Skriv i sammenhængende prosa. Bevar nuancer og uenigheder.",
        "deltagere": [],
    },
}


def cleanup_seed_meeting_types(tool_dir: Path) -> None:
    """Engangs-oprydning: hvis meeting_types.json er PRÆCIS de to urørte
    seed-typer, tømmes den ({}). Har brugeren redigeret/tilføjet noget, røres
    filen ikke. Efter tømning er betingelsen aldrig sand igen."""
    f = tool_dir / "meeting_types.json"
    if not f.exists():
        return
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if _normalize_meeting_types(raw) == _normalize_meeting_types(_SEED_MEETING_TYPES):
        try:
            f.write_text("{}", encoding="utf-8")
        except OSError as e:
            print(f"Kunne ikke rydde seed-mødetyper: {e}", file=sys.stderr)


def load_meeting_types(tool_dir: Path) -> dict:
    """Indlæser meeting_types.json fra tool_dir.

    Mangler filen → seedes fra meeting_types.default.json (hvis den findes og
    har indhold), ellers oprettes en tom fil. Ugyldig JSON → {}. Hver type
    normaliseres så håndredigeret JSON aldrig brækker prompten. Nul mødetyper
    er en gyldig tilstand; kan derfor returnere {}.
    """
    types_file = tool_dir / "meeting_types.json"
    default_file = tool_dir / "meeting_types.default.json"

    if not types_file.exists():
        seed_raw = None
        if default_file.exists():
            try:
                seed_raw = json.loads(default_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                print(f"Kunne ikke læse meeting_types.default.json: {e}", file=sys.stderr)
        normalized = _normalize_meeting_types(seed_raw if isinstance(seed_raw, dict) else {})
        try:
            types_file.write_text(
                json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as e:
            print(f"Kunne ikke seede meeting_types.json: {e}", file=sys.stderr)
        return normalized

    try:
        raw = json.loads(types_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(
            f"Kunne ikke læse meeting_types.json: {e}. Bruger fallback.",
            file=sys.stderr,
        )
        return _normalize_meeting_types({})

    return _normalize_meeting_types(raw)


_DETALJE_PROSA = {
    "kortfattet": (
        "Hold referatet kort og fokuseret: kernebeslutninger, aftaler og "
        "vigtige pointer — ikke hver detalje. Brug gerne korte afsnit."
    ),
    "balanceret": (
        "Skriv et afbalanceret referat: dæk de væsentlige emner med nok "
        "kontekst til at en fraværende kan følge med, uden at gengive alt."
    ),
    "grundig": (
        "Skriv et fyldigt referat i sammenhængende prosa, der bevarer nuancer, "
        "overvejelser og uenigheder. Fang detaljen, ikke kun overskrifterne."
    ),
}


def compose_type_section(meeting_type: dict) -> str:
    """Bygger mødetype-sektionen til SYSTEM_PROMPT ud fra typens knapper.
    Forventer en allerede normaliseret type (se _normalize_meeting_type)."""
    t = _normalize_meeting_type(meeting_type)
    parts: list[str] = ["## SÅDAN SKAL DETTE REFERAT VÆRE", ""]

    parts.append(_DETALJE_PROSA[t["detaljeniveau"]])
    parts.append("")

    if t["citater"]:
        parts.append(
            "Du må gerne tage korte ordrette citater i *kursiv* — men KUN når "
            "tilskrivningen er entydig jf. reglen ovenfor."
        )
    else:
        parts.append("Brug ikke ordrette citater; parafraser i stedet.")
    parts.append("")

    if t["fokus"]:
        parts.append(f"Læg særlig vægt på: {t['fokus']}")
        parts.append("")

    parts.append("## STRUKTUR")
    parts.append("")
    parts.append("### 1. Referat")
    parts.append(
        "Strukturér efter tema (ikke kronologisk). Start med det vigtigste for "
        "driften. Skeln mellem afklarede beslutninger, tentative planer og "
        "uafklarede spørgsmål."
    )
    parts.append("")

    section_no = 2
    if t["opgaveliste"]:
        parts.append(f"### {section_no}. Opgaveliste")
        parts.append(
            "Grupperet pr. person, men kun hvor ansvar er entydigt. For hver "
            "opgave: hvad der skal gøres (med kontekst), deadline hvis nævnt, "
            "og prioritet (høj/middel/lav). Opgaver med uklart ansvar samles "
            "under **Uafklaret ansvar**."
        )
        parts.append("")
        section_no += 1

    parts.append(f"### {section_no}. Uafklarede punkter")
    parts.append(
        "Ting der blev rejst men ikke lukket — hvem der bør tage den videre, "
        "hvis det fremgår entydigt."
    )

    if t["ekstra_instruktioner"]:
        parts.append("")
        parts.append("## EKSTRA INSTRUKTIONER")
        parts.append("")
        parts.append(t["ekstra_instruktioner"])

    parts.append("")
    parts.append("Formatér output som markdown.")
    return "\n".join(parts)


def resolve_type_attendees(
    type_key: str,
    meeting_type: dict,
    attendees_by_type: dict,
    global_default: list[str],
) -> list[str]:
    """Vælger deltager-liste ved type-valg: husket (state) → type.deltagere →
    globale default. Tomme lister springes over."""
    remembered = attendees_by_type.get(type_key) if isinstance(attendees_by_type, dict) else None
    if isinstance(remembered, list) and remembered:
        return list(remembered)
    type_default = meeting_type.get("deltagere") if isinstance(meeting_type, dict) else None
    if isinstance(type_default, list) and type_default:
        return list(type_default)
    return list(global_default)


# ---------------------------------------------------------------------------
# 5. Vocabulary (ordliste til prompt-injektion)
# ---------------------------------------------------------------------------

VOCAB_CATEGORIES = ("personer", "steder", "fagtermer")


def _empty_vocab() -> dict[str, list[str]]:
    return {cat: [] for cat in VOCAB_CATEGORIES}


def _normalize_vocab(raw: dict) -> dict[str, list[str]]:
    """Returnerer altid en dict med alle tre kategorier; ikke-strings og
    tomme strings filtreres væk; entries strippes for whitespace."""
    out = _empty_vocab()
    for cat in VOCAB_CATEGORIES:
        items = raw.get(cat, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, str):
                continue
            stripped = item.strip()
            if stripped:
                out[cat].append(stripped)
    return out


def load_vocabulary(tool_dir: Path) -> dict[str, list[str]]:
    """Indlæser vocabulary.json fra tool_dir.

    Hvis filen ikke findes, seedes den fra vocabulary.default.json (hvis den
    findes) og det seedede indhold returneres. Hvis hverken vocabulary.json
    eller default-filen findes, returneres en tom vocab.

    Ugyldig JSON logges til stderr og giver tom vocab returneret.
    """
    vocab_file = tool_dir / "vocabulary.json"
    default_file = tool_dir / "vocabulary.default.json"

    if not vocab_file.exists():
        if default_file.exists():
            try:
                raw = json.loads(default_file.read_text(encoding="utf-8"))
                normalized = _normalize_vocab(raw)
                vocab_file.write_text(
                    json.dumps(normalized, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                return normalized
            except (OSError, json.JSONDecodeError) as e:
                print(
                    f"Kunne ikke seede vocabulary.json fra default: {e}",
                    file=sys.stderr,
                )
                return _empty_vocab()
        return _empty_vocab()

    try:
        raw = json.loads(vocab_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(
            f"Kunne ikke læse vocabulary.json: {e}. "
            f"Bruger tom vocabulary - filen overskrives ikke automatisk.",
            file=sys.stderr,
        )
        return _empty_vocab()

    if not isinstance(raw, dict):
        print(
            f"vocabulary.json er ikke en dict (fik {type(raw).__name__}). "
            f"Bruger tom vocabulary.",
            file=sys.stderr,
        )
        return _empty_vocab()

    return _normalize_vocab(raw)


_CAT_HEADERS = {
    "personer": "**Personer:**",
    "steder": "**Steder:**",
    "fagtermer": "**Fagtermer:**",
}


def format_vocabulary_for_minutes(vocab: dict[str, list[str]]) -> str:
    """Formaterer vocab som markdown-sektion til referat-SYSTEM_PROMPT.

    Tomme kategorier udelades. Hvis alle tre er tomme returneres en
    placeholder-linje så prompten ikke får et hul.
    """
    lines: list[str] = []
    has_any = any(vocab.get(cat) for cat in VOCAB_CATEGORIES)
    if not has_any:
        return "(ingen ordliste konfigureret)"

    lines.append(
        "Behold disse stavemåder konsekvent. Hvor en note er angivet "
        "(efter en tankestreg), er den ofte vigtig — fx fordi to navne "
        "kan forveksles, eller fordi en bestemt stavning er korrekt."
    )
    lines.append("")
    for cat in VOCAB_CATEGORIES:
        items = vocab.get(cat, [])
        if not items:
            continue
        lines.append(_CAT_HEADERS[cat])
        for item in items:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines).rstrip()


def format_vocabulary_for_gemini(vocab: dict[str, list[str]]) -> str:
    """Formaterer vocab som markdown-sektion til Gemini SYSTEM_INSTRUCTION.

    Formatet matcher referat-versionen i struktur, men med en
    transkriptions-orienteret indledning (bias akustisk genkendelse).
    """
    has_any = any(vocab.get(cat) for cat in VOCAB_CATEGORIES)
    if not has_any:
        return "(ingen ordliste konfigureret)"

    lines: list[str] = [
        "Brug disse stavemåder konsekvent når du genkender de tilsvarende "
        "ord i lyden. Hvor en note er angivet (efter en tankestreg), "
        "er den ofte vigtig — fx fordi to navne kan forveksles akustisk.",
        "",
    ]
    for cat in VOCAB_CATEGORIES:
        items = vocab.get(cat, [])
        if not items:
            continue
        lines.append(_CAT_HEADERS[cat])
        for item in items:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines).rstrip()


HVISKE_INITIAL_PROMPT_MAX_CHARS = 800


# Separator mellem ord og forklaring i ordliste-poster: " — " (em-tankestreg).
_VOCAB_SEP = " — "


def _term_only(entry: str) -> str:
    """Returnerer term-delen af en entry — alt før første ' — ' eller ' ('."""
    for sep in (_VOCAB_SEP, " ("):
        idx = entry.find(sep)
        if idx > 0:
            return entry[:idx].strip()
    return entry.strip()


def split_vocab_entry(entry: str) -> tuple[str, str]:
    """Deler en ordliste-streng i (ord, forklaring) på første _VOCAB_SEP.
    Mangler separatoren, er hele strengen ordet og forklaring er tom."""
    idx = entry.find(_VOCAB_SEP)
    if idx < 0:
        return entry.strip(), ""
    return entry[:idx].strip(), entry[idx + len(_VOCAB_SEP):].strip()


def join_vocab_entry(ord_text: str, forklaring: str) -> str:
    """Samler (ord, forklaring) til lagringsstreng. Tomt ord → tom streng
    (posten skal filtreres væk). Tom forklaring → kun ordet."""
    ord_text = ord_text.strip()
    forklaring = forklaring.strip()
    if not ord_text:
        return ""
    return f"{ord_text}{_VOCAB_SEP}{forklaring}" if forklaring else ord_text


def format_vocabulary_for_hviske(
    vocab: dict[str, list[str]],
    max_chars: int = HVISKE_INITIAL_PROMPT_MAX_CHARS,
) -> str:
    """Bygger en kompakt initial_prompt til faster-whisper.

    Strippes til kun term-delen pr. entry. Personer kommer før steder.
    Fagtermer udelades helt (lavere fejlrate i Hviske end navne).
    Hvis output overskrider max_chars, trunkeres de sidst-tilføjede
    entries indtil under grænsen.
    """
    persons = [t for t in (_term_only(e) for e in vocab.get("personer", [])) if t]
    places = [t for t in (_term_only(e) for e in vocab.get("steder", [])) if t]

    if not persons and not places:
        return ""

    def build(p_list: list[str], s_list: list[str]) -> str:
        parts: list[str] = []
        if p_list:
            parts.append("Egennavne: " + ", ".join(p_list) + ".")
        if s_list:
            parts.append("Steder: " + ", ".join(s_list) + ".")
        return " ".join(parts)

    out = build(persons, places)
    # Trunker mens vi er over grænsen — start med at fjerne sidste sted,
    # derefter sidste person.
    while len(out) > max_chars and (places or persons):
        if places:
            places.pop()
        elif persons:
            persons.pop()
        out = build(persons, places)
    return out


if __name__ == "__main__":
    main()
