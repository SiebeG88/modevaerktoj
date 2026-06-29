# Mødeværktøj

> **Skabelon-/eksempel-projekt:** Dette repo leveres med en fiktiv eksempel-organisation
> ("Eksempelgården") som demonstration. Ordlisten, deltagere og emneprioritering i
> promptene er opdigtede. Tilpas `vocabulary.json` og prompts i `meeting_tool.py`
> (eller via GUI'en) til din egen organisation, inden du tager værktøjet i brug.

Et dansk, open source GUI-værktøj til optagelse, transkription og automatisk
referat-generering af møder. Beregnet til organisationer der vil have AI-assisterede
mødereferater på dansk uden at sende lyd til unødvendige tredjeparter.

## Funktioner

- Optager direkte fra mikrofon (avfoundation via ffmpeg)
- To transkriptionsmotorer:
  - **Hviske (live, lokal)** — `syvai/faster-hviske-v3-conversation` via faster-whisper, kører løbende under optagelsen, ingen cloud-data
  - **Gemini (efter, cloud)** — Gemini 2.5 Pro via Files API, kører efter optagelsen, bedre til danske egennavne og fagtermer. Auto-chunking ved >15 min med parallel transkription og retry på RECITATION-fejl
- Genererer fyldigt referat med Gemini (gemini-2.5-pro) bagefter
- Gemmer transkription + referat som markdown i samme mappe som WAV'en (default `~/Møder`); referatet gemmes også som **PDF** (pandoc + weasyprint)
- Holder Mac vågen under optagelse (caffeinate)
- **Systemlyd-optagelse (Meet/Teams/telefon):** optager modpartens lyd som
  separat spor via BlackHole + Multi-Output. Transkriptet mærkes "Mig:" /
  "Modpart:". Aktivér med checkboxen i appen (klik "Opsæt systemlyd" først, og
  før hver optagesession — lyd-outputtet gendannes efter hver optagelse) eller
  med `--system-audio` i CLI. Mac-relayede telefonopkald (Continuity) fanges af
  samme spor.

## Krav

- macOS (Apple Silicon afprøvet)
- Python 3.12 (testet med MacPorts: `/opt/local/bin/python3.12`)
- ffmpeg + ffprobe (MacPorts: `port install ffmpeg`)
- pandoc + weasyprint til referat-PDF (MacPorts: `port install pandoc`; `pip install weasyprint`). Mangler de, springes PDF'en blot over.
- API-nøgle til Google Gemini — se afsnittet [API-nøgler](#api-nøgler) nedenfor
- BlackHole (`brew install blackhole-2ch`) — kun for systemlyd-optagelse;
  installeres og opsættes automatisk via "Opsæt systemlyd"-knappen
  (eller `python meeting_tool.py --setup-audio`).

### Windows

- Python 3.12 (python.org-installer; vælg "Add python.exe to PATH")
- ffmpeg + ffprobe (download fra ffmpeg.org, læg `bin`-mappen på PATH)
- pandoc til referat-PDF: `winget install --id JohnMacFarlane.Pandoc`
  (PDF kræver desuden GTK-runtime til weasyprint; mangler den, springes PDF'en blot over)
- API-nøgler i `.env` som på macOS

Installation:

```bat
py -3 -m pip install -r requirements.txt
copy .env.example .env
```

Start GUI: dobbeltklik `Start Mødeværktøj.bat` (eller `py -3 meeting_app.py`).

Bemærk: Hviske (lokal transkription) kører på CPU på Windows medmindre der er en NVIDIA-GPU — det er langsommere end på Apple Silicon. Gemini-cloud-motoren er upåvirket.

## Installation

```bash
git clone https://github.com/SiebeG88/modevaerktoj.git
cd modevaerktoj
/opt/local/bin/python3.12 -m pip install -r requirements.txt
cp .env.example .env
# Rediger .env og indsæt dine API-nøgler
```

> Forker du projektet, så peg URL'erne (her og i `updater.py`) på dit eget repo.

## API-nøgler

Værktøjet kræver én API-nøgle — den er ikke inkluderet i repo'et.

- **Google Gemini:** bruges til både cloud-transkription og referat-generering. Opret en nøgle på
  [aistudio.google.com/apikey](https://aistudio.google.com/apikey).

Nøglen kan angives på to måder:

- **I appen:** første gang du starter appen vises en opsætnings-dialog med feltet "Gemini API-nøgle";
  ellers finder du feltet i **Optag**-fanen under indstillingskortet "Gemini API-nøgle".
- **I `.env`:** kopiér `.env.example` til `.env` og indsæt din nøgle ved `GEMINI_API_KEY=`.
  Se `.env.example` for alle tilgængelige indstillinger.

## Brug

**Start GUI:**
- Dobbeltklik `Mødeværktøj.app` i Finder, eller
- Dobbeltklik `Start Mødeværktøj.command`, eller
- Kør `/opt/local/bin/python3.12 meeting_app.py` fra terminal

**Genoptag transkription på eksisterende WAV** (fx hvis Gemini fejlede):

```bash
/opt/local/bin/python3.12 resume_transcribe.py "/sti/til/optagelse.wav"
```

Output (transkription + referat) gemmes i samme mappe som WAV'en.

## Transkribér en eksisterende lydfil

Fanen **Transkribér fil** i appen transkriberer en optagelse der er lavet uden
for værktøjet — fx en iPhone Voice Memo (`.m4a`). Vælg lydfilen, bekræft
mappe/navn/dato/deltagere, vælg motor (Hviske eller Gemini) og tryk
**Transkribér**. Filen konverteres automatisk til WAV, transkriberes og (hvis
valgt) får et referat — gemt i en undermappe under den valgte mappe, præcis som
en optagelse. Understøtter `.m4a .mp3 .wav .aac .caf .mp4 .opus .flac .ogg` m.fl.

## Ordliste (vocabulary.json)

Personer, steder og fagtermer som bruges i prompts gemmes i `vocabulary.json`
ved siden af scriptet (lokal fil, ignoreret af git). Redigér via **Ordliste**-fanen
i appen — indholdet injiceres automatisk i referat-prompten,
Gemini-transkriptionsprompten og Hviske `initial_prompt`.

Ved første kørsel auto-seedes filen fra `vocabulary.default.json` med
**fiktive eksempel-navne fra Eksempelgården** (Anna, Bo, Mads, Henrik, Nordmarken,
Sydmarken osv.). Denne default-fil er et udgangspunkt — redigér den til at
afspejle din organisations rigtige navne, steder og fagtermer.

## Tests

```bash
/opt/local/bin/python3.12 -m pip install -r requirements-dev.txt
/opt/local/bin/python3.12 -m pytest                                    # kør alle tests
/opt/local/bin/python3.12 -m pytest --cov=meeting_tool                 # med coverage
/opt/local/bin/python3.12 -m pytest --cov=meeting_tool --cov-report=html  # HTML-rapport i htmlcov/
```

Coverage-gate: ≥90% på `meeting_tool.py` (konfigureret i `pyproject.toml`).
Alle eksterne afhængigheder (Gemini, ffmpeg, faster-whisper) er mocket i `tests/conftest.py` — testene kører lokalt uden API-nøgler.

## Windows-distribution (pakket .exe) + auto-opdatering

Ud over kildekode-installationen ovenfor kan værktøjet sendes til en kollega som
en **pakket Windows-`.exe`** der ikke kræver Python/ffmpeg-installation.

### For kollegaen (modtager)

1. Hent den nyeste `Modevaerktoj-vX.Y.Z.zip` fra
   [Releases](https://github.com/SiebeG88/modevaerktoj/releases),
   pak den ud.
2. Kør `Mødeværktøj.exe`.
3. **Første gang** vises en opsætnings-dialog: indtast *brugernavn*
   og *Gemini API-nøgle*. De gemmes lokalt i `%APPDATA%\Mødeværktøj\.env`
   (aldrig i selve `.exe`'en) og overlever opdateringer.

Eksempel-ordlisten er bundlet. **Begrænsning:** systemlyd
(modpartens lyd i Meet/Teams/telefon) virker kun på macOS — på Windows optages
kun mikrofonen. Hviske (lokal) kører på CPU og er langsom; Gemini-cloud er
default-motoren.

### Auto-opdatering (OTA)

Ved opstart tjekker appen GitHub Releases for en nyere version, henter og
**sha256-verificerer** den (uverificerede binærer afvises), og genstarter på den
nye version — men **aldrig** midt i en optagelse eller fil-transkription.

### For vedligeholderen — lav en release

1. Bump `__version__` i `_version.py`, så det matcher tagget.
2. `git tag vX.Y.Z && git push --tags`.
3. GitHub Actions (`.github/workflows/release-windows.yml`, kører på
   `windows-latest`) bygger `.exe`'en via `modevaerktoj.spec`, henter ffmpeg, og
   udgiver `…zip` + `…zip.sha256` som en Release. Den release er både
   første-leverancen og OTA-kilden.

> OTA peger på dette repos Releases (`SiebeG88/modevaerktoj`). Forker du
> projektet, så opdatér URL'en i `updater.py` (workflow-filen udleder selv repoet).

## Filer i repo

- `meeting_app.py` — GUI (CustomTkinter)
- `meeting_tool.py` — Optage- og transkriptionsmotor + Gemini-integration
- `resume_transcribe.py` — CLI til at re-transkribere eksisterende WAV
- `_version.py` — versionsnummer (OTA sammenligner release-tags mod denne)
- `app_paths.py` — frozen-aware config-mappe (`%APPDATA%`) + binær-opslag
- `updater.py` — OTA: versions-tjek, download+sha256-verifikation, swap+genstart
- `modevaerktoj.spec` — PyInstaller-bygge-spec (one-folder Windows-`.exe`)
- `.github/workflows/release-windows.yml` — CI: byg `.exe` + udgiv Release ved `v*`-tag
- `Mødeværktøj.app/` — Mac launcher (kører GUI uden terminal-vindue)
- `Start Mødeværktøj.command` — Alternativ Mac-launcher der viser terminal-vindue
- `Start Mødeværktøj.bat` — Windows-launcher til kildekode-kørsel (dobbeltklik i Stifinder)

## Logfil

`~/Library/Logs/meeting-tool.log` — stdout/stderr fra Mødeværktøj.app-launcheren.
Bemærk: GUI-fejl sendes til appens statuslinje, ikke loggen.

## Licens

MIT — se [LICENSE](LICENSE).
