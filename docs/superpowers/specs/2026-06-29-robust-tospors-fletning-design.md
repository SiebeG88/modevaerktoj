# Design: Robust to-spors-fletning (sync + tidsstempler + bleed)

**Dato:** 2026-06-29
**Topic:** Korrekt, korrekt-tilskrevet transkript uanset brugerens lydopsætning

## Baggrund og mål

To-spors-optagelsen mærker mikrofon-sporet `Mig` og systemlyd-sporet `Modpart`,
og fletter dem på starttid (`transcript_merge.merge_tracks`). En faktisk
optagelse (`telefonopkald 25-06-2026`) afslørede tre fejl, der gør det rå
transkript upålideligt mht. hvem-siger-hvad:

1. **Bleed/krydstale** — når brugeren kører på højtaler, fanger mikrofonen også
   modpartens stemme. Modpartens replikker optræder derfor i *begge* spor.
2. **Spor-desync** — de to optagelser deler ikke et fælles nulpunkt. I eksemplet
   var mic-sporet 47,6 min og sys-sporet 56,2 min, og samme akustiske hændelse lå
   ~3,75 min senere i sys-tidslinjen (nogenlunde konstant — altså en start-
   forskydning, ikke clock-drift; begge filer er ægte 16 kHz mono).
3. **Tidsstempel-eksplosion** — Gemini hallucinerer tidsstempler på svære/lange
   chunks; mic-sporets sene segmenter sprang op til 12+ timer på en 47,6-min
   optagelse og brød kronologien.

`merge_tracks` antager fælles nulpunkt og sorterer kun på starttid; koden i
`_record_dual_tracks` antager endda "få ms forskel" (ca. linje 483-484) — begge
antagelser holder ikke.

**Mål:** Gør fletningen robust, så et korrekt og korrekt-tilskrevet transkript
produceres **uanset** lydopsætning (built-in højtaler+mik, kabel, AirPods i alle
tilstande, USB-headset).

## Beslutninger (afklaret i brainstorm)

- **Scope:** alle tre fejl løses samlet (de hænger teknisk sammen: bleed-dedup
  kræver tids-justering, som kræver sanerede tidsstempler).
- **Flette-motor:** lokal/deterministisk Python — ingen ekstra API-kald. Samme
  input → samme output, fuldt enhedstestbart. Transkription/referat bruger stadig
  Gemini som nu.
- **Dedup-profil:** konservativ — bevar brugerens egen tale; fjern kun tydelige,
  længere bleed-kopier. Et tabt ægte udsagn er værre end en overlevende dublet.
- **Sync-tilgang:** B — autoritative start-tidsstempler optages og bruges til at
  justere sporene. Universel (virker med og uden bleed), deterministisk.
- **`merge_tracks` ændres ikke** — orkestratoren forskyder segmenterne før
  fletning, så eksisterende kontrakt og tests står urørt.
- **`Modpart` (sys) er den rene reference**; bleed fjernes fra `Mig` (mic).

## Arkitektur og ændringer

### Dataflow

```
OPTAGELSE  (_record_dual_tracks)
  ├─ mic.wav   + stempl mic's faktiske start (epoch)
  ├─ sys.wav   + stempl sys's faktiske start (epoch)
  └─ skriv <navn>.sync.json { mic_start, sys_start, mic_duration, sys_duration }

TRANSKRIPTION  (transcribe_with_gemini, pr. spor)
  └─ per-chunk: klem tidsstempler til chunkens varighed FØR offset lægges på

FLETNING  (transcript_merge.build_transcript)
  ├─ sanitize_segments()  — klem til [0, sporvarighed], stigende tider, reparér spænd
  ├─ shift_segments()     — læg mic/sys-offset på (fælles t=0 = tidligste start)
  ├─ remove_bleed()       — fjern konservativt "Mig"-kopier af overlappende "Modpart"
  └─ merge_tracks()       — interleave til mærket transkript (UÆNDRET)
```

### 1. Nye funktioner i `transcript_merge.py`

Små, rene funktioner med ét ansvar hver — testbare i isolation.

```python
Segment = tuple[float, float, str]

def sanitize_segments(segments, max_duration, *, max_segment_seconds=120.0) -> list[Segment]:
    """Klem start/end til [0, max_duration]; sikr end>=start; gennemtving ikke-
    faldende starttider (for tidligt start skubbes op til forrige, tekst beholdes);
    reparér urealistiske spænd (end-start > max_segment_seconds → end=start+grænse).
    Teksten bevares altid; kun tider rettes."""

def shift_segments(segments, offset) -> list[Segment]:
    """Læg offset (sek.) på alle tider. Trivielt, men isoleret for testbarhed."""

def remove_bleed(mic_segments, sys_segments, *, overlap_tolerance=4.0,
                 similarity_threshold=0.80, min_chars=25) -> list[Segment]:
    """Fjern KONSERVATIVT 'Mig'-segmenter der er bleed. Et mic-segment droppes kun
    hvis: len(normaliseret tekst) >= min_chars OG der findes et 'Modpart'-segment
    hvis tidsvindue overlapper (udvidet med ±overlap_tolerance) med tekst-lighed
    (difflib.SequenceMatcher på normaliseret tekst) >= similarity_threshold.
    Normalisering: lowercase, fjern tegnsætning, kollaps mellemrum. Korte segmenter
    ('ja', 'mm') og ægte samtidig-tale (forskellig tekst → lav lighed) bevares.
    Returnerer det filtrerede mic-spor."""

def merge_tracks(mic_segments, sys_segments) -> str:   # UÆNDRET
    ...

def build_transcript(mic_segs, sys_segs, *, mic_dur, sys_dur,
                     mic_start, sys_start) -> str:
    """Orkestrator: saner → forskyd til fælles t=0 → fjern bleed → flet."""
    zero = min(mic_start, sys_start)
    mic = shift_segments(sanitize_segments(mic_segs, mic_dur), mic_start - zero)
    sys = shift_segments(sanitize_segments(sys_segs, sys_dur), sys_start - zero)
    mic = remove_bleed(mic, sys)
    return merge_tracks(mic, sys)
```

### 2. Optage-ændring (`_record_dual_tracks`, `meeting_tool.py`)

Fang hvert spors faktiske start uden at omskrive pipelinerne:

- **sys-spor:** I dag forbindes `audiotee_proc.stdout` direkte til sys-ffmpeg's
  stdin (ca. linje 524-527). Indsæt en **læse-tråd** imellem: læs fra AudioTee,
  **stempl `time.time()` ved første ikke-tomme byte**, videresend til ffmpeg.
- **mic-spor:** ffmpeg/avfoundation skriver direkte til `mic.wav`. En **poll-tråd**
  tjekker filstørrelsen hver ~50 ms og **stempler når filen vokser forbi WAV-
  headeren** (> 1 KB = første lyd-frame, ikke kun header). Ingen ændring af mic-
  pipelinen.
- Varigheder hentes via eksisterende `_ffprobe_duration` på de færdige filer.

Funktionen **returnerer** nu `(sys_ok, mic_start, sys_start, mic_dur, sys_dur)`
(eller tilsvarende struktur), og skriver `<navn>.sync.json`:

```json
{ "version": 1,
  "mic_start": 1719312000.123, "sys_start": 1719311999.890,
  "mic_duration": 2853.5, "sys_duration": 3374.8 }
```

Vi forsøger **ikke** at eliminere selve start-gabet (årsagen kan variere) — vi
måler og korrigerer det. Missede mic de første minutter, er den lyd blot fraværende
i mic; sys har stadig modparten.

### 3. Kaldested (`record_then_transcribe_gemini`, ca. linje 1083)

Erstat det direkte `merge_tracks(mic_segs, sys_segs)`-kald med
`build_transcript(mic_segs, sys_segs, mic_dur=..., sys_dur=..., mic_start=...,
sys_start=...)` ved at bruge værdierne fra `_record_dual_tracks`.

### 4. Per-chunk tidsstempel-klem (`transcribe_with_gemini`)

Før offset lægges på i chunk-vejen (ca. linje 1725-1726 / `_offset_transcript`),
klemmes hvert chunks tider til `[0, chunk_seconds]`. Det forhindrer hallucinerede
tal i at forplante sig. `sanitize_segments` i fletningen klemmer derefter til
sporets samlede varighed som ekstra sikkerhedsnet.

### 5. Fejlhåndtering (graceful degradation)

| Situation | Adfærd |
|---|---|
| mic-kun (sys_ok=False / AudioTee mangler) | `build_transcript` med `sys=[]`, offset 0 — kun sanering af mic |
| Start-stempel fanges ikke (tråd-fejl) | Fald tilbage til offset 0 + log advarsel; ingen crash |
| Sidecar mangler/korrupt ved resume | Antag offset 0 + advarsel |
| `sync.json` kan ikke skrives | Log, men afbryd ikke; live-fletning bruger returværdier |

Live-kørslen afhænger **ikke** af sidecar-filen (den får tiderne via returværdier);
sidecar er til genbrug (`resume_transcribe.py`) og debug.

## Test

Pytest på dansk, kørt med `/opt/local/bin/python3.12`.

- **`tests/test_transcript_merge.py`** (udvid):
  - `sanitize_segments`: klem ud over varighed; eksplosions-casen (12-timers tal)
    → afgrænset; monotoni-håndhævelse; spænd-reparation; tom-input.
  - `shift_segments`: positiv/negativ offset; tom.
  - `remove_bleed`: dropper lang høj-lighed-kopi i overlap; bevarer kort "ja";
    bevarer ægte dobbelt-tale (forskellig tekst); respekterer tolerance (ingen
    overlap → behold); tomt sys → behold alt.
  - `build_transcript`: regression der spejler opkaldet — mic-hændelse ved 50 s med
    `mic_start` 225 s efter `sys_start`, sys samme hændelse ved 275 s → efter
    justering falder de sammen, bleed fjernes, korrekt slut-rækkefølge.
  - Eksisterende `merge_tracks`-tests forbliver grønne (uændret funktion).
- **`meeting_tool`**: test af per-chunk klem (hallucineret tid > chunk_seconds →
  klemt før offset).
- Hele suiten grøn.

## Uden for scope (YAGNI)

- Ingen akustisk krydskorrelation (tilgang A) eller LLM-fletning.
- Ingen stereo-/enkeltproces-optagelse (tilgang C).
- Ingen forsøg på at *eliminere* start-gabet i optagelsen — kun måle/korrigere.
- Ingen tidsstempel-*rescale* (kun klem) — simpelt og sikkert.
- Ingen UI-ændringer; ingen ændring i transkriptions- eller referat-prompts.

## Verifikation

- `pytest` grøn, inkl. nye tests.
- Manuel regression mod `telefonopkald 25-06-2026`-sporene: efter justering ligger
  Mikkels intro samme sted i begge spor, ingen 12-timers tidsstempler, og
  modpartens replikker optræder ikke længere dubleret som "Mig".
- Røgtest: kort to-spors-optagelse på højtaler → `sync.json` skrives, kronologi og
  tilskrivning er korrekte i det flettede transkript.
