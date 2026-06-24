"""Fletter to spors transkript-segmenter til ét læsbart, mærket transkript.

Et segment er en (start_sekund, slut_sekund, tekst)-tuple. Mikrofon-sporet
mærkes "Mig:", systemlyd-sporet "Modpart:". Linjerne sorteres på start-tid
(mic vinder ved lige start, så rækkefølgen er deterministisk).
"""
from __future__ import annotations

Segment = tuple[float, float, str]

MIC_LABEL = "Mig"
SYS_LABEL = "Modpart"


def _format_timestamp(seconds: float) -> str:
    """Formatér sekunder til MM:SS (eller H:MM:SS ved ≥ 1 time)."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"


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
