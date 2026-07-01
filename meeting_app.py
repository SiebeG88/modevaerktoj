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
import subprocess
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
    # — Ny "Sidebar App"-palet —
    "sidebar_bg":          "#1b2a4a",  # mørk marineblå sidebar
    "sidebar_icon":        "#7f92b8",  # inaktivt sidebar-ikon
    "sidebar_icon_active": "#ffffff",  # aktivt sidebar-ikon
    "bg":                  "#f7f8fa",  # lys arbejdsflade
    "card":                "#ffffff",  # hvide kort
    "card_border":         "#e6e8ec",
    "accent":              "#3b6fe0",  # blå accent
    "accent_hover":        "#2f5ec9",
    "text":                "#1b2a4a",  # primær tekst
    "text_secondary":      "#5b6474",  # sekundær tekst
    "text_placeholder":    "#8a93a3",
    # — Optage-tilstand —
    "rec_idle":            "#e5382b",  # rød rec-prik (idle)
    "rec_active":          "#e5382b",  # rød (optager)
    "rec_ring":            "#f6b6b0",  # lys rød ring (blink)
    "stop_blue":           "#1b2a4a",
    "stop_hover":          "#0f1c34",
    "success":             "#2e7d5b",
    # — Historik-badges —
    "badge_ok":            "#2e7d5b", "badge_ok_bg":   "#e3f5ec",
    "badge_busy":          "#8a6d00", "badge_busy_bg": "#fbf3d8",
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
        self._oval(cx, cy, r + 4, fill="#e6e8ec", outline="")
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
        self._oval(cx, cy, r, fill=_CLR["rec_active"], outline="#c62828", width=1)

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
        self._oval(cx, cy, r + 4, fill="#e6e8ec", outline="")
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


class Sidebar(ctk.CTkFrame):
    """Fast venstre-navigation med tegnede ikoner. Kalder on_select(view_name)
    ved klik. set_active(name) markerer det aktive punkt (lyst ikon + label)."""

    WIDTH = 78

    def __init__(self, master, items, on_select, **kw):
        super().__init__(master, width=self.WIDTH, corner_radius=0,
                         fg_color=_CLR["sidebar_bg"], **kw)
        self.pack_propagate(False)
        self._on_select = on_select
        self._items = {}   # name -> (holder, canvas, label)
        # Logo-plads
        logo = ctk.CTkFrame(self, width=32, height=32, corner_radius=9,
                            fg_color=_CLR["accent"])
        logo.pack(pady=(16, 18))
        logo.pack_propagate(False)
        for name, label in items:
            holder = ctk.CTkFrame(self, fg_color="transparent", corner_radius=10)
            holder.pack(fill="x", padx=8, pady=3)
            cv = ctk.CTkCanvas(holder, width=28, height=28, highlightthickness=0,
                               bd=0, bg=self._hex(_CLR["sidebar_bg"]))
            cv.pack(pady=(6, 0))
            lbl = ctk.CTkLabel(holder, text=label, font=ctk.CTkFont(size=10),
                               text_color=_CLR["sidebar_icon"])
            lbl.pack(pady=(0, 6))
            for w in (holder, cv, lbl):
                w.bind("<Button-1>", lambda e, n=name: self._on_select(n))
                w.configure(cursor="hand2")
            self._items[name] = (holder, cv, lbl)
            self._draw_icon(cv, name, _CLR["sidebar_icon"])

    @staticmethod
    def _hex(colour):
        """CTkCanvas' bg vil have en almindelig hex-streng (ikke et tema-tuple)."""
        return colour if isinstance(colour, str) else colour[0]

    @staticmethod
    def _draw_icon(canvas, name, colour):
        canvas.delete("all")
        if name == "optag":            # cirkel + prik (rec)
            canvas.create_oval(4, 4, 24, 24, outline=colour, width=2)
            canvas.create_oval(11, 11, 17, 17, fill=colour, outline="")
        elif name == "transkriber":    # dokument med linjer
            canvas.create_rectangle(6, 3, 22, 25, outline=colour, width=2)
            for y in (9, 14, 19):
                canvas.create_line(9, y, 19, y, fill=colour, width=2)
        elif name == "historik":       # søjlegraf/akse
            canvas.create_line(4, 24, 4, 6, fill=colour, width=2)
            canvas.create_line(4, 24, 24, 24, fill=colour, width=2)
            canvas.create_line(7, 18, 12, 13, fill=colour, width=2)
            canvas.create_line(12, 13, 16, 16, fill=colour, width=2)
            canvas.create_line(16, 16, 23, 8, fill=colour, width=2)
        elif name == "indstillinger":  # tandhjul (forenklet: to cirkler)
            canvas.create_oval(6, 6, 22, 22, outline=colour, width=2)
            canvas.create_oval(11, 11, 17, 17, outline=colour, width=2)

    def set_active(self, name):
        for n, (holder, cv, lbl) in self._items.items():
            active = (n == name)
            colour = _CLR["sidebar_icon_active"] if active else _CLR["sidebar_icon"]
            holder.configure(fg_color="#26365a" if active else "transparent")
            lbl.configure(text_color=colour)
            self._draw_icon(cv, n, colour)


class MeetingApp:
    def __init__(self, root: ctk.CTk):
        self.root = root
        self.root.title("Modevarktoej")
        # "Restore"-størrelse når vinduet gendannes fra maksimeret — rummelig.
        self.root.geometry("1100x820")
        self.root.minsize(880, 680)

        # State
        self.recording = False
        self.transcribing = False
        self._timer_running = False
        self.worker_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.start_time: float | None = None
        self.ui_queue: queue.Queue = queue.Queue()
        self.chunks_done = 0
        self._mini_hud = None  # lille altid-øverst HUD ved minimering

        self._build_ui()
        self._refresh_devices()
        self._set_default_folder()

        # Centre window on screen, og åbn derefter maksimeret — brugeren
        # maksimerede ellers manuelt hver gang.
        self.root.update_idletasks()
        self._centre_window()
        try:
            self.root.state("zoomed")          # Windows/Linux
        except Exception:
            try:
                self.root.attributes("-zoomed", True)
            except Exception:
                pass

        # Poll UI queue fra baggrundstråde
        self.root.after(100, self._drain_ui_queue)

        # Timer-loop
        self.root.after(500, self._tick_timer)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Åbn wizarden automatisk ved opstart, så brugeren guides fra start.
        # Køres via after() så hovedvinduet er tegnet og centreret først —
        # ellers "blinker" appen som om den åbner to gange.
        self.root.after(400, lambda: self._open_wizard(0))

        # Vis mini-HUD når vinduet minimeres mens der optages/transkriberes.
        self.root.bind("<Unmap>", self._on_minimize)
        self.root.bind("<Map>", self._on_restore)

        # Tastatur-genveje: Mellemrum = Start/Stop, Esc = minimér til HUD.
        self.root.bind("<space>", self._on_space_key)
        self.root.bind("<Escape>", self._on_escape_key)

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

        # Navigations-shell: fast venstre-sidebar + indholdsflade der skifter view.
        shell = ctk.CTkFrame(self.root, fg_color=_CLR["bg"])
        shell.pack(fill="both", expand=True)

        items = [("optag", "Optag"), ("transkriber", "Fil"),
                 ("historik", "Historik"), ("indstillinger", "Indstil.")]
        self._sidebar = Sidebar(shell, items, self._show_view)
        self._sidebar.pack(side="left", fill="y")

        self._content = ctk.CTkFrame(shell, fg_color=_CLR["bg"])
        self._content.pack(side="left", fill="both", expand=True)

        # Ét view = én frame i _content. Vis/skjul via _show_view.
        self._views = {n: ctk.CTkFrame(self._content, fg_color=_CLR["bg"])
                       for n, _ in items}
        self._current_view = None

        # Alle delte variabler oprettes FØRST, så hjælpe-metoder (motorvalg,
        # systemlyd, mødetype) virker uanset hvilke widgets der p.t. er bygget.
        self.folder_var = ctk.StringVar()
        self.name_var = ctk.StringVar()
        self.date_var = ctk.StringVar(value=datetime.now().strftime("%d-%m-%Y"))
        self.attendees_var = ctk.StringVar(value=", ".join(DEFAULT_ATTENDEES))
        self.device_var = ctk.StringVar()
        self.system_audio_var = ctk.BooleanVar(value=False)
        self.engine_var = ctk.StringVar(value=DEFAULT_ENGINE)
        self.minutes_var = ctk.BooleanVar(value=True)
        self.type_var = ctk.StringVar(value=self._meeting_types[self._type_key]["navn"])
        # Mødeform styrer systemlyd-default: fysisk = kun mik, online/telefon = + modpart
        self.meeting_form_var = ctk.StringVar(value="fysisk")
        self._engine_buttons = {}
        self._wizard = None

        # Optag-viewet er en ren OPTAGE-skærm; al per-møde-opsætning sker i guiden.
        self._build_record_screen(self._views["optag"])

        # Transkribér fil-viewet.
        self._transcribe_tab = TranscribeFileTab(self._views["transkriber"])

        # Historik-viewet.
        self._historik_tab = HistorikTab(self._views["historik"], self)

        # Indstillinger-viewet: en sektions-vælger øverst + tre sektioner
        # (Generelt / Ordliste / Mødetyper). Sidebar forbliver enkel.
        settings_view = self._views["indstillinger"]
        seg = ctk.CTkSegmentedButton(
            settings_view, values=["Generelt", "Ordliste", "Mødetyper"],
            fg_color=_CLR["card"], selected_color=_CLR["accent"],
            selected_hover_color=_CLR["accent_hover"],
            unselected_color=_CLR["card"], unselected_hover_color=_CLR["card_border"],
            text_color=_CLR["text"], command=self._on_settings_seg)
        seg.pack(fill="x", padx=24, pady=(18, 8))
        self._settings_seg = seg
        self._settings_sections = {
            "generelt": ctk.CTkFrame(settings_view, fg_color=_CLR["bg"]),
            "ordliste": ctk.CTkFrame(settings_view, fg_color=_CLR["bg"]),
            "modetyper": ctk.CTkFrame(settings_view, fg_color=_CLR["bg"]),
        }
        self._settings_current = None
        self._build_settings_tab(self._settings_sections["generelt"])
        self._vocab_tab = VocabularyTab(self._settings_sections["ordliste"])
        self._types_tab = MeetingTypesTab(
            self._settings_sections["modetyper"],
            on_change=self._on_meeting_types_changed,
        )
        seg.set("Generelt")
        self._show_settings_section("generelt")

        # Global musehjul-scroll: ét bind_all-handler der finder den
        # scrollbare canvas under markøren og scroller den. Robust mod at
        # hjul-events lander på child-widgets (label/frame) i stedet for canvas.
        self._setup_global_scroll()

        # Vis startskærmen.
        self._show_view("optag")

    def _show_view(self, name: str) -> None:
        """Vis ét view i indholdsfladen, skjul resten, og markér i sidebar."""
        if name not in self._views:
            return
        for n, frame in self._views.items():
            if n == name:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()
        self._current_view = name
        self._sidebar.set_active(name)
        # Genopfrisk Historik når man går ind i det view.
        if name == "historik" and getattr(self, "_historik_tab", None) is not None:
            try:
                self._historik_tab.refresh()
            except Exception:
                pass

    def _on_settings_seg(self, value: str) -> None:
        self._show_settings_section(
            {"Generelt": "generelt", "Ordliste": "ordliste",
             "Mødetyper": "modetyper"}.get(value, "generelt"))

    def _show_settings_section(self, name: str) -> None:
        """Vis én sektion i Indstillinger, skjul resten."""
        if name not in self._settings_sections:
            return
        for n, frame in self._settings_sections.items():
            if n == name:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()
        self._settings_current = name

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

    # ------------------------------------------------------------------
    # Optage-skærm (Optag-fanen) + Indstillinger-fane + guide
    # ------------------------------------------------------------------

    def _build_record_screen(self, parent):
        """Optag-fanen: ren optage-skærm med konfig-oversigt + stor knap."""
        parent.configure(fg_color=_CLR["bg"])
        # Fast bundlinje med Start-knap + timer — ALTID synlig, uafhængig af scroll.
        # Pakkes FØR det scrollbare indhold (side=bottom) så den reserverer pladsen.
        bottom_bar = ctk.CTkFrame(parent, fg_color=_CLR["bg"])
        bottom_bar.pack(side="bottom", fill="x")
        self._bottom_bar = bottom_bar

        outer = ctk.CTkScrollableFrame(
            parent, fg_color=_CLR["bg"],
            scrollbar_button_color=_CLR["card_border"],
            scrollbar_button_hover_color=_CLR["accent"],
        )
        outer.pack(side="top", fill="both", expand=True)

        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.pack(fill="x", padx=28, pady=(24, 4))
        ctk.CTkLabel(
            header, text="Mødeværktøj",
            font=ctk.CTkFont(family="SF Pro Display", size=32, weight="bold"),
            text_color=_CLR["text"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            header, text="Optag  ·  Transkribér  ·  Referat",
            font=ctk.CTkFont(family="SF Pro Text", size=14),
            text_color=_CLR["text_secondary"],
        ).pack(anchor="w", pady=(2, 0))

        # — Aktiv-optagelse-panel (skjult i idle; vises mens der optages) —
        self._active_panel = ctk.CTkFrame(outer, fg_color=_CLR["bg"])
        self._rec_pill = ctk.CTkLabel(
            self._active_panel, text="●  OPTAGER",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=_CLR["rec_active"])
        self._rec_pill.pack(pady=(28, 6))
        self._big_timer = ctk.CTkLabel(
            self._active_panel, text="00:00:00",
            font=ctk.CTkFont(family="SF Mono", size=46, weight="bold"),
            text_color=_CLR["text"])
        self._rec_subtitle = ctk.CTkLabel(
            self._active_panel, text="", font=ctk.CTkFont(size=14),
            text_color=_CLR["text_secondary"])
        # timer_var oprettes længere nede; kobles på i _build_record_screen-slut.
        self._big_timer.pack()
        self._rec_subtitle.pack(pady=(2, 16))
        ctk.CTkButton(
            self._active_panel, text="■  Stop & lav referat",
            height=46, corner_radius=11, fg_color=_CLR["stop_blue"],
            hover_color=_CLR["stop_hover"], text_color="#ffffff",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._toggle_recording).pack(pady=(0, 8))

        # — Konfig-oversigtskort (chip-baseret, 2 kolonner)
        card = ctk.CTkFrame(
            outer, fg_color=_CLR["card"], corner_radius=16,
            border_width=1, border_color=_CLR["card_border"],
        )
        card.pack(fill="x", padx=24, pady=(16, 0))
        self._idle_card = card
        self._sum_labels = {}

        card_head = ctk.CTkFrame(card, fg_color="transparent")
        card_head.pack(fill="x", padx=20, pady=(16, 4))
        head_txt = ctk.CTkFrame(card_head, fg_color="transparent")
        head_txt.pack(side="left", anchor="w")
        ctk.CTkLabel(
            head_txt, text="Klar til optagelse",
            font=ctk.CTkFont(family="SF Pro Display", size=16, weight="bold"),
            text_color=_CLR["text"], anchor="w",
        ).pack(anchor="w")
        # Mødets navn som diskret undertitel (opdateres af _refresh_summary).
        name_lbl = ctk.CTkLabel(
            head_txt, text="—", anchor="w",
            font=ctk.CTkFont(size=13), text_color=_CLR["text_secondary"],
        )
        name_lbl.pack(anchor="w")
        self._sum_labels["name"] = name_lbl
        ctk.CTkButton(
            card_head, text="+ Ny optagelse", height=32, corner_radius=8,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color=_CLR["accent"], hover_color=_CLR["accent_hover"],
            command=lambda: self._open_wizard(0),
        ).pack(side="right")

        # 2-kolonne chip-gitter — hver chip er klikbar og hopper til rette trin.
        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=20, pady=(10, 4))
        grid.grid_columnconfigure(0, weight=1, uniform="chip")
        grid.grid_columnconfigure(1, weight=1, uniform="chip")

        def _chip(key, icon, on_click, r, c):
            chip = ctk.CTkFrame(
                grid, fg_color=_CLR["bg"], corner_radius=10,
                border_width=1, border_color=_CLR["card_border"],
            )
            chip.grid(row=r, column=c, sticky="ew",
                      padx=(0, 6) if c == 0 else (6, 0), pady=4)
            ic = ctk.CTkLabel(
                chip, text=icon, width=22,
                font=ctk.CTkFont(family="Segoe UI Emoji", size=15),
            )
            ic.pack(side="left", padx=(10, 2), pady=9)
            val = ctk.CTkLabel(
                chip, text="—", anchor="w", justify="left",
                font=ctk.CTkFont(size=13, weight="bold"), text_color=_CLR["text"],
            )
            val.pack(side="left", fill="x", expand=True, padx=(2, 10), pady=9)

            def _still_inside():
                # Peger musen stadig et sted inde i chippen (eller et af dens
                # børn)? Bruges til at undgå flimmer når man krydser fra chip
                # til underetiket — <Leave> fyrer ellers falsk.
                x, y = chip.winfo_pointerxy()
                w = chip.winfo_containing(x, y)
                while w is not None:
                    if w == chip:
                        return True
                    w = w.master
                return False

            def _enter(_e):
                chip.configure(border_color=_CLR["accent"])

            def _leave(_e):
                if not _still_inside():
                    chip.configure(border_color=_CLR["card_border"])

            for w in (chip, ic, val):
                try:
                    w.configure(cursor="hand2")
                except Exception:
                    pass
                w.bind("<Button-1>", lambda e, f=on_click: f())
                w.bind("<Enter>", _enter)
                w.bind("<Leave>", _leave)
            self._sum_labels[key] = val

        _chip("form", "🗓", lambda: self._open_wizard(0), 0, 0)
        _chip("type", "🏷", lambda: self._open_wizard(1), 0, 1)
        _chip("attendees", "👥", lambda: self._open_wizard(2), 1, 0)
        _chip("engine", "⚙", lambda: self._open_wizard(3), 1, 1)
        _chip("mic", "🎙", lambda: self._show_view("indstillinger"), 2, 0)
        _chip("folder", "📁", lambda: self._show_view("indstillinger"), 2, 1)

        # Bundlinje: referat-checkbox + samlet Redigér-knap.
        foot = ctk.CTkFrame(card, fg_color="transparent")
        foot.pack(fill="x", padx=20, pady=(8, 16))
        ctk.CTkCheckBox(
            foot, text="Generér referat efter optagelse",
            variable=self.minutes_var, font=ctk.CTkFont(size=13),
            text_color=_CLR["text"], fg_color=_CLR["accent"],
            hover_color=_CLR["accent_hover"], corner_radius=6,
            border_width=2, border_color=_CLR["card_border"],
        ).pack(side="left")
        ctk.CTkButton(
            foot, text="✎  Redigér", width=96, height=30, corner_radius=8,
            font=ctk.CTkFont(size=12), fg_color="transparent",
            border_width=1, border_color=_CLR["card_border"],
            text_color=_CLR["accent"], hover_color="#eef2fb",
            command=lambda: self._open_wizard(0),
        ).pack(side="right")

        # — Hero record-knap + timer i den FASTE bundlinje (altid synlig)
        self.hero_btn = HeroButton(bottom_bar, command=self._toggle_recording, bg=_CLR["bg"])
        self.hero_btn.pack(anchor="center", pady=(8, 0))
        self.timer_var = ctk.StringVar(value="00:00:00")
        self.timer_label = ctk.CTkLabel(
            bottom_bar, textvariable=self.timer_var,
            font=ctk.CTkFont(family="SF Mono", size=22, weight="bold"),
            text_color=_CLR["text"],
        )
        self.timer_label.pack(anchor="center", pady=(2, 10))
        # Det store timer-tal på aktiv-panelet deler timer_var med bundlinjen.
        self._big_timer.configure(textvariable=self.timer_var)
        # Aktiv-panelet er skjult indtil der optages.
        self._active_panel.pack_forget()

        # — Status / log
        log_card = ctk.CTkFrame(
            outer, fg_color=_CLR["card"], corner_radius=16,
            border_width=1, border_color=_CLR["card_border"],
        )
        log_card.pack(fill="both", expand=True, padx=24, pady=(16, 24))
        self.status_var = ctk.StringVar(value="Klar. Tryk på den store knap for at starte.")
        ctk.CTkLabel(
            log_card, textvariable=self.status_var,
            font=ctk.CTkFont(size=13), text_color=_CLR["text_secondary"],
            anchor="w",
        ).pack(fill="x", padx=16, pady=(14, 6))
        self.log = ctk.CTkTextbox(
            log_card, height=110, corner_radius=10,
            border_width=1, border_color=_CLR["card_border"], fg_color="#ffffff",
            font=ctk.CTkFont(family="SF Mono", size=12), text_color=_CLR["text"],
            wrap="word", state="disabled",
        )
        self.log.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        # Hold oversigten synkron med variablerne (også ved inline-redigering).
        for _v in (self.name_var, self.attendees_var, self.type_var,
                   self.engine_var, self.folder_var, self.device_var,
                   self.system_audio_var):
            _v.trace_add("write", lambda *a: self._refresh_summary())

    def _setup_global_scroll(self):
        """Bind ÉT musehjul-handler globalt (bind_all). Det scroller scroll-området
        i den aktive fane, uanset hvilken child-widget (label/frame) hjul-eventet
        landede på — det var grunden til at scroll ikke virkede på macOS."""
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.root.bind_all(seq, self._global_wheel, add="+")

    @staticmethod
    def _scroll_canvas_in(widget):
        """Find CTkScrollableFrames canvas inde i en widget (fx en aktiv fane)."""
        if isinstance(widget, ctk.CTkScrollableFrame):
            return widget._parent_canvas
        for child in widget.winfo_children():
            cv = MeetingApp._scroll_canvas_in(child)
            if cv is not None:
                return cv
        return None

    def _global_wheel(self, event):
        # Scroll scroll-området i det AKTIVE view, uanset hvilken child-widget
        # hjul-eventet ramte (det er det brugeren forventer).
        try:
            active = self._views[self._current_view]
        except (KeyError, AttributeError):
            return
        cv = self._scroll_canvas_in(active)
        if cv is None:
            return
        first, last = cv.yview()
        if first <= 0.0 and last >= 1.0:
            return  # indholdet passer i ruden — intet at scrolle
        if getattr(event, "num", None) == 4:
            cv.yview_scroll(-3, "units")
        elif getattr(event, "num", None) == 5:
            cv.yview_scroll(3, "units")
        else:
            cv.yview_scroll(-3 if event.delta > 0 else 3, "units")

    def _build_settings_tab(self, parent):
        """Indstillinger-fanen: vedvarende/avancerede valg, grupperet i kort."""
        parent.configure(fg_color=_CLR["bg"])
        outer = ctk.CTkScrollableFrame(
            parent, fg_color=_CLR["bg"],
            scrollbar_button_color=_CLR["card_border"],
            scrollbar_button_hover_color=_CLR["accent"],
        )
        outer.pack(fill="both", expand=True)

        ctk.CTkLabel(
            outer, text="Indstillinger",
            font=ctk.CTkFont(family="SF Pro Display", size=28, weight="bold"),
            text_color=_CLR["text"],
        ).pack(anchor="w", padx=28, pady=(24, 4))
        ctk.CTkLabel(
            outer, text="Gemmes og genbruges på tværs af møder.",
            font=ctk.CTkFont(size=13), text_color=_CLR["text_secondary"],
        ).pack(anchor="w", padx=28, pady=(0, 12))

        def _card(title):
            c = ctk.CTkFrame(
                outer, fg_color=_CLR["card"], corner_radius=16,
                border_width=1, border_color=_CLR["card_border"],
            )
            c.pack(fill="x", padx=24, pady=(0, 16))
            inner = ctk.CTkFrame(c, fg_color="transparent")
            inner.pack(fill="x", padx=20, pady=16)
            ctk.CTkLabel(
                inner, text=title,
                font=ctk.CTkFont(family="SF Pro Display", size=15, weight="bold"),
                text_color=_CLR["text"],
            ).pack(anchor="w", pady=(0, 10))
            return inner

        # — Lagring
        store = _card("Lagring")
        self._field_label(store, "Overordnet mappe")
        folder_row = ctk.CTkFrame(store, fg_color="transparent")
        folder_row.pack(fill="x", pady=(0, 0))
        self._folder_entry = ctk.CTkEntry(
            folder_row, textvariable=self.folder_var, height=36, corner_radius=10,
            border_width=1, border_color=_CLR["card_border"], fg_color="#ffffff",
            text_color=_CLR["text"], placeholder_text_color=_CLR["text_placeholder"],
            font=ctk.CTkFont(size=13),
        )
        self._folder_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(
            folder_row, text="Gennemse …", width=110, height=36, corner_radius=10,
            fg_color=_CLR["accent"], hover_color=_CLR["accent_hover"],
            font=ctk.CTkFont(size=13), command=self._pick_folder,
        ).pack(side="right")

        # — Lyd
        audio = _card("Lyd")
        self._field_label(audio, "Standard-mikrofon")
        mic_row = ctk.CTkFrame(audio, fg_color="transparent")
        mic_row.pack(fill="x", pady=(0, 10))
        self.device_combo = ctk.CTkComboBox(
            mic_row, variable=self.device_var, height=36, corner_radius=10,
            border_width=1, border_color=_CLR["card_border"], fg_color="#ffffff",
            text_color=_CLR["text"], button_color=_CLR["accent"],
            button_hover_color=_CLR["accent_hover"], dropdown_fg_color=_CLR["card"],
            dropdown_hover_color="#eef2fb", font=ctk.CTkFont(size=13), state="readonly",
        )
        self.device_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ctk.CTkButton(
            mic_row, text="Opdater", width=80, height=36, corner_radius=10,
            fg_color="transparent", border_width=1, border_color=_CLR["card_border"],
            text_color=_CLR["text"], hover_color="#eef2fb", font=ctk.CTkFont(size=13),
            command=self._refresh_devices,
        ).pack(side="right")
        if sys.platform != "win32":
            self.system_audio_check = ctk.CTkCheckBox(
                audio, text="Optag systemlyd (Meet/Teams/telefon)",
                variable=self.system_audio_var, command=self._on_system_audio_toggle,
                text_color=_CLR["text"], font=ctk.CTkFont(size=13),
            )
            self.system_audio_check.pack(fill="x", pady=(0, 4))
            self.system_audio_status = ctk.CTkLabel(
                audio, text="", font=ctk.CTkFont(size=11),
                text_color=_CLR["text_secondary"], anchor="w", justify="left",
            )
            self.system_audio_status.pack(fill="x", pady=(0, 4))
            self.setup_audio_btn = ctk.CTkButton(
                audio, text="Opsæt systemlyd", height=32, corner_radius=8,
                fg_color="transparent", border_width=1, border_color=_CLR["card_border"],
                text_color=_CLR["text"], hover_color="#eef2fb", font=ctk.CTkFont(size=13),
                command=self._setup_system_audio,
            )
            self.setup_audio_btn.pack(fill="x", pady=(0, 0))
        else:
            ctk.CTkLabel(
                audio,
                text="På Windows optages kun din mikrofon. Systemlyd (modparten) kræver macOS.",
                font=ctk.CTkFont(size=11), text_color=_CLR["text_secondary"],
                anchor="w", justify="left", wraplength=520,
            ).pack(fill="x")

        # — Transkription
        trans = _card("Transkription")
        self._field_label(trans, "Standard-motor")
        engine_frame = ctk.CTkFrame(trans, fg_color="#f1f5f9", corner_radius=10)
        engine_frame.pack(fill="x", pady=(0, 4))
        for val, label in (("hviske", "Hviske (live, lokalt)"),
                           ("gemini", "Gemini (efter, cloud)")):
            btn = ctk.CTkButton(
                engine_frame, text=label, height=32, corner_radius=8,
                font=ctk.CTkFont(size=13), fg_color="transparent",
                text_color=_CLR["text_secondary"], hover_color="#eef2fb",
                command=lambda v=val: self._select_engine(v),
            )
            btn.pack(side="left", fill="x", expand=True, padx=3, pady=3)
            self._engine_buttons[val] = btn
        self._engine_hint = ctk.CTkLabel(
            trans, text="", font=ctk.CTkFont(size=11),
            text_color=_CLR["text_secondary"], anchor="w", justify="left", wraplength=520,
        )
        self._engine_hint.pack(fill="x", pady=(0, 10))
        self._select_engine(self.engine_var.get())
        self._field_label(trans, "Gemini API-nøgle")
        key_row = ctk.CTkFrame(trans, fg_color="transparent")
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
            trans, text="Få en gratis nøgle på aistudio.google.com/apikey",
            font=ctk.CTkFont(size=11), text_color=_CLR["text_secondary"], anchor="w",
        ).pack(fill="x", pady=(2, 0))

        # — Om & opdatering
        import app_paths
        from _version import __version__
        about = _card("Om & opdatering")
        ver_row = ctk.CTkFrame(about, fg_color="transparent")
        ver_row.pack(fill="x")
        ctk.CTkLabel(
            ver_row, text=f"Version {__version__}",
            font=ctk.CTkFont(size=12), text_color=_CLR["text_secondary"],
        ).pack(side="left")
        if app_paths.is_frozen() and sys.platform == "win32":
            self._update_btn = ctk.CTkButton(
                ver_row, text="Søg efter opdatering", width=180, height=36,
                corner_radius=10, fg_color=_CLR["accent"],
                hover_color=_CLR["accent_hover"], command=self._check_update_now,
            )
            self._update_btn.pack(side="right")

    def _refresh_summary(self):
        labels = getattr(self, "_sum_labels", None)
        if not labels:
            return
        form_map = {
            "fysisk": "Fysisk møde (i lokalet)",
            "online": "Online møde (Teams/Meet)",
            "telefon": "Telefonopkald",
        }
        form = form_map.get(self.meeting_form_var.get(), "—")
        if self.system_audio_var.get():
            form += "  ·  systemlyd TIL"
        elif self.meeting_form_var.get() in ("online", "telefon"):
            form += "  ·  kun mikrofon"
        engine_map = {"hviske": "Hviske (lokal, offline)", "gemini": "Gemini (cloud)"}

        def _set(key, text):
            w = labels.get(key)
            if w is not None and w.winfo_exists():
                w.configure(text=text if text else "—")

        _set("form", form)
        _set("type", self.type_var.get())
        _set("name", self.name_var.get())
        _set("attendees", self.attendees_var.get())
        _set("engine", engine_map.get(self.engine_var.get(), self.engine_var.get()))
        _set("mic", self._selected_device_name())
        _set("folder", self.folder_var.get())

    def _clear_meeting_fields(self):
        """Nulstil de mødespecifikke felter (mødeform, type, navn, deltagere),
        så opsummeringen står tom når wizarden springes over. Tekniske
        indstillinger (motor, mikrofon, mappe) bevares."""
        self.meeting_form_var.set("")
        self.type_var.set("")
        self.name_var.set("")
        self.attendees_var.set("")
        self._refresh_summary()

    def _open_wizard(self, start_step=0):
        if self.recording or self.transcribing:
            return
        if self._wizard is not None and self._wizard.winfo_exists():
            self._wizard.go_to(start_step)
            self._wizard.focus()
            return
        self._wizard = MeetingWizard(self, start_step=start_step)

    # ------------------------------------------------------------------
    # Mini-HUD (minimeret tilstand)
    # ------------------------------------------------------------------

    def _on_minimize(self, event=None):
        if event is not None and event.widget is not self.root:
            return
        if self.recording or self.transcribing:
            self._show_mini_hud()

    def _on_restore(self, event=None):
        if event is not None and event.widget is not self.root:
            return
        self._hide_mini_hud()

    def _show_mini_hud(self):
        if self._mini_hud is None or not self._mini_hud.winfo_exists():
            self._mini_hud = MiniHUD(self)
        hud = self._mini_hud
        if self.transcribing:
            hud.set_transcribing()
        else:
            hud.set_recording()
            hud.set_timer(self.timer_var.get())
        hud.set_meeting(self._active_subtitle_text())
        try:
            hud.deiconify()
            hud.lift()
            hud.attributes("-topmost", True)
        except Exception:
            pass
        # Genstart animationsløkken hvis den var sat på pause via withdraw().
        hud.resume_anim()

    def _hide_mini_hud(self):
        if self._mini_hud is not None and self._mini_hud.winfo_exists():
            try:
                # Sæt animationen på pause inden withdraw — undgår spildte
                # after-kald mens HUD'en er skjult (se MiniHUD.pause_anim).
                self._mini_hud.pause_anim()
                self._mini_hud.withdraw()
            except Exception:
                pass

    def _restore_from_hud(self):
        self._hide_mini_hud()
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Faneskift + tastatur-genveje
    # ------------------------------------------------------------------

    def _on_space_key(self, event=None):
        w = self.root.focus_get()
        try:
            cls = w.winfo_class() if w is not None else ""
        except Exception:
            cls = ""
        # Lad mellemrum virke normalt i tekstfelter.
        if cls in ("Entry", "TEntry", "Text"):
            return
        if self._current_view != "optag":
            return
        self._toggle_recording()
        return "break"

    def _on_escape_key(self, event=None):
        # Ignorer Escape i tekstfelter og når en dialog/guide har grabbbet fokus.
        w = self.root.focus_get()
        try:
            cls = w.winfo_class() if w is not None else ""
        except Exception:
            cls = ""
        if cls in ("Entry", "TEntry", "Text"):
            return
        try:
            if self.root.grab_current() not in (None, self.root):
                return
        except Exception:
            pass
        # Minimer kun fra Optag-viewet, og kun mens der optages/transkriberes.
        if self._current_view != "optag":
            return
        if self.recording or self.transcribing:
            try:
                self.root.iconify()
            except Exception:
                pass

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
                    hover_color="#eef2fb",
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

    def _check_update_now(self):
        """Manuelt udløst OTA-tjek (knappen i Indstillinger). Nettet kører i en
        baggrundstråd; alle GUI-opdateringer marshalleres via root.after.
        Genbruger updater + _maybe_apply_update (der venter med at genstarte,
        hvis appen optager eller transkriberer)."""
        import tempfile
        import updater
        from _version import __version__

        self._update_btn.configure(state="disabled", text="Søger…")
        self.status_var.set("Søger efter opdatering…")
        root = self.root

        def worker():
            try:
                rel = updater.check_for_update()
            except Exception as e:  # check_for_update kaster normalt ikke; defensivt
                root.after(0, lambda: self._update_finished(f"Tjek fejlede: {e}"))
                return
            if not rel:
                root.after(0, lambda: self._update_finished(
                    f"Du har allerede den nyeste version (v{__version__})."))
                return
            root.after(0, lambda: self.status_var.set(f"Henter {rel.tag}…"))
            try:
                staging = updater.download_and_stage(
                    rel, Path(tempfile.gettempdir()) / "modevaerktoj-update")
            except updater.UpdateError as e:
                root.after(0, lambda: self._update_finished(f"Opdatering fejlede: {e}"))
                return
            # Anvend + genstart — _maybe_apply_update venter, hvis appen er optaget.
            root.after(0, lambda: _maybe_apply_update(root, self, staging))

        threading.Thread(target=worker, daemon=True).start()

    def _update_finished(self, msg: str):
        """Vis besked og genaktivér opdaterings-knappen (kaldes på GUI-tråden)."""
        self.status_var.set(msg)
        self._update_btn.configure(state="normal", text="Søg efter opdatering")

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
        """Genindlæs mødetyper fra disk og opdatér oversigt + evt. åben guide.

        Kaldes når en type oprettes/redigeres/slettes i Mødetyper-fanen. Bevarer
        markeringen hvis den stadig findes; ellers falder den tilbage til første
        type. Typevalg sker via klikbare kort i guiden, der genopbygges ved hvert
        go_to — de henter altid friske værdier fra self._meeting_types."""
        self._meeting_types = meeting_tool.load_meeting_types(CONFIG_DIR)
        self._type_keys = list(self._meeting_types)
        if self._type_key not in self._meeting_types:
            self._type_key = self._type_keys[0]
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
        # Systemlyd findes kun på macOS — gendan aldrig på Windows.
        if state.get("system_audio") and sys.platform != "win32":
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
        _has_sa = (getattr(self, "system_audio_status", None) is not None
                   and self.system_audio_status.winfo_exists())
        if not self.system_audio_var.get():
            if _has_sa:
                self.system_audio_status.configure(text="")
            return
        if _has_sa:
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
        if self._mini_hud is not None and self._mini_hud.winfo_exists():
            self._mini_hud.set_transcribing()
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
                    self._hide_mini_hud()
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
                    self._hide_mini_hud()
                    self.status_var.set("Fejl.")
                    self._log(f"\nFEJL: {err}")
                    messagebox.showerror("Fejl under optagelse", err)
                elif kind == "system_audio_status":
                    _, idx = item
                    _has_sa = (getattr(self, "system_audio_status", None) is not None
                               and self.system_audio_status.winfo_exists())
                    if idx is None:
                        if _has_sa:
                            self.system_audio_status.configure(
                                text="BlackHole ikke fundet — klik 'Opsæt systemlyd'.")
                    else:
                        if _has_sa:
                            self.system_audio_status.configure(
                                text=f"Klar: systemlyd via BlackHole [{idx}].")
                elif kind == "setup_audio":
                    _, result = item
                    _has_sa = (getattr(self, "system_audio_status", None) is not None
                               and self.system_audio_status.winfo_exists())
                    if result.status == "ok":
                        self.system_audio_var.set(True)
                        if _has_sa:
                            self.system_audio_status.configure(
                                text=f"Klar: systemlyd via BlackHole [{result.system_device}].")
                        self._log("Systemlyd klar.")
                    else:
                        if _has_sa:
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

    def _active_subtitle_text(self) -> str:
        """Undertekst under det store timer-tal mens der optages — KUN
        mødenavnet (aldrig motoren). Falder tilbage til mødetypen hvis navnet
        er tomt."""
        return self.name_var.get().strip() or self.type_var.get()

    def _show_recording_state(self, on: bool) -> None:
        """Skift mellem idle-oversigt og det store aktiv-optage-panel."""
        if on:
            self._rec_subtitle.configure(text=self._active_subtitle_text())
            self._idle_card.pack_forget()
            self._bottom_bar.pack_forget()   # undgå dobbelt timer/stop
            self._active_panel.pack(fill="x", padx=24, pady=(4, 8))
        else:
            self._active_panel.pack_forget()
            self._bottom_bar.pack(side="bottom", fill="x")
            self._idle_card.pack(fill="x", padx=24, pady=(16, 0))

    def _set_ui_recording(self, is_recording: bool):
        self.recording = is_recording
        self._show_recording_state(is_recording)
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
            if (self.recording and self._mini_hud is not None
                    and self._mini_hud.winfo_exists()):
                self._mini_hud.set_timer(self.timer_var.get())
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
            hover_color="#eef2fb",
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
            fg_color="#ffffff",
            button_color=_CLR["accent"],
            button_hover_color=_CLR["accent_hover"],
            text_color=_CLR["text"],
            dropdown_fg_color=_CLR["card"],
            dropdown_hover_color="#eef2fb",
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
            fg_color="#ffffff",
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
            unselected_hover_color="#eef2fb",
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
            fg_color="#ffffff",
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
            fg_color="#ffffff",
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
            fg_color="#ffffff",
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
            hover_color="#eef2fb",
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
            fg_color="#ffffff",
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
            fg_color="#ffffff",
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
            fg_color="#ffffff",
            button_color=_CLR["accent"],
            button_hover_color=_CLR["accent_hover"],
            text_color=_CLR["text"],
            dropdown_fg_color=_CLR["card"],
            dropdown_hover_color="#eef2fb",
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
            fg_color="#ffffff",
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
            fg_color="#ffffff",
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
            fg_color="#ffffff",
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
                hover_color="#eef2fb",
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
            hover_color="#eef2fb",
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
            fg_color="#ffffff",
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
                    hover_color="#eef2fb",
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


def _bind_recursive(widget, callback):
    """Bind <Button-1> på en widget og alle dens efterkommere.

    CustomTkinter-widgets er sammensatte (en CTkLabel indeholder en intern
    tk-label); en enkelt bind fanger ikke klik på børnene. Derfor rekursivt."""
    try:
        widget.bind("<Button-1>", callback)
    except Exception:
        pass
    for child in widget.winfo_children():
        _bind_recursive(child, callback)


class MeetingWizard(ctk.CTkToplevel):
    """Guidet 'Ny optagelse': mødeform → mødetype → navn/deltagere → motor.

    Skriver til de samme variabler/metoder som MeetingApp bruger, så den fulde
    optage-rørledning forbliver uændret. Kan springes over (luk-knap) — så
    bevares de sidst valgte værdier fra state.json."""

    STEPS = ("form", "type", "navn", "motor")
    TITLES = ("Hvilken slags møde?", "Vælg mødetype",
              "Navn & deltagere", "Optagemotor")

    def __init__(self, app, start_step: int = 0):
        super().__init__(app.root)
        self.app = app
        self.title("Ny optagelse")
        self.configure(fg_color=_CLR["bg"])
        self.resizable(False, False)
        self.transient(app.root)
        self._step = 0
        self._type_keys = list(app._meeting_types)

        W, H = 560, 600
        self.geometry(f"{W}x{H}")
        self.update_idletasks()
        try:
            px, py = app.root.winfo_x(), app.root.winfo_y()
            pw, ph = app.root.winfo_width(), app.root.winfo_height()
            x = px + max(0, (pw - W) // 2)
            y = py + max(20, (ph - H) // 3)
            self.geometry(f"{W}x{H}+{x}+{y}")
        except Exception:
            pass

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=28, pady=(24, 8))
        # "Spring over" øverst til højre — hop direkte til opsummeringen med
        # de sidst valgte værdier, uden at gå gennem trinnene.
        skiprow = ctk.CTkFrame(top, fg_color="transparent")
        skiprow.pack(fill="x")
        ctk.CTkButton(
            skiprow, text="Spring over →", width=110, height=26, corner_radius=8,
            fg_color="transparent", hover_color="#eef2fb",
            text_color=_CLR["text_secondary"], font=ctk.CTkFont(size=12),
            command=self._skip,
        ).pack(side="right")
        # Progress-bar: ét segment pr. trin.
        segbar = ctk.CTkFrame(top, fg_color="transparent")
        segbar.pack(fill="x", pady=(0, 8))
        self._seg = []
        for i in range(len(self.STEPS)):
            s = ctk.CTkFrame(segbar, height=5, corner_radius=3,
                             fg_color=_CLR["card_border"])
            s.pack(side="left", fill="x", expand=True,
                   padx=(0 if i == 0 else 6, 0))
            self._seg.append(s)
        self._dots = ctk.CTkLabel(
            top, text="", font=ctk.CTkFont(size=12), text_color=_CLR["text_secondary"],
            anchor="w")
        self._dots.pack(anchor="w")
        self._title = ctk.CTkLabel(
            top, text="", font=ctk.CTkFont(family="SF Pro Display", size=22, weight="bold"),
            text_color=_CLR["text"])
        self._title.pack(anchor="w", pady=(4, 0))

        self._body = ctk.CTkFrame(self, fg_color="transparent")
        self._body.pack(fill="both", expand=True, padx=28, pady=(8, 8))

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=28, pady=(0, 22))
        self._back_btn = ctk.CTkButton(
            footer, text="← Tilbage", width=110, height=40, corner_radius=10,
            fg_color="transparent", border_width=1, border_color=_CLR["card_border"],
            text_color=_CLR["text"], hover_color="#eef2fb", font=ctk.CTkFont(size=14),
            command=self._prev)
        self._back_btn.pack(side="left")
        self._next_btn = ctk.CTkButton(
            footer, text="Næste →", width=170, height=40, corner_radius=10,
            fg_color=_CLR["accent"], hover_color=_CLR["accent_hover"],
            font=ctk.CTkFont(size=14, weight="bold"), command=self._next)
        self._next_btn.pack(side="right")

        self.go_to(max(0, min(start_step, len(self.STEPS) - 1)))
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(60, self._grab)

    def _grab(self):
        try:
            self.grab_set()
        except Exception:
            pass

    def _on_close(self):
        # Luk med X: behold nuværende/sidste valg (fx ved redigering), gå
        # blot tilbage til opsummeringen.
        self.app._refresh_summary()
        self.app._wizard = None
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    def _skip(self):
        # "Spring over": ryd de mødespecifikke felter, så opsummeringen står
        # tom i stedet for at vise gamle/standardværdier.
        self.app._clear_meeting_fields()
        self._on_close()

    # — Navigation -----------------------------------------------------
    def go_to(self, step: int):
        self._step = max(0, min(step, len(self.STEPS) - 1))
        for w in self._body.winfo_children():
            w.destroy()
        builders = (self._build_form, self._build_type, self._build_navn, self._build_motor)
        for i, s in enumerate(self._seg):
            s.configure(fg_color=_CLR["accent"] if i <= self._step else _CLR["card_border"])
        self._dots.configure(text=f"Trin {self._step + 1} af {len(self.STEPS)}")
        self._title.configure(text=self.TITLES[self._step])
        builders[self._step]()
        self._back_btn.configure(state="normal" if self._step > 0 else "disabled")
        self._next_btn.configure(
            text="Færdig ✓" if self._step == len(self.STEPS) - 1 else "Næste →")

    @staticmethod
    def _draw_check(canvas, on):
        """Tegn (eller ryd) et blåt flueben i et 20×20-canvas."""
        canvas.delete("all")
        if on:
            canvas.create_oval(1, 1, 19, 19, fill=_CLR["accent"], outline="")
            canvas.create_line(5, 10, 8, 13, fill="#ffffff", width=2)
            canvas.create_line(8, 13, 14, 6, fill="#ffffff", width=2)

    def _add_check(self, card_inner):
        """Tilføj et flueben-canvas til højre i et kort. Returnér canvas'et."""
        chk = ctk.CTkCanvas(card_inner, width=20, height=20, highlightthickness=0,
                            bd=0, bg=Sidebar._hex(_CLR["card"]))
        chk.pack(side="right", padx=(10, 0))
        self._draw_check(chk, False)
        return chk

    def _prev(self):
        if self._step > 0:
            self.go_to(self._step - 1)

    def _next(self):
        if self._step < len(self.STEPS) - 1:
            self.go_to(self._step + 1)
        else:
            self._finish()

    def _finish(self):
        if not self.app.name_var.get().strip():
            self.app.name_var.set(f"{self.app.type_var.get()} {self.app.date_var.get()}")
        self.app._refresh_summary()
        self.app._wizard = None
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
        # Efter wizarden vises opsummeringen — optagelsen startes først når
        # brugeren trykker på den store knap (ikke automatisk længere).

    # — Trin 1: Mødeform ----------------------------------------------
    def _build_form(self):
        wrap = ctk.CTkFrame(self._body, fg_color="transparent")
        wrap.pack(fill="x", pady=(8, 0))
        forms = (
            ("fysisk", "Fysisk møde", "I lokalet — kun din mikrofon optages."),
            ("online", "Online møde (Teams/Meet)", "Modparten optages også via systemlyd."),
            ("telefon", "Telefonopkald", "Modparten optages også via systemlyd."),
        )
        self._form_cards = {}
        self._form_checks = {}
        for val, title, desc in forms:
            c = ctk.CTkFrame(wrap, fg_color=_CLR["card"], corner_radius=14,
                             border_width=2, border_color=_CLR["card_border"])
            c.pack(fill="x", pady=6)
            inner = ctk.CTkFrame(c, fg_color="transparent")
            inner.pack(fill="x", padx=18, pady=14)
            self._form_checks[val] = self._add_check(inner)
            txt = ctk.CTkFrame(inner, fg_color="transparent")
            txt.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(txt, text=title, font=ctk.CTkFont(size=15, weight="bold"),
                         text_color=_CLR["text"], anchor="w").pack(anchor="w")
            ctk.CTkLabel(txt, text=desc, font=ctk.CTkFont(size=12),
                         text_color=_CLR["text_secondary"], anchor="w",
                         justify="left").pack(anchor="w", pady=(2, 0))
            _bind_recursive(c, lambda e, v=val: self._select_form(v))
            self._form_cards[val] = c
        if sys.platform == "win32":
            ctk.CTkLabel(
                wrap, text="Bemærk: på Windows optages kun din mikrofon — modparten kan ikke optages.",
                font=ctk.CTkFont(size=11), text_color=_CLR["text_secondary"],
                anchor="w", justify="left", wraplength=480).pack(anchor="w", pady=(10, 0))
        self._highlight_form(self.app.meeting_form_var.get())

    def _select_form(self, val):
        self.app.meeting_form_var.set(val)
        want_system = val in ("online", "telefon") and sys.platform != "win32"
        self.app.system_audio_var.set(want_system)
        self.app._on_system_audio_toggle()
        self._highlight_form(val)

    def _highlight_form(self, val):
        for v, c in self._form_cards.items():
            if c.winfo_exists():
                c.configure(border_color=_CLR["accent"] if v == val else _CLR["card_border"])
                self._draw_check(self._form_checks[v], v == val)

    # — Trin 2: Mødetype ----------------------------------------------
    def _build_type(self):
        wrap = ctk.CTkScrollableFrame(self._body, fg_color="transparent")
        wrap.pack(fill="both", expand=True)
        self._type_cards = {}
        self._type_checks = {}
        for key in self._type_keys:
            mt = self.app._meeting_types[key]
            bits = ["grundigt referat" if mt.get("detaljeniveau") == "grundig"
                    else "kortfattet referat"]
            if mt.get("opgaveliste"):
                bits.append("opgaveliste")
            if mt.get("citater"):
                bits.append("citater")
            meta = "  ·  ".join(bits)
            c = ctk.CTkFrame(wrap, fg_color=_CLR["card"], corner_radius=14,
                             border_width=2, border_color=_CLR["card_border"])
            c.pack(fill="x", pady=6, padx=2)
            inner = ctk.CTkFrame(c, fg_color="transparent")
            inner.pack(fill="x", padx=18, pady=14)
            self._type_checks[key] = self._add_check(inner)
            txt = ctk.CTkFrame(inner, fg_color="transparent")
            txt.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(txt, text=mt["navn"], font=ctk.CTkFont(size=15, weight="bold"),
                         text_color=_CLR["text"], anchor="w").pack(anchor="w")
            if mt.get("fokus"):
                ctk.CTkLabel(txt, text=mt["fokus"], font=ctk.CTkFont(size=12),
                             text_color=_CLR["text_secondary"], anchor="w",
                             justify="left", wraplength=430).pack(anchor="w", pady=(2, 0))
            ctk.CTkLabel(txt, text=meta, font=ctk.CTkFont(size=11, weight="bold"),
                         text_color=_CLR["accent"], anchor="w").pack(anchor="w", pady=(6, 0))
            _bind_recursive(c, lambda e, k=key: self._select_type(k))
            self._type_cards[key] = c
        self._highlight_type(self.app._type_key)

    def _select_type(self, key):
        self.app._type_key = key
        self.app.type_var.set(self.app._meeting_types[key]["navn"])
        self.app._on_type_selected(self.app._meeting_types[key]["navn"])
        self._highlight_type(key)

    def _highlight_type(self, key):
        for k, c in self._type_cards.items():
            if c.winfo_exists():
                c.configure(border_color=_CLR["accent"] if k == key else _CLR["card_border"])
                self._draw_check(self._type_checks[k], k == key)

    # — Trin 3: Navn & deltagere --------------------------------------
    def _build_navn(self):
        wrap = ctk.CTkFrame(self._body, fg_color="transparent")
        wrap.pack(fill="x", pady=(8, 0))
        MeetingApp._field_label(wrap, "Mødenavn")
        ctk.CTkEntry(wrap, textvariable=self.app.name_var, height=38, corner_radius=10,
                     border_width=1, border_color=_CLR["card_border"], fg_color="#ffffff",
                     text_color=_CLR["text"], font=ctk.CTkFont(size=14)).pack(fill="x", pady=(0, 12))
        row = ctk.CTkFrame(wrap, fg_color="transparent")
        row.pack(fill="x", pady=(0, 12))
        row.columnconfigure(0, weight=1)
        row.columnconfigure(1, weight=2)
        df = ctk.CTkFrame(row, fg_color="transparent")
        df.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        MeetingApp._field_label(df, "Dato")
        ctk.CTkEntry(df, textvariable=self.app.date_var, height=38, corner_radius=10,
                     border_width=1, border_color=_CLR["card_border"], fg_color="#ffffff",
                     text_color=_CLR["text"], font=ctk.CTkFont(size=14)).pack(fill="x")
        af = ctk.CTkFrame(row, fg_color="transparent")
        af.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        MeetingApp._field_label(af, "Deltagere (komma-adskilt)")
        ctk.CTkEntry(af, textvariable=self.app.attendees_var, height=38, corner_radius=10,
                     border_width=1, border_color=_CLR["card_border"], fg_color="#ffffff",
                     text_color=_CLR["text"], font=ctk.CTkFont(size=14)).pack(fill="x")
        ctk.CTkLabel(wrap, text="Navnet bruges som mappenavn for mødet.",
                     font=ctk.CTkFont(size=11), text_color=_CLR["text_secondary"],
                     anchor="w").pack(anchor="w")

    # — Trin 4: Optagemotor -------------------------------------------
    def _build_motor(self):
        wrap = ctk.CTkFrame(self._body, fg_color="transparent")
        wrap.pack(fill="x", pady=(8, 0))
        opts = (
            ("hviske", "Hviske — lokal & offline",
             "Kører live på din computer under mødet. Ingen internet eller nøgle nødvendig."),
            ("gemini", "Gemini — cloud, bedst til dansk",
             "Kører efter mødet. Bedst til danske navne og fagtermer. Kræver en API-nøgle."),
        )
        self._motor_cards = {}
        self._motor_checks = {}
        for val, title, desc in opts:
            c = ctk.CTkFrame(wrap, fg_color=_CLR["card"], corner_radius=14,
                             border_width=2, border_color=_CLR["card_border"])
            c.pack(fill="x", pady=6)
            inner = ctk.CTkFrame(c, fg_color="transparent")
            inner.pack(fill="x", padx=18, pady=14)
            self._motor_checks[val] = self._add_check(inner)
            txt = ctk.CTkFrame(inner, fg_color="transparent")
            txt.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(txt, text=title, font=ctk.CTkFont(size=15, weight="bold"),
                         text_color=_CLR["text"], anchor="w").pack(anchor="w")
            ctk.CTkLabel(txt, text=desc, font=ctk.CTkFont(size=12),
                         text_color=_CLR["text_secondary"], anchor="w",
                         justify="left", wraplength=440).pack(anchor="w", pady=(2, 0))
            _bind_recursive(c, lambda e, v=val: self._select_motor(v))
            self._motor_cards[val] = c
        self._motor_hint = ctk.CTkLabel(
            wrap, text="", font=ctk.CTkFont(size=11), text_color="#b45309",
            anchor="w", justify="left", wraplength=480)
        self._motor_hint.pack(fill="x", pady=(8, 0))
        self._highlight_motor(self.app.engine_var.get())

    def _select_motor(self, val):
        self.app._select_engine(val)
        self._highlight_motor(val)

    def _highlight_motor(self, val):
        for v, c in self._motor_cards.items():
            if c.winfo_exists():
                c.configure(border_color=_CLR["accent"] if v == val else _CLR["card_border"])
                self._draw_check(self._motor_checks[v], v == val)
        no_key = not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
        if val == "gemini" and no_key:
            self._motor_hint.configure(
                text="OBS  ·  Ingen Gemini-nøgle fundet. Tilføj den under Indstillinger før du bruger Gemini.")
        else:
            self._motor_hint.configure(text="")

    def sync_engine(self):
        if getattr(self, "_motor_cards", None):
            self._highlight_motor(self.app.engine_var.get())


class MiniHUD(ctk.CTkToplevel):
    """Lille, ramme-løst, altid-øverst HUD der vises når hovedvinduet minimeres
    mens der optages/transkriberes. Viser løbende timer + Stop + 'åbn vindue'.

    Ren Tkinter (overrideredirect + -topmost) — ingen ekstra afhængigheder, så
    den virker på både macOS og Windows uden libs som rumps/pystray."""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.overrideredirect(True)
        try:
            self.attributes("-topmost", True)
        except Exception:
            pass
        try:
            self.attributes("-alpha", 0.98)
        except Exception:
            pass
        self.configure(fg_color=_CLR["bg"])
        self._mode = "recording"
        self._spin_chars = ["◐", "◓", "◑", "◒"]
        self._tick = 0
        self._anim_after = None
        self._drag = (0, 0)

        pill = ctk.CTkFrame(self, fg_color=_CLR["sidebar_bg"], corner_radius=18,
                            border_width=0)
        pill.pack(fill="both", expand=True, padx=2, pady=2)
        inner = ctk.CTkFrame(pill, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=14, pady=10)

        self._icon = ctk.CTkLabel(inner, text="●", width=18,
                                  font=ctk.CTkFont(size=18), text_color=_CLR["rec_active"])
        self._icon.pack(side="left", padx=(0, 10))

        mid = ctk.CTkFrame(inner, fg_color="transparent")
        mid.pack(side="left", fill="x", expand=True)
        self._caption = ctk.CTkLabel(mid, text="OPTAGER", anchor="w",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=_CLR["rec_active"])
        self._caption.pack(anchor="w")
        self._timer = ctk.CTkLabel(mid, text="00:00:00", anchor="w",
            font=ctk.CTkFont(family="SF Mono", size=19, weight="bold"),
            text_color=_CLR["sidebar_icon_active"])
        self._timer.pack(anchor="w")
        self._name = ctk.CTkLabel(mid, text="", anchor="w",
            font=ctk.CTkFont(size=11), text_color=_CLR["sidebar_icon"])
        self._name.pack(anchor="w")

        self._stop_btn = ctk.CTkButton(
            inner, text="■", width=40, height=40, corner_radius=12,
            font=ctk.CTkFont(size=14, weight="bold"), fg_color=_CLR["rec_active"],
            hover_color="#c62828", text_color="#ffffff",
            command=self.app._stop_recording)
        self._stop_btn.pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            inner, text="⤢", width=40, height=40, corner_radius=12,
            font=ctk.CTkFont(size=16), fg_color="transparent", border_width=1,
            border_color="#3a4a6a", text_color=_CLR["sidebar_icon_active"],
            hover_color="#26365a", command=self.app._restore_from_hud).pack(side="right")

        # Træk hvor som helst på pillen for at flytte HUD'en.
        for w in (pill, inner, mid, self._caption, self._timer, self._name, self._icon):
            w.bind("<Button-1>", self._start_drag)
            w.bind("<B1-Motion>", self._on_drag)

        self.update_idletasks()
        w = max(300, self.winfo_reqwidth())
        h = self.winfo_reqheight()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{sw - w - 28}+{sh - h - 80}")
        self._tick_anim()

    def _start_drag(self, e):
        self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

    def _on_drag(self, e):
        self.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _tick_anim(self):
        if self._mode == "recording":
            self._icon.configure(
                text="●",
                text_color=_CLR["rec_active"] if self._tick % 2 == 0 else _CLR["rec_ring"])
            self._tick += 1
            self._anim_after = self.after(650, self._tick_anim)
        else:
            self._icon.configure(
                text=self._spin_chars[self._tick % len(self._spin_chars)],
                text_color=_CLR["accent"])
            self._tick += 1
            self._anim_after = self.after(160, self._tick_anim)

    def set_timer(self, text):
        self._timer.configure(text=text)

    def set_meeting(self, text):
        self._name.configure(text=text)

    def pause_anim(self):
        """Afbryd animationsløkken (bruges ved withdraw — ingen spildte wakeups)."""
        if self._anim_after is not None:
            try:
                self.after_cancel(self._anim_after)
            except Exception:
                pass
            self._anim_after = None

    def resume_anim(self):
        """Genstart animationsløkken efter deiconify, hvis den ikke allerede kører."""
        if self._anim_after is None:
            self._tick_anim()

    def set_recording(self):
        self._mode = "recording"
        self._caption.configure(text="OPTAGER", text_color=_CLR["rec_active"])
        self._stop_btn.configure(state="normal")

    def set_transcribing(self):
        self._mode = "transcribing"
        self._caption.configure(text="TRANSKRIBERER", text_color=_CLR["accent"])
        self._timer.configure(text="Gør færdig…")
        self._stop_btn.configure(state="disabled")

    def destroy(self):
        if self._anim_after is not None:
            try:
                self.after_cancel(self._anim_after)
            except Exception:
                pass
        super().destroy()


def _open_path(path, on_error=None):
    """Åbn en fil eller mappe i systemets standard-program (kryds-platform).

    on_error: valgfri callable(str) der modtager fejlbeskeden — brug den til
    at vise fejl i UI'et (f.eks. status_var.set). Fallback til stderr."""
    if path is None:
        return
    p = str(path)
    try:
        if sys.platform == "win32":
            os.startfile(p)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p])
    except Exception as e:
        msg = f"Kunne ikke åbne {p}: {e}"
        if on_error is not None:
            on_error(msg)
        else:
            print(msg, file=sys.stderr)


def historik_badge(status: str):
    """Map en møde-status til (label, tekstfarve, baggrundsfarve).

    Gyldige statusser: 'klar' (referat findes), 'i_gang' (transkriberer),
    '' (ingen badge)."""
    if status == "klar":
        return ("Referat klar", _CLR["badge_ok"], _CLR["badge_ok_bg"])
    if status == "i_gang":
        return ("Transkriberer…", _CLR["badge_busy"], _CLR["badge_busy_bg"])
    return ("", "", "")


class HistorikTab:
    """Historik-fane: lister tidligere møder (undermapper i mødemappen) og giver
    hurtige handlinger — åbn mappe, åbn referat, kopiér referat. Læser kun fra
    disken; rører ikke ved optagelses-/transkriptions-logikken."""

    def __init__(self, parent, app):
        self.parent = parent
        self.app = app
        self._build_ui()
        self.refresh()

    def _base_dir(self):
        try:
            base = Path(self.app.folder_var.get().strip())
            if base.exists():
                return base
        except Exception:
            pass
        return MEETINGS_DIR

    def _build_ui(self):
        self.parent.configure(fg_color=_CLR["bg"])
        self._outer = ctk.CTkScrollableFrame(
            self.parent, fg_color=_CLR["bg"],
            scrollbar_button_color=_CLR["card_border"],
            scrollbar_button_hover_color=_CLR["accent"])
        self._outer.pack(fill="both", expand=True)

        head = ctk.CTkFrame(self._outer, fg_color="transparent")
        head.pack(fill="x", padx=28, pady=(24, 2))
        ctk.CTkLabel(
            head, text="Historik",
            font=ctk.CTkFont(family="SF Pro Display", size=28, weight="bold"),
            text_color=_CLR["text"]).pack(side="left")
        ctk.CTkButton(
            head, text="Opdater", width=90, height=32, corner_radius=10,
            fg_color="transparent", border_width=1, border_color=_CLR["card_border"],
            text_color=_CLR["text"], hover_color="#eef2fb",
            command=self.refresh).pack(side="right")
        ctk.CTkLabel(
            self._outer, text="Tidligere møder i din mødemappe — åbn eller kopiér referatet.",
            font=ctk.CTkFont(size=13), text_color=_CLR["text_secondary"]).pack(
            anchor="w", padx=28, pady=(0, 6))

        self._list = ctk.CTkFrame(self._outer, fg_color="transparent")
        self._list.pack(fill="both", expand=True, padx=24, pady=(8, 24))

    def refresh(self):
        for w in self._list.winfo_children():
            w.destroy()
        base = self._base_dir()
        meetings = []
        try:
            for d in base.iterdir():
                if d.is_dir():
                    meetings.append(d)
        except Exception:
            meetings = []
        meetings.sort(
            key=lambda p: (p.stat().st_mtime if p.is_dir() else 0), reverse=True)
        if not meetings:
            ctk.CTkLabel(
                self._list,
                text="Ingen møder fundet endnu.\nOptag dit første møde fra Optag-fanen.",
                font=ctk.CTkFont(size=14), text_color=_CLR["text_secondary"],
                justify="left").pack(anchor="w", padx=6, pady=30)
            return
        for d in meetings[:60]:
            self._meeting_card(d)

    def _find(self, folder, prefix=None, suffix=None):
        try:
            for f in folder.iterdir():
                if not f.is_file():
                    continue
                n = f.name
                if suffix and not n.lower().endswith(suffix):
                    continue
                if prefix and not n.startswith(prefix):
                    continue
                return f
        except Exception:
            pass
        return None

    def _meeting_card(self, folder):
        referat = self._find(folder, prefix="Referat ", suffix=".md")
        wav = self._find(folder, suffix=".wav")
        try:
            mtime = datetime.fromtimestamp(
                folder.stat().st_mtime).strftime("%d-%m-%Y · %H:%M")
        except Exception:
            mtime = ""

        card = ctk.CTkFrame(
            self._list, fg_color=_CLR["card"], corner_radius=14,
            border_width=1, border_color=_CLR["card_border"])
        card.pack(fill="x", pady=6)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=14)

        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(
            top, text=folder.name, anchor="w",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=_CLR["text"]).pack(side="left")
        # Status-badge: referat på disken → "Referat klar"; ellers ingen badge.
        label, fg, bg = historik_badge("klar" if referat else "")
        if label:
            ctk.CTkLabel(
                top, text=f"  {label}  ", anchor="center", height=22,
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=fg, fg_color=bg, corner_radius=10).pack(side="right")
        ctk.CTkLabel(
            inner, text=mtime, anchor="w", font=ctk.CTkFont(size=11),
            text_color=_CLR["text_secondary"]).pack(anchor="w", pady=(2, 8))

        btns = ctk.CTkFrame(inner, fg_color="transparent")
        btns.pack(fill="x")

        def mkbtn(text, cmd, primary=False, enabled=True):
            b = ctk.CTkButton(
                btns, text=text, height=30, corner_radius=9,
                font=ctk.CTkFont(size=12),
                fg_color=(_CLR["accent"] if primary else "transparent"),
                border_width=(0 if primary else 1), border_color=_CLR["card_border"],
                text_color=("#ffffff" if primary else _CLR["text"]),
                hover_color=(_CLR["accent_hover"] if primary else "#eef2fb"),
                command=cmd)
            if not enabled:
                b.configure(state="disabled", text_color=_CLR["text_secondary"])
            b.pack(side="left", padx=(0, 8))
            return b

        _err = self.app.status_var.set
        mkbtn("Åbn mappe", lambda f=folder: _open_path(f, on_error=_err))
        mkbtn("Åbn referat",
              (lambda r=referat: _open_path(r, on_error=_err)) if referat else None,
              primary=bool(referat), enabled=bool(referat))
        mkbtn("Kopiér referat",
              (lambda r=referat: self._copy(r)) if referat else None,
              enabled=bool(referat))

    def _copy(self, path):
        try:
            text = Path(path).read_text(encoding="utf-8")
            self.app.root.clipboard_clear()
            self.app.root.clipboard_append(text)
            self.app.root.update()
            self.app.status_var.set("Referat kopieret til udklipsholder.")
        except Exception as e:
            self.app.status_var.set(f"Kunne ikke kopiere referat: {e}")


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

    # Semi-transparent vindue (Liquid Glass effekt) — kun på macOS.
    # På Windows lader gennemsigtigheden skrivebordstapetet skinne igennem,
    # så dér holder vi vinduet helt opakt.
    if sys.platform == "darwin":
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
