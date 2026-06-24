"""Stier til bruger-config og bundlede binærer — virker både som kildekode og
som PyInstaller-frosset bundle.

macOS (kildekode): config + binærer ligger ved siden af scriptet (uændret).
Windows (frosset): config i %APPDATA%\\Mødeværktøj\\ (overlever OTA), binærer i
exe-mappen.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_APP_NAME = "Mødeværktøj"


def is_frozen() -> bool:
    """True når koden kører som PyInstaller-bundle (sys.frozen sat)."""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """Mappen med den kørende app — bruges til bundlede filer (binærer, defaults).
    Frosset: mappen med exe'en. Kildekode: denne fils mappe."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def user_config_dir() -> Path:
    """Skrivbar mappe til bruger-state, der overlever OTA-opdateringer.
    Windows: %APPDATA%\\Mødeværktøj\\ (oprettes). macOS/andet: app_dir()."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home())
        d = Path(base) / _APP_NAME
        d.mkdir(parents=True, exist_ok=True)
        return d
    return app_dir()


def resolve_binary(name: str) -> str:
    """Sti til en hjælpe-binær ('ffmpeg'/'ffprobe').
    1) bundlet i app_dir() (med .exe-suffix på Windows) hvis den findes,
    2) ellers navnet selv (PATH-opslag som hidtil)."""
    suffix = ".exe" if sys.platform == "win32" else ""
    candidate = app_dir() / f"{name}{suffix}"
    if candidate.exists():
        return str(candidate)
    return name


def ensure_user_config(template_names: list[str]) -> None:
    """Kopiér read-only template-filer (fx '*.default.json') fra app_dir() til
    user_config_dir() hvis de mangler. No-op når de to mapper er ens (macOS)."""
    src_dir = app_dir()
    dst_dir = user_config_dir()
    if src_dir == dst_dir:
        return
    for name in template_names:
        src = src_dir / name
        dst = dst_dir / name
        if src.exists() and not dst.exists():
            try:
                shutil.copy2(src, dst)
            except OSError as e:
                # Tavs fejl ville få appen til at starte uden seed-config og
                # fejle forvirrende senere — log i det mindste hvad der gik galt.
                print(f"Kunne ikke kopiere {name} til {dst_dir}: {e}",
                      file=sys.stderr)
