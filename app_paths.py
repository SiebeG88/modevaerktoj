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

    Leder flere steder, fordi PyInstaller 6's one-dir-bundle lægger bundlede
    binærer i undermappen '_internal' (og udstiller den via sys._MEIPASS) —
    IKKE ved siden af exe'en som i ældre versioner:
      1) sys._MEIPASS (frosset one-dir data-mappe),
      2) app_dir() (ved siden af exe/script),
      3) app_dir()/_internal (one-dir layout),
      4) ellers navnet selv (PATH-opslag som hidtil)."""
    suffix = ".exe" if sys.platform == "win32" else ""
    fname = f"{name}{suffix}"
    search_dirs = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        search_dirs.append(Path(meipass))
    base = app_dir()
    search_dirs.append(base)
    search_dirs.append(base / "_internal")
    for d in search_dirs:
        candidate = d / fname
        if candidate.exists():
            return str(candidate)
    return name


def _resource_dirs() -> list[Path]:
    """Mapper hvor bundlede read-only ressourcer kan ligge — samme logik som
    resolve_binary: PyInstaller 6 lægger dem i '_internal' (sys._MEIPASS), ikke
    ved siden af exe'en."""
    dirs: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass))
    base = app_dir()
    dirs.append(base)
    dirs.append(base / "_internal")
    return dirs


def resolve_resource(name: str) -> Path | None:
    """Find en bundlet ressource-fil ('*.default.json' m.v.) i bundle-mapperne.
    Returnerer stien hvis fundet, ellers None."""
    for d in _resource_dirs():
        cand = d / name
        if cand.exists():
            return cand
    return None


def ensure_user_config(template_names: list[str]) -> None:
    """Kopiér read-only template-filer (fx '*.default.json') fra bundle-mappen
    til user_config_dir() hvis de mangler. No-op når kilde og mål er ens (macOS,
    hvor ressourcen ligger i selve user_config_dir())."""
    dst_dir = user_config_dir()
    for name in template_names:
        src = resolve_resource(name)
        if src is None:
            continue
        dst = dst_dir / name
        if src.resolve() == dst.resolve():
            continue  # macOS: kilde == mål, intet at kopiere
        if not dst.exists():
            try:
                shutil.copy2(src, dst)
            except OSError as e:
                # Tavs fejl ville få appen til at starte uden seed-config og
                # fejle forvirrende senere — log i det mindste hvad der gik galt.
                print(f"Kunne ikke kopiere {name} til {dst_dir}: {e}",
                      file=sys.stderr)
