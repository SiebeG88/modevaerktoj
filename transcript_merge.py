"""Fletter to spors transkript-segmenter til ét læsbart, mærket transkript.

Et segment er en (start_sekund, slut_sekund, tekst)-tuple. Mikrofon-sporet
mærkes "Mig:", systemlyd-sporet "Modpart:". Linjerne sorteres på start-tid
(mic vinder ved lige start, så rækkefølgen er deterministisk).
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _normalize(text: str) -> str:
    """Lowercase, fjern tegnsætning, kollaps mellemrum — til lighedssammenligning."""
    return " ".join(_PUNCT_RE.sub(" ", text.lower()).split())


Segment = tuple[float, float, str]

MIC_LABEL = "Mig"
SYS_LABEL = "Modpart"


def _format_timestamp(seconds: float) -> str:
    """Formatér sekunder til MM:SS (eller H:MM:SS ved ≥ 1 time)."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"


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


def shift_segments(segments: list[Segment], offset: float) -> list[Segment]:
    """Læg offset (sekunder) til alle start/slut-tider."""
    return [(start + offset, end + offset, text) for start, end, text in segments]


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


def merge_tracks(
    mic_segments: list[Segment],
    sys_segments: list[Segment],
) -> str:
    """Flet de to spors segmenter til ét tidsstemplet, mærket transkript."""

    # sort_key: (start, prioritet) — mic (0) før sys (1) ved samme start.
    tagged: list[tuple[float, int, Segment, str]] = []
    for seg in mic_segments:
        tagged.append((seg[0], 0, seg, MIC_LABEL))
    for seg in sys_segments:
        tagged.append((seg[0], 1, seg, SYS_LABEL))
    tagged.sort(key=lambda t: (t[0], t[1]))

    lines = []
    for _start, _prio, (start, end, text), label in tagged:
        ts = f"[{_format_timestamp(start)} - {_format_timestamp(end)}]"
        lines.append(f"{ts} {label}: {text}")
    return "\n".join(lines)


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
