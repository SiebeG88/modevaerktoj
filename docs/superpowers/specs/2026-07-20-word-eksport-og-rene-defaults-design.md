# Word-eksport + rene defaults — designdokument

**Dato:** 2026-07-20
**Status:** Design godkendt i brainstorm, afventer spec-review
**Omfang:** Eksportformat (Word), deltager-defaults, mødetype-defaults

---

## 1. Formål

Tre brugerønsker efter daglig brug på Windows:

1. **Eksporterede dokumenter skal være Word (.docx) og se pæne ud.** I dag laves
   kun `.md` (PDF'en fejler, fordi pandoc ikke er installeret på Windows), og
   `.md` ser rå/uformateret ud.
2. **De hardkodede deltagernavne (Mads/Lars/Dorte) skal væk.** De dukker altid
   op igen, uanset hvad brugeren skriver. Brugeren vil selv skrive deltagerne,
   og det skrevne skal blive stående — og vises korrekt på dokumentet.
3. **De påtvungne mødetyper (Driftledelsesmøde/Ledergruppemøde) skal væk.**
   Brugeren vil selv oprette typer (ad-hoc fra guiden eller fil-fanen) og
   vælge dem efter oprettelse.

Kerne-pipelinen (optagelse, Hviske/Gemini-transkription, referat-generering)
ændres **ikke**. Dette handler om outputformat + at fjerne påtvungne demodata.

## 2. Omfang

**Inkluderet:**
- Word-eksport (.docx) af BÅDE referat og transkription, pænt formateret, med
  et metadata-hoved (mødenavn, dato, mødetype, deltagere).
- Fjern PDF-generering (pandoc/weasyprint) og `.md`-outputtet (Word er
  primær; `.md` beholdes kun som nød-fallback hvis .docx-skrivning fejler).
- Deltager-defaults: ingen hardkodede navne; tomt felt bliver stående tomt;
  det skrevne huskes (per type + sidst brugte, som i dag).
- Mødetype-defaults: ingen påtvungne typer; appen tolererer NUL typer; en
  engangs-oprydning fjerner de to danske eksempel-typer hvis de er urørte.

**IKKE inkluderet (bevidst):**
- Ændringer i transkription/Gemini/referat-prompt-logikken.
- Visuelt redesign.
- Regenerering af referat efter mødet.

## 3. Word-eksport

**Bibliotek:** `python-docx` (ren Python, bundles i PyInstaller-build'et). IKKE
pandoc — pandoc mangler på Windows, hvilket netop er grunden til at kun `.md`
produceres i dag. `python-docx` har ingen ekstern afhængighed.

**Filer pr. møde:**
- `Referat {navn}.docx` — metadata-hoved + formateret referat.
- `Transkription {navn}.docx` — metadata-hoved + transskriberet dialog.
- PDF og `.md` produceres ikke længere i normal drift.

**Metadata-hoved (øverst i begge dokumenter):**
- Mødenavn som dokumenttitel (Word Title-stil).
- Dato.
- Mødetype (typens `navn`; ved intet valgt: den neutrale defaults `navn`).
- Deltagere (komma-separeret liste — præcis det brugeren skrev; udelades hvis
  tom).

Hovedet bygges af de værdier brugeren indtastede i appen (mødenavn,
dato, valgt/indtastet mødetype, deltagerfelt) — ikke af noget Gemini gætter.

**Markdown→docx-renderer:** referatets markdown fra Gemini konverteres til
Word-elementer:
- `#`/`##`/`###` → Heading 1/2/3.
- `- `/`* ` → punktliste (List Bullet); `1. ` → nummereret (List Number).
- `**fed**` → fed; `*kursiv*` → kursiv (inline, i både afsnit og listepunkter).
- `> citat` → citat-afsnit (Intense Quote-stil).
- Blanklinje-adskilte blokke → separate afsnit.
- Ukendt/almindelig linje → normalt afsnit.

Transkriptionen har sjældent markdown; den skrives som afsnit (én pr.
ikke-tom linje), så talerlinjer bevares.

**Nød-fallback (datatab-sikring):** `.docx`-skrivning pakkes i try/except.
Fejler den, skrives i stedet `.md` (som i dag) + en advarsel logges, så
indholdet aldrig går tabt. I normal drift ses kun `.docx`.

**Berørte filer:** `meeting_tool.py` (`save_output` omskrives; ny
docx-renderer; `write_pdf_from_markdown` + `_find_weasyprint` fjernes),
`requirements.txt`/`requirements-dev.txt` (+`python-docx`), `modevaerktoj.spec`
(sikr at `docx` bundles — hiddenimport hvis nødvendigt), `tests/test_pdf.py`
(fjernes/erstattes), `tests/conftest.py` (`no_real_pandoc`-fixture fjernes).

## 4. Deltager-defaults

- `DEFAULT_ATTENDEES` sættes til `[]` (både `meeting_app.py` og
  `meeting_tool.py`). Feltet starter tomt.
- Fallback'ene `... or list(DEFAULT_ATTENDEES)` fjernes i optage- og
  fil-flowet, så et tomt felt bliver stående tomt (ingen genindsættelse).
- `resolve_type_attendees`' `global_default` bliver `[]`; huskede deltagere
  per type + sidst brugte bevares uændret.
- Gemini-prompten håndterer allerede tom deltagerliste (attribution bliver
  blot mindre styret) — ingen prompt-ændring nødvendig.

## 5. Mødetype-defaults (nul-typer tolereres)

**Leveret default:** `meeting_types.default.json` gøres **tom** (`{}`). Nye
installationer starter uden nogen typer.

**Nul-typer i hele appen:**
- `_normalize_meeting_types({})` returnerer nu `{}` (ikke længere
  `_BUILTIN_MEETING_TYPE`); `_BUILTIN_MEETING_TYPE` fjernes.
- `load_meeting_types` må returnere `{}`; dens "mindst én type"-kontrakt
  udgår. Seedes stadig fra default-filen hvis `meeting_types.json` mangler —
  men default er nu tom, så resultatet er en tom (men eksisterende) fil, og
  der re-seedes aldrig igen.
- **Neutral kørsels-default:** når ingen type er valgt (tom liste, eller intet
  markeret), bruger optage-/transkriptions-flowet en in-memory neutral type
  fra `MEETING_TYPE_DEFAULTS` (`navn="Møde"`, `balanceret`). Den vises ikke og
  gemmes ikke — den sikrer blot at referat-prompten altid har en gyldig type.
- Guiden (trin 2) og fil-fanen håndterer tom liste: kun "+ Ny mødetype"-kortet
  / det typbare felt vises. Går brugeren videre uden at vælge/skrive, bruges
  den neutrale default. Ad-hoc oprettelse (allerede bygget) tilføjer typer der
  så persisteres og kan vælges.
- Alle `next(iter(self._meeting_types))`-opslag guardes mod tom dict
  (`_type_key = None` når tom).
- `state.json`'s `meeting_type`-nøgle der peger på en slettet/ukendt type →
  behandles som "ingen valgt".

**Mødetyper-fanen:** Slet-knappen er ikke længere spærret ved én type —
brugeren kan slette ned til nul. Tom tilstand viser en hjælpetekst
("Opret en mødetype for at komme i gang") og et tomt/deaktiveret formular.

**Engangs-oprydning (fjern de danske eksempler):** ved opstart, hvis
`meeting_types.json` indeholder PRÆCIS de to urørte seed-typer (nøgler
`driftledelse` + `ledergruppe` med uændret indhold svarende til den hidtidige
`meeting_types.default.json`), erstattes den med `{}`. Har brugeren redigeret
eller tilføjet noget, røres filen ikke. Kører kun én gang (efter tømning er
betingelsen ikke længere sand).

## 6. Fejlhåndtering

- `.docx`-skrivefejl → `.md`-fallback + advarsel (se §3).
- Tom deltagerliste og nul mødetyper er gyldige tilstande, ikke fejl.
- Manglende/ugyldig `meeting_types.json` → tom dict (ikke crash).

## 7. Test

- **Word-renderer** (uden GUI): åbn den genererede `.docx` med `python-docx`
  og assertér: metadata-hoved har mødenavn/dato/mødetype/deltagere; en
  `##`-linje bliver Heading 2; `- `-linjer bliver List Bullet; `**x**` bliver
  fed run; tom deltagerliste udelader deltager-linjen; fallback til `.md` når
  docx-skrivning kaster.
- **Deltager-defaults:** tomt felt sendes tomt videre (ingen genindsættelse);
  huskede per-type deltagere bevares.
- **Mødetyper:** `_normalize_meeting_types({})` → `{}`; nul-typer crasher ikke
  guide/fil-tab-konstruktion (smoke-test via shim); neutral default bruges når
  intet valgt; engangs-oprydning tømmer kun de urørte seed-typer og lader
  redigerede være.
- Hele suiten forbliver grøn; GUI-tests skippes på Tk 9.x som hidtil.
