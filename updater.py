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


def _ps_quote(value) -> str:
    """Escapér en sti/streng til et PowerShell single-quoted literal."""
    return "'" + str(value).replace("'", "''") + "'"


def _dir_writable(path) -> bool:
    """True hvis processen kan skrive i mappen.

    Afgør om OTA-swappet kræver administrator-elevation: under
    C:\\Program Files kan en almindelig proces ikke skrive, mens den gamle
    pr.-bruger-sti (%LOCALAPPDATA%\\Programs) altid er skrivbar."""
    probe = Path(path) / ".ota-write-test"
    try:
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def _build_helper_script(app_root, new_dir, exe_path, log_path, pid,
                         elevated: bool) -> str:
    """Byg PowerShell-swap-helperen som tekst.

    elevated=True → helperen kører som administrator (Program Files), og appen
    genstartes via explorer.exe så den IKKE arver administrator-rettigheder.
    """
    if elevated:
        start_app = ("function StartApp { Start-Process -FilePath "
                     "'explorer.exe' -ArgumentList ('\"' + $exe + '\"') }\n")
    else:
        start_app = "function StartApp { Start-Process -FilePath $exe }\n"
    # $procId — IKKE $pid: $PID er en skrivebeskyttet auto-variabel i PowerShell.
    return (
        "$ErrorActionPreference='SilentlyContinue'\n"
        f"$log={_ps_quote(log_path)}; $appRoot={_ps_quote(app_root)}; "
        f"$newDir={_ps_quote(new_dir)}; $exe={_ps_quote(exe_path)}; $procId={pid}\n"
        "$parent=Split-Path -Parent $appRoot; $leaf=Split-Path -Leaf $appRoot\n"
        "$bak=Join-Path $parent ($leaf + '.ota-bak')\n"
        "function L($m){ \"$(Get-Date -Format o) $m\" | Out-File -FilePath $log -Append -Encoding utf8 }\n"
        + start_app +
        "L \"OTA start; venter paa pid $procId\"\n"
        "while (Get-Process -Id $procId -ErrorAction SilentlyContinue) { Start-Sleep -Seconds 1 }\n"
        "L 'proces afsluttet; bytter app-mappe'\n"
        "if (Test-Path -LiteralPath $bak) { Remove-Item -LiteralPath $bak -Recurse -Force }\n"
        "$moved=$false\n"
        "for ($i=0; $i -lt 20; $i++) {\n"
        "  try { Move-Item -LiteralPath $appRoot -Destination $bak -ErrorAction Stop; $moved=$true; break }\n"
        "  catch { Start-Sleep -Seconds 1 }\n"
        "}\n"
        "if (-not $moved) { L 'FEJL: kunne ikke frigive app-mappe; genstarter uaendret'; StartApp; return }\n"
        "Move-Item -LiteralPath $newDir -Destination $appRoot -Force\n"
        "if (Test-Path -LiteralPath $exe) {\n"
        "  L 'opdatering ok; genstarter'\n"
        "  Remove-Item -LiteralPath $bak -Recurse -Force\n"
        "  StartApp\n"
        "} else {\n"
        "  L 'FEJL: ny exe mangler; ruller tilbage'\n"
        "  if (Test-Path -LiteralPath $appRoot) { Remove-Item -LiteralPath $appRoot -Recurse -Force }\n"
        "  Move-Item -LiteralPath $bak -Destination $appRoot\n"
        "  StartApp\n"
        "}\n"
    )


def apply_and_restart(staging_dir) -> None:  # pragma: no cover
    """Pak staging-zip ud og swap app-mappen via en detached PowerShell-helper,
    der venter på at denne proces lukker (fil-låsen frigives), tager backup af
    den gamle mappe, installerer den nye og genstarter exe'en. Ruller tilbage
    hvis den nye exe mangler. Afslut derefter processen HÅRDT med os._exit().

    Hvorfor PowerShell og ikke en .cmd: app-mappen ligger under
    …\\Programs\\Mødeværktøj — en sti med 'ø'/'æ'. En batch-.cmd kan IKKE
    håndtere non-ASCII stier pålideligt (selv med chcp 65001 korrumperes
    rmdir/move → forkert mappe, opdatering fejler). PowerShell håndterer
    Unicode-stier nativt, og -EncodedCommand undgår både fil-encoding og
    ExecutionPolicy. os._exit() bruges frem for sys.exit(), fordi SystemExit
    i et Tkinter after-callback ikke afslutter en frosset (vinduesløs) app
    pålideligt — så ville helperens vente-løkke aldrig se PID'et forsvinde
    ('søger i det uendelige'). Kun meningsfuld på frosset Windows."""
    import base64
    import os
    import subprocess
    import sys
    import zipfile

    import app_paths

    staging_dir = Path(staging_dir)
    new_dir = staging_dir / "new"
    # Ryd et evt. tidligere (mislykket) forsøg, så vi ikke fletter gammelt indhold.
    if new_dir.exists():
        import shutil
        shutil.rmtree(new_dir, ignore_errors=True)
    new_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(staging_dir / "app.zip") as zf:
        zf.extractall(new_dir)

    app_root = app_paths.app_dir()            # …\Mødeværktøj
    exe_path = Path(sys.executable)           # …\Mødeværktøj\Mødeværktøj.exe
    log_path = app_paths.user_config_dir() / "ota-update.log"  # overlever swap
    pid = os.getpid()

    # Program Files kan ikke skrives af en almindelig proces → swap-helperen
    # skal køre eleveret (UAC-prompt). Den gamle pr.-bruger-sti forbliver på
    # den ikke-eleverede vej, præcis som før.
    elevated = not _dir_writable(app_root)
    ps_script = _build_helper_script(
        app_root, new_dir, exe_path, log_path, pid, elevated,
    )
    encoded = base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")

    if elevated:
        # Ydre, ikke-eleveret launcher: udløser UAC for den egentlige helper.
        # Afviser brugeren UAC-prompten, logges det og appen genstartes uændret.
        outer = (
            "$ErrorActionPreference='Stop'\n"
            f"$log={_ps_quote(log_path)}; $exe={_ps_quote(exe_path)}\n"
            "function L($m){ \"$(Get-Date -Format o) $m\" | Out-File -FilePath $log -Append -Encoding utf8 }\n"
            "L 'OTA kraever administrator; beder om UAC-godkendelse'\n"
            "try { Start-Process -FilePath 'powershell' -Verb RunAs -WindowStyle Hidden "
            f"-ArgumentList @('-NoProfile','-NonInteractive','-EncodedCommand','{encoded}') }}\n"
            "catch { L 'FEJL: administrator-godkendelse afvist; genstarter uaendret'; Start-Process -FilePath $exe }\n"
        )
        encoded = base64.b64encode(outer.encode("utf-16-le")).decode("ascii")

    subprocess.Popen(
        ["powershell", "-NoProfile", "-NonInteractive",
         "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        close_fds=True,
    )
    # Hård afslutning: garanterer at PID'et forsvinder, så helperen kan swappe.
    os._exit(0)
