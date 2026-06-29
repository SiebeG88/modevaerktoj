#!/opt/local/bin/python3.12
"""Genoptag transkription + referat for en eksisterende WAV-fil.

Brug:
    python resume_transcribe.py "/sti/til/optagelse.wav"

Læser deltagere og dato fra state.json hvis ikke angivet. Skriver
"Transkription <navn>.md" og "Referat <navn>.md" i samme mappe som WAV'en.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import meeting_tool

TOOL_DIR = Path(__file__).resolve().parent


def main():
    import argparse
    p = argparse.ArgumentParser(
        description="Transkriber + lav referat for en eksisterende WAV"
    )
    p.add_argument("wav", help="Sti til WAV-fil")
    p.add_argument("--date", help="Dato i format DD-MM-YYYY (default: udledes af filnavn)")
    p.add_argument("--output-dir", help="Mappe hvor transkription og referat gemmes (default: WAV'ens mappe)")
    args = p.parse_args()

    wav_path = Path(args.wav).expanduser()
    if not wav_path.exists():
        print(f"WAV findes ikke: {wav_path}", file=sys.stderr)
        sys.exit(1)

    state_file = TOOL_DIR / "state.json"
    state = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    attendees = state.get("attendees") or list(meeting_tool.DEFAULT_ATTENDEES)

    # Dato: parameter > navn-genkendelse > i dag
    if args.date:
        date = args.date
    else:
        import re
        m = re.search(r"(\d{2}-\d{2}-\d{4})", wav_path.stem)
        if m:
            date = m.group(1)
        else:
            from datetime import datetime
            date = datetime.now().strftime("%d-%m-%Y")

    name_base = wav_path.stem
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else wav_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"WAV: {wav_path}", flush=True)
    print(f"Mappe: {output_dir}", flush=True)
    print(f"Dato: {date}", flush=True)
    print(f"Deltagere: {', '.join(attendees)}", flush=True)
    print("", flush=True)

    print("=== Trin 1/2: Gemini-transkription ===", flush=True)
    transcript = meeting_tool.transcribe_with_gemini(
        wav_path,
        attendees=attendees,
        model="gemini-2.5-pro",
    )

    transcript_file = output_dir / f"Transkription {name_base}.md"
    transcript_file.write_text(
        f"# Transkription - {name_base}\n\nDato: {date}\n\n{transcript}\n",
        encoding="utf-8",
    )
    print(f"Transkription gemt: {transcript_file}", flush=True)

    print("", flush=True)
    print("=== Trin 2/2: Referat (Gemini) ===", flush=True)
    minutes = meeting_tool.generate_minutes(transcript, attendees, date)

    minutes_file = output_dir / f"Referat {name_base}.md"
    minutes_file.write_text(f"{minutes}\n", encoding="utf-8")
    print(f"Referat gemt: {minutes_file}", flush=True)

    print("", flush=True)
    print("=== FÆRDIG ===", flush=True)


if __name__ == "__main__":
    main()
