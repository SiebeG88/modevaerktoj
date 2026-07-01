# Mødeværktøj Redesign — Implementeringsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Mødeværktøj et nyt visuelt sprog ("Sidebar App": mørk venstre-sidebar + lys arbejdsflade) og en enklere navigation, uden at røre optage-/transkriptions-/referat-logikken.

**Architecture:** Den øverste `CTkTabview` erstattes af en fast venstre-sidebar (ikon-nav) + en view-switcher der viser/skjuler indholds-frames. Alle skærme restyles via et nyt centralt farvesystem (`_CLR`). Delte `StringVar`/`BooleanVar` og hjælpe-metoder bevares, så guiden og rørledningen virker uændret.

**Tech Stack:** Python 3.12, CustomTkinter (Tkinter), pytest + pytest-mock. GUI-tests følger repoets eksisterende display-guarded mønster (`tests/test_meeting_types_refresh.py`): `pytest.importorskip("customtkinter")`, spring over ved manglende display eller Tk ≥ 9.0.

## Global Constraints

- **Kun UI-lag.** `meeting_tool.py`, rørledning, OTA (`updater.py`), build/installer, filstruktur og mødetype-konfiguration ændres IKKE.
- **Ingen emoji som ikoner** nogen steder i UI'et — kun tegnede (canvas/SVG-lignende) ikoner eller rene former.
- **Motor (Hviske/Gemini) vises ALDRIG på den aktive optage-skærm** — kun i guide-trin 4 og Indstillinger.
- **Sproget i UI er dansk** (som i dag). Kode-kommentarer på dansk, matcher repoet.
- **Ingen nye tredjeparts-afhængigheder.**
- **Eksisterende testsuite i `tests/` skal fortsat passere.**
- **Farvepalet (verbatim):** sidebar_bg `#1b2a4a`, sidebar_icon `#7f92b8`, sidebar_icon_active `#ffffff`, bg `#f7f8fa`, card `#ffffff`, card_border `#e6e8ec`, accent `#3b6fe0`, accent_hover `#2f5ec9`, text `#1b2a4a`, text_secondary `#5b6474`, rec_active `#e5382b`, badge_ok `#2e7d5b`, badge_ok_bg `#e3f5ec`, badge_busy `#8a6d00`, badge_busy_bg `#fbf3d8`.
- **GUI-tests kører kun med display** — brug det eksisterende skip-mønster; kør `pytest -q` som helhed efter hver task.

---

## Filstruktur

| Fil | Ansvar | Ændring |
|---|---|---|
| `meeting_app.py` | Hele UI'et (én fil, som i dag) | Modificeres task for task |
| `tests/test_theme.py` | Palette-tokens (ren data) | Ny |
| `tests/test_shell.py` | Sidebar + view-switch adfærd | Ny (display-guarded) |
| `tests/test_recording_view.py` | Aktiv-optagelse: motor skjult, timer/status | Ny (display-guarded) |
| `tests/test_historik_badges.py` | Status→badge-mapping (ren funktion) | Ny |

Repoet holder UI i én stor `meeting_app.py`. Vi følger det mønster og splitter ikke filen; nye enheder (Sidebar, badge-helper) tilføjes som klasser/funktioner i samme fil.

---

### Task 1: Nyt farvesystem (`_CLR`)

Erstat `_CLR`-dict'en (meeting_app.py:137) med den nye palet og tilføj de nye
nøgler. Behold ALLE eksisterende nøgler (så resten af filen ikke bryder) men
remap deres værdier til det nye sprog.

**Files:**
- Modify: `meeting_app.py:137-153` (`_CLR`-dict)
- Test: `tests/test_theme.py`

**Interfaces:**
- Produces: `meeting_app._CLR` dict med nøgler (mindst): `bg, card, card_border, accent, accent_hover, text, text_secondary, text_placeholder, rec_idle, rec_active, rec_ring, stop_blue, stop_hover, success, sidebar_bg, sidebar_icon, sidebar_icon_active, badge_ok, badge_ok_bg, badge_busy, badge_busy_bg`.

- [ ] **Step 1: Skriv den fejlende test**

```python
# tests/test_theme.py
"""Palette-tokens er ren data — testes uden display."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_app


REQUIRED = {
    "sidebar_bg": "#1b2a4a", "sidebar_icon": "#7f92b8",
    "sidebar_icon_active": "#ffffff", "bg": "#f7f8fa", "card": "#ffffff",
    "card_border": "#e6e8ec", "accent": "#3b6fe0", "accent_hover": "#2f5ec9",
    "text": "#1b2a4a", "text_secondary": "#5b6474", "rec_active": "#e5382b",
    "badge_ok": "#2e7d5b", "badge_ok_bg": "#e3f5ec",
    "badge_busy": "#8a6d00", "badge_busy_bg": "#fbf3d8",
}


def test_palette_has_new_tokens_with_exact_values():
    for key, val in REQUIRED.items():
        assert meeting_app._CLR[key].lower() == val, key


def test_legacy_keys_still_present():
    # Resten af filen slår stadig disse op — de må ikke forsvinde.
    for key in ("rec_idle", "rec_ring", "stop_blue", "stop_hover",
                "success", "text_placeholder", "accent_hover"):
        assert key in meeting_app._CLR
```

- [ ] **Step 2: Kør testen — verificér at den fejler**

Run: `pytest tests/test_theme.py -q`
Expected: FAIL (KeyError på `sidebar_bg` / forkert værdi på `accent`).

- [ ] **Step 3: Skriv minimal implementering**

Erstat `_CLR`-dict'en med:

```python
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
```

- [ ] **Step 4: Kør testen — verificér PASS**

Run: `pytest tests/test_theme.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Kør hele suiten (fanger regressions fra farve-remap)**

Run: `pytest -q`
Expected: Samme antal pass/skip som før task'en (ingen nye fejl).

- [ ] **Step 6: Commit**

```bash
git add meeting_app.py tests/test_theme.py
git commit -m "feat(ui): nyt farvesystem for sidebar-app redesign"
```

---

### Task 2: Navigations-shell (Sidebar + view-switcher)

Erstat den øverste `CTkTabview` (meeting_app.py:418-473) med en fast
venstre-sidebar og en view-switcher. Sidebar har 4 ikon-knapper: Optag,
Transkribér, Historik, Indstillinger. Hvert view er en `CTkFrame` der
`pack`/`pack_forget`'es. Ordliste/Mødetyper flyttes ind som sektioner i
Indstillinger i Task 8 — her får de foreløbig hver sin egen skjulte frame, så
build ikke bryder.

**Files:**
- Modify: `meeting_app.py` — `_build_ui` (405-478), `_on_tab_change`-kald, alle `self._tabview.set(...)`-kald (fx 577-578, 3452-lign.), `_open_wizard`.
- Add (i `meeting_app.py`): `class Sidebar(ctk.CTkFrame)`, `MeetingApp._show_view(name)`.
- Test: `tests/test_shell.py`

**Interfaces:**
- Consumes: `_CLR` (Task 1).
- Produces:
  - `MeetingApp._views: dict[str, ctk.CTkFrame]` — nøgler: `"optag", "transkriber", "historik", "indstillinger"`.
  - `MeetingApp._show_view(name: str) -> None` — viser det ene view, skjuler resten, opdaterer sidebar-markering. Bevarer `self._current_view: str`.
  - `Sidebar(master, items: list[tuple[str,str]], on_select: Callable[[str],None])` hvor hvert item = `(view_name, label)`; metode `set_active(name: str)`.
  - Erstatning for `self._tabview.set("X")`: alle kald bliver `self._show_view("x")`.

- [ ] **Step 1: Skriv den fejlende test**

```python
# tests/test_shell.py
"""Sidebar + view-switch. Display-guarded (samme mønster som test_meeting_types_refresh)."""
from __future__ import annotations
import json, sys
from pathlib import Path
import pytest

ctk = pytest.importorskip("customtkinter")
import tkinter
try:
    _r = tkinter.Tk()
    _v = tuple(int(x) for x in _r.tk.call("info", "patchlevel").split(".")[:2])
    _r.destroy()
    if _v >= (9, 0):
        pytest.skip("Tk 9.x inkompatibel med CTk", allow_module_level=True)
except Exception:
    pytest.skip("Intet Tk-display", allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    (tmp_path / "meeting_types.json").write_text(
        json.dumps({"a": {"navn": "Møde A"}}), encoding="utf-8")
    monkeypatch.setattr(meeting_app, "TOOL_DIR", tmp_path)
    monkeypatch.setattr(meeting_app, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(meeting_app, "load_state", lambda: {})
    monkeypatch.setattr(meeting_app.MeetingApp, "_open_wizard", lambda self, s=0: None)
    root = ctk.CTk(); root.withdraw()
    a = meeting_app.MeetingApp(root)
    yield a
    root.destroy()


def test_views_exist(app):
    for name in ("optag", "transkriber", "historik", "indstillinger"):
        assert name in app._views


def test_show_view_switches_visibility(app):
    app._show_view("historik")
    assert app._current_view == "historik"
    assert app._views["historik"].winfo_manager() != ""      # pakket
    assert app._views["optag"].winfo_manager() == ""          # skjult


def test_no_tabview_attribute(app):
    # CTkTabview er fjernet til fordel for sidebar.
    assert not hasattr(app, "_tabview")
```

- [ ] **Step 2: Kør testen — verificér at den fejler**

Run: `pytest tests/test_shell.py -q`
Expected: FAIL (`_views` findes ikke / `_tabview` findes stadig). Skippet hvis headless — kør da på en maskine med display.

- [ ] **Step 3: Tilføj `Sidebar`-klassen**

Indsæt før `class MeetingApp` (fx efter `HeroButton`):

```python
class Sidebar(ctk.CTkFrame):
    """Fast venstre-navigation med tegnede ikoner. Kalder on_select(view_name)."""

    WIDTH = 78

    def __init__(self, master, items, on_select, **kw):
        super().__init__(master, width=self.WIDTH, corner_radius=0,
                         fg_color=_CLR["sidebar_bg"], **kw)
        self.pack_propagate(False)
        self._on_select = on_select
        self._buttons = {}
        # Logo-plads
        logo = ctk.CTkFrame(self, width=32, height=32, corner_radius=9,
                            fg_color=_CLR["accent"])
        logo.pack(pady=(16, 20)); logo.pack_propagate(False)
        for name, label in items:
            b = ctk.CTkButton(
                self, text=label, width=self.WIDTH - 16, height=42,
                corner_radius=10, fg_color="transparent",
                hover_color="#26365a", text_color=_CLR["sidebar_icon"],
                font=ctk.CTkFont(size=11), command=lambda n=name: on_select(n))
            b.pack(pady=4, padx=8)
            self._buttons[name] = b

    def set_active(self, name):
        for n, b in self._buttons.items():
            if n == name:
                b.configure(fg_color="#26365a", text_color=_CLR["sidebar_icon_active"])
            else:
                b.configure(fg_color="transparent", text_color=_CLR["sidebar_icon"])
```

(Ikonerne som canvas-tegninger tilføjes i Task 3-relaterede polish; her bruges
korte tekst-labels: "Optag", "Fil", "Historik", "Indstil.".)

- [ ] **Step 4: Omskriv `_build_ui` til shell**

Erstat `CTkTabview`-blokken (meeting_app.py:417-473) med sidebar + content.
Behold ALLE `StringVar`/`BooleanVar`-oprettelser (439-451) uændret.

```python
        self.root.configure(fg_color=_CLR["bg"])

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

        # (variabel-oprettelserne fra 439-451 står her — uændret)

        self._build_record_screen(self._views["optag"])
        self._build_settings_tab(self._views["indstillinger"])
        self._transcribe_tab = TranscribeFileTab(self._views["transkriber"])
        self._vocab_tab = VocabularyTab(self._views["indstillinger"])   # flyttes i Task 8
        self._types_tab = MeetingTypesTab(self._views["indstillinger"],
                                          on_change=self._on_meeting_types_changed)
        self._historik_tab = HistorikTab(self._views["historik"], self)

        self._setup_global_scroll()
        self._show_view("optag")
```

Tilføj metoden:

```python
    def _show_view(self, name: str) -> None:
        if name not in self._views:
            return
        for n, frame in self._views.items():
            if n == name:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()
        self._current_view = name
        self._sidebar.set_active(name)
```

- [ ] **Step 5: Erstat alle gamle `_tabview`-kald**

Find og erstat (grep `_tabview`):
- `self._tabview.set("Indstillinger")` → `self._show_view("indstillinger")` (fx 577, 578).
- Fjern `_on_tab_change`-registrering; hvis metoden bruges til andet, behold kroppen men afkobl fra tabview.

Run: `grep -n "_tabview" meeting_app.py` → forventet output: **ingen linjer**.

- [ ] **Step 6: Kør shell-tests + hele suiten**

Run: `pytest tests/test_shell.py -q && pytest -q`
Expected: test_shell PASS (3), resten uændret.

- [ ] **Step 7: Røgtest — start appen**

Run: `python meeting_app.py`
Expected: Sidebar til venstre, 4 knapper skifter view; ingen tracebacks. Luk igen.

- [ ] **Step 8: Commit**

```bash
git add meeting_app.py tests/test_shell.py
git commit -m "feat(ui): venstre-sidebar + view-switcher erstatter CTkTabview"
```

---

### Task 3: Tegnede sidebar-ikoner + RecordButton-restyle

Giv sidebar rigtige tegnede ikoner (ikke tekst) og reskin `HeroButton` til den
nye palet (marineblå cirkel-ramme, rød rec-prik idle, mørk stop, blå spinner).
API'et (`set_recording/set_transcribing/set_idle`) er uændret.

**Files:**
- Modify: `meeting_app.py` — `Sidebar` (ikon-canvas), `HeroButton._draw_idle/_draw_recording/_draw_transcribing` (196-321).
- Test: kør eksisterende + røgtest (rent visuelt; ingen ny logik).

**Interfaces:**
- Consumes: `_CLR` (Task 1), `Sidebar` (Task 2).
- Produces: uændrede signaturer; `HeroButton` bruger nu `_CLR["stop_blue"]` til stop-cirkel og `_CLR["accent"]` til spinner.

- [ ] **Step 1: Tegn sidebar-ikoner**

I `Sidebar.__init__`, erstat tekst-knapperne med en lille `CTkCanvas` + label
pr. punkt. Tilføj en hjælper der tegner ikon efter navn:

```python
    def _draw_icon(self, canvas, name, colour):
        canvas.delete("all")
        if name == "optag":       # cirkel + prik
            canvas.create_oval(4, 4, 24, 24, outline=colour, width=2)
            canvas.create_oval(11, 11, 17, 17, fill=colour, outline="")
        elif name == "transkriber":  # dokument-linjer
            canvas.create_rectangle(6, 3, 22, 25, outline=colour, width=2)
            for y in (9, 14, 19):
                canvas.create_line(9, y, 19, y, fill=colour, width=2)
        elif name == "historik":  # graf
            canvas.create_line(4, 24, 4, 6, fill=colour, width=2)
            canvas.create_line(4, 24, 24, 24, fill=colour, width=2)
            canvas.create_line(7, 18, 12, 13, fill=colour, width=2)
            canvas.create_line(12, 13, 16, 16, fill=colour, width=2)
            canvas.create_line(16, 16, 23, 8, fill=colour, width=2)
        elif name == "indstillinger":  # tandhjul (forenklet)
            canvas.create_oval(6, 6, 22, 22, outline=colour, width=2)
            canvas.create_oval(11, 11, 17, 17, outline=colour, width=2)
```

Gem `(canvas, name)` i `self._icons[name]` og gentegn i `set_active` med
`sidebar_icon_active` for aktiv, `sidebar_icon` ellers.

- [ ] **Step 2: Reskin HeroButton idle**

I `_draw_idle` (196): skift skygge-ring til `#e6e8ec`, hovedcirkel `#ffffff`
med `outline=_CLR["card_border"]`, rød prik `fill=_CLR["rec_idle"]`, "Start"-tekst
`fill=_CLR["text"]`.

- [ ] **Step 3: Reskin recording + transcribing**

`_draw_recording` (215): behold rød hovedcirkel `_CLR["rec_active"]`.
`_draw_transcribing` (299): spinner-bue `outline=_CLR["accent"]` (allerede),
hovedcirkel/tekst med nye tokens.

- [ ] **Step 4: Kør suiten + røgtest**

Run: `pytest -q && python meeting_app.py`
Expected: Ingen fejl; sidebar-ikoner tegnet; stor knap skifter idle→rec→idle
ved klik (start/stop en test-optagelse uden mikrofon-krav — brug bare klik).

- [ ] **Step 5: Commit**

```bash
git add meeting_app.py
git commit -m "feat(ui): tegnede sidebar-ikoner + reskin af record-knap"
```

---

### Task 4: Optage-view — idle-restyle

Restyle konfig-oversigtskortet + header i `_build_record_screen` (498-623) til
det nye sprog. Ingen strukturændring; kun farver/afstand/hierarki.

**Files:**
- Modify: `meeting_app.py:498-623`
- Test: kør suiten + røgtest (visuelt).

**Interfaces:**
- Consumes: `_CLR`, `_show_view` (til "Skift → Indstillinger"-knapper skal nu kalde `_show_view("indstillinger")`, ikke `_tabview.set`).

- [ ] **Step 1: Ret "Skift"-knappernes mål**

I `_row(...)` for `mic` og `folder` (577-578): kaldet er allerede erstattet i
Task 2 Step 5 → bekræft de peger på `self._show_view("indstillinger")`.

- [ ] **Step 2: Restyle header + kort**

- Header-titel "Mødeværktøj" `text_color=_CLR["text"]`, undertitel
  `_CLR["text_secondary"]`.
- Kort: `fg_color=_CLR["card"]`, `border_color=_CLR["card_border"]`.
- "Ny optagelse"-knap: `fg_color=_CLR["accent"]`, `hover_color=_CLR["accent_hover"]`.
- Entry-felter (i guide og her): `fg_color="#ffffff"`, `border_color=_CLR["card_border"]`.
- Log-boks (`self.log`): `fg_color="#ffffff"`.

- [ ] **Step 3: Kør suiten + røgtest**

Run: `pytest -q && python meeting_app.py`
Expected: Optag-view ser nyt ud; "Skift"-knapper åbner guide/Indstillinger.

- [ ] **Step 4: Commit**

```bash
git add meeting_app.py
git commit -m "feat(ui): restyle optage-view (idle)"
```

---

### Task 5: Optage-view — aktiv optagelse (timer/pille/wave/stop)

Når der optages, skal center-området vise: rød "OPTAGER"-pille (blink), stort
timer-tal, kun mødenavnet (IKKE motor), en let animeret bølge, og en
"Stop & lav referat"-knap. Genbrug eksisterende timer/`status_var`.
Bølgen er dekorativ (ingen rigtige audio-niveauer — YAGNI).

**Files:**
- Modify: `meeting_app.py` — `_build_record_screen` (tilføj aktiv-panel), optage-state-skift (`_toggle_recording`/`set_recording`-stien), `_refresh_summary`.
- Test: `tests/test_recording_view.py`

**Interfaces:**
- Consumes: `_CLR`, `self.timer_var`, `self.name_var`, `self.recording`.
- Produces:
  - `MeetingApp._active_subtitle_text() -> str` — returnerer KUN mødenavnet (aldrig motor). Ren funktion, testbar.
  - `MeetingApp._show_recording_state(on: bool)` — skifter mellem idle-panel og aktiv-panel i optage-viewet.

- [ ] **Step 1: Skriv den fejlende test**

```python
# tests/test_recording_view.py
from __future__ import annotations
import json, sys
from pathlib import Path
import pytest

ctk = pytest.importorskip("customtkinter")
import tkinter
try:
    _r = tkinter.Tk()
    _v = tuple(int(x) for x in _r.tk.call("info", "patchlevel").split(".")[:2])
    _r.destroy()
    if _v >= (9, 0):
        pytest.skip("Tk 9.x", allow_module_level=True)
except Exception:
    pytest.skip("Intet Tk-display", allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    (tmp_path / "meeting_types.json").write_text(
        json.dumps({"a": {"navn": "Møde A"}}), encoding="utf-8")
    monkeypatch.setattr(meeting_app, "TOOL_DIR", tmp_path)
    monkeypatch.setattr(meeting_app, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(meeting_app, "load_state", lambda: {})
    monkeypatch.setattr(meeting_app.MeetingApp, "_open_wizard", lambda self, s=0: None)
    root = ctk.CTk(); root.withdraw()
    a = meeting_app.MeetingApp(root)
    yield a
    root.destroy()


def test_active_subtitle_is_only_meeting_name(app):
    app.name_var.set("Bestyrelsesmøde Q3")
    app.engine_var.set("gemini")
    sub = app._active_subtitle_text()
    assert sub == "Bestyrelsesmøde Q3"
    assert "gemini" not in sub.lower()
    assert "hviske" not in sub.lower()
```

- [ ] **Step 2: Kør testen — verificér at den fejler**

Run: `pytest tests/test_recording_view.py -q`
Expected: FAIL (`_active_subtitle_text` findes ikke).

- [ ] **Step 3: Tilføj hjælpe-metode + aktiv-panel**

```python
    def _active_subtitle_text(self) -> str:
        """Undertekst under timeren mens der optages — KUN mødenavnet."""
        return self.name_var.get().strip() or self.type_var.get()
```

Byg et `self._active_panel` (CTkFrame) i `_build_record_screen`, pakket
`side="top"` men skjult ved start (`pack_forget`). Indhold:

```python
        self._active_panel = ctk.CTkFrame(outer, fg_color=_CLR["bg"])
        self._rec_pill = ctk.CTkLabel(
            self._active_panel, text="●  OPTAGER",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=_CLR["rec_active"])
        self._rec_pill.pack(pady=(24, 6))
        self._big_timer = ctk.CTkLabel(
            self._active_panel, textvariable=self.timer_var,
            font=ctk.CTkFont(family="SF Mono", size=44, weight="bold"),
            text_color=_CLR["text"])
        self._big_timer.pack()
        self._rec_subtitle = ctk.CTkLabel(
            self._active_panel, text="", font=ctk.CTkFont(size=14),
            text_color=_CLR["text_secondary"])
        self._rec_subtitle.pack(pady=(2, 14))
        ctk.CTkButton(
            self._active_panel, text="■  Stop & lav referat",
            height=44, corner_radius=11, fg_color=_CLR["stop_blue"],
            hover_color=_CLR["stop_hover"], text_color="#ffffff",
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._toggle_recording).pack()
```

- [ ] **Step 4: Skift mellem idle- og aktiv-panel**

```python
    def _show_recording_state(self, on: bool) -> None:
        if on:
            self._rec_subtitle.configure(text=self._active_subtitle_text())
            self._active_panel.pack(side="top", fill="x")
            # skjul idle-oversigtskortet (gem reference ved bygning som self._idle_card)
            self._idle_card.pack_forget()
        else:
            self._active_panel.pack_forget()
            self._idle_card.pack(fill="x", padx=24, pady=(16, 0))
```

Kald `self._show_recording_state(True)` hvor optagelsen starter, og `(False)`
hvor den stopper/afsluttes (samme sted som `hero_btn.set_recording(...)`).
Gem oversigtskortet i `self._idle_card` ved bygning.

- [ ] **Step 5: Kør test + suite + røgtest**

Run: `pytest tests/test_recording_view.py -q && pytest -q`
Expected: PASS. Røgtest: start optagelse → stort timer + "OPTAGER" + kun navn;
stop → tilbage til oversigt.

- [ ] **Step 6: Commit**

```bash
git add meeting_app.py tests/test_recording_view.py
git commit -m "feat(ui): aktiv optage-skærm (timer/pille/stop) uden motor-visning"
```

---

### Task 6: Guide-restyle (progress-bar + flueben-kort)

Reskin `MeetingWizard` (2787-3062): progress-bar med 4 segmenter i toppen
(erstat prik-teksten), valg-kort med blåt flueben når valgt, footer-knapper i
nyt sprog. Trin, tekster og logik uændret.

**Files:**
- Modify: `meeting_app.py:2787-3062`
- Test: kør suiten + røgtest (visuelt).

**Interfaces:**
- Consumes: `_CLR`. Produces: uændrede signaturer.

- [ ] **Step 1: Progress-bar i stedet for prikker**

I `go_to` (2868): erstat `self._dots`-teksten med 4 tynde segment-frames.
Byg dem én gang i `__init__` som `self._seg = [CTkFrame(...)]` og farv i
`go_to`: segment `i <= step` → `_CLR["accent"]`, ellers `_CLR["card_border"]`.

```python
        segbar = ctk.CTkFrame(top, fg_color="transparent")
        segbar.pack(fill="x", pady=(0, 6))
        self._seg = []
        for i in range(len(self.STEPS)):
            s = ctk.CTkFrame(segbar, height=5, corner_radius=3,
                             fg_color=_CLR["card_border"])
            s.pack(side="left", fill="x", expand=True, padx=(0 if i == 0 else 6, 0))
            self._seg.append(s)
```

I `go_to`:

```python
        for i, s in enumerate(self._seg):
            s.configure(fg_color=_CLR["accent"] if i <= self._step else _CLR["card_border"])
        self._dots.configure(text=f"Trin {self._step + 1} af {len(self.STEPS)}")
```

- [ ] **Step 2: Flueben på valgte kort**

I `_highlight_form/_highlight_type/_highlight_motor`: ud over `border_color`,
vis/skjul et lille flueben-canvas i det valgte kort. Tilføj ved kort-bygning et
skjult `check`-canvas (18×18) i hvert kort og tegn ✓ (to linjer) når valgt:

```python
    @staticmethod
    def _draw_check(canvas, on):
        canvas.delete("all")
        if on:
            canvas.create_oval(0, 0, 18, 18, fill=_CLR["accent"], outline="")
            canvas.create_line(5, 9, 8, 12, fill="#fff", width=2)
            canvas.create_line(8, 12, 13, 6, fill="#fff", width=2)
```

- [ ] **Step 3: Footer-knapper**

`_next_btn`/`_back_btn` (2835-2845): `fg_color=_CLR["accent"]`,
`hover_color=_CLR["accent_hover"]`; sidste trin-tekst "Start optagelse".

- [ ] **Step 4: Kør suiten + røgtest**

Run: `pytest -q && python meeting_app.py`
Expected: Guide åbner ved opstart; progress-bar fyldes; valgte kort får flueben.

- [ ] **Step 5: Commit**

```bash
git add meeting_app.py
git commit -m "feat(ui): guide med progress-bar og flueben-kort"
```

---

### Task 7: Historik-restyle (status-badges)

Reskin `HistorikTab` (3221+) til kort med status-badge. Tilføj en ren
mapping-funktion status→(tekst, farve, bg) der kan unit-testes.

**Files:**
- Modify: `meeting_app.py` — `HistorikTab._build_ui` og møde-kort.
- Test: `tests/test_historik_badges.py`

**Interfaces:**
- Produces: `meeting_app.historik_badge(status: str) -> tuple[str, str, str]`
  (label, fg-hex, bg-hex). Gyldige statusser: `"klar"`, `"i_gang"`, `""`.

- [ ] **Step 1: Skriv den fejlende test**

```python
# tests/test_historik_badges.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_app


def test_badge_klar():
    label, fg, bg = meeting_app.historik_badge("klar")
    assert label == "Referat klar"
    assert fg.lower() == "#2e7d5b" and bg.lower() == "#e3f5ec"


def test_badge_i_gang():
    label, fg, bg = meeting_app.historik_badge("i_gang")
    assert "ranskrib" in label.lower()
    assert fg.lower() == "#8a6d00" and bg.lower() == "#fbf3d8"


def test_badge_none():
    assert meeting_app.historik_badge("") == ("", "", "")
```

- [ ] **Step 2: Kør testen — verificér fejl**

Run: `pytest tests/test_historik_badges.py -q`
Expected: FAIL (`historik_badge` findes ikke).

- [ ] **Step 3: Implementér mapping + brug den**

```python
def historik_badge(status: str):
    """Map en møde-status til (label, tekstfarve, baggrundsfarve). Tom → ingen badge."""
    if status == "klar":
        return ("Referat klar", _CLR["badge_ok"], _CLR["badge_ok_bg"])
    if status == "i_gang":
        return ("Transskriberer…", _CLR["badge_busy"], _CLR["badge_busy_bg"])
    return ("", "", "")
```

I `HistorikTab`: for hvert møde-kort, afled status (findes referat-fil → "klar";
findes kun transskription/ingen referat → afhængig af eksisterende felter → ""),
og hvis label ikke er tom, tilføj en `CTkLabel` med `fg_color=bg`,
`text_color=fg`, `corner_radius=10`. Kortene: `fg_color=_CLR["card"]`,
`border_color=_CLR["card_border"]`.

- [ ] **Step 4: Kør test + suite + røgtest**

Run: `pytest tests/test_historik_badges.py -q && pytest -q && python meeting_app.py`
Expected: PASS; Historik-view viser kort med badges.

- [ ] **Step 5: Commit**

```bash
git add meeting_app.py tests/test_historik_badges.py
git commit -m "feat(ui): historik-kort med status-badges"
```

---

### Task 8: Indstillinger + underfaner (Ordliste/Mødetyper/Transkribér)

Saml sekundær-værktøjer under Indstillinger som interne sektioner (en lille
CTkSegmentedButton eller under-knapper i toppen af Indstillinger-viewet), så
sidebar forbliver enkel. Transkribér fil beholder sit eget sidebar-punkt
(hyppig handling), mens Ordliste + Mødetyper flyttes ind i Indstillinger.

**Files:**
- Modify: `meeting_app.py` — `_build_ui` (view-tildelinger fra Task 2), `_build_settings_tab`.
- Test: `tests/test_shell.py` (udvid), røgtest.

**Interfaces:**
- Consumes: `VocabularyTab`, `MeetingTypesTab`, `_show_view`.
- Produces: `MeetingApp._show_settings_section(name: str)` — `"generelt" | "ordliste" | "modetyper"`.

- [ ] **Step 1: Udvid shell-testen**

```python
def test_settings_sections(app):
    app._show_view("indstillinger")
    for sec in ("generelt", "ordliste", "modetyper"):
        app._show_settings_section(sec)
        assert app._settings_current == sec
```

Run: `pytest tests/test_shell.py::test_settings_sections -q` → FAIL.

- [ ] **Step 2: Byg sektions-skifter i Indstillinger**

I `_build_ui`: byg `VocabularyTab`/`MeetingTypesTab` ind i under-frames af
Indstillinger-viewet i stedet for separate views. Tilføj øverst i
Indstillinger en `CTkSegmentedButton(values=["Generelt","Ordliste","Mødetyper"])`
med `command` → `_show_settings_section`. Implementér metoden så den
pack/pack_forget'er de tre sektions-frames og sætter `self._settings_current`.

- [ ] **Step 3: Fjern Ordliste/Mødetyper som selvstændige views**

Bekræft sidebar-items = `optag, transkriber, historik, indstillinger` (4 stk).
`grep -n '"ordliste"\|"modetyper"' meeting_app.py` → kun inde i settings-sektion.

- [ ] **Step 4: Kør tests + suite + røgtest**

Run: `pytest tests/test_shell.py -q && pytest -q && python meeting_app.py`
Expected: PASS; Indstillinger har 3 sektioner; sidebar har 4 punkter.

- [ ] **Step 5: Commit**

```bash
git add meeting_app.py tests/test_shell.py
git commit -m "feat(ui): saml Ordliste/Mødetyper under Indstillinger"
```

---

### Task 9: MiniHUD-restyle

Reskin `MiniHUD` (3065-3195) til nyt sprog: mørk pille-baggrund, rød blink-prik,
stort timer-tal, stop-knap i rec-rød, "åbn"-knap neutral. Funktion uændret.
Undertekst = kun mødenavn (samme regel som aktiv-skærm).

**Files:**
- Modify: `meeting_app.py:3065-3195`
- Test: kør suiten + røgtest (minimér under optagelse).

**Interfaces:**
- Consumes: `_CLR`, `_active_subtitle_text` (Task 5) — kald den ved `set_meeting`.

- [ ] **Step 1: Reskin pille + knapper**

- `pill`: `fg_color=_CLR["sidebar_bg"]` (mørk), tekst lys.
- `self._caption`/`self._timer`: lyse farver (`#ffffff` / `_CLR["sidebar_icon_active"]`).
- `self._icon` blink: `_CLR["rec_active"]` ↔ `_CLR["rec_ring"]` (allerede).
- Stop-knap: `fg_color=_CLR["rec_active"]`, hover mørkere rød.
- "åbn"-knap (⤢ erstattes af tegnet pil eller neutral tekst "Åbn"): neutral.

- [ ] **Step 2: Sæt undertekst korrekt**

Hvor HUD'en oprettes/opdateres i `MeetingApp`, kald
`hud.set_meeting(self._active_subtitle_text())` (kun navn).

- [ ] **Step 3: Kør suiten + røgtest**

Run: `pytest -q && python meeting_app.py`
Expected: Start optagelse, minimér → HUD i nyt sprog, timer opdaterer, Stop virker.

- [ ] **Step 4: Commit**

```bash
git add meeting_app.py
git commit -m "feat(ui): restyle MiniHUD til sidebar-app sproget"
```

---

### Task 10: Fuld verifikation + Windows-røgtest

Slut-tjek: hele suiten grøn, ingen `_tabview`/emoji-rester, og manuel røgtest på
Windows (frosset bundle er målet — men mindst kildekørsel på Windows hvis muligt).

**Files:**
- Modify: kun evt. småfejl fundet under test.

- [ ] **Step 1: Statiske tjek**

Run:
```bash
grep -n "_tabview" meeting_app.py            # forventet: tomt
grep -nE "🎙|🎙️|●.*emoji" meeting_app.py    # kontrollér: ingen emoji som ikon
python -c "import ast; ast.parse(open('meeting_app.py').read()); print('ok')"
```
Expected: intet `_tabview`, ingen emoji-ikoner, `ok`.

- [ ] **Step 2: Hele testsuiten**

Run: `pytest -q`
Expected: Alle tests pass (GUI-tests skippes kun hvis intet display).

- [ ] **Step 3: Manuel røgtest (macOS + Windows hvis muligt)**

Tjekliste (kør `python meeting_app.py`):
- Guide åbner ved opstart, 4 trin, progress-bar + flueben, "Start optagelse".
- Sidebar: Optag / Fil / Historik / Indstillinger skifter view.
- Optagelse: aktiv-skærm med stort timer + "OPTAGER", KUN mødenavn (ingen motor).
- Stop → tilbage til oversigt; referat-flow uændret.
- Minimér under optagelse → MiniHUD i nyt sprog.
- Historik: kort med badges.
- Indstillinger: 3 sektioner (Generelt/Ordliste/Mødetyper).

- [ ] **Step 4: Opdater CHANGELOG hvis den findes**

Run: `test -f CHANGELOG.md && echo findes || echo mangler`
Hvis den findes: tilføj en linje under næste version om redesignet.

- [ ] **Step 5: Commit + klar til PR**

```bash
git add -A
git commit -m "chore: verifikation af sidebar-app redesign"
```

(Bump `_version.py` + `installer.iss` og PR sker separat, efter godkendelse —
IKKE en del af denne plan, da versionering/release er uden for omfang.)

---

## Self-Review (udført)

**Spec-dækning:** §3 farvesystem → Task 1. §4 shell → Task 2-3. §5.1 idle → Task 4.
§5.2 guide → Task 6. §5.3 aktiv → Task 5. §5.4 Historik → Task 7. §5.5
Indstillinger+underfaner → Task 8. §5.6 MiniHUD → Task 9. §8 test → Task 10.
§9 beslutning 1 (Ordliste/Mødetyper i Indstillinger) → Task 8. §9 beslutning 2
(accent blå) → Task 1 palet. Alle sektioner dækket.

**Placeholder-scan:** Ingen TBD/TODO. Alle kode-steps har konkret kode; visuelle
steps angiver eksakte tokens/linjer.

**Type-konsistens:** `_show_view(name)`, `_views`, `_active_subtitle_text()`,
`_show_recording_state(bool)`, `historik_badge(status)`, `_show_settings_section(name)`
bruges konsistent på tværs af tasks og tests.
