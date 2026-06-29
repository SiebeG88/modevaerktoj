#!/opt/local/bin/python3.12
"""
Mødeværktøj - GUI (CustomTkinter)
Moderne Apple Liquid Glass-inspireret design til optagelse og
live-transkription af driftsledelsesmøder.

Flow:
  1. Vælg mappe
  2. Navngiv mødet
  3. Tryk "Start" - ffmpeg optager + Hviske-v3 transkriberer løbende
  4. Tryk "Stop" - sidste chunks færdiggøres, alt gemmes

Brug:
    python meeting_app.py
"""

from __future__ import annotations

import json
import math
import os
import queue
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

# Importer fra meeting_tool.py i samme mappe
TOOL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOL_DIR))
import meeting_tool  # noqa: E402
import audio_routing  # noqa: E402

DEFAULT_ATTENDEES = ["Mads", "Lars", "Dorte"]
DEFAULT_MODEL = "syvai/faster-hviske-v3-conversation"
DEFAULT_GEMINI_MODEL = "gemini-2.5-pro"
DEFAULT_ENGINE = "hviske"  # "hviske" (live, lokalt) eller "gemini" (efter, cloud)
DEFAULT_CHUNK_DURATION = 900  # 15 min — fast chunk-længde for Hviske live (auto)

# Bruger-config genbruger meeting_tool.CONFIG_DIR (%APPDATA% på Windows).
CONFIG_DIR = meeting_tool.CONFIG_DIR
ENV_FILE = CONFIG_DIR / ".env"


def load_env_file():
    """Laeser KEY=value-linjer fra .env og saetter dem i os.environ.
    Eksisterende miljoevariabler overskrives IKKE (setdefault).
    """
    if not ENV_FILE.exists():
        return
    try:
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)
    except Exception as e:
        print(f"Kunne ikke laese {ENV_FILE}: {e}", file=sys.stderr)


load_env_file()


def _needs_setup() -> bool:
    """True hvis ingen Gemini-nøgle er sat — så viser vi første-start-dialogen.
    Hviske (lokal transkription) virker uden nøgle, så dialogen kan springes over."""
    return not (os.environ.get("GEMINI_API_KEY")
                or os.environ.get("GOOGLE_API_KEY"))


def _write_env(values: dict[str, str]) -> None:
    """Flet ikke-tomme KEY=value ind i ENV_FILE og opdatér os.environ.

    Læser en evt. eksisterende .env først og bevarer andre nøgler (fx
    MEETINGS_DIR) — første-start-dialogen må ikke slette dem."""
    existing: dict[str, str] = {}
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                existing[k.strip()] = v.strip()
    existing.update({k: v for k, v in values.items() if v})
    lines = [f"{k}={v}" for k, v in existing.items()]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for k, v in values.items():
        if v:
            os.environ[k] = v


# Persistent UI-state (sidste valgte mappe, chunk-laengde m.v.)
STATE_FILE = CONFIG_DIR / "state.json"


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict):
    try:
        STATE_FILE.write_text(
            json.dumps(state, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"Kunne ikke gemme state: {e}", file=sys.stderr)


# Mappe hvor mode-outputs gemmes (kan overstyres via MEETINGS_DIR i .env)
_DEFAULT_MEETINGS_DIR = meeting_tool.default_meetings_dir()
MEETINGS_DIR = Path(
    os.environ.get("MEETINGS_DIR", str(_DEFAULT_MEETINGS_DIR))
)


# ---------------------------------------------------------------------------
# Colour palette -- Liquid Glass aesthetic
# ---------------------------------------------------------------------------
# These are used throughout for a consistent frosted-glass look.
_CLR = {
    "bg":            "#e8eef6",   # window background - slightly deeper for glass contrast
    "card":          "#f8fafd",   # card surface - near-white, feels translucent
    "card_border":   "#c8d5e4",   # slightly more visible border for glass edge
    "accent":        "#3b82f6",   # blue accent (buttons, highlights)
    "accent_hover":  "#2563eb",
    "text":          "#1e293b",   # primary text
    "text_secondary":"#334155",   # secondary/muted text (slate-700 — mørkere for bedre kontrast)
    "text_placeholder":"#475569", # placeholder i entries — lysere end labels, stadig læsbar
    "rec_idle":      "#ef4444",   # red dot when idle
    "rec_active":    "#dc2626",   # deeper red when recording
    "rec_ring":      "#fecaca",   # light red ring around button
    "stop_blue":     "#3b82f6",   # blue for stop state
    "stop_hover":    "#2563eb",
    "success":       "#22c55e",
}


class HeroButton(ctk.CTkCanvas):
    """Large circular record/stop button drawn on a canvas for full
    visual control -- supports pulsing animation while recording."""

    SIZE = 140  # diameter
    _PULSE_MS = 40  # ms between animation frames

    def __init__(self, master, command=None, **kwargs):
        super().__init__(
            master,
            width=self.SIZE + 20,
            height=self.SIZE + 20,
            highlightthickness=0,
            bd=0,
            **kwargs,
        )
        self._command = command
        self._is_recording = False
        self._is_transcribing = False
        self._pulse_phase = 0.0
        self._pulse_after_id: str | None = None
        self._spin_angle = 0.0
        self._spin_after_id: str | None = None

        # Draw initial idle state
        self._draw_idle()

        self.bind("<Button-1>", self._on_click)
        # Cursor hand when hovering
        self.bind("<Enter>", lambda e: self.configure(cursor="hand2"))
        self.bind("<Leave>", lambda e: self.configure(cursor=""))

    def _on_click(self, _event=None):
        if self._command:
            self._command()

    # -- Drawing helpers ---------------------------------------------------

    def _clear(self):
        self.delete("all")

    def _draw_idle(self):
        self._clear()
        cx, cy = (self.SIZE + 20) / 2, (self.SIZE + 20) / 2
        r = self.SIZE / 2

        # Outer soft shadow ring
        self._oval(cx, cy, r + 4, fill="#e2e8f0", outline="")
        # Main circle -- white with subtle border
        self._oval(cx, cy, r, fill="#ffffff", outline=_CLR["card_border"], width=1.5)
        # Red dot in centre
        self._oval(cx, cy - 8, 18, fill=_CLR["rec_idle"], outline="")
        # "Start" label
        self.create_text(
            cx, cy + 20,
            text="Start",
            font=("SF Pro Display", 17, "bold"),
            fill=_CLR["text"],
        )

    def _draw_recording(self, pulse: float = 0.0):
        """Draw the recording state.  *pulse* is 0..1 for animation."""
        self._clear()
        cx, cy = (self.SIZE + 20) / 2, (self.SIZE + 20) / 2
        r = self.SIZE / 2

        # Pulsing outer ring (scale + opacity via colour lerp)
        ring_extra = 6 + 6 * pulse
        ring_alpha = int(80 + 100 * (1 - pulse))
        ring_colour = self._alpha_red(ring_alpha)
        self._oval(cx, cy, r + ring_extra, fill=ring_colour, outline="")

        # Main red circle
        self._oval(cx, cy, r, fill=_CLR["rec_active"], outline="#b91c1c", width=1)

        # White stop square
        sq = 20
        self.create_rectangle(
            cx - sq, cy - sq - 4, cx + sq, cy + sq - 4,
            fill="#ffffff", outline="", width=0,
        )
        # "Stop" label
        self.create_text(
            cx, cy + 28,
            text="Stop",
            font=("SF Pro Display", 16, "bold"),
            fill="#ffffff",
        )

    def _oval(self, cx, cy, r, **kw):
        self.create_oval(cx - r, cy - r, cx + r, cy + r, **kw)

    @staticmethod
    def _alpha_red(a: int) -> str:
        """Return a reddish colour with perceived transparency (against
        the card background white)."""
        # blend red (239,68,68) towards white by (255-a)/255
        t = a / 255
        rr = int(239 * t + 255 * (1 - t))
        gg = int(68 * t + 255 * (1 - t))
        bb = int(68 * t + 255 * (1 - t))
        return f"#{rr:02x}{gg:02x}{bb:02x}"

    # -- Public API --------------------------------------------------------

    def set_recording(self, recording: bool):
        self._is_recording = recording
        self._is_transcribing = False
        self._stop_spin()
        if recording:
            self._pulse_phase = 0.0
            self._draw_recording(0.0)
            self._start_pulse()
        else:
            self._stop_pulse()
            self._draw_idle()

    def set_transcribing(self):
        """Vis transkriberer-tilstand: roterende spinner + tekst."""
        self._is_recording = False
        self._is_transcribing = True
        self._stop_pulse()
        self._spin_angle = 0.0
        self._animate_spinner()

    def set_idle(self):
        self._is_recording = False
        self._is_transcribing = False
        self._stop_pulse()
        self._stop_spin()
        self._draw_idle()

    def _stop_spin(self):
        if self._spin_after_id is not None:
            self.after_cancel(self._spin_after_id)
            self._spin_after_id = None

    def _animate_spinner(self):
        if not self._is_transcribing:
            return
        self._spin_angle = (self._spin_angle - 12) % 360
        self._draw_transcribing(self._spin_angle)
        self._spin_after_id = self.after(self._PULSE_MS, self._animate_spinner)

    def _draw_transcribing(self, angle: float):
        self._clear()
        cx, cy = (self.SIZE + 20) / 2, (self.SIZE + 20) / 2
        r = self.SIZE / 2

        # Outer soft shadow ring + hvid hovedcirkel (som idle)
        self._oval(cx, cy, r + 4, fill="#e2e8f0", outline="")
        self._oval(cx, cy, r, fill="#ffffff", outline=_CLR["card_border"], width=1.5)

        # Roterende spinner-bue
        sr = 34
        self.create_arc(
            cx - sr, cy - sr - 8, cx + sr, cy + sr - 8,
            start=angle, extent=270,
            style="arc", outline=_CLR["accent"], width=5,
        )
        # Tekst
        self.create_text(
            cx, cy + 30,
            text="Transkriberer …",
            font=("SF Pro Display", 13, "bold"),
            fill=_CLR["text"],
        )

    def _start_pulse(self):
        self._stop_pulse()
        self._animate_pulse()

    def _stop_pulse(self):
        if self._pulse_after_id is not None:
            self.after_cancel(self._pulse_after_id)
            self._pulse_after_id = None

    def _animate_pulse(self):
        if not self._is_recording:
            return
        self._pulse_phase += 0.04
        if self._pulse_phase > 1.0:
            self._pulse_phase = 0.0
        # Smooth sine pulse
        val = (math.sin(self._pulse_phase * 2 * math.pi) + 1) / 2
        self._draw_recording(val)
        self._pulse_after_id = self.after(self._PULSE_MS, self._animate_pulse)


class MeetingApp:
    def __init__(self, root: ctk.CTk):
        self.root = root
        self.root.title("Modevarktoej")
        self.root.geometry("800x720")

        # State
        self.recording = False
        self.transcribing = False
        self._timer_running = False
        self.worker_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.start_time: float | None = None
        self.ui_queue: queue.Queue = queue.Queue()
        self.chunks_done = 0

        # Settings card collapsed state
        self._settings_visible = True

        self._build_ui()
        self._refresh_devices()
        self._set_default_folder()

        # Centre window on screen
        self.root.update_idletasks()
        self._centre_window()

        # Poll UI queue fra baggrundstråde
        self.root.after(100, self._drain_ui_queue)

        # Timer-loop
        self.root.after(500, self._tick_timer)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Centre on screen
    # ------------------------------------------------------------------

    def _centre_window(self):
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 3   # slightly above centre feels more natural
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Mødetype-state (indlæses én gang ved opstart)
        self._meeting_types = meeting_tool.load_meeting_types(CONFIG_DIR)
        _state = load_state()
        self._type_key = _state.get("meeting_type")
        if self._type_key not in self._meeting_types:
            self._type_key = next(iter(self._meeting_types))
        self._type_keys = list(self._meeting_types)

        # Configure the window background
        self.root.configure(fg_color=_CLR["bg"])

        # Tabview på topniveau: "Optag" + "Ordliste"
        self._tabview = ctk.CTkTabview(
            self.root,
            fg_color=_CLR["bg"],
            segmented_button_fg_color=_CLR["card"],
            segmented_button_selected_color=_CLR["accent"],
            segmented_button_selected_hover_color=_CLR["accent_hover"],
            segmented_button_unselected_color=_CLR["card"],
            segmented_button_unselected_hover_color=_CLR["card_border"],
            text_color=_CLR["text"],
        )
        self._tabview.pack(fill="both", expand=True, padx=0, pady=0)
        self._tabview.add("Optag")
        self._tabview.add("Transkribér fil")
        self._tabview.add("Ordliste")
        self._tabview.add("Mødetyper")

        # Optag-fanen: behold eksisterende scrollable-wrapper
        self._outer = ctk.CTkScrollableFrame(
            self._tabview.tab("Optag"),
            fg_color=_CLR["bg"],
            scrollbar_button_color=_CLR["card_border"],
            scrollbar_button_hover_color=_CLR["accent"],
        )
        self._outer.pack(fill="both", expand=True, padx=0, pady=0)

        # ── Header ──────────────────────────────────────────────────
        header = ctk.CTkFrame(self._outer, fg_color="transparent")
        header.pack(fill="x", padx=28, pady=(24, 4))

        ctk.CTkLabel(
            header,
            text="Mødeværktøj",
            font=ctk.CTkFont(family="SF Pro Display", size=32, weight="bold"),
            text_color=_CLR["text"],
        ).pack(anchor="w")

        ctk.CTkLabel(
            header,
            text="Optag  ·  Transkriber  ·  Referat",
            font=ctk.CTkFont(family="SF Pro Text", size=14),
            text_color=_CLR["text_secondary"],
        ).pack(anchor="w", pady=(2, 0))

        # ── Settings card ───────────────────────────────────────────
        self._settings_card = ctk.CTkFrame(
            self._outer,
            fg_color=_CLR["card"],
            corner_radius=16,
            border_width=1,
            border_color=_CLR["card_border"],
        )
        self._settings_card.pack(fill="x", padx=24, pady=(16, 0))

        # Card header with collapse toggle
        card_header = ctk.CTkFrame(self._settings_card, fg_color="transparent")
        card_header.pack(fill="x", padx=20, pady=(16, 4))

        ctk.CTkLabel(
            card_header,
            text="Indstillinger",
            font=ctk.CTkFont(family="SF Pro Display", size=16, weight="bold"),
            text_color=_CLR["text"],
        ).pack(side="left")

        self._toggle_btn = ctk.CTkButton(
            card_header,
            text="Skjul",
            width=60,
            height=28,
            corner_radius=8,
            font=ctk.CTkFont(size=12),
            fg_color="transparent",
            text_color=_CLR["accent"],
            hover_color="#e0e7ff",
            command=self._toggle_settings,
        )
        self._toggle_btn.pack(side="right")

        # Inner frame for settings fields (what gets hidden/shown)
        self._settings_inner = ctk.CTkFrame(self._settings_card, fg_color="transparent")
        self._settings_inner.pack(fill="x", padx=20, pady=(4, 16))

        # -- Parent folder
        self._field_label(self._settings_inner, "Overordnet mappe")
        folder_row = ctk.CTkFrame(self._settings_inner, fg_color="transparent")
        folder_row.pack(fill="x", pady=(0, 10))

        self.folder_var = ctk.StringVar()
        self._folder_entry = ctk.CTkEntry(
            folder_row,
            textvariable=self.folder_var,
            height=36,
            corner_radius=10,
            border_width=1,
            border_color=_CLR["card_border"],
            fg_color="#f8fafc",
            text_color=_CLR["text"],
            placeholder_text_color=_CLR["text_placeholder"],
            font=ctk.CTkFont(size=13),
        )
        self._folder_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        ctk.CTkButton(
            folder_row,
            text="Gennemse ...",
            width=110,
            height=36,
            corner_radius=10,
            fg_color=_CLR["accent"],
            hover_color=_CLR["accent_hover"],
            font=ctk.CTkFont(size=13),
            command=self._pick_folder,
        ).pack(side="right")

        # -- Mødetype dropdown
        self._field_label(self._settings_inner, "Mødetype")
        _type_labels = [self._meeting_types[k]["navn"] for k in self._type_keys]
        self.type_var = ctk.StringVar(value=self._meeting_types[self._type_key]["navn"])
        self.type_combo = ctk.CTkComboBox(
            self._settings_inner,
            variable=self.type_var,
            values=_type_labels,
            height=36,
            corner_radius=10,
            border_width=1,
            border_color=_CLR["card_border"],
            state="readonly",
            fg_color="#f8fafc",
            button_color=_CLR["accent"],
            button_hover_color=_CLR["accent_hover"],
            text_color=_CLR["text"],
            dropdown_fg_color=_CLR["card"],
            dropdown_hover_color="#e0e7ff",
            font=ctk.CTkFont(size=13),
            command=self._on_type_selected,
        )
        self.type_combo.pack(fill="x", pady=(0, 10))

        # -- Meeting name
        self._field_label(self._settings_inner, "Mødenavn")
        self.name_var = ctk.StringVar()
        ctk.CTkEntry(
            self._settings_inner,
            textvariable=self.name_var,
            height=36,
            corner_radius=10,
            border_width=1,
            border_color=_CLR["card_border"],
            fg_color="#f8fafc",
            text_color=_CLR["text"],
            placeholder_text_color=_CLR["text_placeholder"],
            font=ctk.CTkFont(size=13),
        ).pack(fill="x", pady=(0, 10))

        # -- Date + Participants side by side
        row2 = ctk.CTkFrame(self._settings_inner, fg_color="transparent")
        row2.pack(fill="x", pady=(0, 10))
        row2.columnconfigure(0, weight=1)
        row2.columnconfigure(1, weight=2)

        # Date
        date_frame = ctk.CTkFrame(row2, fg_color="transparent")
        date_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self._field_label(date_frame, "Dato")
        self.date_var = ctk.StringVar(value=datetime.now().strftime("%d-%m-%Y"))
        ctk.CTkEntry(
            date_frame,
            textvariable=self.date_var,
            height=36,
            corner_radius=10,
            border_width=1,
            border_color=_CLR["card_border"],
            fg_color="#f8fafc",
            text_color=_CLR["text"],
            placeholder_text_color=_CLR["text_placeholder"],
            font=ctk.CTkFont(size=13),
        ).pack(fill="x")

        # Participants
        att_frame = ctk.CTkFrame(row2, fg_color="transparent")
        att_frame.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self._field_label(att_frame, "Deltagere (komma-adskilt)")
        self.attendees_var = ctk.StringVar(value=", ".join(DEFAULT_ATTENDEES))
        ctk.CTkEntry(
            att_frame,
            textvariable=self.attendees_var,
            height=36,
            corner_radius=10,
            border_width=1,
            border_color=_CLR["card_border"],
            fg_color="#f8fafc",
            text_color=_CLR["text"],
            placeholder_text_color=_CLR["text_placeholder"],
            font=ctk.CTkFont(size=13),
        ).pack(fill="x")

        # -- Microphone selector
        self._field_label(self._settings_inner, "Mikrofon")
        mic_row = ctk.CTkFrame(self._settings_inner, fg_color="transparent")
        mic_row.pack(fill="x", pady=(0, 10))

        self.device_var = ctk.StringVar()
        self.device_combo = ctk.CTkComboBox(
            mic_row,
            variable=self.device_var,
            height=36,
            corner_radius=10,
            border_width=1,
            border_color=_CLR["card_border"],
            fg_color="#f8fafc",
            text_color=_CLR["text"],
            button_color=_CLR["accent"],
            button_hover_color=_CLR["accent_hover"],
            dropdown_fg_color=_CLR["card"],
            dropdown_hover_color="#e0e7ff",
            font=ctk.CTkFont(size=13),
            state="readonly",
        )
        self.device_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))

        ctk.CTkButton(
            mic_row,
            text="Opdater",
            width=80,
            height=36,
            corner_radius=10,
            fg_color="transparent",
            border_width=1,
            border_color=_CLR["card_border"],
            text_color=_CLR["text"],
            hover_color="#e0e7ff",
            font=ctk.CTkFont(size=13),
            command=self._refresh_devices,
        ).pack(side="right")

        # -- Systemlyd (dual-track via BlackHole)
        self.system_audio_var = ctk.BooleanVar(value=False)
        self.system_audio_check = ctk.CTkCheckBox(
            self._settings_inner,
            text="Optag systemlyd (Meet/Teams/telefon)",
            variable=self.system_audio_var,
            command=self._on_system_audio_toggle,
            text_color=_CLR["text"],
            font=ctk.CTkFont(size=13),
        )
        self.system_audio_check.pack(fill="x", pady=(0, 4))

        self.system_audio_status = ctk.CTkLabel(
            self._settings_inner,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=_CLR["text_secondary"],
            anchor="w",
            justify="left",
        )
        self.system_audio_status.pack(fill="x", pady=(0, 4))

        self.setup_audio_btn = ctk.CTkButton(
            self._settings_inner,
            text="Opsæt systemlyd",
            height=32,
            corner_radius=8,
            fg_color="transparent",
            border_width=1,
            border_color=_CLR["card_border"],
            text_color=_CLR["text"],
            hover_color="#e0e7ff",
            font=ctk.CTkFont(size=13),
            command=self._setup_system_audio,
        )
        self.setup_audio_btn.pack(fill="x", pady=(0, 10))

        # -- Transkriptions-motor (segmented control)
        self._field_label(self._settings_inner, "Transkription")
        self.engine_var = ctk.StringVar(value=DEFAULT_ENGINE)

        engine_frame = ctk.CTkFrame(
            self._settings_inner,
            fg_color="#f1f5f9",
            corner_radius=10,
        )
        engine_frame.pack(fill="x", pady=(0, 4))

        self._engine_buttons: dict[str, ctk.CTkButton] = {}
        engine_options = [
            ("hviske", "Hviske (live, lokalt)"),
            ("gemini", "Gemini (efter, cloud)"),
        ]
        for val, label in engine_options:
            btn = ctk.CTkButton(
                engine_frame,
                text=label,
                height=32,
                corner_radius=8,
                font=ctk.CTkFont(size=13),
                fg_color="transparent",
                text_color=_CLR["text_secondary"],
                hover_color="#e0e7ff",
                command=lambda v=val: self._select_engine(v),
            )
            btn.pack(side="left", fill="x", expand=True, padx=3, pady=3)
            self._engine_buttons[val] = btn

        self._engine_hint = ctk.CTkLabel(
            self._settings_inner,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=_CLR["text_secondary"],
            anchor="w",
            justify="left",
        )
        self._engine_hint.pack(fill="x", pady=(0, 10))

        # Highlight default engine
        self._select_engine(self.engine_var.get())

        # -- Generate minutes checkbox
        self.minutes_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            self._settings_inner,
            text="Generer referat efter optagelse",
            variable=self.minutes_var,
            font=ctk.CTkFont(size=13),
            text_color=_CLR["text"],
            fg_color=_CLR["accent"],
            hover_color=_CLR["accent_hover"],
            corner_radius=6,
            border_width=2,
            border_color=_CLR["card_border"],
        ).pack(anchor="w", pady=(2, 0))

        # -- Gemini API-nøgle (kan indsættes/ændres når som helst)
        self._field_label(self._settings_inner, "Gemini API-nøgle")
        key_row = ctk.CTkFrame(self._settings_inner, fg_color="transparent")
        key_row.pack(fill="x", pady=(0, 2))
        self.gemini_key_entry = ctk.CTkEntry(
            key_row, height=36, corner_radius=10, show="*",
            placeholder_text="Indsæt din Gemini-nøgle",
        )
        self.gemini_key_entry.pack(side="left", fill="x", expand=True)
        _existing_key = os.environ.get("GEMINI_API_KEY", "")
        if _existing_key:
            self.gemini_key_entry.insert(0, _existing_key)
        ctk.CTkButton(
            key_row, text="Gem nøgle", width=90, height=36, corner_radius=10,
            fg_color=_CLR["accent"], hover_color=_CLR["accent_hover"],
            command=self._save_gemini_key,
        ).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(
            self._settings_inner,
            text="Få en gratis nøgle på aistudio.google.com/apikey",
            font=ctk.CTkFont(size=11),
            text_color=_CLR["text_secondary"],
            anchor="w",
        ).pack(fill="x", pady=(0, 10))

        # ── Hero recording button area ──────────────────────────────
        hero_frame = ctk.CTkFrame(self._outer, fg_color="transparent")
        hero_frame.pack(fill="x", pady=(20, 0))

        self.hero_btn = HeroButton(
            hero_frame,
            command=self._toggle_recording,
            bg=_CLR["bg"],
        )
        self.hero_btn.pack(anchor="center")

        # Timer label directly below button
        self.timer_var = ctk.StringVar(value="00:00:00")
        self.timer_label = ctk.CTkLabel(
            hero_frame,
            textvariable=self.timer_var,
            font=ctk.CTkFont(family="SF Mono", size=28, weight="bold"),
            text_color=_CLR["text"],
        )
        self.timer_label.pack(anchor="center", pady=(6, 0))

        # ── Status / Log card ───────────────────────────────────────
        log_card = ctk.CTkFrame(
            self._outer,
            fg_color=_CLR["card"],
            corner_radius=16,
            border_width=1,
            border_color=_CLR["card_border"],
        )
        log_card.pack(fill="both", expand=True, padx=24, pady=(16, 24))

        # Status line
        self.status_var = ctk.StringVar(value="Klar. Vaelg mappe og navngiv moedet.")
        ctk.CTkLabel(
            log_card,
            textvariable=self.status_var,
            font=ctk.CTkFont(size=13),
            text_color=_CLR["text_secondary"],
            anchor="w",
        ).pack(fill="x", padx=16, pady=(14, 6))

        # Log textbox
        self.log = ctk.CTkTextbox(
            log_card,
            height=180,
            corner_radius=10,
            border_width=1,
            border_color=_CLR["card_border"],
            fg_color="#f8fafc",
            font=ctk.CTkFont(family="SF Mono", size=12),
            text_color=_CLR["text"],
            wrap="word",
            state="disabled",
        )
        self.log.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        # Byg Transkribér fil-fanen
        self._transcribe_tab = TranscribeFileTab(
            self._tabview.tab("Transkribér fil")
        )

        # Byg Ordliste-fanen
        self._vocab_tab = VocabularyTab(self._tabview.tab("Ordliste"))

        # Byg Mødetyper-fanen — on_change opdaterer dropdowns i de andre faner
        self._types_tab = MeetingTypesTab(
            self._tabview.tab("Mødetyper"),
            on_change=self._on_meeting_types_changed,
        )

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _field_label(parent, text: str):
        ctk.CTkLabel(
            parent,
            text=text,
            font=ctk.CTkFont(size=12),
            text_color=_CLR["text_secondary"],
            anchor="w",
        ).pack(fill="x", pady=(0, 3))

    def _select_engine(self, value: str):
        self.engine_var.set(value)
        for v, btn in self._engine_buttons.items():
            if v == value:
                btn.configure(
                    fg_color=_CLR["accent"],
                    text_color="#ffffff",
                    hover_color=_CLR["accent_hover"],
                )
            else:
                btn.configure(
                    fg_color="transparent",
                    text_color=_CLR["text_secondary"],
                    hover_color="#e0e7ff",
                )
        if value == "gemini":
            self._engine_hint.configure(
                text=(
                    "Gemini 2.5 Pro kører efter optagelsen (auto-chunking). "
                    "Bedst til danske navne og fagtermer."
                )
            )
        else:
            self._engine_hint.configure(
                text="Hviske-v3 kører live under optagelsen på din Mac (offline)."
            )

    def _save_gemini_key(self):
        """Gem Gemini-nøglen fra indstillingsfeltet i .env + os.environ."""
        key = self.gemini_key_entry.get().strip()
        if not key:
            self.status_var.set("Indtast en Gemini-nøgle først.")
            return
        _write_env({"GEMINI_API_KEY": key})
        self.status_var.set("Gemini-nøgle gemt.")

    def _toggle_settings(self):
        if self._settings_visible:
            self._settings_inner.pack_forget()
            self._toggle_btn.configure(text="Vis")
            self._settings_visible = False
        else:
            self._settings_inner.pack(fill="x", padx=20, pady=(4, 16))
            self._toggle_btn.configure(text="Skjul")
            self._settings_visible = True

    # ------------------------------------------------------------------
    # Mødetype
    # ------------------------------------------------------------------

    def _key_for_label(self, label: str) -> str:
        for k in self._type_keys:
            if self._meeting_types[k]["navn"] == label:
                return k
        return self._type_keys[0]

    def _on_type_selected(self, label: str):
        self._type_key = self._key_for_label(label)
        mtype = self._meeting_types[self._type_key]
        self.name_var.set(f"{mtype['navn']} {self.date_var.get()}")
        state = load_state()
        attendees = meeting_tool.resolve_type_attendees(
            self._type_key, mtype,
            state.get("attendees_by_type", {}),
            DEFAULT_ATTENDEES,
        )
        self.attendees_var.set(", ".join(attendees))

    def refresh_meeting_types(self):
        """Genindlæs mødetyper fra disk og opdatér optage-fanens dropdown.

        Spejler TranscribeFileTab.refresh_meeting_types — kaldes når en type
        oprettes/redigeres/slettes i Mødetyper-fanen. Bevarer markeringen hvis
        den stadig findes; ellers falder den tilbage til første type."""
        self._meeting_types = meeting_tool.load_meeting_types(CONFIG_DIR)
        self._type_keys = list(self._meeting_types)
        if self._type_key not in self._meeting_types:
            self._type_key = self._type_keys[0]
        labels = [self._meeting_types[k]["navn"] for k in self._type_keys]
        self.type_combo.configure(values=labels)
        self.type_var.set(self._meeting_types[self._type_key]["navn"])

    def _on_meeting_types_changed(self):
        """Callback fra Mødetyper-fanen: opdatér begge mødetype-dropdowns."""
        self.refresh_meeting_types()
        self._transcribe_tab.refresh_meeting_types()

    # ------------------------------------------------------------------
    # Defaults
    # ------------------------------------------------------------------

    def _set_default_folder(self):
        """Default: sidst brugte mappe, ellers overordnet mode-mappe."""
        state = load_state()
        last = state.get("last_folder")
        if last and Path(last).exists():
            self.folder_var.set(last)
        else:
            self.folder_var.set(str(MEETINGS_DIR))
        # Husk om referat-checkbox skal vaere paa
        last_minutes = state.get("generate_minutes")
        if isinstance(last_minutes, bool):
            self.minutes_var.set(last_minutes)
        # Husk sidst valgte deltagere
        last_attendees = state.get("attendees")
        if isinstance(last_attendees, list) and last_attendees:
            self.attendees_var.set(", ".join(last_attendees))
        # Husk sidst valgte transkriptions-motor
        last_engine = state.get("engine")
        if last_engine in ("hviske", "gemini"):
            self._select_engine(last_engine)
        # Husk om systemlyd-optagelse var slået til
        if state.get("system_audio"):
            self.system_audio_var.set(True)
            self._on_system_audio_toggle()
        # Auto-udfyld navn + deltagere ud fra valgt mødetype
        self._on_type_selected(self.type_var.get())

    def _refresh_devices(self):
        devices = meeting_tool.list_audio_devices()
        self._device_ids = {}
        if not devices:
            self.device_combo.configure(values=["(ingen enheder fundet)"])
            self.device_combo.set("(ingen enheder fundet)")
            return
        labels = []
        self._device_labels = {}  # dev_id -> label
        for dev_id, name in devices:
            label = f"[{dev_id}] {name}" if isinstance(dev_id, int) else name
            self._device_ids[label] = dev_id
            self._device_labels[dev_id] = label
            labels.append(label)
        self.device_combo.configure(values=labels)
        # Vælg den bedste RIGTIGE mikrofon: udeluk virtuelle enheder
        # (BlackHole/Teams/Zoom) og foretræk den indbyggede Mac-mikrofon.
        preferred_id = meeting_tool.select_preferred_input_device(devices)
        preferred = self._device_labels.get(preferred_id, labels[0])
        self.device_combo.set(preferred)

    def _on_system_audio_toggle(self):
        if not self.system_audio_var.get():
            self.system_audio_status.configure(text="")
            return
        self.system_audio_status.configure(text="Tjekker BlackHole ...")

        def worker():
            idx = audio_routing.detect_blackhole()
            self._ui_queue_put(("system_audio_status", idx))

        threading.Thread(target=worker, daemon=True).start()

    def _setup_system_audio(self):
        self._log("Opsætter systemlyd-routing ...")

        def worker():
            try:
                result = audio_routing.setup_system_output()
            except Exception as exc:  # defensivt: undgå tavs trådsdød
                result = audio_routing.SetupResult(
                    status="needs_manual", guidance=str(exc))
            self._ui_queue_put(("setup_audio", result))

        threading.Thread(target=worker, daemon=True).start()

    def _parse_device_id(self):
        label = self.device_var.get()
        ids = getattr(self, "_device_ids", {})
        if label in ids:
            return ids[label]
        # gammelt mac-format "[1] navn"
        try:
            return int(label.split("]")[0].strip("["))
        except Exception:
            pass
        # ellers: første kendte enhed (rigtig type pr. platform), ellers 1
        if ids:
            return next(iter(ids.values()))
        return 1

    def _selected_device_name(self):
        """Navnet på den valgte enhed uden '[id] '-præfiks.

        Bruges til at slå enheden op til et AKTUELT index lige før optagelse
        i stedet for at genbruge et cachet (og evt. forældet) index.
        """
        label = self.device_var.get()
        if label.startswith("[") and "]" in label:
            return label.split("]", 1)[1].strip()
        return label.strip()

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _pick_folder(self):
        if self.recording:
            return
        current = self.folder_var.get()
        initial = current if os.path.isdir(current) else str(MEETINGS_DIR)
        chosen = filedialog.askdirectory(
            title="Vælg mappe til optagelse og referat",
            initialdir=initial,
        )
        if chosen:
            self.folder_var.set(chosen)

    def _toggle_recording(self):
        if self.transcribing:
            return  # ignorér klik mens sidste transkription gøres færdig
        if self.recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        folder = self.folder_var.get().strip()
        name = self.name_var.get().strip()
        date = self.date_var.get().strip()

        if not folder:
            messagebox.showerror("Fejl", "Vælg en mappe.")
            return
        if not name:
            messagebox.showerror("Fejl", "Giv mødet et navn.")
            return
        if not date:
            messagebox.showerror("Fejl", "Angiv en dato.")
            return

        # Sanitér mødenavn til mappenavn (fjern slashes m.v.)
        safe_name = name.replace("/", "-").replace(":", "-").strip()
        if not safe_name:
            messagebox.showerror("Fejl", "Mødenavnet kan ikke bruges som mappenavn.")
            return

        # Opret undermappe pr. mode under den valgte parent-mappe
        parent_dir = Path(folder)
        output_dir = parent_dir / safe_name
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Fejl", f"Kunne ikke oprette mappe: {e}")
            return

        # Parse deltagere (komma- eller semikolon-adskilt)
        raw_attendees = self.attendees_var.get()
        attendees = [
            a.strip() for a in raw_attendees.replace(";", ",").split(",")
            if a.strip()
        ]
        if not attendees:
            attendees = list(DEFAULT_ATTENDEES)

        # Husk valg til naeste gang
        _saved = load_state()
        _abt = _saved.get("attendees_by_type", {})
        _abt[self._type_key] = attendees
        save_state({
            **_saved,
            "last_folder": str(parent_dir),
            "generate_minutes": self.minutes_var.get(),
            "attendees": attendees,
            "engine": self.engine_var.get(),
            "system_audio": self.system_audio_var.get(),
            "meeting_type": self._type_key,
            "attendees_by_type": _abt,
        })

        # Ryd log
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.chunks_done = 0

        # Setup state
        self.recording = True
        self.transcribing = False
        self.stop_event = threading.Event()
        self.start_time = time.time()
        self._timer_running = True
        self._set_ui_recording(True)
        self._log(f"=== {name} ===")
        self._log(f"Mappe: {output_dir}")
        self._log(f"Deltagere: {', '.join(attendees)}")
        self._log("")

        # Start worker-tråd. Send enhedens NAVN, ikke et cachet index — index
        # genoversættes lige før optagelse (avfoundation-indekser flytter sig).
        device_name = self._selected_device_name()
        chunk_dur = DEFAULT_CHUNK_DURATION
        gen_minutes = self.minutes_var.get()
        engine = self.engine_var.get()
        system_audio = self.system_audio_var.get()
        meeting_type = self._meeting_types[self._type_key]

        self._log(f"Motor: {engine}")
        self._log("")

        self.worker_thread = threading.Thread(
            target=self._run_recording,
            args=(output_dir, date, name, device_name, chunk_dur, gen_minutes, attendees, engine, system_audio, meeting_type),
            daemon=True,
        )
        self.worker_thread.start()

    def _stop_recording(self):
        if not self.recording:
            return
        self._log("\n>>> Stopper optagelse — transkriberer færdig ...\n")
        self.recording = False
        self.transcribing = True
        self._timer_running = False  # frys timeren på sluttiden (nulstil ikke)
        self.timer_label.configure(text_color=_CLR["text_secondary"])
        self.hero_btn.set_transcribing()
        self.status_var.set("Transkriberer …")
        self.stop_event.set()
        # Spinner + frosset timer vises indtil worker melder 'done'

    def _on_close(self):
        if self.recording or self.transcribing:
            msg = (
                "Der optages stadig. Stop og luk?"
                if self.recording
                else "Transkriptionen er ikke færdig. Luk alligevel?"
            )
            if not messagebox.askyesno("Luk Mødeværktøj?", msg):
                return
            self.stop_event.set()
            # Giv worker en chance for at afslutte
            self.root.after(200, self.root.destroy)
        else:
            self.root.destroy()

    # ------------------------------------------------------------------
    # Worker-tråd (kører i baggrunden, kommunikerer via ui_queue)
    # ------------------------------------------------------------------

    def _run_recording(self, output_dir, date, name, device_name, chunk_dur, gen_minutes, attendees, engine, system_audio, meeting_type=None):
        system_device = None
        previous_output = None
        # Slå enheden op til et AKTUELT index og verificér at den giver signal,
        # FØR optagelsen starter. Forhindrer en tavs optagelse hvis indekserne
        # har flyttet sig (fx efter et tidligere møde) eller hvis en virtuel
        # enhed som BlackHole er valgt ved en fejl.
        try:
            device_id = meeting_tool.prepare_input_device(
                device_name, on_status=self._ui_status
            )
        except meeting_tool.SilentInputError as e:
            self._ui_queue_put(("error", str(e)))
            return
        if system_audio:
            routing = audio_routing.ensure_routing_active()
            if routing.status == "ok":
                system_device = routing.system_device
                previous_output = routing.previous_output
            else:
                self._ui_status(
                    "Systemlyd-routing kunne ikke opsættes — optager kun mikrofon.")
                self._ui_status(routing.guidance)
        try:
            if engine == "gemini":
                wav_path, transcript = meeting_tool.record_then_transcribe_gemini(
                    output_dir=output_dir,
                    date=date,
                    device_id=device_id,
                    system_device=system_device,
                    attendees=attendees,
                    model=DEFAULT_GEMINI_MODEL,
                    stop_event=self.stop_event,
                    on_status=self._ui_status,
                    on_chunk_done=self._ui_chunk_done,
                    recording_name=name,
                    meeting_type_name=meeting_type["navn"] if meeting_type else "driftsledelsesmøde",
                )
            else:
                wav_path, transcript = meeting_tool.record_and_transcribe_live(
                    output_dir=output_dir,
                    date=date,
                    device_id=device_id,
                    model_size=DEFAULT_MODEL,
                    system_device=system_device,
                    chunk_duration=chunk_dur,
                    stop_event=self.stop_event,
                    on_status=self._ui_status,
                    on_chunk_done=self._ui_chunk_done,
                    recording_name=name,
                )

            # Læs det aktuelle navn fra UI (brugeren kan have ændret det under optagelse)
            current_name = self.name_var.get().strip().replace("/", "-").replace(":", "-").strip()
            if current_name and current_name != name:
                self._ui_status(f"Mødenavn ændret: {name} → {current_name}")
                # Omdøb WAV-filen
                new_wav = wav_path.parent / f"{current_name}.wav"
                try:
                    wav_path.rename(new_wav)
                    wav_path = new_wav
                except Exception as e:
                    self._ui_status(f"Kunne ikke omdøbe WAV: {e}")
                # Omdøb output-mappen
                new_dir = output_dir.parent / current_name
                if new_dir != output_dir:
                    try:
                        output_dir.rename(new_dir)
                        output_dir = new_dir
                    except Exception as e:
                        self._ui_status(f"Kunne ikke omdøbe mappe: {e}")
                name = current_name

            self._ui_status("Transkription færdig. Gemmer filer ...")

            # Læs også opdaterede deltagere fra UI
            raw = self.attendees_var.get()
            current_attendees = [
                a.strip() for a in raw.replace(";", ",").split(",") if a.strip()
            ]
            if current_attendees:
                attendees = current_attendees

            minutes = None
            if gen_minutes and transcript.strip():
                self._ui_status("Genererer referat ...")
                try:
                    minutes = meeting_tool.generate_minutes(
                        transcript, attendees, date, meeting_type=meeting_type
                    )
                except Exception as e:
                    self._ui_status(f"Kunne ikke generere referat: {e}")

            meeting_tool.save_output(
                output_dir=output_dir,
                date=date,
                transcript=transcript,
                minutes=minutes,
                name_base=name,
            )

            self._ui_queue_put(("done", str(output_dir), name))

        except Exception as e:
            self._ui_queue_put(("error", str(e)))
        finally:
            # Gendan brugerens normale lyd-output efter dual-track-optagelse.
            if previous_output:
                audio_routing.restore_system_output(previous_output)

    # ------------------------------------------------------------------
    # UI queue (thread-safe kommunikation)
    # ------------------------------------------------------------------

    def _ui_queue_put(self, item):
        self.ui_queue.put(item)

    def _ui_status(self, msg: str):
        self._ui_queue_put(("status", msg))

    def _ui_chunk_done(self, idx: int, segments: list):
        self._ui_queue_put(("chunk_done", idx, segments))

    def _drain_ui_queue(self):
        try:
            while True:
                item = self.ui_queue.get_nowait()
                kind = item[0]
                if kind == "status":
                    msg = item[1]
                    self.status_var.set(msg)
                    self._log(msg)
                elif kind == "chunk_done":
                    _, idx, segments = item
                    self.chunks_done += 1
                    self._log(
                        f"\n— Chunk {idx:04d} ({len(segments)} segmenter) —"
                    )
                    # Vis kun de første par segmenter for at spare plads
                    for start, end, text in segments[:5]:
                        self._log(
                            f"  [{meeting_tool.format_timestamp(start)}] {text}"
                        )
                    if len(segments) > 5:
                        self._log(f"  ... (+{len(segments) - 5} mere)")
                elif kind == "done":
                    _, out_dir, name = item
                    self.transcribing = False
                    self._set_ui_recording(False)
                    self.status_var.set("Færdig.")
                    self._log(f"\n=== FÆRDIG ===\nFiler gemt i: {out_dir}")
                    messagebox.showinfo(
                        "Færdig",
                        f"Mødet er gemt i:\n{out_dir}\n\n"
                        f"Filer:\n"
                        f"  - {name}.wav\n"
                        f"  - Transkription {name}.md\n"
                        f"  - Referat {name}.md (hvis valgt)",
                    )
                elif kind == "error":
                    _, err = item
                    self.transcribing = False
                    self._set_ui_recording(False)
                    self.status_var.set("Fejl.")
                    self._log(f"\nFEJL: {err}")
                    messagebox.showerror("Fejl under optagelse", err)
                elif kind == "system_audio_status":
                    _, idx = item
                    if idx is None:
                        self.system_audio_status.configure(
                            text="BlackHole ikke fundet — klik 'Opsæt systemlyd'.")
                    else:
                        self.system_audio_status.configure(
                            text=f"Klar: systemlyd via BlackHole [{idx}].")
                elif kind == "setup_audio":
                    _, result = item
                    if result.status == "ok":
                        self.system_audio_var.set(True)
                        self.system_audio_status.configure(
                            text=f"Klar: systemlyd via BlackHole [{result.system_device}].")
                        self._log("Systemlyd klar.")
                    else:
                        self.system_audio_status.configure(
                            text="Opsætning kræver handling — se loggen.")
                        self._log(result.guidance)
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._drain_ui_queue)

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------

    def _set_ui_recording(self, is_recording: bool):
        self.recording = is_recording
        self.hero_btn.set_recording(is_recording)
        if is_recording:
            self.timer_label.configure(text_color=_CLR["rec_active"])
        else:
            self.timer_label.configure(text_color=_CLR["text"])
            self.start_time = None
            self._timer_running = False
            self.timer_var.set("00:00:00")

    def _log(self, msg: str):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _tick_timer(self):
        if self._timer_running and self.start_time is not None:
            elapsed = int(time.time() - self.start_time)
            h, rem = divmod(elapsed, 3600)
            m, s = divmod(rem, 60)
            self.timer_var.set(f"{h:02d}:{m:02d}:{s:02d}")
        self.root.after(500, self._tick_timer)


class VocabularyTab:
    """Ordliste-fane: Personer, Steder, Fagtermer. Læser og gemmer
    vocabulary.json i CONFIG_DIR."""

    CATEGORIES = (
        ("personer", "Personer", "Tilføj person"),
        ("steder", "Steder", "Tilføj sted"),
        ("fagtermer", "Fagtermer", "Tilføj fagterm"),
    )

    VOCAB_FILE = CONFIG_DIR / "vocabulary.json"

    def __init__(self, parent: ctk.CTkBaseClass):
        self.parent = parent
        # Per-kategori liste af (ord_entry, forklaring_entry)-par i visnings-rækkefølge
        self._entries: dict[str, list[tuple[ctk.CTkEntry, ctk.CTkEntry]]] = {
            cat: [] for cat, _, _ in self.CATEGORIES
        }
        # Per-kategori container-frame hvor rækker pakkes
        self._rows_frames: dict[str, ctk.CTkFrame] = {}

        self._build_ui()
        self._load()

    def _build_ui(self):
        self.parent.configure(fg_color=_CLR["bg"])

        outer = ctk.CTkScrollableFrame(
            self.parent,
            fg_color=_CLR["bg"],
            scrollbar_button_color=_CLR["card_border"],
            scrollbar_button_hover_color=_CLR["accent"],
        )
        outer.pack(fill="both", expand=True, padx=0, pady=0)

        # Header
        ctk.CTkLabel(
            outer,
            text="Ordliste",
            font=ctk.CTkFont(family="SF Pro Display", size=32, weight="bold"),
            text_color=_CLR["text"],
        ).pack(anchor="w", padx=28, pady=(24, 4))

        ctk.CTkLabel(
            outer,
            text="Navne og termer som bruges i Gemini/Hviske-prompts",
            font=ctk.CTkFont(family="SF Pro Text", size=14),
            text_color=_CLR["text_secondary"],
        ).pack(anchor="w", padx=28, pady=(0, 16))

        # Tre kategori-kort
        for key, title, add_label in self.CATEGORIES:
            self._build_category(outer, key, title, add_label)

        # Gem/Annullér-bjælke
        action_bar = ctk.CTkFrame(outer, fg_color="transparent")
        action_bar.pack(fill="x", padx=24, pady=(8, 24))

        ctk.CTkButton(
            action_bar,
            text="Gem",
            width=120,
            height=36,
            corner_radius=10,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=_CLR["accent"],
            hover_color=_CLR["accent_hover"],
            command=self._save,
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            action_bar,
            text="Annullér",
            width=120,
            height=36,
            corner_radius=10,
            font=ctk.CTkFont(size=14),
            fg_color="transparent",
            text_color=_CLR["text_secondary"],
            border_width=1,
            border_color=_CLR["card_border"],
            hover_color=_CLR["card_border"],
            command=self._load,
        ).pack(side="right")

        self._status_label = ctk.CTkLabel(
            action_bar,
            text="",
            font=ctk.CTkFont(size=12),
            text_color=_CLR["text_secondary"],
        )
        self._status_label.pack(side="left")

    def _build_category(self, parent, key: str, title: str, add_label: str):
        card = ctk.CTkFrame(
            parent,
            fg_color=_CLR["card"],
            corner_radius=16,
            border_width=1,
            border_color=_CLR["card_border"],
        )
        card.pack(fill="x", padx=24, pady=(0, 16))

        ctk.CTkLabel(
            card,
            text=title,
            font=ctk.CTkFont(family="SF Pro Display", size=16, weight="bold"),
            text_color=_CLR["text"],
        ).pack(anchor="w", padx=20, pady=(16, 8))

        rows = ctk.CTkFrame(card, fg_color="transparent")
        rows.pack(fill="x", padx=20)
        self._rows_frames[key] = rows

        add_btn = ctk.CTkButton(
            card,
            text=f"+ {add_label}",
            width=160,
            height=30,
            corner_radius=8,
            font=ctk.CTkFont(size=13),
            fg_color="transparent",
            text_color=_CLR["accent"],
            border_width=1,
            border_color=_CLR["card_border"],
            hover_color="#e0e7ff",
            command=lambda k=key: self._add_row(k, "", focus=True),
        )
        add_btn.pack(anchor="w", padx=20, pady=(8, 16))

    def _add_row(self, key: str, ord_text: str = "", forklaring: str = "", focus: bool = False):
        rows = self._rows_frames[key]
        row = ctk.CTkFrame(rows, fg_color="transparent")
        row.pack(fill="x", pady=2)

        ord_entry = ctk.CTkEntry(
            row,
            width=160,
            placeholder_text="ord",
            font=ctk.CTkFont(family="SF Pro Text", size=13),
            fg_color="#ffffff",
            text_color=_CLR["text"],
            placeholder_text_color=_CLR["text_placeholder"],
            border_color=_CLR["card_border"],
        )
        ord_entry.pack(side="left", padx=(0, 8))
        ord_entry.insert(0, ord_text)

        forklaring_entry = ctk.CTkEntry(
            row,
            placeholder_text="forklaring (valgfri)",
            font=ctk.CTkFont(family="SF Pro Text", size=13),
            fg_color="#ffffff",
            text_color=_CLR["text"],
            placeholder_text_color=_CLR["text_placeholder"],
            border_color=_CLR["card_border"],
        )
        forklaring_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        forklaring_entry.insert(0, forklaring)

        pair = (ord_entry, forklaring_entry)

        delete_btn = ctk.CTkButton(
            row,
            text="×",
            width=30,
            height=28,
            corner_radius=6,
            font=ctk.CTkFont(size=16, weight="bold"),
            fg_color="transparent",
            text_color=_CLR["text_secondary"],
            hover_color=_CLR["rec_ring"],
        )
        delete_btn.pack(side="right")
        delete_btn.configure(command=lambda r=row, p=pair, k=key: self._delete_row(k, r, p))

        self._entries[key].append(pair)

        if focus:
            ord_entry.focus_set()

    def _delete_row(self, key: str, row_frame, pair):
        try:
            self._entries[key].remove(pair)
        except ValueError:
            pass
        row_frame.destroy()

    def _load(self):
        """Genindlæs fra disk og bygg række-widgets fra bunden."""
        for key, _, _ in self.CATEGORIES:
            for pair in list(self._entries[key]):
                pair[0].master.destroy()  # pair[0].master er row-frame'en
            self._entries[key].clear()

        vocab = meeting_tool.load_vocabulary(CONFIG_DIR)
        for key, _, _ in self.CATEGORIES:
            for item in vocab.get(key, []):
                ord_text, forklaring = meeting_tool.split_vocab_entry(item)
                self._add_row(key, ord_text, forklaring, focus=False)
        self._set_status("Indlæst.")

    def _save(self):
        """Skriv state til vocabulary.json. Rækker uden ord filtreres."""
        data = {}
        for key, _, _ in self.CATEGORIES:
            items = []
            for ord_entry, forklaring_entry in self._entries[key]:
                joined = meeting_tool.join_vocab_entry(
                    ord_entry.get(), forklaring_entry.get()
                )
                if joined:  # tomt ord → join returnerer "" → filtreres væk
                    items.append(joined)
            data[key] = items
        try:
            self.VOCAB_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            self._set_status(f"Fejl ved gemning: {e}")
            return
        self._set_status("Gemt.")

    def _set_status(self, msg: str):
        self._status_label.configure(text=msg)
        # Ryd status efter 3 sekunder
        self.parent.after(3000, lambda: self._status_label.configure(text=""))


class MeetingTypesTab:
    """Mødetyper-fane: opret, redigér og slet mødetyper. Læser/gemmer
    meeting_types.json i CONFIG_DIR (spejler VocabularyTab)."""

    TYPES_FILE = CONFIG_DIR / "meeting_types.json"

    def __init__(self, parent: ctk.CTkBaseClass, on_change=None):
        self.parent = parent
        # Kaldes efter hver gem/opret/slet, så andre faners dropdowns opdateres.
        self._on_change = on_change
        self.types = meeting_tool.load_meeting_types(CONFIG_DIR)
        self.current_key = next(iter(self.types))
        self._build_ui()
        self._load_into_form(self.current_key)

    def _notify_change(self):
        if self._on_change is not None:
            self._on_change()

    def _build_ui(self):
        self.parent.configure(fg_color=_CLR["bg"])

        outer = ctk.CTkScrollableFrame(
            self.parent,
            fg_color=_CLR["bg"],
            scrollbar_button_color=_CLR["card_border"],
            scrollbar_button_hover_color=_CLR["accent"],
        )
        outer.pack(fill="both", expand=True, padx=0, pady=0)

        # Header
        ctk.CTkLabel(
            outer,
            text="Mødetyper",
            font=ctk.CTkFont(family="SF Pro Display", size=32, weight="bold"),
            text_color=_CLR["text"],
        ).pack(anchor="w", padx=28, pady=(24, 4))

        ctk.CTkLabel(
            outer,
            text="Opret, redigér og slet mødetyper der styrer referat og transkription",
            font=ctk.CTkFont(family="SF Pro Text", size=14),
            text_color=_CLR["text_secondary"],
        ).pack(anchor="w", padx=28, pady=(0, 16))

        # Kort
        card = ctk.CTkFrame(
            outer,
            fg_color=_CLR["card"],
            corner_radius=16,
            border_width=1,
            border_color=_CLR["card_border"],
        )
        card.pack(fill="x", padx=24, pady=(0, 16))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=16)

        # -- Type-vælger
        MeetingApp._field_label(inner, "Vælg mødetype")
        self.selector_var = ctk.StringVar(value=self.types[self.current_key]["navn"])
        self.selector = ctk.CTkComboBox(
            inner,
            variable=self.selector_var,
            values=[t["navn"] for t in self.types.values()],
            height=36,
            corner_radius=10,
            border_width=1,
            border_color=_CLR["card_border"],
            state="readonly",
            fg_color="#f8fafc",
            button_color=_CLR["accent"],
            button_hover_color=_CLR["accent_hover"],
            text_color=_CLR["text"],
            dropdown_fg_color=_CLR["card"],
            dropdown_hover_color="#e0e7ff",
            font=ctk.CTkFont(size=13),
            command=self._on_select,
        )
        self.selector.pack(fill="x", pady=(0, 10))

        # -- Navn
        MeetingApp._field_label(inner, "Navn")
        self.navn_var = ctk.StringVar()
        ctk.CTkEntry(
            inner,
            textvariable=self.navn_var,
            height=36,
            corner_radius=10,
            border_width=1,
            border_color=_CLR["card_border"],
            fg_color="#f8fafc",
            text_color=_CLR["text"],
            placeholder_text_color=_CLR["text_placeholder"],
            font=ctk.CTkFont(size=13),
        ).pack(fill="x", pady=(0, 10))

        # -- Detaljeniveau
        MeetingApp._field_label(inner, "Detaljeniveau")
        self.detalje = ctk.CTkSegmentedButton(
            inner,
            values=list(meeting_tool.DETALJENIVEAUER),
            font=ctk.CTkFont(size=13),
            text_color=_CLR["text"],
            selected_color=_CLR["accent"],
            selected_hover_color=_CLR["accent_hover"],
            unselected_color=_CLR["card_border"],
            unselected_hover_color="#e0e7ff",
        )
        self.detalje.pack(fill="x", pady=(0, 10))

        # -- Checkboxes
        checks_frame = ctk.CTkFrame(inner, fg_color="transparent")
        checks_frame.pack(fill="x", pady=(0, 10))
        self.citater_var = ctk.BooleanVar()
        ctk.CTkCheckBox(
            checks_frame,
            text="Medtag ordrette citater",
            variable=self.citater_var,
            font=ctk.CTkFont(size=13),
            text_color=_CLR["text"],
            checkmark_color="#ffffff",
            fg_color=_CLR["accent"],
            hover_color=_CLR["accent_hover"],
            border_color=_CLR["card_border"],
        ).pack(side="left", padx=(0, 20))
        self.opgave_var = ctk.BooleanVar()
        ctk.CTkCheckBox(
            checks_frame,
            text="Opgaveliste pr. person",
            variable=self.opgave_var,
            font=ctk.CTkFont(size=13),
            text_color=_CLR["text"],
            checkmark_color="#ffffff",
            fg_color=_CLR["accent"],
            hover_color=_CLR["accent_hover"],
            border_color=_CLR["card_border"],
        ).pack(side="left")

        # -- Fokus
        MeetingApp._field_label(inner, "Fokus")
        self.fokus_var = ctk.StringVar()
        ctk.CTkEntry(
            inner,
            textvariable=self.fokus_var,
            height=36,
            corner_radius=10,
            border_width=1,
            border_color=_CLR["card_border"],
            fg_color="#f8fafc",
            text_color=_CLR["text"],
            placeholder_text="Hvad skal referatet lægge særlig vægt på?",
            placeholder_text_color=_CLR["text_placeholder"],
            font=ctk.CTkFont(size=13),
        ).pack(fill="x", pady=(0, 10))

        # -- Ekstra instruktioner
        MeetingApp._field_label(inner, "Ekstra instruktioner")
        self.ekstra_box = ctk.CTkTextbox(
            inner,
            height=80,
            corner_radius=10,
            border_width=1,
            border_color=_CLR["card_border"],
            fg_color="#f8fafc",
            text_color=_CLR["text"],
            font=ctk.CTkFont(size=13),
            wrap="word",
        )
        self.ekstra_box.pack(fill="x", pady=(0, 10))

        # -- Deltagere
        MeetingApp._field_label(inner, "Default-deltagere (komma-adskilt)")
        self.deltagere_var = ctk.StringVar()
        ctk.CTkEntry(
            inner,
            textvariable=self.deltagere_var,
            height=36,
            corner_radius=10,
            border_width=1,
            border_color=_CLR["card_border"],
            fg_color="#f8fafc",
            text_color=_CLR["text"],
            placeholder_text="Komma-adskilt liste af standard-deltagere",
            placeholder_text_color=_CLR["text_placeholder"],
            font=ctk.CTkFont(size=13),
        ).pack(fill="x", pady=(0, 10))

        # -- Knapper + statuslinje
        action_bar = ctk.CTkFrame(inner, fg_color="transparent")
        action_bar.pack(fill="x", pady=(4, 0))

        ctk.CTkButton(
            action_bar,
            text="Ny type",
            width=110,
            height=36,
            corner_radius=10,
            font=ctk.CTkFont(size=14),
            fg_color="transparent",
            text_color=_CLR["accent"],
            border_width=1,
            border_color=_CLR["card_border"],
            hover_color="#e0e7ff",
            command=self._new,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            action_bar,
            text="Gem",
            width=110,
            height=36,
            corner_radius=10,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=_CLR["accent"],
            hover_color=_CLR["accent_hover"],
            command=self._save,
        ).pack(side="left", padx=(0, 8))

        self.delete_btn = ctk.CTkButton(
            action_bar,
            text="Slet",
            width=110,
            height=36,
            corner_radius=10,
            font=ctk.CTkFont(size=14),
            fg_color="transparent",
            text_color=_CLR["text_secondary"],
            border_width=1,
            border_color=_CLR["card_border"],
            hover_color=_CLR["rec_ring"],
            command=self._delete,
        )
        self.delete_btn.pack(side="left")

        self._status_label = ctk.CTkLabel(
            action_bar,
            text="",
            font=ctk.CTkFont(size=12),
            text_color=_CLR["text_secondary"],
        )
        self._status_label.pack(side="left", padx=(12, 0))

    def _refresh_selector(self):
        """Opdatér type-dropdown og Slet-knap-state."""
        names = [t["navn"] for t in self.types.values()]
        self.selector.configure(values=names)
        self.selector_var.set(self.types[self.current_key]["navn"])
        # Slet spærret når kun én type tilbage
        self.delete_btn.configure(
            state=("disabled" if len(self.types) <= 1 else "normal")
        )

    def _on_select(self, label: str):
        for k, t in self.types.items():
            if t["navn"] == label:
                self.current_key = k
                break
        self._load_into_form(self.current_key)

    def _load_into_form(self, key: str):
        t = self.types[key]
        self.navn_var.set(t["navn"])
        self.detalje.set(t["detaljeniveau"])
        self.citater_var.set(t["citater"])
        self.opgave_var.set(t["opgaveliste"])
        self.fokus_var.set(t["fokus"])
        self.ekstra_box.delete("1.0", "end")
        self.ekstra_box.insert("1.0", t["ekstra_instruktioner"])
        self.deltagere_var.set(", ".join(t["deltagere"]))
        self._refresh_selector()

    def _form_to_type(self) -> dict:
        raw = {
            "navn": self.navn_var.get(),
            "detaljeniveau": self.detalje.get(),
            "citater": bool(self.citater_var.get()),
            "opgaveliste": bool(self.opgave_var.get()),
            "fokus": self.fokus_var.get(),
            "ekstra_instruktioner": self.ekstra_box.get("1.0", "end").rstrip("\n"),
            "deltagere": [d.strip() for d in self.deltagere_var.get().split(",") if d.strip()],
        }
        return meeting_tool._normalize_meeting_type(raw)

    def _persist(self):
        try:
            self.TYPES_FILE.write_text(
                json.dumps(self.types, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._set_status("Gemt.")
        except OSError as e:
            self._set_status(f"Kunne ikke gemme: {e}")

    def _save(self):
        new_type = self._form_to_type()
        new_navn = new_type["navn"]
        # Advar ved duplikat navn (anden nøgle med samme navn)
        for k, t in self.types.items():
            if k != self.current_key and t["navn"] == new_navn:
                self._set_status(
                    f"Advarsel: Typen '{new_navn}' findes allerede. Omdøb for at undgå forveksling."
                )
                return
        self.types[self.current_key] = new_type
        self._persist()
        self._refresh_selector()
        self._notify_change()

    def _new(self):
        # Unik nøgle
        base = "type"
        i = 1
        while f"{base}{i}" in self.types:
            i += 1
        key = f"{base}{i}"
        self.types[key] = meeting_tool._normalize_meeting_type({"navn": f"Ny mødetype {i}"})
        self.current_key = key
        self._load_into_form(key)
        self._persist()
        self._notify_change()

    def _delete(self):
        if len(self.types) <= 1:
            return
        del self.types[self.current_key]
        self.current_key = next(iter(self.types))
        self._load_into_form(self.current_key)
        self._persist()
        self._notify_change()

    def _set_status(self, msg: str):
        self._status_label.configure(text=msg)
        self.parent.after(4000, lambda: self._status_label.configure(text=""))


class TranscribeFileTab:
    """Fane til at transkribere en eksisterende lydfil (fx en iPhone Voice
    Memo, .m4a). Genbruger meeting_tool.transcribe_file: konverter -> transkriber
    (Hviske/Gemini) -> evt. referat -> gem. Kører i baggrundstråd og pumper
    status til loggen via en queue, så UI ikke fryser."""

    AUDIO_FILETYPES = [
        (
            "Lydfiler",
            "*.m4a *.mp3 *.wav *.aac *.caf *.mp4 *.m4v *.mov *.opus *.flac *.ogg "
            "*.M4A *.MP3 *.WAV *.AAC *.CAF *.MP4 *.M4V *.MOV *.OPUS *.FLAC *.OGG",
        ),
        ("Alle filer", "*.*"),
    ]

    def __init__(self, parent: ctk.CTkBaseClass):
        self.parent = parent
        self._input_path: Path | None = None
        self.processing = False
        self.worker_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.ui_queue: queue.Queue = queue.Queue()
        self._engine_buttons: dict[str, ctk.CTkButton] = {}

        # Mødetype-state
        self._meeting_types = meeting_tool.load_meeting_types(CONFIG_DIR)
        _state = load_state()
        self._type_key = _state.get("meeting_type")
        if self._type_key not in self._meeting_types:
            self._type_key = next(iter(self._meeting_types))
        self._type_keys = list(self._meeting_types)

        self._build_ui()
        self._load_defaults()
        self.parent.after(100, self._drain_queue)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.parent.configure(fg_color=_CLR["bg"])

        outer = ctk.CTkScrollableFrame(
            self.parent,
            fg_color=_CLR["bg"],
            scrollbar_button_color=_CLR["card_border"],
            scrollbar_button_hover_color=_CLR["accent"],
        )
        outer.pack(fill="both", expand=True, padx=0, pady=0)

        # ── Header ──────────────────────────────────────────────────
        ctk.CTkLabel(
            outer,
            text="Transkribér fil",
            font=ctk.CTkFont(family="SF Pro Display", size=32, weight="bold"),
            text_color=_CLR["text"],
        ).pack(anchor="w", padx=28, pady=(24, 4))

        ctk.CTkLabel(
            outer,
            text="Vælg en optagelse (fx iPhone Voice Memo) og lav transkription + referat",
            font=ctk.CTkFont(family="SF Pro Text", size=14),
            text_color=_CLR["text_secondary"],
        ).pack(anchor="w", padx=28, pady=(0, 16))

        # ── Indstillingskort ────────────────────────────────────────
        card = ctk.CTkFrame(
            outer,
            fg_color=_CLR["card"],
            corner_radius=16,
            border_width=1,
            border_color=_CLR["card_border"],
        )
        card.pack(fill="x", padx=24, pady=(0, 0))

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=16)

        # -- Lydfil
        MeetingApp._field_label(inner, "Lydfil")
        file_row = ctk.CTkFrame(inner, fg_color="transparent")
        file_row.pack(fill="x", pady=(0, 10))

        self.file_var = ctk.StringVar()
        ctk.CTkEntry(
            file_row,
            textvariable=self.file_var,
            height=36,
            corner_radius=10,
            border_width=1,
            border_color=_CLR["card_border"],
            fg_color="#f8fafc",
            text_color=_CLR["text"],
            placeholder_text="Ingen fil valgt",
            placeholder_text_color=_CLR["text_placeholder"],
            font=ctk.CTkFont(size=13),
            state="readonly",
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))

        ctk.CTkButton(
            file_row,
            text="Vælg fil ...",
            width=110,
            height=36,
            corner_radius=10,
            fg_color=_CLR["accent"],
            hover_color=_CLR["accent_hover"],
            font=ctk.CTkFont(size=13),
            command=self._pick_file,
        ).pack(side="right")

        # -- Overordnet mappe
        MeetingApp._field_label(inner, "Overordnet mappe")
        folder_row = ctk.CTkFrame(inner, fg_color="transparent")
        folder_row.pack(fill="x", pady=(0, 10))

        self.folder_var = ctk.StringVar()
        ctk.CTkEntry(
            folder_row,
            textvariable=self.folder_var,
            height=36,
            corner_radius=10,
            border_width=1,
            border_color=_CLR["card_border"],
            fg_color="#f8fafc",
            text_color=_CLR["text"],
            placeholder_text_color=_CLR["text_placeholder"],
            font=ctk.CTkFont(size=13),
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))

        ctk.CTkButton(
            folder_row,
            text="Gennemse ...",
            width=110,
            height=36,
            corner_radius=10,
            fg_color=_CLR["accent"],
            hover_color=_CLR["accent_hover"],
            font=ctk.CTkFont(size=13),
            command=self._pick_folder,
        ).pack(side="right")

        # -- Mødetype dropdown
        MeetingApp._field_label(inner, "Mødetype")
        _type_labels = [self._meeting_types[k]["navn"] for k in self._type_keys]
        self.type_var = ctk.StringVar(value=self._meeting_types[self._type_key]["navn"])
        self.type_combo = ctk.CTkComboBox(
            inner,
            variable=self.type_var,
            values=_type_labels,
            height=36,
            corner_radius=10,
            border_width=1,
            border_color=_CLR["card_border"],
            state="readonly",
            fg_color="#f8fafc",
            button_color=_CLR["accent"],
            button_hover_color=_CLR["accent_hover"],
            text_color=_CLR["text"],
            dropdown_fg_color=_CLR["card"],
            dropdown_hover_color="#e0e7ff",
            font=ctk.CTkFont(size=13),
            command=self._on_type_selected,
        )
        self.type_combo.pack(fill="x", pady=(0, 10))

        # -- Mødenavn
        MeetingApp._field_label(inner, "Mødenavn")
        self.name_var = ctk.StringVar()
        ctk.CTkEntry(
            inner,
            textvariable=self.name_var,
            height=36,
            corner_radius=10,
            border_width=1,
            border_color=_CLR["card_border"],
            fg_color="#f8fafc",
            text_color=_CLR["text"],
            placeholder_text="Udfyldes automatisk fra filnavnet",
            placeholder_text_color=_CLR["text_placeholder"],
            font=ctk.CTkFont(size=13),
        ).pack(fill="x", pady=(0, 10))

        # -- Dato + Deltagere
        row2 = ctk.CTkFrame(inner, fg_color="transparent")
        row2.pack(fill="x", pady=(0, 10))
        row2.columnconfigure(0, weight=1)
        row2.columnconfigure(1, weight=2)

        date_frame = ctk.CTkFrame(row2, fg_color="transparent")
        date_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        MeetingApp._field_label(date_frame, "Dato")
        self.date_var = ctk.StringVar(value=datetime.now().strftime("%d-%m-%Y"))
        ctk.CTkEntry(
            date_frame,
            textvariable=self.date_var,
            height=36,
            corner_radius=10,
            border_width=1,
            border_color=_CLR["card_border"],
            fg_color="#f8fafc",
            text_color=_CLR["text"],
            placeholder_text_color=_CLR["text_placeholder"],
            font=ctk.CTkFont(size=13),
        ).pack(fill="x")

        att_frame = ctk.CTkFrame(row2, fg_color="transparent")
        att_frame.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        MeetingApp._field_label(att_frame, "Deltagere (komma-adskilt)")
        self.attendees_var = ctk.StringVar(value=", ".join(DEFAULT_ATTENDEES))
        ctk.CTkEntry(
            att_frame,
            textvariable=self.attendees_var,
            height=36,
            corner_radius=10,
            border_width=1,
            border_color=_CLR["card_border"],
            fg_color="#f8fafc",
            text_color=_CLR["text"],
            placeholder_text_color=_CLR["text_placeholder"],
            font=ctk.CTkFont(size=13),
        ).pack(fill="x")

        # -- Transkriptions-motor (segmented control)
        MeetingApp._field_label(inner, "Transkription")
        self.engine_var = ctk.StringVar(value=DEFAULT_ENGINE)

        engine_frame = ctk.CTkFrame(inner, fg_color="#f1f5f9", corner_radius=10)
        engine_frame.pack(fill="x", pady=(0, 4))

        for val, label in (
            ("hviske", "Hviske (lokalt)"),
            ("gemini", "Gemini (cloud)"),
        ):
            btn = ctk.CTkButton(
                engine_frame,
                text=label,
                height=32,
                corner_radius=8,
                font=ctk.CTkFont(size=13),
                fg_color="transparent",
                text_color=_CLR["text_secondary"],
                hover_color="#e0e7ff",
                command=lambda v=val: self._select_engine(v),
            )
            btn.pack(side="left", fill="x", expand=True, padx=3, pady=3)
            self._engine_buttons[val] = btn

        self._engine_hint = ctk.CTkLabel(
            inner,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=_CLR["text_secondary"],
            anchor="w",
            justify="left",
        )
        self._engine_hint.pack(fill="x", pady=(0, 10))
        self._select_engine(self.engine_var.get())

        # -- Generer referat
        self.minutes_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            inner,
            text="Generer referat bagefter",
            variable=self.minutes_var,
            font=ctk.CTkFont(size=13),
            text_color=_CLR["text"],
            fg_color=_CLR["accent"],
            hover_color=_CLR["accent_hover"],
            corner_radius=6,
            border_width=2,
            border_color=_CLR["card_border"],
        ).pack(anchor="w", pady=(2, 0))

        # ── Knapper ─────────────────────────────────────────────────
        btn_row = ctk.CTkFrame(outer, fg_color="transparent")
        btn_row.pack(fill="x", padx=24, pady=(16, 0))

        self._start_btn = ctk.CTkButton(
            btn_row,
            text="Transkribér",
            height=44,
            corner_radius=12,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=_CLR["accent"],
            hover_color=_CLR["accent_hover"],
            command=self._start,
        )
        self._start_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self._stop_btn = ctk.CTkButton(
            btn_row,
            text="Stop",
            width=110,
            height=44,
            corner_radius=12,
            font=ctk.CTkFont(size=15),
            fg_color="transparent",
            border_width=1,
            border_color=_CLR["card_border"],
            text_color=_CLR["text"],
            hover_color="#e0e7ff",
            command=self._stop,
            state="disabled",
        )
        self._stop_btn.pack(side="right")

        # ── Status / log ────────────────────────────────────────────
        log_card = ctk.CTkFrame(
            outer,
            fg_color=_CLR["card"],
            corner_radius=16,
            border_width=1,
            border_color=_CLR["card_border"],
        )
        log_card.pack(fill="both", expand=True, padx=24, pady=(16, 24))

        self.status_var = ctk.StringVar(value="Klar. Vælg en lydfil.")
        ctk.CTkLabel(
            log_card,
            textvariable=self.status_var,
            font=ctk.CTkFont(size=13),
            text_color=_CLR["text_secondary"],
            anchor="w",
        ).pack(fill="x", padx=16, pady=(14, 6))

        self.log = ctk.CTkTextbox(
            log_card,
            height=160,
            corner_radius=10,
            border_width=1,
            border_color=_CLR["card_border"],
            fg_color="#f8fafc",
            font=ctk.CTkFont(family="SF Mono", size=12),
            text_color=_CLR["text"],
            wrap="word",
            state="disabled",
        )
        self.log.pack(fill="both", expand=True, padx=16, pady=(0, 16))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _select_engine(self, value: str):
        self.engine_var.set(value)
        for v, btn in self._engine_buttons.items():
            if v == value:
                btn.configure(
                    fg_color=_CLR["accent"],
                    text_color="#ffffff",
                    hover_color=_CLR["accent_hover"],
                )
            else:
                btn.configure(
                    fg_color="transparent",
                    text_color=_CLR["text_secondary"],
                    hover_color="#e0e7ff",
                )
        if value == "gemini":
            self._engine_hint.configure(
                text=(
                    "Gemini 2.5 Pro (cloud). Bedst til danske navne og fagtermer; "
                    "auto-chunking ved lange filer. Sender lyd til Google."
                )
            )
        else:
            self._engine_hint.configure(
                text="Hviske-v3 kører lokalt på din Mac (offline, ingen cloud-data)."
            )

    # ------------------------------------------------------------------
    # Mødetype
    # ------------------------------------------------------------------

    def _key_for_label(self, label: str) -> str:
        for k in self._type_keys:
            if self._meeting_types[k]["navn"] == label:
                return k
        return self._type_keys[0]

    def _on_type_selected(self, label: str):
        self._type_key = self._key_for_label(label)
        mtype = self._meeting_types[self._type_key]
        self.name_var.set(f"{mtype['navn']} {self.date_var.get()}")
        state = load_state()
        attendees = meeting_tool.resolve_type_attendees(
            self._type_key, mtype,
            state.get("attendees_by_type", {}),
            DEFAULT_ATTENDEES,
        )
        self.attendees_var.set(", ".join(attendees))

    def refresh_meeting_types(self):
        """Genindlæs mødetyper fra disk og opdatér dropdownen.

        Kaldes når en type oprettes/redigeres/slettes i Mødetyper-fanen, så
        listen ikke kun afspejler tilstanden ved appens opstart. Bevarer den
        nuværende markering hvis den stadig findes; ellers falder den tilbage
        til første type."""
        self._meeting_types = meeting_tool.load_meeting_types(CONFIG_DIR)
        self._type_keys = list(self._meeting_types)
        if self._type_key not in self._meeting_types:
            self._type_key = self._type_keys[0]
        labels = [self._meeting_types[k]["navn"] for k in self._type_keys]
        self.type_combo.configure(values=labels)
        self.type_var.set(self._meeting_types[self._type_key]["navn"])

    def _load_defaults(self):
        state = load_state()
        last = state.get("last_folder")
        if last and Path(last).exists():
            self.folder_var.set(last)
        else:
            self.folder_var.set(str(MEETINGS_DIR))
        last_attendees = state.get("attendees")
        if isinstance(last_attendees, list) and last_attendees:
            self.attendees_var.set(", ".join(last_attendees))
        last_engine = state.get("engine")
        if last_engine in ("hviske", "gemini"):
            self._select_engine(last_engine)
        last_minutes = state.get("generate_minutes")
        if isinstance(last_minutes, bool):
            self.minutes_var.set(last_minutes)
        # Auto-udfyld navn + deltagere ud fra valgt mødetype
        self._on_type_selected(self.type_var.get())

    @staticmethod
    def _date_from_name(stem: str) -> str | None:
        import re
        m = re.search(r"(\d{2}-\d{2}-\d{4})", stem)
        return m.group(1) if m else None

    def _pick_file(self):
        if self.processing:
            return
        chosen = filedialog.askopenfilename(
            title="Vælg lydfil",
            initialdir=str(Path.home()),
            filetypes=self.AUDIO_FILETYPES,
        )
        if not chosen:
            return
        path = Path(chosen)
        self._input_path = path
        self.file_var.set(str(path))
        # Auto-udfyld navn fra filstammen (saniteret)
        safe = path.stem.replace("/", "-").replace(":", "-").strip()
        if safe:
            self.name_var.set(safe)
        # Auto-udfyld dato hvis filnavnet indeholder en
        found = self._date_from_name(path.stem)
        if found:
            self.date_var.set(found)
        self.status_var.set(f"Valgt: {path.name}")

    def _pick_folder(self):
        if self.processing:
            return
        current = self.folder_var.get()
        initial = current if os.path.isdir(current) else str(MEETINGS_DIR)
        chosen = filedialog.askdirectory(
            title="Vælg mappe til transkription og referat",
            initialdir=initial,
        )
        if chosen:
            self.folder_var.set(chosen)

    # ------------------------------------------------------------------
    # Kørsel
    # ------------------------------------------------------------------

    def _start(self):
        if self.processing:
            return
        if not self._input_path or not self._input_path.exists():
            messagebox.showerror("Fejl", "Vælg en lydfil der findes.")
            return
        folder = self.folder_var.get().strip()
        name = self.name_var.get().strip()
        date = self.date_var.get().strip()
        if not folder:
            messagebox.showerror("Fejl", "Vælg en overordnet mappe.")
            return
        if not name:
            messagebox.showerror("Fejl", "Giv mødet et navn.")
            return
        if not date:
            messagebox.showerror("Fejl", "Angiv en dato.")
            return

        safe_name = name.replace("/", "-").replace(":", "-").strip()
        if not safe_name:
            messagebox.showerror("Fejl", "Mødenavnet kan ikke bruges som mappenavn.")
            return

        output_dir = Path(folder) / safe_name
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Fejl", f"Kunne ikke oprette mappe: {e}")
            return

        attendees = [
            a.strip()
            for a in self.attendees_var.get().replace(";", ",").split(",")
            if a.strip()
        ] or list(DEFAULT_ATTENDEES)
        engine = self.engine_var.get()
        make_minutes = self.minutes_var.get()
        meeting_type = self._meeting_types[self._type_key]

        # Husk valg til næste gang — merge med eksisterende state
        _saved = load_state()
        _abt = _saved.get("attendees_by_type", {})
        _abt[self._type_key] = attendees
        save_state({
            **_saved,
            "last_folder": folder,
            "attendees": attendees,
            "engine": engine,
            "generate_minutes": make_minutes,
            "meeting_type": self._type_key,
            "attendees_by_type": _abt,
        })

        # Ryd log
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

        self._set_processing(True)
        self.stop_event = threading.Event()
        self._log(f"=== {safe_name} ===")
        self._log(f"Fil: {self._input_path.name}")
        self._log(f"Mappe: {output_dir}")
        self._log(f"Motor: {engine}")
        self._log(f"Deltagere: {', '.join(attendees)}")
        self._log("")

        self.worker_thread = threading.Thread(
            target=self._run,
            args=(self._input_path, output_dir, date, safe_name,
                  attendees, engine, make_minutes, meeting_type),
            daemon=True,
        )
        self.worker_thread.start()

    def _run(self, input_path, output_dir, date, name, attendees, engine,
             make_minutes, meeting_type=None):
        try:
            self._ui_status("Konverterer lyd til WAV ...")
            engine_label = "Gemini" if engine == "gemini" else "Hviske"
            self._ui_status(
                f"Transkriberer med {engine_label} – dette kan tage et stykke tid ..."
            )
            wav_path, transcript, minutes = meeting_tool.transcribe_file(
                input_path=input_path,
                output_dir=output_dir,
                date=date,
                name_base=name,
                attendees=attendees,
                engine=engine,
                make_minutes=make_minutes,
                model_size=DEFAULT_MODEL,
                gemini_model=DEFAULT_GEMINI_MODEL,
                on_status=self._ui_status,
                stop_event=self.stop_event,
                meeting_type=meeting_type,
            )
            self.ui_queue.put(("done", str(output_dir), name, bool(minutes)))
        except Exception as e:
            self.ui_queue.put(("error", str(e)))

    def _stop(self):
        if not self.processing:
            return
        self.stop_event.set()
        self.status_var.set("Stopper ...")
        self._log("\n>>> Stop anmodet (virker for Gemini-chunks) ...")

    # ------------------------------------------------------------------
    # Queue / UI
    # ------------------------------------------------------------------

    def _ui_status(self, msg: str):
        self.ui_queue.put(("status", msg))

    def _drain_queue(self):
        try:
            while True:
                item = self.ui_queue.get_nowait()
                kind = item[0]
                if kind == "status":
                    self.status_var.set(item[1])
                    self._log(item[1])
                elif kind == "done":
                    _, out_dir, name, had_minutes = item
                    self._set_processing(False)
                    self.status_var.set("Færdig.")
                    self._log(f"\n=== FÆRDIG ===\nFiler gemt i: {out_dir}")
                    referat_linje = (
                        f"  - Referat {name}.md\n" if had_minutes else ""
                    )
                    messagebox.showinfo(
                        "Færdig",
                        f"Transkriptionen er gemt i:\n{out_dir}\n\n"
                        f"Filer:\n"
                        f"  - {name}.wav\n"
                        f"  - Transkription {name}.md\n"
                        f"{referat_linje}",
                    )
                elif kind == "error":
                    self._set_processing(False)
                    self.status_var.set("Fejl.")
                    self._log(f"\nFEJL: {item[1]}")
                    messagebox.showerror("Fejl under transkription", item[1])
        except queue.Empty:
            pass
        finally:
            self.parent.after(100, self._drain_queue)

    def _set_processing(self, processing: bool):
        self.processing = processing
        if processing:
            self._start_btn.configure(state="disabled", text="Arbejder ...")
            self._stop_btn.configure(state="normal")
        else:
            self._start_btn.configure(state="normal", text="Transkribér")
            self._stop_btn.configure(state="disabled")

    def _log(self, msg: str):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")


def _first_run_setup(root: ctk.CTk) -> None:
    """Modal første-start-dialog der beder om Gemini-nøglen.
    Kan springes over — lokal Hviske-transkription virker uden nøgle."""
    dialog = ctk.CTkToplevel(root)
    dialog.title("Mødeværktøj — opsætning")
    dialog.resizable(False, False)
    dialog.update_idletasks()
    dialog.grab_set()

    ctk.CTkLabel(
        dialog,
        text=(
            "Velkommen til Mødeværktøjet!\n"
            "Indsæt din Gemini-nøgle for at bruge cloud-transkription og "
            "referat-generering. Du kan også fortsætte uden og kun bruge "
            "lokal transkription."
        ),
        wraplength=360,
        justify="left",
    ).grid(row=0, column=0, columnspan=2, padx=20, pady=(20, 12), sticky="w")

    ctk.CTkLabel(dialog, text="Gemini-nøgle:").grid(
        row=1, column=0, padx=(20, 8), pady=6, sticky="e"
    )
    gemini_entry = ctk.CTkEntry(dialog, width=240, show="*")
    gemini_entry.grid(row=1, column=1, padx=(0, 20), pady=6, sticky="w")

    ctk.CTkLabel(
        dialog,
        text="Få en gratis nøgle på aistudio.google.com/apikey",
        font=ctk.CTkFont(size=11),
        text_color="gray",
    ).grid(row=2, column=0, columnspan=2, padx=20, pady=(0, 10), sticky="w")

    def _save():
        _write_env({"GEMINI_API_KEY": gemini_entry.get().strip()})
        dialog.destroy()

    btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
    btn_row.grid(row=3, column=0, columnspan=2, padx=20, pady=(12, 20))
    ctk.CTkButton(btn_row, text="Gem og fortsæt", command=_save).pack(
        side="left", padx=6
    )
    ctk.CTkButton(
        btn_row,
        text="Fortsæt uden nøgle",
        fg_color="transparent",
        text_color=_CLR["text_secondary"],
        border_width=1,
        command=dialog.destroy,
    ).pack(side="left", padx=6)

    root.wait_window(dialog)


def _start_update_check(root, app) -> None:
    """Baggrundstråd: tjek GitHub for en nyere version og hent+verificér den.
    Når en opdatering er klar, planlægges anvendelse på GUI-tråden (idle-gated).
    Kaldes kun på frosset Windows."""
    import tempfile
    import threading
    import updater

    def worker():
        rel = updater.check_for_update()
        if not rel:
            return
        try:
            staging = updater.download_and_stage(
                rel, Path(tempfile.gettempdir()) / "modevaerktoj-update"
            )
        except updater.UpdateError as e:
            print(f"OTA: opdatering kunne ikke hentes: {e}", file=sys.stderr)
            return
        root.after(0, lambda: _maybe_apply_update(root, app, staging))

    threading.Thread(target=worker, daemon=True).start()


def _maybe_apply_update(root, app, staging) -> None:
    """Anvend en hentet opdatering — men ALDRIG midt i en optagelse eller
    transkription. Er appen optaget, prøv igen om lidt."""
    import updater

    # Optaget = live-optagelse, live-transkription ELLER en fil-transkription i
    # TranscribeFileTab (eget processing-flag). Alle tre må afbryde en genstart.
    transcribe_tab = getattr(app, "_transcribe_tab", None)
    busy = bool(getattr(app, "recording", False)
                or getattr(app, "transcribing", False)
                or getattr(transcribe_tab, "processing", False))
    if busy:
        root.after(30_000, lambda: _maybe_apply_update(root, app, staging))
        return
    try:
        updater.apply_and_restart(staging)  # genstarter appen; vender ikke retur
    except Exception as e:  # pragma: no cover
        print(f"OTA: opdatering kunne ikke anvendes: {e}", file=sys.stderr)


def main():
    meeting_tool.seed_user_config()

    # CustomTkinter global appearance
    ctk.set_appearance_mode("system")    # follows macOS dark/light
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()
    if _needs_setup():
        _first_run_setup(root)   # modal; kan springes over (Hviske virker uden nøgle)

    # Set window icon if .icns exists in app bundle
    icns_path = TOOL_DIR / "Mødeværktøj.app" / "Contents" / "Resources" / "AppIcon.icns"
    if icns_path.exists():
        try:
            root.iconbitmap(str(icns_path))
        except Exception:
            pass

    # Semi-transparent vindue (Liquid Glass effekt)
    root.attributes("-alpha", 0.92)

    app = MeetingApp(root)

    # OTA: tjek for ny version i baggrunden — kun på frosset Windows-bundle.
    import app_paths
    if app_paths.is_frozen() and sys.platform == "win32":
        _start_update_check(root, app)

    # Bring vindue frem i front (vigtig naar launched fra .app bundle)
    root.lift()
    root.attributes("-topmost", True)
    root.after(200, lambda: root.attributes("-topmost", False))
    root.focus_force()

    root.mainloop()


if __name__ == "__main__":
    main()
