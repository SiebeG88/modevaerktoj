"""OTA-opdatering for Windows-bundlet: tjek GitHub Releases, hent, verificér og
swap via en detached helper. Versions-helpere er rene og platformsneutrale."""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from _version import __version__

# OTA-funktionen peger på dette repos GitHub Releases.
# Forker du projektet, så skift til dit eget repo her.
GITHUB_RELEASES_API = (
    "https://api.github.com/repos/SiebeG88/modevaerktoj/releases/latest"
)


class UpdateError(Exception):
    """Opdatering kunne ikke hentes/verificeres sikkert."""


@dataclass
class Release:
    tag: str
    asset_url: str
    sha256_url: str


def parse_version(tag: str) -> tuple[int, ...]:
    """'v1.2.3' / '1.2.3' → (1, 2, 3). Ikke-numeriske dele droppes."""
    nums = re.findall(r"\d+", tag)
    return tuple(int(n) for n in nums) if nums else (0,)


def is_newer(remote_tag: str, local: str = __version__) -> bool:
    """True hvis remote_tag er en nyere version end local."""
    return parse_version(remote_tag) > parse_version(local)


def check_for_update(local: str = __version__, timeout: int = 10) -> Release | None:
    """Hent seneste release fra GitHub. Returnér Release hvis nyere end local
    OG både .zip- og .zip.sha256-asset findes — uden checksum afvises
    opdateringen af sikkerhedshensyn. Alle netfejl/uventede svar → None
    (aldrig kast)."""
    try:
        with urllib.request.urlopen(GITHUB_RELEASES_API, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    tag = data.get("tag_name", "")
    if not tag or not is_newer(tag, local):
        return None
    asset_url = sha256_url = ""
    for asset in (data.get("assets") or []):
        if not isinstance(asset, dict):
            continue
        name = asset.get("name", "")
        url = asset.get("browser_download_url", "")
        if name.endswith(".zip.sha256"):
            sha256_url = url
        elif name.endswith(".zip"):
            asset_url = url
    # Kræv BÅDE zip og checksum — en uverificeret binær må aldrig installeres.
    if not asset_url or not sha256_url:
        return None
    return Release(tag=tag, asset_url=asset_url, sha256_url=sha256_url)


def _fetch_bytes(url: str, timeout: int = 60) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


def _fetch_text(url: str, timeout: int = 30) -> str:
    """Hent en tekstfil og returnér første whitespace-separerede token (fx
    hash'et fra en 'sha256sum'-fil). Tom krop → tom streng (kaster ikke)."""
    parts = _fetch_bytes(url, timeout).decode("utf-8").strip().split()
    return parts[0] if parts else ""


def download_and_stage(release: Release, staging_dir) -> Path:
    """Hent release-zip til staging_dir, verificér sha256. Returnér staging_dir.
    Manglende checksum, mismatch eller fejl → UpdateError (ingen verificeret
    halv-fil efterlades)."""
    staging_dir = Path(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    if not release.sha256_url:
        # Defense-in-depth: check_for_update kræver allerede en checksum, men en
        # uverificeret binær må under ingen omstændigheder skrives til disk.
        raise UpdateError(
            "Ingen checksum — opdatering kasseret af sikkerhedshensyn."
        )
    try:
        data = _fetch_bytes(release.asset_url)
    except OSError as e:
        raise UpdateError(f"Download fejlede: {e}") from e
    try:
        expected = _fetch_text(release.sha256_url).lower()
    except (OSError, ValueError) as e:
        raise UpdateError(f"Checksum-hentning fejlede: {e}") from e
    if not expected or hashlib.sha256(data).hexdigest() != expected:
        raise UpdateError("Checksum-mismatch — opdatering kasseret.")
    (staging_dir / "app.zip").write_bytes(data)
    return staging_dir


def apply_and_restart(staging_dir) -> None:  # pragma: no cover
    """Pak staging-zip ud, skriv en detached helper-.cmd der venter på at denne
    proces lukker (fil-låsen frigives), erstatter app-mappen og genstarter exe'en.
    Kald derefter sys.exit(). Kun meningsfuld på frosset Windows."""
    import os
    import subprocess
    import sys
    import zipfile

    import app_paths

    staging_dir = Path(staging_dir)
    new_dir = staging_dir / "new"
    new_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(staging_dir / "app.zip") as zf:
        zf.extractall(new_dir)

    app_root = app_paths.app_dir()            # …\Mødeværktøj
    exe_path = Path(sys.executable)           # …\Mødeværktøj\Mødeværktøj.exe
    pid = os.getpid()

    cmd_path = staging_dir / "apply_update.cmd"
    cmd_path.write_text(
        "@echo off\r\n"
        ":wait\r\n"
        f'tasklist /FI "PID eq {pid}" | find "{pid}" >nul && (timeout /t 1 >nul & goto wait)\r\n'
        f'rmdir /s /q "{app_root}"\r\n'
        f'move "{new_dir}" "{app_root}"\r\n'
        f'start "" "{exe_path}"\r\n'
        'del "%~f0"\r\n',
        encoding="utf-8",
    )
    subprocess.Popen(
        ["cmd", "/c", str(cmd_path)],
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        close_fds=True,
    )
    sys.exit(0)
