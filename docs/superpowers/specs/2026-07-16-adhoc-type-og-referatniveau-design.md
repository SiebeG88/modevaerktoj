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

**IKKE inkluderet (bevidst):**
- Overstyring af citater/opgaveliste/fokus pr. møde — de følger typen.
- Samme vælger i "Transkribér fil"-fanen — kan tilføjes senere, trivielt.
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
