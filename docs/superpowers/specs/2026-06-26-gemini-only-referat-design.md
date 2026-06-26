# Design: Gemini som eneste motor + Gemini-nøgle i UI

**Dato:** 2026-06-26
**Branch:** `feature/gemini-only-referat`

## Baggrund og mål

Mødeværktøjet bruger i dag **Claude (Anthropic)** til referat-generering og
**Gemini/Hviske** til transkription. Claude bruges *kun* til referatet — intet
andet sted i appen.

Målet er at gøre det nemt for nye brugere: en ny bruger skal kunne komme i gang
med **én Gemini-nøgle** og intet andet. Konkret:

1. Gemini skal fremover skrive referatet (i stedet for Claude).
2. Claude/Anthropic fjernes helt fra værktøjet (afhængighed, kode, opstart, docs).
3. Der skal være et synligt sted i UI'et, hvor brugeren kan indsætte/ændre sin
   Gemini-nøgle — både ved første start og senere i indstillingerne.

Transkriptions-flowet (Hviske lokalt / Gemini cloud) ændres **ikke**.

## Beslutninger (afklaret i brainstorm)

- **Gemini som komplet vej:** én Gemini-nøgle driver både transkription og referat.
- **Referat skrives altid af Gemini.** Ingen motor-vælger i UI'et.
- **Claude/Anthropic fjernes helt** fra kodebasen, da det kun blev brugt til referat.
- **Første-start er skipbar:** Hviske (lokal transkription) kræver ingen nøgle, så
  en bruger kan fortsætte uden nøgle og tilføje den senere i indstillingerne.

## Arkitektur og ændringer

### 1. Referat med Gemini (`meeting_tool.py`)

Omskriv `generate_minutes(transcript, attendees, date, meeting_type=None) -> str`
til at kalde Gemini i stedet for Claude. **Signaturen bevares uændret**, så de tre
kaldesteder ikke skal røres ud over evt. statustekst:

- `meeting_app.py:1236` (Optag-fanen, `_run_recording`)
- `meeting_tool.py:2151-2152` (`transcribe_file`)
- `meeting_tool.py:2442` (CLI)

Detaljer:

- Genbrug eksisterende `SYSTEM_PROMPT` (linje ~1826) og `USER_PROMPT` (linje ~1895)
  — de er sproglige og motor-uafhængige. Transkriptet indsættes som hidtil i
  `USER_PROMPT` via `{transcript}`.
- Genbrug vokabular-formateringen, der bygger `vocabulary_section` til
  `SYSTEM_PROMPT`. `format_vocabulary_for_claude` omdøbes til
  `format_vocabulary_for_minutes` (samme adfærd; opdater kaldested).
- Ny konstant `GEMINI_MINUTES_MODEL = "gemini-2.5-pro"` (bedst til dansk),
  overstyrbar via env-variablen `GEMINI_MINUTES_MODEL`.
- Kald-mønster (samme `google-genai`-API som transkriptionen i dag):
  ```python
  from google import genai
  from google.genai import types

  api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
  if not api_key:
      raise RuntimeError(
          "GEMINI_API_KEY ikke sat. Indsæt din Gemini-nøgle i indstillingerne "
          "eller i .env."
      )
  client = genai.Client(api_key=api_key)
  response = client.models.generate_content(
      model=os.environ.get("GEMINI_MINUTES_MODEL") or GEMINI_MINUTES_MODEL,
      contents=[user_msg],
      config=types.GenerateContentConfig(
          system_instruction=system,
          temperature=0.2,
          max_output_tokens=32000,
      ),
  )
  return response.text
  ```
- Erstat `_friendly_anthropic_error` med en tilsvarende venlig Gemini-fejl
  (manglende nøgle / kvote opbrugt / auth) hvis det er let; ellers lad
  `RuntimeError`-beskeden ovenfor stå som den brugervendte fejl. Fejlen vises
  allerede i log-vinduet via `_ui_status(f"Kunne ikke generere referat: {e}")`.

### 2. Fjern Claude/Anthropic helt

**`meeting_tool.py`** — slet:
- `import anthropic` (linje ~1912, ~1963)
- `_anthropic_client()` (~1909-1917)
- `_friendly_anthropic_error()` (~1920-1923)
- `_claude_model()` (~1936-1942)
- `_claude_request_extra()` (~1945)
- `CLAUDE_MINUTES_MODEL` (~1906), `CLAUDE_GATEWAY_MODEL_DEFAULT` (~1933)
- Claude-referencer i modul-docstring (linje ~19-20) og kommentaren linje ~1823.

**`requirements.txt`** — fjern `anthropic>=0.40.0`.

**`resume_transcribe.py:82`** — brug Gemini-modelnavn i print i stedet for
`CLAUDE_MINUTES_MODEL`.

**`.env.example`** — fjern `ANTHROPIC_API_KEY`-blokken; behold `GEMINI_API_KEY`.

**`README.md`** — fjern Claude/Anthropic/Gateway-referencer (funktioner linje ~18,
krav linje ~34, API-nøgler linje ~74, første-start linje ~139, Gateway-afsnit
~168-173, arkitektur ~179). Beskriv at referatet skrives af Gemini, og at kun en
Gemini-nøgle kræves.

### 3. Gemini-nøgle i UI

**Første-start-dialog (`_first_run_setup`, `meeting_app.py:2576-2627`):**
- Fjern "Gateway-nøgle (Claude)"-feltet og `ANTHROPIC_BASE_URL` /
  `ANTHROPIC_AUTH_TOKEN` fra `_save()`.
- Behold brugernavn + **Gemini-nøgle** (maskeret). Tilføj en hjælpe-linje:
  "Få en gratis nøgle på aistudio.google.com/apikey".
- Tilføj en **"Fortsæt uden nøgle"**-knap (kun lokal Hviske-transkription).

**Indstillingskortet (Optag-fanen, `_settings_inner`, `meeting_app.py:452-738`):**
- Nyt felt **"Gemini API-nøgle"** efter eksisterende felt-mønster:
  ```python
  self._field_label(self._settings_inner, "Gemini API-nøgle")
  self.gemini_key_entry = ctk.CTkEntry(self._settings_inner, height=36,
                                        corner_radius=10, show="*")
  self.gemini_key_entry.pack(fill="x", pady=(0, 4))
  ```
- Forudfyld med nuværende nøgle (`os.environ.get("GEMINI_API_KEY", "")`), så den
  kan ses/ændres (maskeret).
- Lille **"Gem nøgle"**-knap, der kalder
  `_write_env({"GEMINI_API_KEY": self.gemini_key_entry.get().strip()})` og giver
  en kort statuskvittering ("Gemini-nøgle gemt").
- Samme hjælpe-link som i første-start.

### 4. Opstart (`_needs_setup` + `main`, `meeting_app.py`)

- `_needs_setup()` (linje 75-79) tjekker fremover Gemini-nøgle:
  ```python
  def _needs_setup() -> bool:
      return not (os.environ.get("GEMINI_API_KEY")
                  or os.environ.get("GOOGLE_API_KEY"))
  ```
- `main()` (linje 2682-2690): vis første-start-dialogen når `_needs_setup()` er
  sand, men **fjern force-exit**. Efter dialogen fortsætter appen uanset, da Hviske
  virker uden nøgle. (Referat-forsøg uden nøgle fejler venligt med beskeden fra
  punkt 1.)

### 5. Tekst/labels (`meeting_app.py`)

- `meeting_app.py:728` checkbox: "Generer referat med Claude efter optagelse" →
  **"Generer referat efter optagelse"**.
- `meeting_app.py:2201` checkbox (Transkribér fil): "Generer referat med Claude
  bagefter" → **"Generer referat bagefter"**.
- `meeting_app.py:1236` status: "Genererer referat med Claude ..." →
  **"Genererer referat ..."**.
- `meeting_app.py:1416` vocab-label: "...bruges i Claude/Gemini/Hviske-prompts" →
  **"...bruges i Gemini/Hviske-prompts"**.

### 6. Tests

- Slet `tests/test_claude.py` og `tests/test_gateway.py`.
- Fjern `fake_anthropic_client`-fixturen i `tests/conftest.py` (linje 122-141) og
  anthropic-referencer i fil-docstring.
- Tilføj `tests/test_gemini_minutes.py`: verificér at `generate_minutes`
  - kalder `client.models.generate_content` med `GEMINI_MINUTES_MODEL`
    (og at env-override virker),
  - sender deltagere i `system_instruction`,
  - sender dato + transkript i `contents`,
  - returnerer `response.text`,
  - rejser venlig fejl når nøgle mangler.
  Genbrug det eksisterende Gemini-mock-mønster fra `test_gemini_single.py` /
  `conftest.py`.
- Kør hele suiten (`pytest`) og bekræft grøn.

## Uden for scope (YAGNI)

- Ingen referat-motor-vælger i UI'et (Claude er fjernet).
- Ingen model-dropdown i UI'et (env-override `GEMINI_MINUTES_MODEL` rækker).
- Ingen ændringer i transkriptions-flowet eller systemlyd-optagelsen.

## Verifikation

- `pytest` grøn (inkl. ny `test_gemini_minutes.py`).
- `grep -rniE "claude|anthropic" --include="*.py"` returnerer ingen funktionelle
  referencer (kun evt. ufarlige historiske noter).
- Manuel røgtest: start appen uden nøgle → første-start-dialog vises, kan skippes;
  indtast Gemini-nøgle i indstillinger → "Gem nøgle" → optag kort → referat
  genereres af Gemini.
