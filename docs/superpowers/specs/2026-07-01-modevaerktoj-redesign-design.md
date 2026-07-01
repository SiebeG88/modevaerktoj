# Redesign af Mødeværktøj — designdokument

**Dato:** 2026-07-01
**Status:** Godkendt visuelt (brainstorm med visuel companion), afventer spec-review
**Retning:** "Sidebar App" (mørk sidebar til venstre + lys arbejdsflade)

---

## 1. Formål

Give Mødeværktøj et tydeligt nyt udseende **og** en enklere brugsflow. Den
forrige omgang (v1.0.4/1.0.5) tilføjede struktur — guide, MiniHUD, Historik —
men beholdt næsten samme visuelle sprog (lys "glass"-blå). Derfor føltes den
"ens" ved første blik. Dette redesign skifter selve det visuelle sprog og
lægger en fast venstre-sidebar ind, så appen ligner et rigtigt værktøj og er
lettere at navigere.

**Målet er ikke** at ændre optage-/transkriptions-/referat-rørledningen. Al
kernelogik (audio, Hviske/Gemini, referat-generering, filstruktur) forbliver
uændret. Dette er et **UI-lag-redesign**.

## 2. Omfang

**Inkluderet:**
- Ny navigations-shell: venstre sidebar erstatter den øverste `CTkTabview`.
- Nyt farve-/typografi-system (afløser `_CLR`-dict).
- Restyling af alle skærme: Optag (idle + optagelse aktiv), guiden, Historik,
  Indstillinger, samt underfaner (Transkribér fil, Ordliste, Mødetyper).
- Restyling af MiniHUD så den matcher.
- Nyt start-/optage-ikon: rent design (cirkel + rød "rec"-prik), **ingen emoji**.

**Ikke inkluderet (uændret):**
- Optage-, transkriptions- og referat-logik (`meeting_tool.py`, rørledning).
- OTA-opdatering, versionering, build/installer.
- Datamodeller, filstruktur for møder, mødetyper-konfiguration.

## 3. Visuelt system

Afløser den nuværende `_CLR`-dict. Foreslået palet (kan finpudses under
implementering):

| Rolle | Farve | Note |
|---|---|---|
| Sidebar-baggrund | `#1b2a4a` | mørk marineblå, fast venstre |
| Sidebar-ikon (aktiv) | `#ffffff` | + accent-markering |
| Sidebar-ikon (inaktiv) | `#7f92b8` | dæmpet |
| Arbejdsflade-baggrund | `#f7f8fa` | lys grå |
| Kort-flade | `#ffffff` | hvide kort |
| Kort-kant | `#e6e8ec` | |
| Accent | `#3b6fe0` | knapper, valgt kort, progress |
| Accent (hover) | `#2f5ec9` | |
| Primær tekst | `#1b2a4a` | |
| Sekundær tekst | `#5b6474` | |
| Optager (rød) | `#e5382b` | live-prik, OPTAGER-pille |
| Status "klar/ok" (grøn) | `#2e7d5b` | Historik-badge "Referat klar" |
| Status "i gang" (gul) | `#8a6d00` | Historik-badge "Transskriberer" |

**Ikonografi:** line-style SVG-lignende ikoner tegnet på canvas (rec = cirkel med
prik, Historik = graf, Indstillinger = tandhjul). Ingen emoji nogen steder.

**Typografi:** beholder eksisterende font-stak (SF Pro / Segoe UI fallback);
tydeligere hierarki med større, federe overskrifter.

## 4. Navigations-shell (arkitektur-ændring)

**Nu:** `MeetingApp._build_ui()` bygger en top-`CTkTabview` med 6 faner
(Optag, Transkribér fil, Ordliste, Mødetyper, Historik, Indstillinger).

**Nyt:** en fast venstre **sidebar** (smal, ~76–80 px) med ikon-navigation, og en
**indholdsflade** til højre der skifter view. Sidebar er altid synlig.

Sidebar-punkter (primære):
1. **Optag** (rec-ikon) — optage-skærmen.
2. **Historik** (graf-ikon) — liste over møder.
3. **Indstillinger** (tandhjul) — vedvarende opsætning.

Sekundære (konfiguration) placeres som sektioner/underfaner **inde i
Indstillinger**: Transkribér fil, Ordliste, Mødetyper. Begrundelse: de er alle
opsætning/hjælpeværktøjer og hører ikke til i den daglige optage-flow. (Dette
er den ene ikke-triviale beslutning — se §9.)

**Implementering:** ny lille `Sidebar`-widget (CTkFrame med ikon-knapper) +
en view-switcher der `pack_forget()`/`pack()`'er indholds-frames, i stedet for
`CTkTabview.set()`. Alle delte `StringVar`/`BooleanVar` og hjælpe-metoder
bevares uændret, så guiden og rørledningen virker som før.

## 5. Skærme

### 5.1 Optag — idle
Ren optage-skærm i shell'en. Viser konfig-oversigt (Mødeform, Mødetype, Navn,
Deltagere, Motor, Mikrofon, Mappe) som i dag, men restylet, plus knappen **Ny
optagelse** der åbner guiden. Stor start-knap = ren cirkel med rød rec-prik
(afløser nuværende `HeroButton`-emoji-look). Checkbox "Generér referat efter
optagelse" bevares.

### 5.2 Guide "Ny optagelse"
Åbnes **ved opstart** (og via "Ny optagelse"). Beholder de 4 trin fra
`MeetingWizard`, uændret indhold/logik:
1. **Hvilken slags møde?** — Fysisk / Online (Teams/Meet) / Telefonopkald.
2. **Vælg mødetype** — fra konfigurerede typer (detaljeniveau, opgaveliste,
   citater, fokus).
3. **Navn & deltagere** — mødenavn, dato, deltagere.
4. **Optagemotor** — Hviske (lokal/offline) eller Gemini (cloud, bedst dansk).

Ny styling: **progress-bar med 4 segmenter** øverst (aktivt segment = accent),
valg-kort med **blåt flueben** når valgt, footer med "← Tilbage" / "Næste →",
sidste trin = "Start optagelse" der starter optagelsen direkte.
Windows-bemærkningen (kun mikrofon optages) bevares på trin 1.

### 5.3 Optagelse aktiv
Det man ser mens der optages:
- Rød **OPTAGER**-pille med blinkende prik.
- Stort **timer**-tal (tabular-nums), fx `00:14:32`.
- Undertekst = **kun mødenavnet** (fx "Bestyrelsesmøde Q3"). **Ikke** motoren
  (Hviske/Gemini vises ikke her — kun i guide/Indstillinger).
- Live **lyd-bølge** (waveform).
- Knap **"Stop & lav referat"** (mørk, med stop-firkant).

### 5.4 Historik
Liste af møder som kort, hvert med: navn, dato · varighed · deltagere, og et
**status-badge**: "Referat klar" (grøn), "Transskriberer…" (gul), eller intet.
Klik åbner mødet (eksisterende adfærd i `HistorikTab`).

### 5.5 Indstillinger (+ underfaner)
Vedvarende opsætning (mikrofon, mappe, Gemini-nøgle, systemlyd m.m.) i shell'en,
restylet. Underfaner/sektioner: **Transkribér fil**, **Ordliste**, **Mødetyper**
— samme funktion som i dag, ny styling.

### 5.6 MiniHUD
Lille, ramme-løs, altid-øverst HUD ved minimering under optagelse. Restyles til
det nye sprog (mørk baggrund, rød prik, timer). Funktion uændret.

## 6. Komponenter og grænseflader

| Enhed | Ansvar | Ændring |
|---|---|---|
| `Sidebar` (ny) | Ikon-nav, aktiv-markering, kalder view-switch | Ny lille widget |
| View-switcher | Vise/skjule indholds-frames | Afløser `CTkTabview` |
| Farvesystem (`_CLR`) | Central palet | Erstattes med ny palet |
| `RecordButton` | Ren cirkel + rød prik, states (idle/optager) | Afløser `HeroButton` |
| `MeetingWizard` | 4-trins guide | Ny styling, samme trin/logik |
| Optage-view | Idle-oversigt + aktiv-optagelse | Ny styling + aktiv-state layout |
| `HistorikTab` | Møde-liste med badges | Ny styling |
| Indstillinger-view | Opsætning + underfaner | Ny styling + rummer Ordliste/Mødetyper/Transkribér |
| `MiniHUD` | Flydende optage-indikator | Ny styling |

Hver enhed skal kunne forstås og testes for sig: sidebar kender kun til
"skift til view X"; view-switcher kender kun frames; farvesystemet er ren data.

## 7. Fejlhåndtering
Uændret fra i dag. Mangler Gemini-nøgle → guide-trin 4 og Indstillinger viser
advarsel (eksisterende `_motor_hint`). Optagelse må ikke afbrydes af en
opdatering (eksisterende `_maybe_apply_update`-vagt). Ingen ny fejl-flow.

## 8. Test
- **Regressions-tests** (eksisterende suite i `tests/`) skal fortsat passere —
  de dækker rørledning/updater/audio, som ikke ændres.
- UI-tests hvor de findes (fx `test_meeting_types_refresh.py`) tilpasses hvis en
  widget-sti ændres.
- Manuel røgtest på **Windows** (frosset bundle): guide åbner ved opstart, alle
  4 sidebar-views, optagelse aktiv-skærm, MiniHUD ved minimering.
- Verificér at layoutet ser ens/ordentligt ud på Windows (ikke kun macOS).

## 9. Beslutninger der kræver bekræftelse
1. **Placering af Ordliste/Mødetyper/Transkribér fil:** forslag = som sektioner
   inde i Indstillinger (ikke egne sidebar-ikoner), for at holde sidebar enkel.
   Alternativ: giv dem egne sidebar-ikoner.
2. **Accentfarve:** forslag = blå `#3b6fe0` (som i mockup). Kan skiftes til
   teal-grøn eller TBO-farver hvis ønsket.

## 10. Kilder (mockups)
Godkendte mockups ligger i
`.superpowers/brainstorm/50485-1782884009/content/`:
`visual-direction-v2.html` (retning C valgt), `wizard-c-style.html`,
`recording-v2.html`.
