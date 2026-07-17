# Ad-hoc mødetype + referatniveau pr. møde — implementeringsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** I guidens trin 2 kan brugeren (a) skrive en ny mødetype som gemmes automatisk med standardværdier, og (b) vælge referatniveau (Kort/Mellem/Grundig) der kun gælder det aktuelle møde.

**Architecture:** Ren logik (navneopslag, nøgle-slug, niveau-overstyring, persistering) lægges som modul-funktioner i `meeting_app.py` og testes uden GUI. `MeetingApp` får en tynd `_save_adhoc_type` + et `referat_level_override`-felt der anvendes i `_start_recording`. Guidens `_build_type` får et "+ Ny mødetype"-kort og en segmenteret niveau-vælger. `meeting_tool.py` ændres ikke.

**Tech Stack:** Python 3.12, CustomTkinter, pytest (+pytest-mock). Spec: `docs/superpowers/specs/2026-07-16-adhoc-type-og-referatniveau-design.md`.

## Global Constraints

- Branch: `feature/adhoc-type-og-referatniveau` — aldrig push til `main`.
- `meeting_tool.py` ændres IKKE (kun genbrug: `MEETING_TYPE_DEFAULTS`, `DETALJENIVEAUER`, `_normalize_meeting_type`).
- Kode-identifikatorer på engelsk, kommentarer/strenge/docstrings på dansk (som resten af filen).
- Hele den eksisterende suite skal forblive grøn: `python -m pytest tests/ -q` (300+ tests; GUI-tests springes over på Tk 9.x — det er forventet).
- Kør tests med repoets venv: `.venv312/bin/python -m pytest` hvis den findes, ellers `python3 -m pytest`.

---

### Task 1: Rene hjælpefunktioner (slug, ensure, override, persist)

**Files:**
- Modify: `meeting_app.py` (imports øverst, linje ~19-30; nye funktioner umiddelbart FØR `class MeetingTypesTab`, linje ~2029)
- Test: `tests/test_adhoc_type.py` (ny fil)

**Interfaces:**
- Consumes: `meeting_tool.MEETING_TYPE_DEFAULTS`, `meeting_tool.DETALJENIVEAUER`, `meeting_tool._normalize_meeting_type`
- Produces (bruges af Task 2 og 3):
  - `_slugify_type_name(navn: str) -> str`
  - `ensure_meeting_type(types: dict, navn: str) -> tuple[dict, str, bool]` — `(types, nøgle, oprettet)`; muterer aldrig input
  - `override_detaljeniveau(mtype: dict, niveau: str | None) -> dict` — kopi med nyt niveau, ellers samme objekt
  - `persist_meeting_types(types: dict, path: Path) -> str | None` — `None` = succes, ellers fejlbesked

- [ ] **Step 1: Skriv de fejlende tests**

Opret `tests/test_adhoc_type.py`:

```python
"""Ad-hoc mødetyper fra guiden: slug, navneopslag, niveau-overstyring, persistering."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_app
import meeting_tool


def _types() -> dict:
    return {
        "driftledelse": meeting_tool._normalize_meeting_type(
            {"navn": "Driftledelsesmøde", "detaljeniveau": "kortfattet"}
        ),
    }


class TestSlugifyTypeName:
    def test_translittererer_og_binder(self):
        assert meeting_app._slugify_type_name("Ansættelsessamtale") == "ansaettelsessamtale"
        assert meeting_app._slugify_type_name("Møde: Økonomi & Løn") == "moede-oekonomi-loen"

    def test_kun_specialtegn_giver_fallback(self):
        assert meeting_app._slugify_type_name("!!!") == "type"


class TestEnsureMeetingType:
    def test_creates_new_type_with_defaults(self):
        types, key, created = meeting_app.ensure_meeting_type(_types(), "Ansættelsessamtale")
        assert created is True
        assert key == "ansaettelsessamtale"
        t = types[key]
        assert t["navn"] == "Ansættelsessamtale"
        assert t["detaljeniveau"] == "balanceret"
        assert t["opgaveliste"] is True
        assert t["citater"] is False
        assert t["deltagere"] == []

    def test_reuses_existing_on_case_insensitive_name_match(self):
        base = _types()
        types, key, created = meeting_app.ensure_meeting_type(base, "  driftledelsesmøde ")
        assert created is False
        assert key == "driftledelse"
        assert types is base  # ingen kopi, ingen ændring

    def test_does_not_mutate_input_dict(self):
        base = _types()
        types, key, _ = meeting_app.ensure_meeting_type(base, "Ny type")
        assert key not in base
        assert key in types

    def test_slug_collision_gets_numeric_suffix(self):
        base = _types()
        base["ny-type"] = meeting_tool._normalize_meeting_type({"navn": "Noget andet"})
        _, key, created = meeting_app.ensure_meeting_type(base, "Ny type")
        assert created is True
        assert key == "ny-type-2"

    def test_empty_name_raises(self):
        with pytest.raises(ValueError):
            meeting_app.ensure_meeting_type(_types(), "   ")


class TestOverrideDetaljeniveau:
    def test_returns_copy_with_new_level(self):
        t = _types()["driftledelse"]
        out = meeting_app.override_detaljeniveau(t, "grundig")
        assert out["detaljeniveau"] == "grundig"
        assert out is not t
        assert t["detaljeniveau"] == "kortfattet"  # original uændret

    def test_same_level_returns_original(self):
        t = _types()["driftledelse"]
        assert meeting_app.override_detaljeniveau(t, "kortfattet") is t

    def test_invalid_or_none_level_returns_original(self):
        t = _types()["driftledelse"]
        assert meeting_app.override_detaljeniveau(t, "episk") is t
        assert meeting_app.override_detaljeniveau(t, None) is t


class TestPersistMeetingTypes:
    def test_writes_json_and_returns_none(self, tmp_path):
        path = tmp_path / "meeting_types.json"
        assert meeting_app.persist_meeting_types(_types(), path) is None
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["driftledelse"]["navn"] == "Driftledelsesmøde"

    def test_oserror_returns_message(self, tmp_path):
        # tmp_path er en mappe → write_text fejler med OSError
        msg = meeting_app.persist_meeting_types(_types(), tmp_path)
        assert msg is not None and "Kunne ikke gemme" in msg
```

- [ ] **Step 2: Kør testene — de skal fejle**

Kør: `python -m pytest tests/test_adhoc_type.py -q`
Forventet: FAIL/ERROR med `AttributeError: module 'meeting_app' has no attribute '_slugify_type_name'` (m.fl.)

- [ ] **Step 3: Implementér hjælpefunktionerne**

I `meeting_app.py`: tilføj `re` til imports (alfabetisk, mellem `queue` og `subprocess`):

```python
import re
```

Indsæt umiddelbart FØR `class MeetingTypesTab` (linje ~2029):

```python
# ---------------------------------------------------------------------------
# Ad-hoc mødetyper (oprettes direkte fra guidens trin 2)
# ---------------------------------------------------------------------------

_SLUG_TRANS = str.maketrans({"æ": "ae", "ø": "oe", "å": "aa"})


def _slugify_type_name(navn: str) -> str:
    """Nøgle af et typenavn: små bogstaver, æ/ø/å translittereret,
    alt ikke-alfanumerisk → '-'. Tomt resultat → 'type'."""
    s = navn.strip().lower().translate(_SLUG_TRANS)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "type"


def ensure_meeting_type(types: dict, navn: str) -> tuple[dict, str, bool]:
    """Slå navnet op (trimmet, case-insensitivt) i types; findes det,
    genbruges den eksisterende type — der oprettes aldrig dubletter, og
    eksisterende typer overskrives aldrig. Ellers oprettes en ny type med
    standardværdier (jf. spec §3); slug-kollision får talsuffiks (-2, -3, …).

    Returnerer (types, nøgle, oprettet). Ved oprettelse returneres en NY
    dict — den givne muteres aldrig."""
    target = navn.strip()
    if not target:
        raise ValueError("Tomt typenavn")
    folded = target.casefold()
    for k, t in types.items():
        if t.get("navn", "").strip().casefold() == folded:
            return types, k, False
    key = base = _slugify_type_name(target)
    n = 2
    while key in types:
        key = f"{base}-{n}"
        n += 1
    new_types = dict(types)
    new_types[key] = meeting_tool._normalize_meeting_type({"navn": target})
    return new_types, key, True


def override_detaljeniveau(mtype: dict, niveau: str | None) -> dict:
    """Kopi af mtype med detaljeniveau erstattet — kun hvis niveau er gyldigt
    og forskelligt fra typens eget. Ellers returneres mtype uændret (samme
    objekt). Originalen muteres aldrig, så typen i self._meeting_types og
    meeting_types.json påvirkes ikke."""
    if niveau not in meeting_tool.DETALJENIVEAUER or mtype.get("detaljeniveau") == niveau:
        return mtype
    out = dict(mtype)
    out["detaljeniveau"] = niveau
    return out


def persist_meeting_types(types: dict, path: Path) -> str | None:
    """Skriv types til path (samme format som Mødetyper-fanen).
    Returnerer None ved succes, ellers en dansk fejlbesked."""
    try:
        path.write_text(
            json.dumps(types, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return None
    except OSError as e:
        return f"Kunne ikke gemme: {e}"
```

- [ ] **Step 4: Kør testene igen — de skal bestå**

Kør: `python -m pytest tests/test_adhoc_type.py -q`
Forventet: 12 passed

- [ ] **Step 5: Commit**

```bash
git add meeting_app.py tests/test_adhoc_type.py
git commit -m "feat: rene hjælpere til ad-hoc mødetyper og niveau-overstyring"
```

---

### Task 2: MeetingApp-plumbing — gem ad-hoc type, overstyringsfelt, anvendelse ved start

**Files:**
- Modify: `meeting_app.py`:
  - `MeetingApp.__init__` — nyt felt efter `self._type_keys = list(self._meeting_types)` (linje ~486)
  - `MeetingApp._on_type_selected` (linje ~1260) — nulstil overstyring
  - `MeetingApp._start_recording` (linje ~1501: `meeting_type = self._meeting_types[self._type_key]`) — anvend overstyring + nulstil efter start
  - Ny metode `MeetingApp._save_adhoc_type` (placér efter `refresh_meeting_types`, linje ~1284)
  - Ny metode `MeetingTypesTab.reload_from_disk` (placér efter `_persist`, linje ~2322)
- Test: `tests/test_adhoc_type.py` (udvid)

**Interfaces:**
- Consumes: `ensure_meeting_type`, `persist_meeting_types` (Task 1); `MeetingTypesTab.TYPES_FILE`; `self._on_meeting_types_changed()` (eksisterende)
- Produces (bruges af Task 3):
  - `MeetingApp._save_adhoc_type(navn: str) -> str` — returnerer nøglen (ny eller genbrugt)
  - `MeetingApp.referat_level_override: str | None` — `None` = følg typen
  - `MeetingTypesTab.reload_from_disk() -> None`

- [ ] **Step 1: Skriv de fejlende tests**

Tilføj nederst i `tests/test_adhoc_type.py`:

```python
from types import MethodType, SimpleNamespace


def _fake_app(tmp_path, monkeypatch, types=None):
    """Minimal MeetingApp-attrap: kun det _save_adhoc_type rører."""
    monkeypatch.setattr(
        meeting_app.MeetingTypesTab, "TYPES_FILE", tmp_path / "meeting_types.json"
    )
    calls = []
    app = SimpleNamespace(
        _meeting_types=types if types is not None else _types(),
        _type_keys=["driftledelse"],
        _on_meeting_types_changed=lambda: calls.append("changed"),
        _types_tab=SimpleNamespace(reload_from_disk=lambda: calls.append("tab-reload")),
        status_var=SimpleNamespace(set=lambda s: calls.append(("status", s))),
    )
    app.calls = calls
    app._save_adhoc_type = MethodType(meeting_app.MeetingApp._save_adhoc_type, app)
    return app


class TestSaveAdhocType:
    def test_creates_persists_and_refreshes(self, tmp_path, monkeypatch):
        app = _fake_app(tmp_path, monkeypatch)
        key = app._save_adhoc_type("Ansættelsessamtale")
        assert key == "ansaettelsessamtale"
        data = json.loads(
            (tmp_path / "meeting_types.json").read_text(encoding="utf-8")
        )
        assert data[key]["navn"] == "Ansættelsessamtale"
        assert "changed" in app.calls and "tab-reload" in app.calls

    def test_existing_name_reuses_without_writing(self, tmp_path, monkeypatch):
        app = _fake_app(tmp_path, monkeypatch)
        key = app._save_adhoc_type("Driftledelsesmøde")
        assert key == "driftledelse"
        assert not (tmp_path / "meeting_types.json").exists()
        assert app.calls == []

    def test_write_failure_keeps_type_in_memory(self, tmp_path, monkeypatch):
        app = _fake_app(tmp_path, monkeypatch)
        # Peg TYPES_FILE på en mappe → persist fejler med OSError
        monkeypatch.setattr(meeting_app.MeetingTypesTab, "TYPES_FILE", tmp_path)
        key = app._save_adhoc_type("Ansættelsessamtale")
        assert key == "ansaettelsessamtale"
        assert key in app._meeting_types          # brugbar for DETTE møde
        assert key in app._type_keys
        assert "changed" not in app.calls          # intet disk-refresh
        assert any(c[0] == "status" for c in app.calls if isinstance(c, tuple))
```

- [ ] **Step 2: Kør testene — de skal fejle**

Kør: `python -m pytest tests/test_adhoc_type.py -q`
Forventet: de 3 nye FAIL med `AttributeError: 'MeetingApp' ... has no attribute '_save_adhoc_type'`; de 12 gamle passed

- [ ] **Step 3: Implementér plumbing**

**(a)** I `MeetingApp.__init__`, efter `self._type_keys = list(self._meeting_types)` (linje ~486):

```python
        # Referatniveau-overstyring for det NÆSTE møde (None = følg typen).
        # Sættes i guidens trin 2; nulstilles ved typeskift og efter start.
        self.referat_level_override: str | None = None
```

**(b)** I `MeetingApp._on_type_selected` (linje ~1260), som første linje i metoden:

```python
    def _on_type_selected(self, label: str):
        self.referat_level_override = None  # nyt typevalg → følg typens niveau
        self._type_key = self._key_for_label(label)
```

**(c)** Ny metode efter `refresh_meeting_types` (linje ~1284):

```python
    def _save_adhoc_type(self, navn: str) -> str:
        """Opret (eller genbrug) en mødetype ud fra et ad-hoc navn fra guiden.

        Ny type gemmes i meeting_types.json og alle dropdowns/faner
        opdateres. Kan filen ikke skrives, beholdes typen i hukommelsen så
        DETTE møde stadig kan startes — den er blot ikke gemt til næste gang
        (jf. spec §6). Returnerer typens nøgle."""
        types, key, created = ensure_meeting_type(self._meeting_types, navn)
        if not created:
            return key
        fejl = persist_meeting_types(types, MeetingTypesTab.TYPES_FILE)
        if fejl is None:
            self._on_meeting_types_changed()      # genindlæser alt fra disk
            self._types_tab.reload_from_disk()    # Mødetyper-fanens kopi
        else:
            self._meeting_types = types
            self._type_keys = list(types)
            self.status_var.set(fejl)
        return key
```

**(d)** I `MeetingApp._start_recording`: erstat linjen (linje ~1501)

```python
        meeting_type = self._meeting_types[self._type_key]
```

med

```python
        # Pr.-møde-overstyring af referatniveau: kopi af typen, original røres ikke
        meeting_type = override_detaljeniveau(
            self._meeting_types[self._type_key], self.referat_level_override
        )
```

og tilføj umiddelbart efter `self.worker_thread.start()` (linje ~1512):

```python
        self.referat_level_override = None  # gjaldt kun dette møde
```

**(e)** Ny metode i `MeetingTypesTab` efter `_persist` (linje ~2322):

```python
    def reload_from_disk(self):
        """Genindlæs typer fra disk (fx efter ad-hoc oprettelse i guiden), så
        et senere Gem her i fanen ikke overskriver med en forældet kopi.
        Bevarer markeringen hvis nøglen stadig findes."""
        self.types = meeting_tool.load_meeting_types(CONFIG_DIR)
        if self.current_key not in self.types:
            self.current_key = next(iter(self.types))
        self._load_into_form(self.current_key)  # kalder også _refresh_selector
```

- [ ] **Step 4: Kør testene — de skal bestå**

Kør: `python -m pytest tests/test_adhoc_type.py -q`
Forventet: 15 passed

- [ ] **Step 5: Kør hele suiten**

Kør: `python -m pytest tests/ -q`
Forventet: alt grønt (samme antal skipped som før — GUI-tests på Tk 9.x)

- [ ] **Step 6: Commit**

```bash
git add meeting_app.py tests/test_adhoc_type.py
git commit -m "feat: gem ad-hoc mødetype fra guiden + referatniveau-overstyring i optageflowet"
```

---

### Task 3: Guidens trin 2 — "+ Ny mødetype"-kort og niveau-vælger

**Files:**
- Modify: `meeting_app.py`:
  - Modul-konstanter (placér lige efter `_SLUG_TRANS`-blokken fra Task 1)
  - `MeetingWizard.__init__` — nye felter efter `self._type_keys = list(app._meeting_types)` (linje ~3047)
  - `MeetingWizard._next` (linje ~3161) — commit ad-hoc ved afgang fra trin 2
  - `MeetingWizard._build_type` (linje ~3228) — ad-hoc kort + niveau-række
  - `MeetingWizard._select_type` (linje ~3262) og `_highlight_type` (linje ~3268)
  - Nye metoder: `_select_adhoc`, `_commit_adhoc_type`, `_current_level`, `_sync_level_seg`, `_on_level_selected`
- Test: `tests/test_wizard_adhoc.py` (ny fil, GUI — skip-guarded som `tests/test_meeting_types_refresh.py`)

**Interfaces:**
- Consumes: `app._save_adhoc_type(navn) -> str`, `app.referat_level_override` (Task 2); `_bind_recursive`, `self._add_check`, `self._draw_check`, `_CLR` (eksisterende)
- Produces: kun UI-adfærd — ingen nye offentlige grænseflader

- [ ] **Step 1: Skriv den fejlende GUI-test**

Opret `tests/test_wizard_adhoc.py`:

```python
"""GUI-tests: ad-hoc mødetype + referatniveau-vælger i guidens trin 2.

Kræver et display (Tk). Springes over hvis ingen findes eller Tk >= 9
(samme guard som test_meeting_types_refresh)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

ctk = pytest.importorskip("customtkinter")
import tkinter

try:
    _probe_root = tkinter.Tk()
    _tk_version = tuple(
        int(x) for x in _probe_root.tk.call("info", "patchlevel").split(".")[:2]
    )
    _probe_root.destroy()
    if _tk_version >= (9, 0):
        pytest.skip(
            "Tk 9.x er inkompatibel med customtkinter CTkScrollableFrame — spring over",
            allow_module_level=True,
        )
except Exception:  # pragma: no cover - kun på headless miljøer
    pytest.skip("Intet Tk-display tilgængeligt", allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_app
import meeting_tool


@pytest.fixture
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture
def fake_app(root, tmp_path, monkeypatch):
    """Minimal app-attrap til MeetingWizard: rigtig _save_adhoc_type,
    resten SimpleNamespace. Typefil isoleret til tmp_path."""
    monkeypatch.setattr(
        meeting_app.MeetingTypesTab, "TYPES_FILE", tmp_path / "meeting_types.json"
    )
    types = {
        "driftledelse": meeting_tool._normalize_meeting_type(
            {"navn": "Driftledelsesmøde", "detaljeniveau": "kortfattet"}
        ),
    }
    app = SimpleNamespace(
        root=root,
        _meeting_types=types,
        _type_keys=list(types),
        _type_key="driftledelse",
        type_var=ctk.StringVar(value="Driftledelsesmøde"),
        referat_level_override=None,
        _on_meeting_types_changed=lambda: None,
        _types_tab=SimpleNamespace(reload_from_disk=lambda: None),
        status_var=SimpleNamespace(set=lambda s: None),
        _on_type_selected=lambda label: None,
        tmp_path=tmp_path,
    )
    app._save_adhoc_type = MethodType(meeting_app.MeetingApp._save_adhoc_type, app)
    return app


def _wizard_on_type_step(fake_app):
    wiz = meeting_app.MeetingWizard(fake_app, start_step=1)
    wiz.withdraw()
    return wiz


def test_commit_adhoc_creates_type_and_selects_it(fake_app):
    wiz = _wizard_on_type_step(fake_app)
    wiz._adhoc_var.set("Ansættelsessamtale")
    wiz._select_adhoc()
    wiz._commit_adhoc_type()
    assert fake_app._type_key == "ansaettelsessamtale"
    assert fake_app.type_var.get() == "Ansættelsessamtale"
    data = json.loads(
        (fake_app.tmp_path / "meeting_types.json").read_text(encoding="utf-8")
    )
    assert data["ansaettelsessamtale"]["detaljeniveau"] == "balanceret"
    wiz.destroy()


def test_commit_with_empty_name_keeps_existing_selection(fake_app):
    wiz = _wizard_on_type_step(fake_app)
    wiz._select_adhoc()          # aktivt, men feltet er tomt
    wiz._commit_adhoc_type()
    assert fake_app._type_key == "driftledelse"
    assert not (fake_app.tmp_path / "meeting_types.json").exists()
    wiz.destroy()


def test_level_selector_defaults_to_type_level(fake_app):
    wiz = _wizard_on_type_step(fake_app)
    assert wiz._level_seg.get() == "Kort"          # driftledelse = kortfattet
    assert fake_app.referat_level_override is None
    wiz.destroy()


def test_choosing_other_level_sets_override_only(fake_app):
    wiz = _wizard_on_type_step(fake_app)
    wiz._on_level_selected("Grundig")
    assert fake_app.referat_level_override == "grundig"
    # Typen selv er urørt
    assert fake_app._meeting_types["driftledelse"]["detaljeniveau"] == "kortfattet"
    # Vælges typens eget niveau igen → ingen overstyring
    wiz._on_level_selected("Kort")
    assert fake_app.referat_level_override is None
    wiz.destroy()


def test_type_click_resets_override_and_syncs_selector(fake_app):
    wiz = _wizard_on_type_step(fake_app)
    wiz._on_level_selected("Grundig")
    wiz._select_type("driftledelse")
    assert fake_app.referat_level_override is None
    assert wiz._level_seg.get() == "Kort"
    wiz.destroy()


def test_override_survives_adhoc_commit(fake_app):
    wiz = _wizard_on_type_step(fake_app)
    wiz._adhoc_var.set("Ansættelsessamtale")
    wiz._select_adhoc()
    wiz._on_level_selected("Grundig")   # overstyring for ad-hoc mødet
    wiz._commit_adhoc_type()
    assert fake_app.referat_level_override == "grundig"
    wiz.destroy()
```

- [ ] **Step 2: Kør testene — de skal fejle (eller skippe på Tk 9.x)**

Kør: `python -m pytest tests/test_wizard_adhoc.py -q`
Forventet på maskine med Tk 8.6: FAIL med `AttributeError: 'MeetingWizard' object has no attribute '_adhoc_var'`. På denne Mac (Tk 9.x): 6 skipped — så verificeres via Windows/CI senere; fortsæt.

- [ ] **Step 3: Implementér guide-ændringerne**

**(a)** Modul-konstanter, lige efter `_SLUG_TRANS`-funktionsblokken fra Task 1:

```python
# Etiketter i guidens niveau-vælger ↔ interne niveauer
_NIVEAU_LABELS = {"kortfattet": "Kort", "balanceret": "Mellem", "grundig": "Grundig"}
_NIVEAU_KEYS = {v: k for k, v in _NIVEAU_LABELS.items()}
```

**(b)** I `MeetingWizard.__init__`, efter `self._type_keys = list(app._meeting_types)` (linje ~3047):

```python
        self._adhoc_var = ctk.StringVar()  # navn til ad-hoc mødetype (trin 2)
        self._adhoc_active = False
```

**(c)** I `MeetingWizard._next` (linje ~3161): commit ad-hoc ved afgang fra typetrinnet (X-luk og "Spring over" committer bevidst IKKE — jf. spec: gem sker når man går videre):

```python
    def _next(self):
        if self.STEPS[self._step] == "type":
            self._commit_adhoc_type()
        if self._step < len(self.STEPS) - 1:
            self.go_to(self._step + 1)
        else:
            self._finish()
```

**(d)** I `MeetingWizard._build_type`: EFTER `for key in self._type_keys:`-løkken (dvs. efter `self._type_cards[key] = c`, linje ~3259) og FØR `self._highlight_type(...)` indsættes ad-hoc kortet; niveau-rækken pakkes efter `wrap`; sidste linje justeres:

```python
        # "+ Ny mødetype": skriv et navn — typen oprettes automatisk med
        # standardværdier når du går videre (spec §3).
        c = ctk.CTkFrame(wrap, fg_color=_CLR["card"], corner_radius=14,
                         border_width=2, border_color=_CLR["card_border"])
        c.pack(fill="x", pady=6, padx=2)
        inner = ctk.CTkFrame(c, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=14)
        self._adhoc_check = self._add_check(inner)
        txt = ctk.CTkFrame(inner, fg_color="transparent")
        txt.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(txt, text="+ Ny mødetype", font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=_CLR["text"], anchor="w").pack(anchor="w")
        adhoc_entry = ctk.CTkEntry(
            txt, textvariable=self._adhoc_var, height=34, corner_radius=8,
            placeholder_text="Skriv navnet, fx Ansættelsessamtale",
            border_width=1, border_color=_CLR["card_border"], fg_color="#ffffff",
            text_color=_CLR["text"], placeholder_text_color=_CLR["text_placeholder"],
            font=ctk.CTkFont(size=13))
        adhoc_entry.pack(fill="x", pady=(6, 0))
        ctk.CTkLabel(txt, text="Oprettes med standardindstillinger — kan rettes i Indstillinger → Mødetyper",
                     font=ctk.CTkFont(size=11), text_color=_CLR["text_secondary"],
                     anchor="w", justify="left", wraplength=430).pack(anchor="w", pady=(4, 0))
        self._adhoc_card = c
        adhoc_entry.bind("<FocusIn>", lambda e: self._select_adhoc())
        adhoc_entry.bind("<KeyRelease>", lambda e: self._select_adhoc())
        _bind_recursive(c, lambda e: self._select_adhoc())

        # Referatniveau for DETTE møde (overstyrer typens detaljeniveau)
        lvl_row = ctk.CTkFrame(self._body, fg_color="transparent")
        lvl_row.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(lvl_row, text="Referat", font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=_CLR["text"], anchor="w").pack(side="left", padx=(2, 12))
        self._level_seg = ctk.CTkSegmentedButton(
            lvl_row, values=list(_NIVEAU_KEYS), font=ctk.CTkFont(size=13),
            text_color=_CLR["text"], selected_color=_CLR["accent"],
            selected_hover_color=_CLR["accent_hover"],
            unselected_color=_CLR["card_border"], unselected_hover_color="#eef2fb",
            command=self._on_level_selected)
        self._level_seg.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(lvl_row, text="Gælder kun dette møde", font=ctk.CTkFont(size=11),
                     text_color=_CLR["text_secondary"]).pack(side="left", padx=(12, 0))
        self._sync_level_seg()

        self._highlight_type(None if self._adhoc_active else self.app._type_key)
```

(Den eksisterende linje `self._highlight_type(self.app._type_key)` erstattes af den sidste linje ovenfor.)

**(e)** Justér `_select_type` og `_highlight_type` (linje ~3262):

```python
    def _select_type(self, key):
        self._adhoc_active = False
        self.app._type_key = key
        self.app.type_var.set(self.app._meeting_types[key]["navn"])
        self.app._on_type_selected(self.app._meeting_types[key]["navn"])  # nulstiller også overstyring
        self._highlight_type(key)
        self._sync_level_seg()

    def _highlight_type(self, key):
        for k, c in self._type_cards.items():
            if c.winfo_exists():
                c.configure(border_color=_CLR["accent"] if k == key else _CLR["card_border"])
                self._draw_check(self._type_checks[k], k == key)
        if self._adhoc_card.winfo_exists():
            on = key is None and self._adhoc_active
            self._adhoc_card.configure(border_color=_CLR["accent"] if on else _CLR["card_border"])
            self._draw_check(self._adhoc_check, on)
```

**(f)** Nye metoder, placér efter `_highlight_type`:

```python
    def _select_adhoc(self):
        """Markér ad-hoc kortet (kaldes ved klik/fokus/tastetryk i feltet)."""
        if not self._adhoc_active:
            self.app.referat_level_override = None  # frisk valg → følg typen
        self._adhoc_active = True
        self._highlight_type(None)
        self._sync_level_seg()

    def _commit_adhoc_type(self):
        """Opret/genbrug ad-hoc typen hvis feltet er aktivt og udfyldt.
        Tomt felt → behold den senest markerede eksisterende type (spec §6)."""
        navn = self._adhoc_var.get().strip()
        if not (self._adhoc_active and navn):
            return
        override = self.app.referat_level_override   # bevar pr.-møde-valget
        key = self.app._save_adhoc_type(navn)
        self._adhoc_active = False
        self._adhoc_var.set("")
        self._type_keys = list(self.app._meeting_types)
        self.app._type_key = key
        self.app.type_var.set(self.app._meeting_types[key]["navn"])
        self.app._on_type_selected(self.app._meeting_types[key]["navn"])
        self.app.referat_level_override = override

    # — Referatniveau (pr. møde) ---------------------------------------
    def _current_level(self) -> str:
        """Niveauet vælgeren skal vise: aktiv overstyring, ellers typens eget
        (ad-hoc typer oprettes som 'balanceret')."""
        if self.app.referat_level_override is not None:
            return self.app.referat_level_override
        if self._adhoc_active:
            return "balanceret"
        return self.app._meeting_types[self.app._type_key]["detaljeniveau"]

    def _sync_level_seg(self):
        if self._level_seg.winfo_exists():
            self._level_seg.set(_NIVEAU_LABELS[self._current_level()])

    def _on_level_selected(self, label: str):
        """Afviger valget fra typens eget niveau, sættes en pr.-møde-
        overstyring; ellers ryddes den (None = følg typen)."""
        niveau = _NIVEAU_KEYS[label]
        base = ("balanceret" if self._adhoc_active
                else self.app._meeting_types[self.app._type_key]["detaljeniveau"])
        self.app.referat_level_override = None if niveau == base else niveau
```

- [ ] **Step 4: Kør GUI-testene**

Kør: `python -m pytest tests/test_wizard_adhoc.py -q`
Forventet på Tk 8.6: 6 passed. På denne Mac (Tk 9.x): 6 skipped — kør da i stedet konstruktions-røgtesten i Step 5.

- [ ] **Step 5: Konstruktions-røgtest (kun hvis GUI-tests skippede)**

Verificér at trin 2 KONSTRUERER uden fejl på Tk 9.x med CTkScrollableFrame-shim'et. Gem som `/tmp/smoke_wizard.py` (skal IKKE committes) og kør `python /tmp/smoke_wizard.py`:

```python
"""Røgtest: guidens trin 2 (ad-hoc kort + niveau-vælger) konstruerer uden fejl."""
import customtkinter as ctk

class _ShimScroll(ctk.CTkFrame):
    def __init__(self, *a, **kw):
        kw.pop("scrollbar_button_color", None)
        kw.pop("scrollbar_button_hover_color", None)
        super().__init__(*a, **kw)

ctk.CTkScrollableFrame = _ShimScroll

import meeting_app

root = ctk.CTk()
root.withdraw()
app = meeting_app.MeetingApp(root)
app._open_wizard(1)                      # trin 2: Vælg mødetype
wiz = app._wizard
wiz._adhoc_var.set("Ansættelsessamtale")
wiz._select_adhoc()
wiz._on_level_selected("Grundig")
assert app.referat_level_override == "grundig"
wiz._select_type(app._type_key)
assert app.referat_level_override is None
print("OK: trin 2 konstruerer, vælger + ad-hoc kort virker")
root.destroy()
```

Forventet: `OK: trin 2 konstruerer, vælger + ad-hoc kort virker` og exit 0.
NB: scriptet rører den RIGTIGE `meeting_types.json` kun hvis `_commit_adhoc_type` kaldes — det gør det ikke her, så ingen oprydning nødvendig.

- [ ] **Step 6: Kør hele suiten**

Kør: `python -m pytest tests/ -q`
Forventet: alt grønt; kun kendte GUI-skips

- [ ] **Step 7: Commit**

```bash
git add meeting_app.py tests/test_wizard_adhoc.py
git commit -m "feat: ad-hoc mødetype + referatniveau-vælger i guidens trin 2"
```

---

### Task 4: Afsluttende verifikation + PR

**Files:**
- Ingen nye — verifikation og PR

- [ ] **Step 1: Fuld suite + hurtig selvinspektion**

Kør: `python -m pytest tests/ -q`
Forventet: alt grønt. Tjek desuden `git diff main --stat` — kun `meeting_app.py`, to testfiler og docs bør være ændret (`meeting_tool.py` må IKKE optræde).

- [ ] **Step 2: Push branch og opret PR**

```bash
git push -u origin feature/adhoc-type-og-referatniveau
gh pr create --title "Ad-hoc mødetype i guiden + referatniveau pr. møde" --body "$(cat <<'EOF'
## Hvad
- **+ Ny mødetype** direkte i guidens trin 2: skriv et navn — typen gemmes automatisk med standardværdier (balanceret, opgaveliste, ingen citater) og kan rafineres i Indstillinger → Mødetyper. Navnematch (case-insensitivt) genbruger eksisterende type; slug-kollision får talsuffiks.
- **Referatniveau pr. møde**: Kort / Mellem / Grundig-vælger i trin 2. Forvalgt = typens eget niveau; andet valg gælder KUN det aktuelle møde (typen og state.json røres ikke).
- `MeetingTypesTab.reload_from_disk()` så fanens kopi ikke overskriver ad-hoc typer ved senere Gem.

## Hvordan
Rene hjælpere (`ensure_meeting_type`, `override_detaljeniveau`, `persist_meeting_types`) + tynd GUI-wiring. `meeting_tool.py`/referat-pipelinen er urørt — overstyringen er en dict-kopi med andet `detaljeniveau`.

Spec: `docs/superpowers/specs/2026-07-16-adhoc-type-og-referatniveau-design.md`

## Test
- `tests/test_adhoc_type.py` (15 rene enhedstests)
- `tests/test_wizard_adhoc.py` (6 GUI-tests, skip-guarded på Tk 9.x/headless)
- Fuld suite grøn. Visuel verifikation på Windows anbefales før release.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Verificér CI**

Kør: `gh pr checks --watch`
Forventet: testjob grønt. (`ai-review`-checket fejler org-bredt på en udgået model — kendt støj, ignorér.)

---

### Task 5: Samme muligheder i "Transkribér fil"-fanen (spec §8)

**Files:**
- Modify: `meeting_app.py`:
  - `MeetingApp._build_ui` — linjen `self._transcribe_tab = TranscribeFileTab(self._views["transkriber"])` (linje ~545)
  - Ny metode `MeetingApp._on_transcribe_types_changed` (placér efter `_on_meeting_types_changed`)
  - `TranscribeFileTab.__init__` — ny valgfri parameter
  - `TranscribeFileTab._build_ui` — combo typbar + hjælpetekst + niveau-række (efter `self.type_combo.pack(fill="x", pady=(0, 10))`)
  - `TranscribeFileTab._on_type_selected` og `refresh_meeting_types` — synk vælgeren
  - `TranscribeFileTab._start` — brug `_resolve_meeting_type()`; synk vælgeren efter tråd-start
  - Nye metoder: `_sync_level_seg`, `_resolve_meeting_type`
- Test: `tests/test_adhoc_type.py` (udvid med `TestResolveMeetingType`, 5 tests)

**Interfaces:**
- Consumes: `ensure_meeting_type`, `override_detaljeniveau`, `persist_meeting_types`, `_NIVEAU_LABELS`, `_NIVEAU_KEYS` (Task 1/3); `MeetingTypesTab.TYPES_FILE`; `MeetingApp._on_meeting_types_changed`, `MeetingTypesTab.reload_from_disk` (Task 2)
- Produces: `TranscribeFileTab._resolve_meeting_type() -> dict`; `TranscribeFileTab(parent, on_types_changed=None)`

- [ ] **Step 1: Skriv de fejlende tests**

Tilføj nederst i `tests/test_adhoc_type.py`:

```python
def _fake_tab(tmp_path, monkeypatch, seg_label="Kort", types=None):
    """Minimal TranscribeFileTab-attrap: kun det _resolve_meeting_type rører."""
    monkeypatch.setattr(
        meeting_app.MeetingTypesTab, "TYPES_FILE", tmp_path / "meeting_types.json"
    )
    calls = []
    t = types if types is not None else _types()
    first = next(iter(t))
    tab = SimpleNamespace(
        _meeting_types=t,
        _type_keys=list(t),
        _type_key=first,
        type_var=SimpleNamespace(_v=t[first]["navn"]),
        _level_seg=SimpleNamespace(get=lambda: seg_label),
        _on_types_changed=lambda: calls.append("changed"),
        status_var=SimpleNamespace(set=lambda s: calls.append(("status", s))),
    )
    tab.type_var.get = lambda: tab.type_var._v
    tab.type_var.set = lambda v: setattr(tab.type_var, "_v", v)
    tab.calls = calls
    tab._resolve_meeting_type = MethodType(
        meeting_app.TranscribeFileTab._resolve_meeting_type, tab
    )
    return tab


class TestResolveMeetingType:
    def test_same_type_same_level_returns_original(self, tmp_path, monkeypatch):
        tab = _fake_tab(tmp_path, monkeypatch, seg_label="Kort")  # driftledelse = kortfattet
        result = tab._resolve_meeting_type()
        assert result is tab._meeting_types["driftledelse"]
        assert tab.calls == []
        assert not (tmp_path / "meeting_types.json").exists()

    def test_level_differs_returns_copy(self, tmp_path, monkeypatch):
        tab = _fake_tab(tmp_path, monkeypatch, seg_label="Grundig")
        result = tab._resolve_meeting_type()
        assert result["detaljeniveau"] == "grundig"
        assert tab._meeting_types["driftledelse"]["detaljeniveau"] == "kortfattet"
        assert result is not tab._meeting_types["driftledelse"]

    def test_typed_new_name_creates_persists_and_overrides(self, tmp_path, monkeypatch):
        tab = _fake_tab(tmp_path, monkeypatch, seg_label="Grundig")
        tab.type_var.set("Ansættelsessamtale")
        result = tab._resolve_meeting_type()
        assert tab._type_key == "ansaettelsessamtale"
        assert tab.type_var.get() == "Ansættelsessamtale"
        assert "changed" in tab.calls
        data = json.loads((tmp_path / "meeting_types.json").read_text(encoding="utf-8"))
        assert data["ansaettelsessamtale"]["detaljeniveau"] == "balanceret"  # typen selv
        assert result["detaljeniveau"] == "grundig"  # kørslens kopi

    def test_typed_existing_name_reuses_without_writing(self, tmp_path, monkeypatch):
        tab = _fake_tab(tmp_path, monkeypatch, seg_label="Kort")
        tab.type_var.set("  driftledelsesmøde ")
        result = tab._resolve_meeting_type()
        assert tab._type_key == "driftledelse"
        assert not (tmp_path / "meeting_types.json").exists()
        assert "changed" not in tab.calls
        assert result is tab._meeting_types["driftledelse"]

    def test_write_failure_keeps_type_in_memory(self, tmp_path, monkeypatch):
        tab = _fake_tab(tmp_path, monkeypatch, seg_label="Mellem")
        monkeypatch.setattr(meeting_app.MeetingTypesTab, "TYPES_FILE", tmp_path)  # mappe → OSError
        tab.type_var.set("Ansættelsessamtale")
        result = tab._resolve_meeting_type()
        assert tab._type_key == "ansaettelsessamtale"
        assert "ansaettelsessamtale" in tab._meeting_types
        assert "changed" not in tab.calls
        assert any(isinstance(c, tuple) and c[0] == "status" for c in tab.calls)
        assert result["detaljeniveau"] == "balanceret"
```

- [ ] **Step 2: Kør testene — de skal fejle**

Kør: `.venv312/bin/python -m pytest tests/test_adhoc_type.py -q`
Forventet: 5 nye FAIL med `AttributeError: ... has no attribute '_resolve_meeting_type'`; 16 gamle passed

- [ ] **Step 3: Implementér**

**(a)** `TranscribeFileTab.__init__`: ændr signaturen til

```python
    def __init__(self, parent: ctk.CTkBaseClass, on_types_changed=None):
```

og tilføj efter `self.parent = parent` (eller tilsvarende første linjer):

```python
        # Kaldes når fanen opretter en ad-hoc mødetype, så andre faner opdateres.
        self._on_types_changed = on_types_changed
```

**(b)** I `MeetingApp._build_ui` (linje ~545): erstat

```python
        self._transcribe_tab = TranscribeFileTab(self._views["transkriber"])
```

med

```python
        self._transcribe_tab = TranscribeFileTab(
            self._views["transkriber"],
            on_types_changed=self._on_transcribe_types_changed,
        )
```

**(c)** Ny metode på `MeetingApp` efter `_on_meeting_types_changed`:

```python
    def _on_transcribe_types_changed(self):
        """Callback fra Transkribér fil-fanen: en ad-hoc type blev oprettet dér."""
        self._on_meeting_types_changed()
        self._types_tab.reload_from_disk()
```

**(d)** I `TranscribeFileTab._build_ui`, mødetype-dropdownen: ændr `state="readonly"` til `state="normal"`, ændr `self.type_combo.pack(fill="x", pady=(0, 10))` til `pady=(0, 4)`, og indsæt derefter:

```python
        ctk.CTkLabel(
            inner,
            text="Skriv et nyt navn for at oprette en mødetype automatisk (gemmes ved start)",
            font=ctk.CTkFont(size=11), text_color=_CLR["text_secondary"],
            anchor="w", justify="left",
        ).pack(anchor="w", pady=(0, 8))

        # -- Referatniveau (kun denne kørsel)
        lvl_row = ctk.CTkFrame(inner, fg_color="transparent")
        lvl_row.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(lvl_row, text="Referat", font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=_CLR["text"], anchor="w").pack(side="left", padx=(0, 12))
        self._level_seg = ctk.CTkSegmentedButton(
            lvl_row, values=list(_NIVEAU_KEYS), font=ctk.CTkFont(size=13),
            text_color=_CLR["text"], selected_color=_CLR["accent"],
            selected_hover_color=_CLR["accent_hover"],
            unselected_color=_CLR["card_border"], unselected_hover_color="#eef2fb")
        self._level_seg.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(lvl_row, text="Gælder kun dette møde", font=ctk.CTkFont(size=11),
                     text_color=_CLR["text_secondary"]).pack(side="left", padx=(12, 0))
        self._sync_level_seg()
```

**(e)** Nye metoder på `TranscribeFileTab` (placér ved `_key_for_label`/`_on_type_selected`):

```python
    def _sync_level_seg(self):
        """Sæt niveau-vælgeren til den valgte types eget niveau."""
        if self._level_seg.winfo_exists():
            self._level_seg.set(
                _NIVEAU_LABELS[self._meeting_types[self._type_key]["detaljeniveau"]]
            )

    def _resolve_meeting_type(self) -> dict:
        """Effektiv mødetype for denne kørsel.

        Matcher det indtastede navn ingen kendt type, oprettes en ad-hoc type
        (samme regler som i guiden: navnematch genbruger, skrivefejl → typen
        beholdes i hukommelsen for denne kørsel). Afviger niveau-vælgeren fra
        typens eget niveau, returneres en kopi med det valgte niveau —
        originalen og meeting_types.json røres aldrig af selve overstyringen."""
        navn = self.type_var.get().strip()
        if navn and navn != self._meeting_types[self._type_key]["navn"]:
            types, key, created = ensure_meeting_type(self._meeting_types, navn)
            if created:
                fejl = persist_meeting_types(types, MeetingTypesTab.TYPES_FILE)
                self._meeting_types = types
                self._type_keys = list(types)
                self._type_key = key
                if fejl is None:
                    if self._on_types_changed is not None:
                        self._on_types_changed()  # alle faner genindlæser fra disk
                else:
                    self.status_var.set(fejl)
            else:
                self._type_key = key
            self.type_var.set(self._meeting_types[self._type_key]["navn"])
        mtype = self._meeting_types[self._type_key]
        return override_detaljeniveau(mtype, _NIVEAU_KEYS.get(self._level_seg.get()))
```

**(f)** I `TranscribeFileTab._on_type_selected`: tilføj som sidste linje

```python
        self._sync_level_seg()
```

**(g)** I `TranscribeFileTab.refresh_meeting_types`: tilføj som sidste linje

```python
        self._sync_level_seg()
```

**(h)** I `TranscribeFileTab._start`: erstat

```python
        meeting_type = self._meeting_types[self._type_key]
```

med

```python
        meeting_type = self._resolve_meeting_type()
```

og tilføj umiddelbart efter fanens `self.worker_thread.start()`:

```python
        self._sync_level_seg()  # niveau-valget gjaldt kun denne kørsel
```

- [ ] **Step 4: Kør testene — de skal bestå**

Kør: `.venv312/bin/python -m pytest tests/test_adhoc_type.py -q`
Forventet: 21 passed

- [ ] **Step 5: Konstruktions-røgtest (Tk 9.x)**

Som Task 3 Step 5, men for fanen: gem i `/tmp/smoke_transcribe.py` (IKKE commit), kør med `.venv312/bin/python`:

```python
"""Røgtest: Transkribér fil-fanen konstruerer med niveau-vælger + typbar combo."""
import customtkinter as ctk

class _ShimScroll(ctk.CTkFrame):
    def __init__(self, *a, **kw):
        kw.pop("scrollbar_button_color", None)
        kw.pop("scrollbar_button_hover_color", None)
        super().__init__(*a, **kw)

ctk.CTkScrollableFrame = _ShimScroll

import meeting_app

root = ctk.CTk()
root.withdraw()
tab = meeting_app.TranscribeFileTab(ctk.CTkFrame(root))
assert tab._level_seg.get() in ("Kort", "Mellem", "Grundig")
assert tab.type_combo.cget("state") == "normal"
mt = tab._resolve_meeting_type()          # uændret navn + typens niveau → original
assert mt is tab._meeting_types[tab._type_key]
print("OK: fanen konstruerer, vælger synkroniseret, resolve virker")
root.destroy()
```

Forventet: `OK: ...` og exit 0. (Scriptet opretter ingen typer — navnet er uændret.)

- [ ] **Step 6: Kør hele suiten**

Kør: `.venv312/bin/python -m pytest tests/ -q`
Forventet: 346 passed, 4 skipped

- [ ] **Step 7: Commit**

```bash
git add meeting_app.py tests/test_adhoc_type.py
git commit -m "feat: ad-hoc mødetype + referatniveau-vælger i Transkribér fil-fanen"
```
