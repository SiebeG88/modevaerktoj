# Ad-hoc mødetype + referatniveau pr. møde — designdokument

**Dato:** 2026-07-16
**Status:** Design godkendt i brainstorm, afventer spec-review
**Omfang:** Kun guiden ("Ny optagelse"), trin 2 ("Vælg mødetype")

---

## 1. Formål

To småfriktioner i den daglige brug:

1. **Man kan ikke oprette en mødetype dér hvor man står.** Skal man optage et
   møde af en ny slags (fx en ansættelsessamtale), skal man først ind i
   Indstillinger → Mødetyper, oprette typen, og så tilbage til guiden.
2. **Referatets detaljeniveau er låst til typen.** Nogle gange vil man have et
   kort referat af et møde hvis type normalt giver et grundigt — uden at
   ændre typen permanent.

Begge løses i guidens trin 2. Referat-rørledningen (`generate_minutes`,
promptbygningen, `meeting_tool.py`) ændres **ikke**.

## 2. Omfang

**Inkluderet:**
- Ad-hoc mødetype: tekstfelt i guidens trin 2; typen gemmes automatisk i
  `meeting_types.json` med standardværdier.
- Referatniveau pr. møde: segmenteret vælger (Kort / Mellem / Grundig) i
  guidens trin 2; overstyrer typens `detaljeniveau` for dette ene møde.

- Samme muligheder i "Transkribér fil"-fanen (tilføjet som udvidelse, se §8):
  typbart mødetype-felt (ad-hoc oprettelse) + niveau-vælger pr. kørsel.

**IKKE inkluderet (bevidst):**
- Overstyring af citater/opgaveliste/fokus pr. møde — de følger typen.
- Regenerering af referat med andet niveau efter mødet.

## 3. Ad-hoc mødetype

**UI:** Under de eksisterende type-kort i trin 2 vises et kort
**"+ Ny mødetype"** med et tekstfelt til navnet.

**Adfærd:**
- Skrives et navn, betragtes den nye type som valgt (kortet markeres som de
  øvrige type-kort).
- Typen **oprettes og gemmes først** når brugeren går videre / starter
  optagelsen — ikke ved hvert tastetryk.
- Nye typer får standardværdier fra `MEETING_TYPE_DEFAULTS` i
  `meeting_tool.py`: `detaljeniveau="balanceret"`, `citater=False`,
  `opgaveliste=True`, tom `fokus`/`ekstra_instruktioner`/`deltagere` — dog med
  det indtastede `navn`.
- **Nøgle:** slug af navnet (små bogstaver, æ/ø/å translittereres, ikke-alfanumerisk
  → `-`). Kolliderer sluggen med en eksisterende nøgle uden at navnet matcher
  (se næste punkt), tilføjes et talsuffiks (`-2`, `-3`, …).
- **Duplikat-navn:** matcher navnet (case-insensitivt, trimmet) en eksisterende
  type, genbruges den eksisterende — der oprettes ingen dublet, og den
  eksisterende type overskrives aldrig.
- Efter gem: `refresh_meeting_types()`-mekanismen genindlæser typer, så alle
  dropdowns (hovedskærm, guide, Transkribér fil, Mødetyper-fanen) ser den nye
  type med det samme.
- Gemmes via samme skrive-mekanik som Mødetyper-fanen bruger
  (`meeting_types.json` i `CONFIG_DIR`), så normalisering og fejlhåndtering
  er fælles.

## 4. Referatniveau pr. møde

**UI:** Under typelisten i trin 2 en række:

```
Referat:  [ Kort ]  [ Mellem ]  [ Grundig ]
```

Tre knapper i segmenteret stil (én aktiv ad gangen), i appens eksisterende
visuelle sprog (accentfarve på den aktive).

**Adfærd:**
- Etiketterne mapper til de interne niveauer: Kort=`kortfattet`,
  Mellem=`balanceret`, Grundig=`grundig`.
- Når guiden åbner trin 2, er typens eget niveau forvalgt.
- Skiftes type (eller indtastes ad-hoc type), nulstilles vælgeren til den
  valgte types niveau (ad-hoc = `balanceret`).
- Vælger brugeren et andet niveau, gælder det **kun dette møde**. Typen i
  `meeting_types.json` ændres ikke, og valget persisteres ikke i
  `state.json`.
- **Mekanik:** ved start af optagelsen kopieres den valgte types dict, og
  `detaljeniveau` erstattes i kopien. Kopien gives videre til den eksisterende
  pipeline (`_run_recording` → `generate_minutes`). Original-dicten i
  `self._meeting_types` røres aldrig.

## 5. Berørte filer

- `meeting_app.py` — `MeetingWizard` trin 2 (`type`-step): nyt kort + vælger;
  start-flow: gem evt. ad-hoc type + byg overstyret kopi.
- `meeting_types.json` — får nye poster ved ad-hoc oprettelse (ingen
  skemaændring).
- `meeting_tool.py` — **ingen ændringer** (evt. genbrug af eksisterende
  hjælpefunktioner).
- `tests/` — nye tests, se nedenfor.

## 6. Fejlhåndtering

- Tomt/whitespace-navn i ad-hoc feltet = intet oprettes; den senest markerede
  eksisterende type bruges.
- Kan `meeting_types.json` ikke skrives, vises samme fejlbesked som
  Mødetyper-fanen giver i dag; mødet kan stadig startes med typen i hukommelsen
  (den er bare ikke gemt til næste gang).
- Overstyringskopien normaliseres ikke igen — den bygger på en allerede
  normaliseret type, og `detaljeniveau` sættes kun til en af de tre gyldige
  værdier fra vælgeren.

## 7. Test

- Ad-hoc: oprettelse med defaults + indtastet navn; duplikat-navn
  (case-insensitivt) genbruger eksisterende type uden at overskrive;
  slug-kollision; tomt navn opretter intet; refresh opdaterer dropdowns.
- Niveau-overstyring: kopien har det valgte niveau; originalen i
  `self._meeting_types` er uændret; vælger nulstilles ved typeskift; uden
  aktiv overstyring sendes typens eget niveau.
- Hele den eksisterende suite (300+) forbliver grøn.

## 8. Udvidelse: samme muligheder i "Transkribér fil"-fanen

Brugerønske efter første leverance: de samme to muligheder skal også findes
når man transkriberer en eksisterende lydfil.

**Ad-hoc mødetype:** Mødetype-dropdownen i fanen bliver typbar
(`state="normal"` i stedet for `"readonly"`). Skriver brugeren et navn der
ikke matcher en eksisterende type (case-insensitivt, trimmet), oprettes
typen automatisk med standardværdier **når kørslen startes** — samme
regler som i guiden (navnematch genbruger, slug-kollision får talsuffiks,
skrivefejl → typen beholdes i hukommelsen for denne kørsel). En lille
hjælpetekst under dropdownen forklarer det.

**Referatniveau pr. kørsel:** Samme Kort/Mellem/Grundig-vælger som i
guiden, under mødetype-feltet. Forvalgt = typens eget niveau; nulstilles
ved typeskift og efter start. Afviger valget, sendes en kopi af typen med
andet `detaljeniveau` — typen og `meeting_types.json` røres ikke. I fanen
er vælgeren selv tilstanden (ingen separat override-attribut): niveau ≠
typens eget = overstyring.

**Wiring:** `TranscribeFileTab` får en valgfri `on_types_changed`-callback
(spejler `MeetingTypesTab.on_change`). `MeetingApp` binder den til
`_on_meeting_types_changed()` + `_types_tab.reload_from_disk()`, så alle
faner ser en ad-hoc type oprettet fra fil-fanen. Kernen samles i
`TranscribeFileTab._resolve_meeting_type()` (testbar uden GUI).
