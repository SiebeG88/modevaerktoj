# Robust to-spors-fletning — Implementeringsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Producér et korrekt, korrekt-tilskrevet transkript fra to-spors-optagelser uanset lydopsætning ved at synkronisere sporene, sanere tidsstempler og fjerne bleed deterministisk.

**Architecture:** Ny ren-Python-logik i `transcript_merge.py` (sanering, forskydning, bleed-dedup, orkestrator) kaldt fra de to live-optagevejene i `meeting_tool.py`. Optagelsen stempler hvert spors faktiske start (epoch) via en pump-tråd (sys) og en filstørrelses-poll-tråd (mic), gemmer dem i en `.sync.json`-sidecar, og Gemini-chunktranskriptionen klemmer tidsstempler pr. chunk før offset. `merge_tracks` ændres ikke.

**Tech Stack:** Python 3.12 (kør tests med `/opt/local/bin/python3.12 -m pytest`), `difflib` (stdlib), `pytest` + `pytest-mock`. Ingen nye afhængigheder.

---

## Filstruktur

- **`transcript_merge.py`** (modify) — tilføj `sanitize_segments`, `shift_segments`, `_normalize`, `remove_bleed`, `build_transcript`. `merge_tracks` urørt.
- **`meeting_tool.py`** (modify) — tilføj `dataclass DualTrackResult`, `_resolve_sync`, `_write_sync_sidecar`, `_clamp_chunk_timestamps`; omskriv `_record_dual_tracks` (start-stempling + sidecar + ny returtype); skift `merge_tracks`→`build_transcript` i begge kaldesteder; klem pr. chunk i `transcribe_with_gemini`.
- **`tests/test_transcript_merge.py`** (modify) — nye testklasser for de fire flette-funktioner.
- **`tests/test_dual_sync.py`** (create) — unit-tests for `_resolve_sync` + `_write_sync_sidecar`.
- **`tests/test_recording.py`** (modify) — test for `_clamp_chunk_timestamps`.

---

## Task 1: `sanitize_segments`

**Files:**
- Modify: `transcript_merge.py`
- Test: `tests/test_transcript_merge.py`

- [ ] **Step 1: Skriv den fejlende test**

Tilføj nederst i `tests/test_transcript_merge.py` (og udvid import-linjen øverst til `from transcript_merge import merge_tracks, sanitize_segments`):

```python
class TestSanitizeSegments:
    def test_clamps_to_duration(self):
        out = sanitize_segments([(3010.0, 4210.0, "langt fremme")], 2853.0)
        assert out == [(2853.0, 2853.0, "langt fremme")]

    def test_enforces_non_decreasing_start(self):
        out = sanitize_segments([(10.0, 12.0, "a"), (5.0, 7.0, "b")], 100.0)
        assert out[0][0] == 10.0
        assert out[1][0] == 10.0          # skubbet op til forrige start
        assert out[1][1] >= out[1][0]

    def test_repairs_overlong_span(self):
        out = sanitize_segments([(10.0, 400.0, "spaend")], 1000.0, max_segment_seconds=120.0)
        assert out == [(10.0, 130.0, "spaend")]

    def test_end_not_before_start(self):
        out = sanitize_segments([(50.0, 30.0, "omvendt")], 100.0)
        assert out[0][1] >= out[0][0]

    def test_empty(self):
        assert sanitize_segments([], 100.0) == []
```

- [ ] **Step 2: Kør testen og bekræft den fejler**

Run: `/opt/local/bin/python3.12 -m pytest tests/test_transcript_merge.py::TestSanitizeSegments -v`
Expected: FAIL med `ImportError: cannot import name 'sanitize_segments'`.

- [ ] **Step 3: Skriv minimal implementation**

Tilføj i `transcript_merge.py` efter `_format_timestamp` (før `merge_tracks`):

```python
def sanitize_segments(
    segments: list[Segment],
    max_duration: float,
    *,
    max_segment_seconds: float = 120.0,
) -> list[Segment]:
    """Klem og reparér tidsstempler, så de er fysisk mulige og monotont stigende.

    - start/end klemmes til [0, max_duration]
    - end >= start
    - starttider gøres ikke-faldende (for tidligt start skubbes op til forrige)
    - spænd > max_segment_seconds reduceres (end = start + max_segment_seconds)
    Teksten bevares uændret.
    """
    cleaned: list[Segment] = []
    prev_start = 0.0
    for start, end, text in segments:
        start = min(max(start, 0.0), max_duration)
        end = min(max(end, 0.0), max_duration)
        if end < start:
            end = start
        if start < prev_start:
            start = prev_start
            if end < start:
                end = start
        if end - start > max_segment_seconds:
            end = min(start + max_segment_seconds, max_duration)
        cleaned.append((start, end, text))
        prev_start = start
    return cleaned
```

- [ ] **Step 4: Kør testen og bekræft den passerer**

Run: `/opt/local/bin/python3.12 -m pytest tests/test_transcript_merge.py::TestSanitizeSegments -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add transcript_merge.py tests/test_transcript_merge.py
git commit -m "feat: sanitize_segments — klem og reparér tidsstempler"
```

---

## Task 2: `shift_segments`

**Files:**
- Modify: `transcript_merge.py`
- Test: `tests/test_transcript_merge.py`

- [ ] **Step 1: Skriv den fejlende test**

Udvid import-linjen til `from transcript_merge import merge_tracks, sanitize_segments, shift_segments` og tilføj:

```python
class TestShiftSegments:
    def test_positive_offset(self):
        assert shift_segments([(1.0, 2.0, "x")], 5.0) == [(6.0, 7.0, "x")]

    def test_zero_offset(self):
        assert shift_segments([(1.0, 2.0, "x")], 0.0) == [(1.0, 2.0, "x")]

    def test_empty(self):
        assert shift_segments([], 5.0) == []
```

- [ ] **Step 2: Kør testen og bekræft den fejler**

Run: `/opt/local/bin/python3.12 -m pytest tests/test_transcript_merge.py::TestShiftSegments -v`
Expected: FAIL med `ImportError: cannot import name 'shift_segments'`.

- [ ] **Step 3: Skriv minimal implementation**

Tilføj i `transcript_merge.py` efter `sanitize_segments`:

```python
def shift_segments(segments: list[Segment], offset: float) -> list[Segment]:
    """Læg offset (sekunder) til alle start/slut-tider."""
    return [(start + offset, end + offset, text) for start, end, text in segments]
```

- [ ] **Step 4: Kør testen og bekræft den passerer**

Run: `/opt/local/bin/python3.12 -m pytest tests/test_transcript_merge.py::TestShiftSegments -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add transcript_merge.py tests/test_transcript_merge.py
git commit -m "feat: shift_segments — forskyd segment-tider med offset"
```

---

## Task 3: `remove_bleed` (+ `_normalize`)

**Files:**
- Modify: `transcript_merge.py`
- Test: `tests/test_transcript_merge.py`

- [ ] **Step 1: Skriv den fejlende test**

Udvid import-linjen til `from transcript_merge import (merge_tracks, sanitize_segments, shift_segments, remove_bleed)` og tilføj:

```python
class TestRemoveBleed:
    def test_drops_long_overlapping_copy(self):
        phrase = "det her er en lang saetning der er helt ens i begge spor"
        assert remove_bleed([(10.0, 16.0, phrase)], [(11.0, 17.0, phrase)]) == []

    def test_keeps_short_segment(self):
        assert remove_bleed([(10.0, 11.0, "Ja.")], [(10.0, 11.0, "Ja.")]) == [(10.0, 11.0, "Ja.")]

    def test_keeps_genuine_double_talk(self):
        mic = [(10.0, 16.0, "jeg synes vi skal starte med oekonomien nu")]
        sys = [(10.0, 16.0, "kan I overhovedet hoere mig derinde paa kontoret")]
        assert remove_bleed(mic, sys) == mic

    def test_keeps_when_no_time_overlap(self):
        phrase = "det her er en lang saetning der er helt ens i begge spor"
        assert remove_bleed([(10.0, 16.0, phrase)], [(100.0, 106.0, phrase)]) == [(10.0, 16.0, phrase)]

    def test_empty_sys_keeps_all(self):
        mic = [(10.0, 16.0, "uanset hvad skal det her blive staaende her")]
        assert remove_bleed(mic, []) == mic
```

- [ ] **Step 2: Kør testen og bekræft den fejler**

Run: `/opt/local/bin/python3.12 -m pytest tests/test_transcript_merge.py::TestRemoveBleed -v`
Expected: FAIL med `ImportError: cannot import name 'remove_bleed'`.

- [ ] **Step 3: Skriv minimal implementation**

Tilføj øverst i `transcript_merge.py` (efter modul-docstring, ved de andre imports):

```python
import re
from difflib import SequenceMatcher

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _normalize(text: str) -> str:
    """Lowercase, fjern tegnsætning, kollaps mellemrum — til lighedssammenligning."""
    return " ".join(_PUNCT_RE.sub(" ", text.lower()).split())
```

Tilføj efter `shift_segments`:

```python
def remove_bleed(
    mic_segments: list[Segment],
    sys_segments: list[Segment],
    *,
    overlap_tolerance: float = 4.0,
    similarity_threshold: float = 0.80,
    min_chars: int = 25,
) -> list[Segment]:
    """Fjern KONSERVATIVT 'Mig'-segmenter, der er bleed-kopier af et tids-
    overlappende 'Modpart'-segment.

    Et mic-segment droppes kun hvis len(normaliseret tekst) >= min_chars OG der
    findes et sys-segment, hvis tidsvindue (udvidet med ±overlap_tolerance)
    overlapper, med tekst-lighed >= similarity_threshold. Korte segmenter ('ja',
    'mm') og ægte samtidig-tale (forskellig tekst → lav lighed) bevares.
    Forudsætter at begge spor allerede er justeret til fælles t=0.
    """
    kept: list[Segment] = []
    for m_start, m_end, m_text in mic_segments:
        m_norm = _normalize(m_text)
        if len(m_norm) < min_chars:
            kept.append((m_start, m_end, m_text))
            continue
        is_bleed = False
        for s_start, s_end, s_text in sys_segments:
            if s_end + overlap_tolerance < m_start or s_start - overlap_tolerance > m_end:
                continue
            s_norm = _normalize(s_text)
            if not s_norm:
                continue
            if SequenceMatcher(None, m_norm, s_norm).ratio() >= similarity_threshold:
                is_bleed = True
                break
        if not is_bleed:
            kept.append((m_start, m_end, m_text))
    return kept
```

- [ ] **Step 4: Kør testen og bekræft den passerer**

Run: `/opt/local/bin/python3.12 -m pytest tests/test_transcript_merge.py::TestRemoveBleed -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add transcript_merge.py tests/test_transcript_merge.py
git commit -m "feat: remove_bleed — konservativ bleed-dedup via tekst-lighed"
```

---

## Task 4: `build_transcript` (orkestrator)

**Files:**
- Modify: `transcript_merge.py`
- Test: `tests/test_transcript_merge.py`

- [ ] **Step 1: Skriv den fejlende test**

Udvid import-linjen til at inkludere `build_transcript` og tilføj:

```python
class TestBuildTranscript:
    def test_aligns_and_dedups_bleed(self):
        # sys (Modpart) er ren reference. mic startede 225 s senere end sys,
        # og samme ytring optræder i mic (bleed) ved 50 s og i sys ved 275 s.
        phrase = "vi er landbrug og digitalisering med tres medarbejdere i aarhus"
        mic = [(50.0, 60.0, phrase)]
        sys = [(275.0, 285.0, phrase)]
        out = build_transcript(
            mic, sys, mic_dur=300.0, sys_dur=600.0,
            mic_start=1225.0, sys_start=1000.0,
        )
        lines = out.splitlines()
        assert len(lines) == 1
        assert lines[0] == "[04:35 - 04:45] Modpart: " + phrase

    def test_mic_only(self):
        out = build_transcript(
            [(0.0, 5.0, "kun mig her")], [],
            mic_dur=10.0, sys_dur=0.0, mic_start=1000.0, sys_start=1000.0,
        )
        assert out == "[00:00 - 00:05] Mig: kun mig her"
```

- [ ] **Step 2: Kør testen og bekræft den fejler**

Run: `/opt/local/bin/python3.12 -m pytest tests/test_transcript_merge.py::TestBuildTranscript -v`
Expected: FAIL med `ImportError: cannot import name 'build_transcript'`.

- [ ] **Step 3: Skriv minimal implementation**

Tilføj i `transcript_merge.py` efter `merge_tracks`:

```python
def build_transcript(
    mic_segs: list[Segment],
    sys_segs: list[Segment],
    *,
    mic_dur: float,
    sys_dur: float,
    mic_start: float,
    sys_start: float,
) -> str:
    """Saner → forskyd til fælles t=0 → fjern bleed → flet til mærket transkript."""
    zero = min(mic_start, sys_start)
    mic = shift_segments(sanitize_segments(mic_segs, mic_dur), mic_start - zero)
    sys = shift_segments(sanitize_segments(sys_segs, sys_dur), sys_start - zero)
    mic = remove_bleed(mic, sys)
    return merge_tracks(mic, sys)
```

- [ ] **Step 4: Kør testen og bekræft den passerer**

Run: `/opt/local/bin/python3.12 -m pytest tests/test_transcript_merge.py -v`
Expected: PASS (alle klasser, inkl. de eksisterende `TestMergeTracks`).

- [ ] **Step 5: Commit**

```bash
git add transcript_merge.py tests/test_transcript_merge.py
git commit -m "feat: build_transcript — orkestrér sanering, justering, dedup, fletning"
```

---

## Task 5: Per-chunk tidsstempel-klem i Gemini-transkription

**Files:**
- Modify: `meeting_tool.py` (ny `_clamp_chunk_timestamps` nær `_offset_transcript` ~linje 1396; kald i `run_one` ~linje 1725)
- Test: `tests/test_recording.py`

- [ ] **Step 1: Skriv den fejlende test**

Tilføj i `tests/test_recording.py`:

```python
class TestClampChunkTimestamps:
    def test_clamps_hallucinated_timestamps(self):
        text = "[00:30 - 00:31] Hej.\n[50:10 - 1:10:10] Lang saetning."
        out = mt._clamp_chunk_timestamps(text, 720)   # 12-min chunk
        lines = out.splitlines()
        assert lines[0] == "[00:30 - 00:31] Hej."
        assert lines[1] == "[12:00 - 12:00] Lang saetning."

    def test_leaves_lines_without_timestamp(self):
        assert mt._clamp_chunk_timestamps("ingen tidsstempel her", 720) == "ingen tidsstempel her"
```

- [ ] **Step 2: Kør testen og bekræft den fejler**

Run: `/opt/local/bin/python3.12 -m pytest tests/test_recording.py::TestClampChunkTimestamps -v`
Expected: FAIL med `AttributeError: module 'meeting_tool' has no attribute '_clamp_chunk_timestamps'`.

- [ ] **Step 3: Skriv minimal implementation**

Tilføj i `meeting_tool.py` umiddelbart efter `_offset_transcript` (ca. linje 1415):

```python
def _clamp_chunk_timestamps(text: str, chunk_seconds: float) -> str:
    """Klem hvert segments [start - end]-præfiks til [0, chunk_seconds].

    Modvirker Gemini-hallucinerede tidsstempler, FØR chunk-offset lægges på, så
    urealistiske tal (fx 12 timer på et 12-min chunk) ikke forplanter sig.
    """
    global _TIMESTAMP_LINE_RE
    if _TIMESTAMP_LINE_RE is None:
        _TIMESTAMP_LINE_RE = re.compile(
            r"^\[\s*((?:\d+:)?\d+:\d+)\s*-\s*((?:\d+:)?\d+:\d+)\s*\]"
        )
    out_lines = []
    for line in text.splitlines():
        m = _TIMESTAMP_LINE_RE.match(line)
        if m:
            t1 = max(0.0, min(float(_parse_timestamp(m.group(1))), chunk_seconds))
            t2 = max(0.0, min(float(_parse_timestamp(m.group(2))), chunk_seconds))
            if t2 < t1:
                t2 = t1
            rest = line[m.end():]
            out_lines.append(f"[{format_timestamp(int(t1))} - {format_timestamp(int(t2))}]{rest}")
        else:
            out_lines.append(line)
    return "\n".join(out_lines)
```

Find `run_one` i `transcribe_with_gemini` (ca. linje 1718-1726). Indsæt klemningen mellem transkription og offset:

```python
        def run_one(idx_path):
            idx, path = idx_path
            label = f"{idx+1:02d}/{len(chunk_paths)}"
            text, diag = _gemini_transcribe_single(
                client, path, system_instruction, model, label, _status,
                stop_event=stop_event,
            )
            text = _clamp_chunk_timestamps(text, chunk_seconds)
            offset = idx * chunk_seconds
            return idx, _offset_transcript(text, offset), diag
```

- [ ] **Step 4: Kør testen og bekræft den passerer**

Run: `/opt/local/bin/python3.12 -m pytest tests/test_recording.py::TestClampChunkTimestamps -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add meeting_tool.py tests/test_recording.py
git commit -m "feat: klem Gemini-chunk-tidsstempler foer offset (mod hallucination)"
```

---

## Task 6: Sync-resolution + sidecar (rene helpers)

**Files:**
- Modify: `meeting_tool.py` (tilføj `from dataclasses import dataclass` i import-blokken; `DualTrackResult`, `_resolve_sync`, `_write_sync_sidecar` umiddelbart før `_record_dual_tracks` ~linje 465)
- Test: `tests/test_dual_sync.py` (create)

- [ ] **Step 1: Skriv den fejlende test**

Opret `tests/test_dual_sync.py`:

```python
"""Tests for to-spors start-stempel-resolution og sidecar."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meeting_tool as mt


def test_resolve_sync_uses_stamps(tmp_path):
    mic = tmp_path / "m.mic.wav"; mic.write_bytes(b"x" * 2000)
    sys_ = tmp_path / "m.sys.wav"; sys_.write_bytes(b"x" * 2000)
    res = mt._resolve_sync({"mic": 1000.0, "sys": 996.0}, mic, sys_, True,
                           now=2000.0, duration_fn=lambda p: 42.0)
    assert res.sys_ok is True
    assert res.mic_start == 1000.0
    assert res.sys_start == 996.0
    assert res.mic_duration == 42.0
    assert res.sys_duration == 42.0


def test_resolve_sync_fallbacks_when_sys_missing(tmp_path):
    mic = tmp_path / "m.mic.wav"; mic.write_bytes(b"x" * 2000)
    sys_ = tmp_path / "m.sys.wav"  # findes ikke
    res = mt._resolve_sync({"mic": None, "sys": None}, mic, sys_, False,
                           now=2000.0, duration_fn=lambda p: 42.0)
    assert res.sys_ok is False
    assert res.mic_start == 2000.0       # fallback til now
    assert res.sys_start == 2000.0       # = mic_start
    assert res.sys_duration == 0.0


def test_write_sync_sidecar_roundtrip(tmp_path):
    res = mt.DualTrackResult(True, 1000.0, 996.0, 42.0, 50.0)
    p = tmp_path / "m.sync.json"
    mt._write_sync_sidecar(p, res)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["mic_start"] == 1000.0
    assert data["sys_start"] == 996.0
    assert data["sys_duration"] == 50.0
```

- [ ] **Step 2: Kør testen og bekræft den fejler**

Run: `/opt/local/bin/python3.12 -m pytest tests/test_dual_sync.py -v`
Expected: FAIL med `AttributeError: module 'meeting_tool' has no attribute '_resolve_sync'`.

- [ ] **Step 3: Skriv minimal implementation**

Tilføj `from dataclasses import dataclass` til import-blokken øverst i `meeting_tool.py` (nær `import threading`). Tilføj umiddelbart før `def _record_dual_tracks(`:

```python
@dataclass
class DualTrackResult:
    """Resultat af to-spors-optagelse: status, faktiske starttider (epoch) og varigheder."""
    sys_ok: bool
    mic_start: float
    sys_start: float
    mic_duration: float
    sys_duration: float


def _resolve_sync(stamps, mic_path: Path, sys_path: Path, sys_ok: bool,
                  *, now: float, duration_fn) -> "DualTrackResult":
    """Udled DualTrackResult fra start-stempler med sikre fallbacks.

    `stamps` er {"mic": float|None, "sys": float|None}. Manglende mic-stempel →
    `now`; manglende sys-stempel → mic_start. `duration_fn(path)->float` er
    injicerbar (i produktion `_ffprobe_duration`)."""
    mic_start = stamps.get("mic") if stamps.get("mic") is not None else now
    sys_start = stamps.get("sys") if stamps.get("sys") is not None else mic_start
    mic_duration = duration_fn(mic_path) if mic_path.exists() else 0.0
    sys_final = bool(sys_ok and sys_path.exists() and sys_path.stat().st_size > 0)
    sys_duration = duration_fn(sys_path) if sys_final else 0.0
    return DualTrackResult(sys_final, mic_start, sys_start, mic_duration, sys_duration)


def _write_sync_sidecar(path: Path, result: "DualTrackResult") -> None:
    """Skriv sync-metadata som JSON ved siden af WAV-filerne."""
    import json
    path.write_text(json.dumps({
        "version": 1,
        "mic_start": result.mic_start,
        "sys_start": result.sys_start,
        "mic_duration": result.mic_duration,
        "sys_duration": result.sys_duration,
    }), encoding="utf-8")
```

- [ ] **Step 4: Kør testen og bekræft den passerer**

Run: `/opt/local/bin/python3.12 -m pytest tests/test_dual_sync.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add meeting_tool.py tests/test_dual_sync.py
git commit -m "feat: DualTrackResult + _resolve_sync + sidecar-writer"
```

---

## Task 7: Start-stempling i `_record_dual_tracks`

**Files:**
- Modify: `meeting_tool.py` (`_record_dual_tracks`, ~linje 465-587)

Denne task ændrer subprocess-I/O og verificeres ved at hele suiten forbliver grøn (ingen test rammer funktionen direkte) + manuel røgtest i Task 9. Returtypen ændres fra `bool` til `DualTrackResult`; kaldestederne opdateres i Task 8.

- [ ] **Step 1: Erstat hele funktionskroppen**

Erstat `_record_dual_tracks` (fra `del sys_device` til `return ...`) med:

```python
    del sys_device  # bagudkompatibilitet — AudioTee tap'per systemlyd direkte

    def _status(msg):
        (on_status or print)(msg)

    stamps: dict[str, float | None] = {"mic": None, "sys": None}
    capture_stop = threading.Event()

    # --- Mikrofon-spor: ffmpeg/avfoundation ---
    mic_cmd = [
        FFMPEG, "-f", "avfoundation", "-i", f":{mic_device}",
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        str(mic_path), "-y", "-loglevel", "warning",
    ]
    mic_proc = subprocess.Popen(mic_cmd, stdin=subprocess.PIPE)

    # Poll-tråd: stempl mic-start når filen vokser forbi WAV-headeren (>1 KB).
    def _poll_mic_start():
        while not capture_stop.is_set():
            try:
                if mic_path.stat().st_size > 1024:
                    stamps["mic"] = time.time()
                    return
            except OSError:
                pass
            capture_stop.wait(0.05)
    mic_poll_thread = threading.Thread(target=_poll_mic_start, daemon=True)
    mic_poll_thread.start()

    # --- Systemlyd-spor: AudioTee → (pump-tråd) → ffmpeg pipe → WAV ---
    sys_ok = True
    audiotee_proc: subprocess.Popen | None = None
    sys_ffmpeg_proc: subprocess.Popen | None = None
    sys_pump_thread: threading.Thread | None = None
    if not AUDIOTEE_BIN.exists():
        _status(
            f"AudioTee-binær mangler ({AUDIOTEE_BIN.name}) — "
            f"optager kun mikrofon. Byg med: cd /tmp/audiotee && swift build -c release"
        )
        sys_ok = False
    else:
        try:
            audiotee_proc = subprocess.Popen(
                [str(AUDIOTEE_BIN), "--sample-rate", "16000"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            sys_ffmpeg_proc = subprocess.Popen(
                [
                    FFMPEG,
                    "-f", "s16le", "-ar", "16000", "-ac", "1",
                    "-i", "pipe:0",
                    "-c:a", "copy",
                    str(sys_path), "-y", "-loglevel", "warning",
                ],
                stdin=subprocess.PIPE,
            )

            def _pump_sys():
                first = True
                try:
                    while True:
                        data = audiotee_proc.stdout.read(4096)
                        if not data:
                            break
                        if first:
                            stamps["sys"] = time.time()
                            first = False
                        try:
                            sys_ffmpeg_proc.stdin.write(data)
                        except (BrokenPipeError, ValueError):
                            break
                finally:
                    try:
                        sys_ffmpeg_proc.stdin.close()
                    except Exception:
                        pass
            sys_pump_thread = threading.Thread(target=_pump_sys, daemon=True)
            sys_pump_thread.start()
        except Exception as e:  # pragma: no cover
            _status(f"Kunne ikke starte systemlyd-optagelse: {e} — fortsætter mic-kun.")
            sys_ok = False

    def stop_all():
        try:
            if mic_proc.stdin and not mic_proc.stdin.closed:
                mic_proc.stdin.write(b"q")
                mic_proc.stdin.flush()
                mic_proc.stdin.close()
        except Exception:  # pragma: no cover
            mic_proc.terminate()
        if audiotee_proc is not None and audiotee_proc.poll() is None:
            try:
                audiotee_proc.send_signal(signal.SIGINT)
            except Exception:  # pragma: no cover
                try:
                    audiotee_proc.terminate()
                except Exception:
                    pass

    def _drain(p, label):
        try:
            p.wait(timeout=12)
        except subprocess.TimeoutExpired:  # pragma: no cover
            _status(f"{label} svarer ikke — terminerer ...")
            try:
                p.terminate()
                p.wait(timeout=3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    if stop_event is not None:
        stop_event.wait()
        stop_all()
        _drain(mic_proc, "ffmpeg-mic")
        if audiotee_proc is not None:
            _drain(audiotee_proc, "AudioTee")
        if sys_pump_thread is not None:
            sys_pump_thread.join(timeout=5)
        if sys_ffmpeg_proc is not None:
            _drain(sys_ffmpeg_proc, "ffmpeg-sys")
    else:  # pragma: no cover
        original = signal.signal(signal.SIGINT, lambda *_: stop_all())
        try:
            _drain(mic_proc, "ffmpeg-mic")
            if audiotee_proc is not None:
                _drain(audiotee_proc, "AudioTee")
            if sys_pump_thread is not None:
                sys_pump_thread.join(timeout=5)
            if sys_ffmpeg_proc is not None:
                _drain(sys_ffmpeg_proc, "ffmpeg-sys")
        finally:
            signal.signal(signal.SIGINT, original)

    capture_stop.set()
    mic_poll_thread.join(timeout=1)

    result = _resolve_sync(
        stamps, mic_path, sys_path, sys_ok,
        now=time.time(), duration_fn=_ffprobe_duration,
    )
    name = mic_path.name
    suffix = ".mic.wav"
    base = name[:-len(suffix)] if name.endswith(suffix) else mic_path.stem
    sidecar_path = mic_path.with_name(base + ".sync.json")
    try:
        _write_sync_sidecar(sidecar_path, result)
    except Exception as e:  # pragma: no cover
        _status(f"Kunne ikke skrive {sidecar_path.name}: {e}")
    return result
```

- [ ] **Step 2: Opdatér returtype-annotationen**

Skift funktionens signatur-returtype fra `-> bool:` til `-> "DualTrackResult":` og opdatér docstringen, så den nævner at funktionen returnerer `DualTrackResult` og skriver `<navn>.sync.json`.

- [ ] **Step 3: Kør hele suiten (forvent grøn — Task 8 retter kaldesteder hvis nødvendigt)**

Run: `/opt/local/bin/python3.12 -m pytest -q`
Expected: PASS. (Ingen test kalder `_record_dual_tracks` direkte; kaldestederne kompilerer stadig — `sys_ok = _record_dual_tracks(...)` binder nu et `DualTrackResult`-objekt, hvilket er sandt/falsk-agtigt nok til at suiten ikke fejler, men rettes korrekt i Task 8.)

- [ ] **Step 4: Commit**

```bash
git add meeting_tool.py
git commit -m "feat: stempl mic/sys-start i _record_dual_tracks + skriv sync.json"
```

---

## Task 8: Kobl `build_transcript` ind i begge optagevejene

**Files:**
- Modify: `meeting_tool.py` (`record_and_transcribe_live` ~linje 667/684/708/716; `record_then_transcribe_gemini` ~linje 1054/1059/1074/1083)
- Test: `tests/test_recording.py`

- [ ] **Step 1: Skriv/udvid testen**

Tilføj i `tests/test_recording.py` en test, der sikrer at Gemini-dual-track-vejen kalder `build_transcript` med start/varighed fra `_record_dual_tracks`:

```python
class TestGeminiDualTrackUsesBuildTranscript:
    def test_calls_build_transcript_with_sync(self, tmp_path, fake_gemini_client, mocker):
        mocker.patch("meeting_tool._record_dual_tracks",
                     return_value=mt.DualTrackResult(
                         sys_ok=True, mic_start=1225.0, sys_start=1000.0,
                         mic_duration=300.0, sys_duration=600.0))
        # mic.wav skal "findes" så funktionens existens-check passerer
        def _touch(*a, **k):
            (tmp_path / "Driftledelsesmøde 25-06-2026.mic.wav").write_bytes(b"RIFF" * 500)
            return "[00:00 - 00:05] hej"
        mocker.patch("meeting_tool.transcribe_with_gemini", side_effect=_touch)
        bt = mocker.patch("meeting_tool.build_transcript", return_value="FLETTET")

        wav, transcript = mt.record_then_transcribe_gemini(
            output_dir=tmp_path, date="25-06-2026", device_id=0,
            system_device=0, attendees=["A"], stop_event=None,
        )
        assert transcript == "FLETTET"
        _, kwargs = bt.call_args
        assert kwargs["mic_start"] == 1225.0
        assert kwargs["sys_start"] == 1000.0
        assert kwargs["mic_dur"] == 300.0
        assert kwargs["sys_dur"] == 600.0
```

- [ ] **Step 2: Kør testen og bekræft den fejler**

Run: `/opt/local/bin/python3.12 -m pytest tests/test_recording.py::TestGeminiDualTrackUsesBuildTranscript -v`
Expected: FAIL (`build_transcript` importeres ikke / kaldes ikke endnu — fx `AttributeError`/assert-fejl).

- [ ] **Step 3: Opdatér `record_then_transcribe_gemini`**

Linje ~1054: skift importen til `from transcript_merge import build_transcript`.
Linje ~1059: skift
```python
            sys_ok = _record_dual_tracks(
                mic_path, sys_path, device_id, system_device, stop_event, _status,
            )
```
til
```python
            dual = _record_dual_tracks(
                mic_path, sys_path, device_id, system_device, stop_event, _status,
            )
            sys_ok = dual.sys_ok
```
Linje ~1083: skift `return mic_path, merge_tracks(mic_segs, sys_segs)` til
```python
        return mic_path, build_transcript(
            mic_segs, sys_segs,
            mic_dur=dual.mic_duration, sys_dur=dual.sys_duration,
            mic_start=dual.mic_start, sys_start=dual.sys_start,
        )
```

- [ ] **Step 4: Opdatér `record_and_transcribe_live`**

Linje ~667: skift importen til `from transcript_merge import build_transcript`.
Linje ~684: skift
```python
            sys_ok = _record_dual_tracks(
                mic_path, sys_path, device_id, system_device, stop_event, _status,
            )
```
til
```python
            dual = _record_dual_tracks(
                mic_path, sys_path, device_id, system_device, stop_event, _status,
            )
```
Linje ~708: skift `if sys_ok:` til `if dual.sys_ok:`.
Linje ~716: skift `return mic_path, merge_tracks(mic_segs, sys_segs)` til
```python
        return mic_path, build_transcript(
            mic_segs, sys_segs,
            mic_dur=dual.mic_duration, sys_dur=dual.sys_duration,
            mic_start=dual.mic_start, sys_start=dual.sys_start,
        )
```

- [ ] **Step 5: Kør testen + hele suiten**

Run: `/opt/local/bin/python3.12 -m pytest tests/test_recording.py::TestGeminiDualTrackUsesBuildTranscript -v`
Expected: PASS.
Run: `/opt/local/bin/python3.12 -m pytest -q`
Expected: PASS (hele suiten).

- [ ] **Step 6: Commit**

```bash
git add meeting_tool.py tests/test_recording.py
git commit -m "feat: brug build_transcript i begge to-spors-optagevejene"
```

---

## Task 9: Fuld verifikation

**Files:** ingen (verifikation)

- [ ] **Step 1: Kør hele test-suiten**

Run: `/opt/local/bin/python3.12 -m pytest -q`
Expected: alle grønne.

- [ ] **Step 2: Manuel røgtest (kort to-spors-optagelse)**

Start appen, lav en kort optagelse på højtaler (så der bevidst er bleed), tal samtidig med en afspillet video. Bekræft:
- `<navn>.sync.json` skrives ved siden af `.mic.wav`/`.sys.wav` med plausible `mic_start`/`sys_start`.
- Det flettede transkript har ingen tidsstempler > optagelsens varighed.
- Modpartens replikker optræder ikke dubleret som "Mig".

- [ ] **Step 3: Regression mod den oprindelige optagelse (valgfrit, hvis WAV-filerne haves)**

Hvis `telefonopkald 25-06-2026.mic.wav`/`.sys.wav` stadig findes: kør en lille ad-hoc-transkription gennem `build_transcript` (med `mic_start`/`sys_start` sat så sys ligger ~225 s før mic) og bekræft at Mikkels intro nu ligger samme sted i begge spor og ikke er dubleret.

- [ ] **Step 4: Afsluttende commit (hvis der er ikke-committede ændringer)**

```bash
git status
```

---

## Self-review-noter

- **Spec-dækning:** sync (Task 6+7+8), tidsstempel-sanering (Task 1 + Task 5), bleed-dedup (Task 3), orkestrator (Task 4), fejlhåndtering/fallbacks (Task 6 `_resolve_sync` + Task 7 graceful degradation), `merge_tracks` uændret (Task 4 kører eksisterende `TestMergeTracks`). Alle spec-punkter har en task.
- **Navne-konsistens:** `DualTrackResult.{sys_ok,mic_start,sys_start,mic_duration,sys_duration}` bruges identisk i Task 6, 7 og 8. `build_transcript(..., mic_dur, sys_dur, mic_start, sys_start)` matcher kaldene i Task 8.
- **Ingen pladsholdere:** alle kodeblokke er fuldstændige.
