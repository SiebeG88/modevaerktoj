# Word-eksport + rene defaults — implementeringsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eksportér referat + transkription som pænt formaterede Word-dokumenter (med metadata-hoved), fjern hardkodede deltagernavne, og gør mødetyper valgfrie (nul tolereres, danske eksempler ryddes).

**Architecture:** Ren markdown→docx-renderer i nyt modul `docx_export.py` (python-docx, testbar uden GUI). `meeting_tool.save_output` kalder den og får mødetype+deltagere med til hovedet; PDF/`.md`-primæroutput fjernes (`.md` kun som nød-fallback). Deltager-/mødetype-defaults tømmes i `meeting_tool.py`+`meeting_app.py`; appen tolererer nul mødetyper via en in-memory neutral default, og en engangs-oprydning fjerner de to urørte danske seed-typer.

**Tech Stack:** Python 3.12, python-docx, CustomTkinter, pytest. Spec: `docs/superpowers/specs/2026-07-20-word-eksport-og-rene-defaults-design.md`.

## Global Constraints

- Branch: `feature/word-eksport-og-rene-defaults` — aldrig push til `main`.
- Kode-identifikatorer på engelsk, kommentarer/strenge/docstrings på dansk (som resten af filen).
- Transkription/Gemini/referat-prompt-LOGIKKEN ændres ikke (undtagen den nødvendige None-guard i `generate_minutes` så tom typeliste ikke crasher).
- Kør tests med repoets venv: `.venv312/bin/python -m pytest tests/ -q`. GUI-tests springes over på Tk 9.x — forventet.
- Ny afhængighed `python-docx` skal installeres i `.venv312` FØR test og bundles i `modevaerktoj.spec`.

---

### Task 1: Word-renderer-modul `docx_export.py` + afhængighed

**Files:**
- Create: `docx_export.py`
- Modify: `requirements.txt`, `requirements-dev.txt`, `modevaerktoj.spec`
- Test: `tests/test_docx_export.py`

**Interfaces:**
- Produces (bruges af Task 2):
  - `write_meeting_docx(path: Path, *, title: str, date: str, meeting_type_name: str | None, attendees: list[str], body: str, is_transcript: bool) -> Path`

- [ ] **Step 1: Installér afhængighed i venv + requirements**

Tilføj `python-docx>=1.1.0` som sidste linje i både `requirements.txt` og `requirements-dev.txt`. Kør:

```bash
.venv312/bin/python -m pip install "python-docx>=1.1.0"
```
Forventet: "Successfully installed python-docx-... lxml-...".

- [ ] **Step 2: Skriv de fejlende tests**

Opret `tests/test_docx_export.py`:

```python
"""Word-renderer: metadata-hoved + markdown→docx uden GUI."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
docx = pytest.importorskip("docx")
import docx_export


def _para_texts(doc):
    return [p.text for p in doc.paragraphs]


def _style_of(doc, text):
    for p in doc.paragraphs:
        if p.text == text:
            return p.style.name
    return None


def test_header_has_all_metadata(tmp_path):
    out = docx_export.write_meeting_docx(
        tmp_path / "r.docx", title="Ugemøde", date="20-07-2026",
        meeting_type_name="Driftmøde", attendees=["Ole", "Jacob"],
        body="Noget indhold.", is_transcript=False)
    doc = docx.Document(str(out))
    texts = _para_texts(doc)
    assert any(p.text == "Ugemøde" for p in doc.paragraphs)
    assert "Dato: 20-07-2026" in texts
    assert "Mødetype: Driftmøde" in texts
    assert "Deltagere: Ole, Jacob" in texts


def test_empty_attendees_omits_line(tmp_path):
    out = docx_export.write_meeting_docx(
        tmp_path / "r.docx", title="M", date="20-07-2026",
        meeting_type_name="Møde", attendees=[], body="x", is_transcript=False)
    doc = docx.Document(str(out))
    assert not any(p.text.startswith("Deltagere:") for p in doc.paragraphs)


def test_markdown_headings_and_lists(tmp_path):
    body = "## Beslutninger\n\n- Punkt et\n- Punkt to\n\n1. Først\n"
    out = docx_export.write_meeting_docx(
        tmp_path / "r.docx", title="M", date="d",
        meeting_type_name=None, attendees=[], body=body, is_transcript=False)
    doc = docx.Document(str(out))
    assert _style_of(doc, "Beslutninger") == "Heading 2"
    assert _style_of(doc, "Punkt et") == "List Bullet"
    assert _style_of(doc, "Først") == "List Number"


def test_bold_becomes_bold_run(tmp_path):
    out = docx_export.write_meeting_docx(
        tmp_path / "r.docx", title="M", date="d",
        meeting_type_name=None, attendees=[], body="Dette er **vigtigt** nu",
        is_transcript=False)
    doc = docx.Document(str(out))
    p = next(p for p in doc.paragraphs if "vigtigt" in p.text)
    bold_runs = [r.text for r in p.runs if r.bold]
    assert "vigtigt" in bold_runs


def test_transcript_keeps_speaker_lines_as_paragraphs(tmp_path):
    body = "Ole: Hej alle\nJacob: Godmorgen\n"
    out = docx_export.write_meeting_docx(
        tmp_path / "t.docx", title="T", date="d",
        meeting_type_name=None, attendees=[], body=body, is_transcript=True)
    doc = docx.Document(str(out))
    texts = _para_texts(doc)
    assert "Ole: Hej alle" in texts
    assert "Jacob: Godmorgen" in texts
```

- [ ] **Step 3: Kør testene — de skal fejle**

Kør: `.venv312/bin/python -m pytest tests/test_docx_export.py -q`
Forventet: FAIL med `ModuleNotFoundError: No module named 'docx_export'`.

- [ ] **Step 4: Implementér `docx_export.py`**

```python
"""Word-eksport (.docx) af referat + transkription.

Ren, GUI-fri renderer bygget på python-docx (ingen ekstern afhængighed som
pandoc — virker derfor også på Windows uden ekstra installation). Konverterer
referatets lette markdown til Word-elementer og skriver et metadata-hoved med
mødenavn, dato, mødetype og deltagere."""
from __future__ import annotations

import re
from pathlib import Path

from docx import Document

# Inline-tokens: **fed** og *kursiv*. Capturing-group så re.split beholder dem.
_INLINE_RE = re.compile(r"(\*\*.+?\*\*|\*.+?\*)")
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")
_BULLET_RE = re.compile(r"^[-*]\s+(.*)$")
_NUMBER_RE = re.compile(r"^\d+\.\s+(.*)$")


def _add_inline(paragraph, text: str) -> None:
    """Tilføj tekst til et afsnit med **fed**/*kursiv* som separate runs."""
    for part in _INLINE_RE.split(text):
        if not part:
            continue
        if len(part) >= 4 and part.startswith("**") and part.endswith("**"):
            paragraph.add_run(part[2:-2]).bold = True
        elif len(part) >= 2 and part.startswith("*") and part.endswith("*"):
            paragraph.add_run(part[1:-1]).italic = True
        else:
            paragraph.add_run(part)


def _render_markdown(doc, body: str) -> None:
    """Render referatets lette markdown til Word-elementer. Ukendte linjer
    bliver almindelige afsnit; blanklinjer springes over (ingen tomme afsnit)."""
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _HEADING_RE.match(line)
        if m:
            doc.add_heading(m.group(2), level=len(m.group(1)))
            continue
        m = _BULLET_RE.match(line)
        if m:
            _add_inline(doc.add_paragraph(style="List Bullet"), m.group(1))
            continue
        m = _NUMBER_RE.match(line)
        if m:
            _add_inline(doc.add_paragraph(style="List Number"), m.group(1))
            continue
        if line.startswith(">"):
            _add_inline(doc.add_paragraph(style="Intense Quote"), line[1:].strip())
            continue
        _add_inline(doc.add_paragraph(), line)


def _add_meta_line(doc, label: str, value: str) -> None:
    p = doc.add_paragraph()
    p.add_run(f"{label}: ").bold = True
    p.add_run(value)


def write_meeting_docx(
    path: Path,
    *,
    title: str,
    date: str,
    meeting_type_name: str | None,
    attendees: list[str],
    body: str,
    is_transcript: bool,
) -> Path:
    """Skriv ét mødedokument (.docx) med metadata-hoved + indhold.

    Referatet (is_transcript=False) renderes fra markdown; transkriptionen
    (is_transcript=True) skrives som ét afsnit pr. ikke-tom linje, så
    talerlinjer bevares. Returnerer path."""
    doc = Document()
    doc.add_heading(title, level=0)  # Word "Title"-stil
    _add_meta_line(doc, "Dato", date)
    if meeting_type_name:
        _add_meta_line(doc, "Mødetype", meeting_type_name)
    if attendees:
        _add_meta_line(doc, "Deltagere", ", ".join(attendees))
    doc.add_paragraph()  # luft før indhold

    if is_transcript:
        for raw in body.splitlines():
            line = raw.strip()
            if line:
                doc.add_paragraph(line)
    else:
        _render_markdown(doc, body)

    doc.save(str(path))
    return path
```

- [ ] **Step 5: Kør testene — de skal bestå**

Kør: `.venv312/bin/python -m pytest tests/test_docx_export.py -q`
Forventet: 5 passed.

- [ ] **Step 6: Bundl python-docx i PyInstaller-spec'et**

I `modevaerktoj.spec`, umiddelbart efter linjen
`ctk_datas, ctk_binaries, ctk_hiddenimports = collect_all("customtkinter")`
tilføj:

```python
# python-docx skibber en default-skabelon (docx/templates/default.docx) som
# pakke-data — Document() fejler uden den, så collect_all bundler alt.
docx_datas, docx_binaries, docx_hiddenimports = collect_all("docx")
```

I `datas`-listen, efter `datas += ctk_datas`, tilføj `datas += docx_datas`.
I `binaries`-listen, efter `binaries += ctk_binaries`, tilføj `binaries += docx_binaries`.
I `hiddenimports`-listen, tilføj `"docx"` og skift `] + ctk_hiddenimports,` til
`] + ctk_hiddenimports + docx_hiddenimports,`.

- [ ] **Step 7: Verificér spec'et er gyldig Python**

Kør: `.venv312/bin/python -c "import ast; ast.parse(open('modevaerktoj.spec').read()); print('spec ok')"`
Forventet: `spec ok`.

- [ ] **Step 8: Commit**

```bash
git add docx_export.py tests/test_docx_export.py requirements.txt requirements-dev.txt modevaerktoj.spec
git commit -m "feat: docx_export-modul (markdown→Word) + python-docx-afhængighed"
```

---

### Task 2: Kobl `save_output` til Word + fjern PDF

**Files:**
- Modify: `meeting_tool.py` (`save_output` linje ~2177; dens kald i `transcribe_file` linje ~2258; fjern `write_pdf_from_markdown` + `_find_weasyprint`), `meeting_app.py` (`save_output`-kald i optage-workeren linje ~1681)
- Delete: `tests/test_pdf.py`
- Modify: `tests/conftest.py` (fjern `no_real_pandoc`-fixture)
- Test: `tests/test_save_output.py` (ny)

**Interfaces:**
- Consumes: `docx_export.write_meeting_docx` (Task 1)
- Produces: `save_output(output_dir, date, transcript, minutes, name_base=None, meeting_type=None, attendees=None)` — skriver `.docx` for transkription (altid) og referat (hvis minutes); `.md`-fallback ved docx-fejl.

- [ ] **Step 1: Skriv de fejlende tests**

Opret `tests/test_save_output.py`:

```python
"""save_output skriver Word-dokumenter (transkription + referat)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
docx = pytest.importorskip("docx")
import meeting_tool


def test_writes_docx_for_transcript_and_minutes(tmp_path):
    meeting_tool.save_output(
        output_dir=tmp_path, date="20-07-2026",
        transcript="Ole: hej", minutes="## Referat\n\n- punkt",
        name_base="Ugemøde",
        meeting_type={"navn": "Driftmøde"}, attendees=["Ole"])
    assert (tmp_path / "Transkription Ugemøde.docx").exists()
    assert (tmp_path / "Referat Ugemøde.docx").exists()
    assert not (tmp_path / "Referat Ugemøde.pdf").exists()
    assert not (tmp_path / "Referat Ugemøde.md").exists()
    doc = docx.Document(str(tmp_path / "Referat Ugemøde.docx"))
    assert any(p.text == "Mødetype: Driftmøde" for p in doc.paragraphs)


def test_no_minutes_writes_only_transcript(tmp_path):
    meeting_tool.save_output(
        output_dir=tmp_path, date="d", transcript="x", minutes=None,
        name_base="M", meeting_type=None, attendees=[])
    assert (tmp_path / "Transkription M.docx").exists()
    assert not (tmp_path / "Referat M.docx").exists()


def test_docx_failure_falls_back_to_md(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("docx nede")
    monkeypatch.setattr(meeting_tool.docx_export, "write_meeting_docx", boom)
    meeting_tool.save_output(
        output_dir=tmp_path, date="d", transcript="raw tekst", minutes="referat",
        name_base="M", meeting_type=None, attendees=[])
    assert (tmp_path / "Transkription M.md").exists()
    assert (tmp_path / "Referat M.md").exists()
    assert "referat" in (tmp_path / "Referat M.md").read_text(encoding="utf-8")
```

- [ ] **Step 2: Kør testene — de skal fejle**

Kør: `.venv312/bin/python -m pytest tests/test_save_output.py -q`
Forventet: FAIL (`save_output` skriver `.md`/PDF, ikke `.docx`; `meeting_tool.docx_export` findes ikke endnu).

- [ ] **Step 3: Importér docx_export + omskriv save_output**

I `meeting_tool.py`, tilføj til imports øverst (efter de øvrige lokale imports, fx efter `import app_paths`):

```python
import docx_export
```

Erstat hele `save_output`-funktionen (linje ~2177-2205) med:

```python
def save_output(
    output_dir: Path,
    date: str,
    transcript: str,
    minutes: str | None,
    name_base: str | None = None,
    meeting_type: dict | None = None,
    attendees: list[str] | None = None,
):
    """Gemmer transkription og (evt.) referat som Word-dokumenter med
    metadata-hoved. Fejler .docx-skrivning, falder vi tilbage til .md så
    intet indhold går tabt."""
    output_dir.mkdir(parents=True, exist_ok=True)
    base = name_base or date
    mt_name = meeting_type.get("navn") if isinstance(meeting_type, dict) else None
    att = attendees or []

    def _write(kind_title: str, filename_stem: str, body: str, is_transcript: bool):
        try:
            path = docx_export.write_meeting_docx(
                output_dir / f"{filename_stem}.docx",
                title=f"{kind_title} – {base}", date=date,
                meeting_type_name=mt_name, attendees=att,
                body=body, is_transcript=is_transcript)
            print(f"{kind_title} gemt: {path}", flush=True)
        except Exception as e:  # nød-fallback: bevar indholdet som .md
            md = output_dir / f"{filename_stem}.md"
            md.write_text(f"# {kind_title} - {base}\n\nDato: {date}\n\n{body}\n",
                          encoding="utf-8")
            print(f"Advarsel: Word-eksport fejlede ({e}); gemte {md}", file=sys.stderr)

    _write("Transkription", f"Transkription {base}", transcript, True)
    if minutes:
        _write("Referat", f"Referat {base}", minutes, False)
```

- [ ] **Step 4: Fjern PDF-hjælperne**

Slet funktionerne `write_pdf_from_markdown` (linje ~2139) og `_find_weasyprint` (find den med `grep -n "_find_weasyprint" meeting_tool.py`) helt fra `meeting_tool.py`.

- [ ] **Step 5: Opdatér save_output-kaldet i transcribe_file**

I `meeting_tool.py` (`transcribe_file`, kald ~linje 2252), tilføj de to nye argumenter:

```python
    save_output(
        output_dir=output_dir,
        date=date,
        transcript=transcript,
        minutes=minutes,
        name_base=name_base,
        meeting_type=meeting_type,
        attendees=attendees,
    )
```

- [ ] **Step 6: Opdatér save_output-kaldet i optage-workeren**

I `meeting_app.py` (~linje 1681) tilføj de to nye argumenter (variablerne `meeting_type` og `attendees` findes allerede i scope):

```python
            meeting_tool.save_output(
                output_dir=output_dir,
                date=date,
                transcript=transcript,
                minutes=minutes,
                name_base=name,
                meeting_type=meeting_type,
                attendees=attendees,
            )
```

- [ ] **Step 7: Fjern PDF-testen + pandoc-fixturen**

```bash
git rm tests/test_pdf.py
```
I `tests/conftest.py`: fjern hele `no_real_pandoc`-fixturen (autouse-fixturen der stubber `write_pdf_from_markdown`; find den med `grep -n "no_real_pandoc" tests/conftest.py`). Hvis den refererer `test_pdf` i en `if`-gren, fjernes hele fixturen.

- [ ] **Step 8: Kør de nye tests + hele suiten**

Kør: `.venv312/bin/python -m pytest tests/test_save_output.py -q`
Forventet: 3 passed.
Kør: `.venv312/bin/python -m pytest tests/ -q`
Forventet: grøn (test_pdf væk; ingen referencer til write_pdf_from_markdown).

- [ ] **Step 9: Commit**

```bash
git add meeting_tool.py meeting_app.py tests/conftest.py tests/test_save_output.py
git rm --cached tests/test_pdf.py 2>/dev/null; git add -A tests/
git commit -m "feat: gem referat+transkription som Word; fjern PDF-generering"
```

---

### Task 3: Tomme deltager-defaults

**Files:**
- Modify: `meeting_tool.py` (`DEFAULT_ATTENDEES` linje ~2273; `resolve_type_attendees` `global_default`-brug), `meeting_app.py` (`DEFAULT_ATTENDEES` linje ~41; fallback'ene `or list(DEFAULT_ATTENDEES)` linje ~1494 og ~3052)
- Test: `tests/test_empty_attendees.py` (ny)

**Interfaces:**
- Produces: `DEFAULT_ATTENDEES == []` i begge moduler; tomt deltagerfelt bevares tomt.

- [ ] **Step 1: Skriv de fejlende tests**

Opret `tests/test_empty_attendees.py`:

```python
"""Deltager-defaults er tomme; huskede per-type deltagere bevares."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_app
import meeting_tool


def test_default_attendees_empty():
    assert meeting_app.DEFAULT_ATTENDEES == []
    assert meeting_tool.DEFAULT_ATTENDEES == []


def test_resolve_empty_when_nothing_remembered():
    mt = meeting_tool._normalize_meeting_type({"navn": "M"})  # deltagere=[]
    got = meeting_tool.resolve_type_attendees("m", mt, {}, meeting_tool.DEFAULT_ATTENDEES)
    assert got == []


def test_resolve_prefers_remembered():
    mt = meeting_tool._normalize_meeting_type({"navn": "M"})
    got = meeting_tool.resolve_type_attendees(
        "m", mt, {"m": ["Ole"]}, meeting_tool.DEFAULT_ATTENDEES)
    assert got == ["Ole"]
```

- [ ] **Step 2: Kør testene — de skal fejle**

Kør: `.venv312/bin/python -m pytest tests/test_empty_attendees.py -q`
Forventet: FAIL på `test_default_attendees_empty` (lister er `["Mads","Lars","Dorte"]`).

- [ ] **Step 3: Tøm DEFAULT_ATTENDEES**

I `meeting_tool.py` linje ~2273: `DEFAULT_ATTENDEES = []`
I `meeting_app.py` linje ~41: `DEFAULT_ATTENDEES = []`

- [ ] **Step 4: Fjern genindsættelses-fallback'ene**

I `meeting_app.py` (~linje 1494), i optage-flowets deltager-parsing, ændr

```python
        if not attendees:
            attendees = list(DEFAULT_ATTENDEES)
```
til (fjern fallback — tomt bliver tomt):
```python
        # Tomt felt bevares tomt; ingen genindsættelse af standardnavne.
```

I `meeting_app.py` (~linje 3052), i fil-fanens `_start`, ændr
```python
        ] or list(DEFAULT_ATTENDEES)
```
til
```python
        ]
```

- [ ] **Step 5: Kør testene + suiten**

Kør: `.venv312/bin/python -m pytest tests/test_empty_attendees.py tests/ -q`
Forventet: grøn (nye tests passed; ingen regression — bemærk at tests der antog default-navne skal opdateres hvis de fejler; ret dem til tom-forventning).

- [ ] **Step 6: Commit**

```bash
git add meeting_app.py meeting_tool.py tests/test_empty_attendees.py
git commit -m "feat: tomme deltager-defaults — intet genindsættes"
```

---

### Task 4: Nul mødetyper tolereres + engangs-oprydning (kernelogik)

**Files:**
- Modify: `meeting_tool.py` (`_normalize_meeting_types` ~linje 2626; fjern `_BUILTIN_MEETING_TYPE` ~linje 2611; `load_meeting_types` kontrakt; `generate_minutes` None-gren ~linje 1922; ny `neutral_meeting_type()` + `cleanup_seed_meeting_types()`), `meeting_types.default.json`
- Test: `tests/test_zero_meeting_types.py` (ny)

**Interfaces:**
- Produces (bruges af Task 5): `neutral_meeting_type() -> dict` (normaliseret, navn "Møde"); `cleanup_seed_meeting_types(tool_dir: Path) -> None`; `_normalize_meeting_types({})` returnerer `{}`; `load_meeting_types` kan returnere `{}`.

- [ ] **Step 1: Skriv de fejlende tests**

Opret `tests/test_zero_meeting_types.py`:

```python
"""Nul mødetyper tolereres; neutral default; engangs-oprydning af seed."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_tool

_PRISTINE = {
    "driftledelse": {
        "navn": "Driftledelsesmøde", "detaljeniveau": "kortfattet",
        "citater": False, "opgaveliste": True,
        "fokus": "Konkrete opgaver, aftaler og beslutninger for den daglige drift.",
        "ekstra_instruktioner": "", "deltagere": [],
    },
    "ledergruppe": {
        "navn": "Ledergruppemøde", "detaljeniveau": "grundig",
        "citater": True, "opgaveliste": False,
        "fokus": "Strategi, langsigtet retning og principielle drøftelser.",
        "ekstra_instruktioner": "Skriv i sammenhængende prosa. Bevar nuancer og uenigheder.",
        "deltagere": [],
    },
}


def test_normalize_empty_stays_empty():
    assert meeting_tool._normalize_meeting_types({}) == {}


def test_neutral_meeting_type_is_valid():
    mt = meeting_tool.neutral_meeting_type()
    assert mt["navn"] == "Møde"
    assert mt["detaljeniveau"] == "balanceret"


def test_none_type_normalizes_to_neutral_without_crash():
    # generate_minutes' None-gren bruger _normalize_meeting_type(meeting_type or {}):
    # tom typeliste må aldrig ramme next(iter({})). Verificér guard-udtrykket.
    mt = meeting_tool._normalize_meeting_type(None or {})
    assert mt["navn"] == "Møde"


def test_cleanup_clears_pristine_seed(tmp_path):
    (tmp_path / "meeting_types.json").write_text(
        json.dumps(_PRISTINE, ensure_ascii=False), encoding="utf-8")
    meeting_tool.cleanup_seed_meeting_types(tmp_path)
    assert json.loads((tmp_path / "meeting_types.json").read_text(encoding="utf-8")) == {}


def test_cleanup_preserves_edited(tmp_path):
    edited = json.loads(json.dumps(_PRISTINE))
    edited["driftledelse"]["navn"] = "Mit møde"
    (tmp_path / "meeting_types.json").write_text(
        json.dumps(edited, ensure_ascii=False), encoding="utf-8")
    meeting_tool.cleanup_seed_meeting_types(tmp_path)
    data = json.loads((tmp_path / "meeting_types.json").read_text(encoding="utf-8"))
    assert data["driftledelse"]["navn"] == "Mit møde"


def test_cleanup_preserves_extra_type(tmp_path):
    extra = json.loads(json.dumps(_PRISTINE))
    extra["egen"] = {"navn": "Egen type"}
    (tmp_path / "meeting_types.json").write_text(
        json.dumps(extra, ensure_ascii=False), encoding="utf-8")
    meeting_tool.cleanup_seed_meeting_types(tmp_path)
    data = json.loads((tmp_path / "meeting_types.json").read_text(encoding="utf-8"))
    assert "egen" in data
```

- [ ] **Step 2: Kør testene — de skal fejle**

Kør: `.venv312/bin/python -m pytest tests/test_zero_meeting_types.py -q`
Forventet: FAIL (`_normalize_meeting_types({})` giver builtin; `neutral_meeting_type`/`cleanup_seed_meeting_types` findes ikke).

- [ ] **Step 3: Tillad tom dict + fjern builtin**

I `meeting_tool.py`, slet hele `_BUILTIN_MEETING_TYPE`-dict'en (~linje 2607-2622, inkl. kommentaren over den).

Erstat `_normalize_meeting_types` med:

```python
def _normalize_meeting_types(raw: dict) -> dict:
    """Normaliserer alle typer. Ikke-dict → {}; tom dict bevares tom
    (nul mødetyper er en gyldig tilstand)."""
    if not isinstance(raw, dict):
        return {}
    return {str(k): _normalize_meeting_type(v) for k, v in raw.items()}
```

- [ ] **Step 4: Tilføj neutral default + oprydning**

Tilføj i `meeting_tool.py` (fx lige efter `_normalize_meeting_types`):

```python
def neutral_meeting_type() -> dict:
    """En gyldig, ikke-gemt standardtype til brug når ingen er valgt
    (navn 'Møde', balanceret niveau). Vises ikke og persisteres ikke."""
    return _normalize_meeting_type({})


# De to oprindelige seed-typer, som gamle installationer stadig har liggende.
# Bruges KUN til engangs-oprydning så de urørte danske eksempler kan fjernes.
_SEED_MEETING_TYPES = {
    "driftledelse": {
        "navn": "Driftledelsesmøde", "detaljeniveau": "kortfattet",
        "citater": False, "opgaveliste": True,
        "fokus": "Konkrete opgaver, aftaler og beslutninger for den daglige drift.",
        "ekstra_instruktioner": "", "deltagere": [],
    },
    "ledergruppe": {
        "navn": "Ledergruppemøde", "detaljeniveau": "grundig",
        "citater": True, "opgaveliste": False,
        "fokus": "Strategi, langsigtet retning og principielle drøftelser.",
        "ekstra_instruktioner": "Skriv i sammenhængende prosa. Bevar nuancer og uenigheder.",
        "deltagere": [],
    },
}


def cleanup_seed_meeting_types(tool_dir: Path) -> None:
    """Engangs-oprydning: hvis meeting_types.json er PRÆCIS de to urørte
    seed-typer, tømmes den ({}). Har brugeren redigeret/tilføjet noget, røres
    filen ikke. Efter tømning er betingelsen aldrig sand igen."""
    f = tool_dir / "meeting_types.json"
    if not f.exists():
        return
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if _normalize_meeting_types(raw) == _normalize_meeting_types(_SEED_MEETING_TYPES):
        try:
            f.write_text("{}", encoding="utf-8")
        except OSError as e:
            print(f"Kunne ikke rydde seed-mødetyper: {e}", file=sys.stderr)
```

- [ ] **Step 5: Guard generate_minutes' None-gren**

I `meeting_tool.py` `generate_minutes` (~linje 1922), erstat

```python
    if meeting_type is None:
        _types = load_meeting_types(CONFIG_DIR)
        meeting_type = _types.get("driftledelse") or next(iter(_types.values()))
    meeting_type = _normalize_meeting_type(meeting_type)
```
med (undgå crash på tom typeliste; brug neutral default):
```python
    meeting_type = _normalize_meeting_type(meeting_type or {})
```

- [ ] **Step 6: Tøm default-seed-filen**

Erstat hele indholdet af `meeting_types.default.json` med:

```json
{}
```

- [ ] **Step 7: Ret test-fixtur der antog default-typer (hvis nogen fejler)**

Kør: `.venv312/bin/python -m pytest tests/ -q`
Hvis tests i `tests/test_meeting_types.py` antager builtin/2-typer ved tom/ugyldig JSON, opdatér dem til at forvente `{}`. Vis kun de linjer der fejler og ret forventningen (fx `assert result == {}` i stedet for `"driftledelse" in result`).

- [ ] **Step 8: Kør de nye tests + suiten**

Kør: `.venv312/bin/python -m pytest tests/test_zero_meeting_types.py tests/ -q`
Forventet: grøn.

- [ ] **Step 9: Commit**

```bash
git add meeting_tool.py meeting_types.default.json tests/test_zero_meeting_types.py tests/test_meeting_types.py
git commit -m "feat: nul mødetyper tolereres + engangs-oprydning af danske seed-typer"
```

---

### Task 5: GUI håndterer nul mødetyper + wiring

**Files:**
- Modify: `meeting_app.py`:
  - Opstart: efter `meeting_tool.seed_user_config()` (~linje 4062) kald `cleanup_seed_meeting_types`
  - `MeetingApp.__init__` (~linje 483-486) og `TranscribeFileTab.__init__` (~linje 2498-2504): guard `_type_key` mod tom dict (None)
  - `MeetingApp._on_type_selected` / `_key_for_label` (~linje 1260): tåle None/tom
  - `MeetingApp._start_recording` (~linje 1528): neutral type når `_type_key` er None
  - `TranscribeFileTab._resolve_meeting_type` (~linje 2894): neutral type når ingen valgt og intet indtastet
  - `MeetingWizard._build_type` (~linje 3228) og `_current_level` (~linje 3462): tåle tom typeliste
  - `MeetingTypesTab`: `_refresh_selector` delete-guard (~linje 2272), `current_key` (~linje 2040) + `_load_into_form` + `_delete` tåle nul; tom-tilstands-hint
- Test: GUI-smoke via shim (køres manuelt/CI), + `tests/test_zero_meeting_types.py` udvides med et rent `_resolve`-tilfælde

**Interfaces:**
- Consumes: `meeting_tool.neutral_meeting_type`, `meeting_tool.cleanup_seed_meeting_types` (Task 4)

- [ ] **Step 1: Skriv den fejlende test (ren, uden GUI)**

Tilføj nederst i `tests/test_zero_meeting_types.py`:

```python
from types import MethodType, SimpleNamespace


def test_file_tab_resolve_uses_neutral_when_no_type(tmp_path, monkeypatch):
    import meeting_app
    monkeypatch.setattr(
        meeting_app.MeetingTypesTab, "TYPES_FILE", tmp_path / "meeting_types.json")
    tab = SimpleNamespace(
        _meeting_types={}, _type_keys=[], _type_key=None,
        type_var=SimpleNamespace(get=lambda: "", set=lambda v: None),
        _level_seg=SimpleNamespace(get=lambda: "Mellem"),
        _on_types_changed=lambda: None,
        status_var=SimpleNamespace(set=lambda s: None),
    )
    tab._resolve_meeting_type = MethodType(
        meeting_app.TranscribeFileTab._resolve_meeting_type, tab)
    mt = tab._resolve_meeting_type()
    assert mt["navn"] == "Møde"  # neutral default
```

- [ ] **Step 2: Kør testen — den skal fejle**

Kør: `.venv312/bin/python -m pytest tests/test_zero_meeting_types.py::test_file_tab_resolve_uses_neutral_when_no_type -q`
Forventet: FAIL (KeyError/None-indeksering i `_resolve_meeting_type`).

- [ ] **Step 3: Kald oprydning ved opstart**

I `meeting_app.py` `main()` (~linje 4062), efter `meeting_tool.seed_user_config()`:

```python
    meeting_tool.cleanup_seed_meeting_types(CONFIG_DIR)
```

- [ ] **Step 4: Guard `_type_key` mod tom dict ved konstruktion**

I `MeetingApp.__init__` (~linje 483-486), erstat

```python
        self._type_key = _state.get("meeting_type")
        if self._type_key not in self._meeting_types:
            self._type_key = next(iter(self._meeting_types))
        self._type_keys = list(self._meeting_types)
```
med
```python
        self._type_key = _state.get("meeting_type")
        if self._type_key not in self._meeting_types:
            self._type_key = next(iter(self._meeting_types), None)  # None = ingen typer
        self._type_keys = list(self._meeting_types)
```

Samme mønster i `TranscribeFileTab.__init__` (~linje 2500-2503): skift
`self._type_key = next(iter(self._meeting_types))` til
`self._type_key = next(iter(self._meeting_types), None)` og lad den efterfølgende
`if self._type_key not in ...`-guard (hvis nogen) bruge `next(..., None)`.

- [ ] **Step 5: Tåle None i `_key_for_label` / `_on_type_selected`**

I `MeetingApp._key_for_label` (~linje 1254), skift fallbacken

```python
        return self._type_keys[0]
```
til
```python
        return self._type_keys[0] if self._type_keys else None
```

I `MeetingApp._on_type_selected` (~linje 1260) — tilføj tidlig retur når der ingen typer er (label kan være tom):

```python
    def _on_type_selected(self, label: str):
        self.referat_level_override = None
        self._type_key = self._key_for_label(label)
        if self._type_key is None:
            self.name_var.set("")
            return
        mtype = self._meeting_types[self._type_key]
        ...  # resten uændret
```

- [ ] **Step 6: Neutral type ved optagelse uden valgt type**

I `MeetingApp._start_recording` (~linje 1528), erstat

```python
        meeting_type = override_detaljeniveau(
            self._meeting_types[self._type_key], self.referat_level_override
        )
```
med
```python
        base_type = (self._meeting_types.get(self._type_key)
                     if self._type_key else None) or meeting_tool.neutral_meeting_type()
        meeting_type = override_detaljeniveau(base_type, self.referat_level_override)
```

- [ ] **Step 7: Neutral type i fil-fanens `_resolve_meeting_type`**

I `TranscribeFileTab._resolve_meeting_type` (~linje 2894), erstat den afsluttende

```python
        mtype = self._meeting_types[self._type_key]
        return override_detaljeniveau(mtype, _NIVEAU_KEYS.get(self._level_seg.get()))
```
med
```python
        mtype = (self._meeting_types.get(self._type_key)
                 if self._type_key else None) or meeting_tool.neutral_meeting_type()
        return override_detaljeniveau(mtype, _NIVEAU_KEYS.get(self._level_seg.get()))
```

(Behold niveau-aflæsningen som første linje i metoden, hvis en ad-hoc type oprettes; kun den afsluttende opslag ændres.)

- [ ] **Step 8: Wizard tåler tom typeliste**

I `MeetingWizard._current_level` (~linje 3462), erstat

```python
        return self.app._meeting_types[self.app._type_key]["detaljeniveau"]
```
med
```python
        key = self.app._type_key
        if key and key in self.app._meeting_types:
            return self.app._meeting_types[key]["detaljeniveau"]
        return "balanceret"
```

I `MeetingWizard._build_type` (~linje 3260), sidste linje er
`self._highlight_type(None if self._adhoc_active else self.app._type_key)` — den
tåler allerede None. `for key in self._type_keys:`-løkken kører 0 gange ved tom
liste, så kun "+ Ny mødetype"-kortet vises. Ingen ændring nødvendig her ud over
at bekræfte i smoke-testen (Step 11).

- [ ] **Step 9: MeetingTypesTab tåler nul typer**

I `MeetingTypesTab.__init__` (~linje 2040), erstat

```python
        self.current_key = next(iter(self.types))
```
med
```python
        self.current_key = next(iter(self.types), None)  # None = ingen typer
```

I `_load_into_form` (~linje 2289), tilføj tidlig retur når `key` er None (tomt
formular):

```python
    def _load_into_form(self, key):
        if key is None:
            self.navn_var.set("")
            self.fokus_var.set("")
            self.deltagere_var.set("")
            self.ekstra_box.delete("1.0", "end")
            self._refresh_selector()
            return
        t = self.types[key]
        ...  # resten uændret
```

I `_refresh_selector` (~linje 2272), erstat delete-guarden

```python
        self.delete_btn.configure(
            state=("disabled" if len(self.types) <= 1 else "normal")
        )
```
med (tillad sletning ned til nul; kun deaktiveret når intet er valgt):
```python
        self.delete_btn.configure(
            state=("disabled" if self.current_key is None else "normal")
        )
```
Og gør selector-værdierne tomme-sikre: hvis `names` er tom, sæt
`self.selector.configure(values=[])` og `self.selector_var.set("")`.

I `_delete` (~linje 2351): efter sletning, sæt `self.current_key =
next(iter(self.types), None)` og kald `_load_into_form(self.current_key)`.

- [ ] **Step 10: Kør den rene test + suiten**

Kør: `.venv312/bin/python -m pytest tests/test_zero_meeting_types.py tests/ -q`
Forventet: grøn.

- [ ] **Step 11: Konstruktions-røgtest (Tk 9.x)**

Gem i `/tmp/smoke_zero_types.py` (IKKE commit) og kør med `.venv312/bin/python`:

```python
"""Røgtest: guide + fil-fane + Mødetyper-fane konstruerer med NUL mødetyper."""
import json, customtkinter as ctk

class _ShimScroll(ctk.CTkFrame):
    def __init__(self, *a, **kw):
        kw.pop("scrollbar_button_color", None); kw.pop("scrollbar_button_hover_color", None)
        super().__init__(*a, **kw)
ctk.CTkScrollableFrame = _ShimScroll

import meeting_app, meeting_tool
# Tving tom typeliste
meeting_tool.load_meeting_types = lambda *a, **k: {}

root = ctk.CTk(); root.withdraw()
app = meeting_app.MeetingApp(root)
assert app._type_key is None
app._open_wizard(1)                       # trin 2 med nul typer
app._wizard._adhoc_var.set("Testtype"); app._wizard._select_adhoc()
app._start_recording  # eksisterer
tab = meeting_app.TranscribeFileTab(ctk.CTkFrame(root))
assert tab._resolve_meeting_type()["navn"] == "Møde"
types_tab = meeting_app.MeetingTypesTab(ctk.CTkFrame(root))
assert types_tab.current_key is None
print("OK: nul-typer konstruerer i guide, fil-fane og Mødetyper-fane")
root.destroy()
```
Forventet: `OK: ...` og exit 0.

- [ ] **Step 12: Kør hele suiten**

Kør: `.venv312/bin/python -m pytest tests/ -q`
Forventet: grøn; kun kendte GUI-skips på Tk 9.x.

- [ ] **Step 13: Commit**

```bash
git add meeting_app.py tests/test_zero_meeting_types.py
git commit -m "feat: GUI tåler nul mødetyper + engangs-oprydning ved opstart"
```

---

### Task 6: Afsluttende verifikation + PR

- [ ] **Step 1: Fuld suite + diff-inspektion**

Kør: `.venv312/bin/python -m pytest tests/ -q` — grøn.
Tjek `git diff main --stat`: kun `docx_export.py`, `meeting_tool.py`, `meeting_app.py`, `meeting_types.default.json`, `requirements*.txt`, `modevaerktoj.spec`, `tests/*`, docs. Bekræft at `grep -rn "write_pdf_from_markdown\|_find_weasyprint\|DEFAULT_ATTENDEES = \[\"" meeting_tool.py meeting_app.py` ikke giver rester (PDF væk, ingen hardkodede navne).

- [ ] **Step 2: Push + PR**

```bash
git push -u origin feature/word-eksport-og-rene-defaults
gh pr create --title "Word-eksport + rene deltager/mødetype-defaults" --body "$(cat <<'EOF'
## Hvad
- **Word-eksport (.docx)** af referat + transkription med metadata-hoved (mødenavn, dato, mødetype, deltagere), formateret fra referatets markdown via python-docx (ikke pandoc — virker på Windows uden ekstra installation). PDF og `.md` fjernet (`.md` kun som nød-fallback ved docx-fejl).
- **Tomme deltager-defaults**: ingen hardkodede Mads/Lars/Dorte; tomt felt bliver tomt; det skrevne huskes.
- **Nul mødetyper tolereres**: ingen påtvungne typer; opret ad-hoc og vælg efter oprettelse; neutral in-memory default ("Møde") når intet er valgt; Mødetyper-fanen kan slette ned til nul.
- **Engangs-oprydning**: de to urørte danske seed-typer (Driftledelse/Ledergruppe) fjernes automatisk; redigerede/egne typer bevares.

## Test
- Nye: `tests/test_docx_export.py`, `tests/test_save_output.py`, `tests/test_empty_attendees.py`, `tests/test_zero_meeting_types.py`. Fjernet: `tests/test_pdf.py`.
- Fuld suite grøn. Konstruktions-røgtest af nul-typer i guide/fil-fane/Mødetyper-fane kørt via shim. python-docx bundlet i `modevaerktoj.spec` (collect_all).
- **Pending før release:** visuel verifikation på Windows Tk 8.6 (Word-output ser rigtigt ud; nul-type-flow).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
