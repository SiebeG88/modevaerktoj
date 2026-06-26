# Gemini-only referat + Gemini-nøgle i UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lad Gemini skrive mødereferatet, fjern Claude/Anthropic helt, og gør Gemini-nøglen tilgængelig i UI'et, så en ny bruger kun behøver én Gemini-nøgle.

**Architecture:** `generate_minutes()` omskrives til at kalde Gemini via det eksisterende `google-genai`-mønster (samme signatur, så kaldestederne er uændrede). Al Claude/Anthropic-kode, -afhængighed og -tests fjernes. UI'et får et Gemini-nøglefelt både i første-start-dialogen og i indstillingskortet; opstart kræver ikke længere en Claude-nøgle.

**Tech Stack:** Python 3.12, CustomTkinter (GUI), `google-genai` (Gemini), pytest + pytest-mock.

**Spec:** `docs/superpowers/specs/2026-06-26-gemini-only-referat-design.md`

---

## Filstruktur

- `meeting_tool.py` — kernelogik. Omskriv `generate_minutes`, fjern Claude-helpers, omdøb `format_vocabulary_for_claude`.
- `meeting_app.py` — GUI. `_needs_setup`, `_first_run_setup`, `main`, nyt nøglefelt i indstillingskortet, tekst-labels.
- `requirements.txt` — fjern `anthropic`.
- `resume_transcribe.py` — fjern Claude-modelreference.
- `.env.example`, `README.md` — docs.
- `tests/conftest.py` — fjern `fake_anthropic_client`.
- `tests/test_claude.py` — slettes.
- `tests/test_gateway.py` → `tests/test_setup.py` — fjern Claude-tests, opdatér `_needs_setup`-tests.
- `tests/test_gemini_minutes.py` — ny.
- `tests/test_vocabulary_format.py` — opdatér omdøbt funktionsnavn.

---

## Task 1: Gemini skriver referatet (meeting_tool.py + tests)

Kerneændringen. Holder testsuiten grøn ved at opdatere alle tests, der refererer fjernede `meeting_tool`-symboler, i samme task.

**Files:**
- Modify: `meeting_tool.py` (referat-sektion ~1822-2000 + vocab-formatter ~2746)
- Modify: `resume_transcribe.py:82`
- Modify: `requirements.txt:2`
- Modify: `tests/conftest.py` (docstring linje 5 + fixture linje 124-142)
- Modify: `tests/test_vocabulary_format.py` (3 referencer)
- Modify: `tests/test_gateway.py` (fjern Claude-tests linje 10-108)
- Delete: `tests/test_claude.py`
- Create: `tests/test_gemini_minutes.py`

- [ ] **Step 1: Slet den gamle Claude-test**

```bash
git rm tests/test_claude.py
```

- [ ] **Step 2: Fjern `fake_anthropic_client`-fixturen i conftest**

I `tests/conftest.py`: slet hele blokken `# ----- Anthropic-klient -----` med
`fake_anthropic_client`-fixturen (linje 122-142). Ret også docstring-linjen
`- anthropic.Anthropic (Claude messages)` (linje 5) — slet den linje.

- [ ] **Step 3: Fjern de Claude-specifikke tests i test_gateway.py**

I `tests/test_gateway.py`: slet funktionerne fra `test_anthropic_client_direct_when_no_base_url`
(linje 10) til og med `test_generate_minutes_unmapped_status_reraises` (linje 108).
**Behold** alt fra `test_needs_setup_true_when_no_keys` (linje 111) og ned
(`_needs_setup`-, `_write_env`- og `_maybe_apply_update`-tests). `_needs_setup`-testene
opdateres i Task 2 — lad dem stå urørt her.

- [ ] **Step 4: Skriv den nye Gemini-referat-test (failing)**

Opret `tests/test_gemini_minutes.py`:

```python
"""Tests for generate_minutes (Gemini API)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_tool as mt


class TestGenerateMinutes:
    def test_returns_text(self, fake_gemini_client):
        fake_gemini_client.models.generate_content.return_value = \
            fake_gemini_client._build_response("# Referat\n\nMødet handlede om ...")
        result = mt.generate_minutes(
            transcript="[00:00 - 00:05] Hej alle.",
            attendees=["Anna", "Dorte"],
            date="22-05-2026",
        )
        assert "Referat" in result

    def test_uses_gemini_pro_model(self, fake_gemini_client):
        mt.generate_minutes("[00:00 - 00:05] test", ["A"], "22-05-2026")
        call = fake_gemini_client.models.generate_content.call_args
        assert call.kwargs["model"] == mt.GEMINI_MINUTES_MODEL
        assert "gemini" in call.kwargs["model"]

    def test_model_env_override(self, monkeypatch, fake_gemini_client):
        monkeypatch.setenv("GEMINI_MINUTES_MODEL", "gemini-2.5-flash")
        mt.generate_minutes("[00:00 - 00:05] test", ["A"], "22-05-2026")
        call = fake_gemini_client.models.generate_content.call_args
        assert call.kwargs["model"] == "gemini-2.5-flash"

    def test_system_instruction_includes_attendees(self, fake_gemini_client):
        mt.generate_minutes("[00:00 - 00:05] x", ["Anna", "Bo"], "22-05-2026")
        import google.genai as _genai
        cfg = _genai.types.GenerateContentConfig.call_args
        system = cfg.kwargs["system_instruction"]
        assert "Anna" in system
        assert "Bo" in system

    def test_contents_include_date_and_transcript(self, fake_gemini_client):
        mt.generate_minutes("[00:00 - 00:05] xyz-123", ["A"], "22-05-2026")
        call = fake_gemini_client.models.generate_content.call_args
        joined = "".join(call.kwargs["contents"])
        assert "22-05-2026" in joined
        assert "xyz-123" in joined

    def test_missing_key_raises(self, monkeypatch, fake_gemini_client):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
            mt.generate_minutes("transcript", ["A"], "22-05-2026")
```

- [ ] **Step 5: Kør den nye test — verificér FAIL**

Run: `python -m pytest tests/test_gemini_minutes.py -v`
Expected: FAIL (bl.a. `AttributeError: ... GEMINI_MINUTES_MODEL` og at
`generate_minutes` stadig kalder Claude/`anthropic`).

- [ ] **Step 6: Omskriv referat-sektionen i meeting_tool.py**

Erstat hele blokken fra kommentaren `# 4. Referat og opgaver via Claude API`
(linje ~1822) til slutningen af `generate_minutes` (linje ~2000) således at
`SYSTEM_PROMPT` og `USER_PROMPT` **bevares uændret**, mens Claude-helperne
(`CLAUDE_MINUTES_MODEL`, `_anthropic_client`, `_friendly_anthropic_error`,
`CLAUDE_GATEWAY_MODEL_DEFAULT`, `_claude_model`, `_claude_request_extra`) **slettes**
og `generate_minutes` får denne nye krop:

```python
# ---------------------------------------------------------------------------
# 4. Referat og opgaver via Gemini API
# ---------------------------------------------------------------------------

# (SYSTEM_PROMPT og USER_PROMPT står uændret her ovenfor.)

GEMINI_MINUTES_MODEL = "gemini-2.5-pro"


def generate_minutes(
    transcript: str,
    attendees: list[str],
    date: str,
    meeting_type: dict | None = None,
) -> str:
    """Genererer referat og opgaver via Gemini API.

    meeting_type: en (normaliseret) mødetype-dict. None → driftledelse-fallback.
    """
    from google import genai
    from google.genai import types

    if meeting_type is None:
        _types = load_meeting_types(CONFIG_DIR)
        meeting_type = _types.get("driftledelse") or next(iter(_types.values()))
    meeting_type = _normalize_meeting_type(meeting_type)

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
```

- [ ] **Step 7: Omdøb `format_vocabulary_for_claude` → `format_vocabulary_for_minutes`**

I `meeting_tool.py` linje ~2746: omdøb funktionen og opdatér dens docstring
("til Claude SYSTEM_PROMPT" → "til referat-SYSTEM_PROMPT"). Kaldestedet i
`generate_minutes` (step 6) bruger allerede det nye navn. `format_vocabulary_for_gemini`
(transkription) og `format_vocabulary_for_hviske` røres ikke.

- [ ] **Step 8: Ret modul-docstring i meeting_tool.py**

Linje ~19-20: erstat
`- ANTHROPIC_API_KEY miljoevariabel sat (til referat)` og
`- Python-pakker: faster-whisper, anthropic` med:
```
    - GEMINI_API_KEY miljoevariabel sat (til Gemini-transkription og referat)
    - Python-pakker: faster-whisper, google-genai
```

- [ ] **Step 9: Opdatér `tests/test_vocabulary_format.py`**

Erstat de 3 forekomster af `meeting_tool.format_vocabulary_for_claude` med
`meeting_tool.format_vocabulary_for_minutes` (linje 13, 25, 33).

- [ ] **Step 10: Fjern anthropic fra requirements + ret resume_transcribe**

I `requirements.txt`: slet linjen `anthropic>=0.40.0`.

I `resume_transcribe.py:82`: erstat
`print(f"=== Trin 2/2: Referat ({meeting_tool.CLAUDE_MINUTES_MODEL}) ===", flush=True)`
med
`print("=== Trin 2/2: Referat (Gemini) ===", flush=True)`

- [ ] **Step 11: Kør hele suiten — verificér grøn**

Run: `python -m pytest -q`
Expected: PASS (ingen referencer til fjernede Claude-symboler). Hvis en test
fejler med `AttributeError` på et Claude-symbol, mangler et kaldested at blive
opdateret — find det med `grep -rn "CLAUDE_MINUTES_MODEL\|_anthropic_client\|_claude_model\|_claude_request_extra\|format_vocabulary_for_claude" .`

- [ ] **Step 12: Commit**

```bash
git add meeting_tool.py resume_transcribe.py requirements.txt tests/
git commit -m "feat: Gemini skriver referatet; fjern Claude fra kernelogik + tests"
```

---

## Task 2: Opstart kræver kun Gemini-nøgle (meeting_app.py + tests)

`_needs_setup` skifter til Gemini, første-start-dialogen forenkles til kun
Gemini-nøgle og bliver skipbar, og `main()` afslutter ikke længere appen.

**Files:**
- Modify: `meeting_app.py` (`_needs_setup` 75-79, `_first_run_setup` 2576-2627, `main` 2682-2690)
- Rename + modify: `tests/test_gateway.py` → `tests/test_setup.py`

- [ ] **Step 1: Opdatér `_needs_setup`-testene og omdøb testfilen**

```bash
git mv tests/test_gateway.py tests/test_setup.py
```

I `tests/test_setup.py`: erstat de tre `_needs_setup`-tests
(`test_needs_setup_true_when_no_keys`, `test_needs_setup_false_with_gateway_token`,
`test_needs_setup_false_with_direct_key`) med:

```python
def test_needs_setup_true_when_no_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert meeting_app._needs_setup() is True


def test_needs_setup_false_with_gemini_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-123")
    assert meeting_app._needs_setup() is False


def test_needs_setup_false_with_google_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "g-456")
    assert meeting_app._needs_setup() is False
```

I samme fil: i `test_write_env_persists_and_sets_environ` skift eksempel-nøglen
`"MT_USER": "Dorte"` til `"MEETINGS_DIR": "/data/m"` og assertionen
`assert "MT_USER=Dorte" in text` til `assert "MEETINGS_DIR=/data/m" in text`
(MT_USER er dødt efter Claude-fjernelse). I `test_write_env_preserves_existing_keys`
skift `{"ANTHROPIC_AUTH_TOKEN": "gw-1"}` til `{"GEMINI_API_KEY": "g-1"}` og de to
tilhørende assertions tilsvarende.

- [ ] **Step 2: Kør de opdaterede setup-tests — verificér FAIL**

Run: `python -m pytest tests/test_setup.py -v`
Expected: FAIL på `_needs_setup`-testene (koden tjekker stadig ANTHROPIC).

- [ ] **Step 3: Omskriv `_needs_setup` (meeting_app.py:75-79)**

```python
def _needs_setup() -> bool:
    """True hvis ingen Gemini-nøgle er sat — så viser vi første-start-dialogen.
    Hviske (lokal transkription) virker uden nøgle, så dialogen kan springes over."""
    return not (os.environ.get("GEMINI_API_KEY")
                or os.environ.get("GOOGLE_API_KEY"))
```

- [ ] **Step 4: Kør setup-tests — verificér PASS**

Run: `python -m pytest tests/test_setup.py -v`
Expected: PASS.

- [ ] **Step 5: Forenkl `_first_run_setup` (meeting_app.py:2576-2627)**

Erstat hele funktionen med (kun Gemini-nøgle, hjælpe-link, skipbar):

```python
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
```

- [ ] **Step 6: Fjern force-exit i `main` (meeting_app.py:2682-2690)**

Erstat:
```python
    root = ctk.CTk()
    if _needs_setup():
        _first_run_setup(root)   # modal; blokerer til nøgler er gemt
        if _needs_setup():
            # Brugeren lukkede dialogen uden at gemme nøgler — afslut pænt i
            # stedet for at fortsætte og crashe ved første API-kald.
            print("Opsætning afbrudt — ingen API-nøgle. Afslutter.",
                  file=sys.stderr)
            root.destroy()
            return
```
med:
```python
    root = ctk.CTk()
    if _needs_setup():
        _first_run_setup(root)   # modal; kan springes over (Hviske virker uden nøgle)
```

- [ ] **Step 7: Kør hele suiten — verificér grøn**

Run: `python -m pytest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add meeting_app.py tests/test_setup.py
git commit -m "feat: opstart kræver kun Gemini-nøgle; forenklet skipbar første-start"
```

---

## Task 3: Gemini-nøglefelt i indstillingskortet (meeting_app.py)

Et synligt sted i den løbende UI til at indsætte/ændre Gemini-nøglen. CustomTkinter-widgets
testes ikke automatisk her (følger kodebasens eksisterende mønster); verificeres manuelt.

**Files:**
- Modify: `meeting_app.py` (`_settings_inner`-blok efter minutes-checkbox ~737, ny metode i `MeetingApp`)

- [ ] **Step 1: Tilføj nøglefeltet efter referat-checkboxen**

I `_build_ui`, umiddelbart efter referat-checkboxens `.pack(anchor="w", pady=(2, 0))`
(linje ~737), indsæt:

```python
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
```

- [ ] **Step 2: Tilføj `_save_gemini_key`-metoden**

I `MeetingApp`, ved siden af de andre små helpers (fx efter `_select_engine`),
tilføj:

```python
    def _save_gemini_key(self):
        """Gem Gemini-nøglen fra indstillingsfeltet i .env + os.environ."""
        key = self.gemini_key_entry.get().strip()
        if not key:
            self.status_var.set("Indtast en Gemini-nøgle først.")
            return
        _write_env({"GEMINI_API_KEY": key})
        self.status_var.set("Gemini-nøgle gemt.")
```

- [ ] **Step 3: Kør suiten — verificér uændret grøn**

Run: `python -m pytest -q`
Expected: PASS (ingen ny test; sikrer at intet er brudt).

- [ ] **Step 4: Manuel røgtest**

Start appen: `python meeting_app.py`. Åbn indstillingskortet. Bekræft at
"Gemini API-nøgle"-feltet vises (maskeret), at en eksisterende nøgle er forudfyldt,
og at "Gem nøgle" giver statuskvitteringen "Gemini-nøgle gemt." Bekræft at
`.env` indeholder den indtastede nøgle bagefter.

- [ ] **Step 5: Commit**

```bash
git add meeting_app.py
git commit -m "feat: Gemini-nøglefelt i indstillingskortet"
```

---

## Task 4: Tekst/labels uden Claude (meeting_app.py)

**Files:**
- Modify: `meeting_app.py` (linje 728, 1236, 1416, 2201)

- [ ] **Step 1: Ret de fire tekststeder**

- Linje 728: `text="Generer referat med Claude efter optagelse",` →
  `text="Generer referat efter optagelse",`
- Linje 1236: `self._ui_status("Genererer referat med Claude ...")` →
  `self._ui_status("Genererer referat ...")`
- Linje 1416: `text="Navne og termer som bruges i Claude/Gemini/Hviske-prompts",` →
  `text="Navne og termer som bruges i Gemini/Hviske-prompts",`
- Linje 2201: `text="Generer referat med Claude bagefter",` →
  `text="Generer referat bagefter",`

- [ ] **Step 2: Verificér ingen Claude-tekst tilbage i UI-strenge**

Run: `grep -n "Claude" meeting_app.py`
Expected: ingen output.

- [ ] **Step 3: Commit**

```bash
git add meeting_app.py
git commit -m "chore: fjern Claude-omtale i UI-tekster"
```

---

## Task 5: Docs — .env.example + README (kun Gemini)

**Files:**
- Modify: `.env.example`
- Modify: `README.md`

- [ ] **Step 1: Skriv `.env.example` om**

Erstat hele filen med:

```
# Mødeværktøj - miljøvariabler
# Kopier til .env og indsæt din egen nøgle. .env må aldrig committes.

# Gemini API-nøgle (kræves til cloud-transkription og referat-generering).
# Få nøgle på https://aistudio.google.com/apikey
GEMINI_API_KEY=

# (Valgfrit) Overstyr referat-modellen (default gemini-2.5-pro).
# GEMINI_MINUTES_MODEL=gemini-2.5-flash

# (Valgfrit) Overstyr default-mappe for mødefiler.
# MEETINGS_DIR=/absolut/sti/til/anden/mappe
```

- [ ] **Step 2: Opdatér README.md**

Ret følgende steder (find på indhold, ikke linjenr., da de kan skride):

- Funktioner-punktet "Genererer fyldigt referat med Claude (claude-opus-4-7) bagefter"
  → "Genererer fyldigt referat med Gemini (gemini-2.5-pro) bagefter".
- Krav-punktet "API-nøgler til Anthropic og (valgfrit) Google Gemini — se afsnittet ..."
  → "API-nøgle til Google Gemini — se afsnittet [API-nøgler](#api-nøgler) nedenfor".
- Afsnittet `## API-nøgler`: fjern Anthropic/Claude-bulletten; behold kun Gemini
  ("opret en nøgle på aistudio.google.com/apikey") og opdatér teksten til at kun
  én Gemini-nøgle kræves. Beskriv at nøglen kan indtastes i appen (første-start
  eller indstillingskortet) eller i `.env`.
- Linjen ~107 "indholdet injiceres automatisk i Claude-referatprompten" →
  "...i referat-prompten".
- Linjen ~125 "(Gemini, Claude, ffmpeg, faster-whisper)" → "(Gemini, ffmpeg, faster-whisper)".
- Første-start-afsnittet (~139) der nævner "API-nøgle (Claude) og Gemini-nøgle" →
  beskriv kun Gemini-nøglen.
- Hele Gateway-afsnittet (~168-173, "Claude routes gennem Vercel AI Gateway ...")
  → slet det.
- Arkitektur-linjen ~179 "meeting_tool.py — ... + Claude-integration" →
  "... + Gemini-integration".

- [ ] **Step 3: Verificér ingen funktionel Claude/Anthropic-omtale tilbage**

Run: `grep -rniE "claude|anthropic" README.md .env.example`
Expected: ingen output (eller kun ufarlige historiske noter, som rettes).

- [ ] **Step 4: Commit**

```bash
git add README.md .env.example
git commit -m "docs: kun Gemini-nøgle krævet; fjern Claude/Gateway-omtale"
```

---

## Task 6: Slut-verifikation

**Files:** ingen (kun verifikation; ret evt. fund i den relevante task ovenfor).

- [ ] **Step 1: Hele testsuiten grøn**

Run: `python -m pytest -q`
Expected: alle tests PASS.

- [ ] **Step 2: Ingen funktionelle Claude/Anthropic-referencer i kode**

Run: `grep -rniE "claude|anthropic" --include="*.py" . | grep -v "/.git/"`
Expected: ingen funktionelle referencer (kun evt. ufarlige kommentarer/historik).

- [ ] **Step 3: Manuel ende-til-ende-røgtest**

1. Fjern (eller omdøb) `.env` så ingen nøgle er sat → start `python meeting_app.py`
   → første-start-dialog vises, kan både gemme nøgle og "Fortsæt uden nøgle".
2. Indtast Gemini-nøgle i indstillingskortet → "Gem nøgle" → kvittering vises.
3. Optag et kort møde (eller brug "Transkribér fil") med referat slået til →
   bekræft at referatet genereres af Gemini og gemmes som markdown.

---

## Self-review (udført ved skrivning)

- **Spec-dækning:** Alle spec-punkter (1 referat, 2 fjern-Claude, 3 nøgle i UI,
  4 opstart, 5 labels, 6 tests, docs) er dækket af Task 1-6.
- **Pladsholdere:** Ingen TBD/TODO; alle kode-trin har fuld kode.
- **Type/navne-konsistens:** `GEMINI_MINUTES_MODEL`, `format_vocabulary_for_minutes`,
  `_save_gemini_key`, `gemini_key_entry`, `_needs_setup` (Gemini) bruges konsistent
  på tværs af tasks og tests.
- **Grønne commits:** Hver task efterlader testsuiten grøn (Task 1 opdaterer alle
  tests for fjernede symboler i samme commit; resume_transcribe/requirements rettes
  sammen med symbol-fjernelsen).
