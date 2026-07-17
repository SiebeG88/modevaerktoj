<#
  Reparer-Modevaerktoj-OTA.ps1
  Engangs-bootstrap: geninstallerer Moedevaerktoej fra den nyeste GitHub-release
  saa den faar den rettede OTA-updater. Unicode-sikker (haandterer 'oe'/'ae' i
  stien), verificerer SHA256, og ruller tilbage hvis noget gaar galt.
  Efter denne koersel virker fremtidige OTA-opdateringer af sig selv.

  Brug: hoejreklik -> "Koer med PowerShell", eller dobbeltklik paa .bat-filen.
#>
$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'   # undgaa progress-render i NonInteractive
$repo = 'SiebeG88/modevaerktoj'

# GitHub kraever TLS 1.2 (PS 5.1 defaulter til TLS 1.0)
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

function Write-Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }

function Get-Web($url, $outFile) {
    # Robust download uden IE-parser/prompts (virker ogsaa NonInteractive)
    $wc = New-Object System.Net.WebClient
    $wc.Headers.Add('User-Agent', 'mv-ota-fix')
    if ($outFile) { $wc.DownloadFile($url, $outFile); return }
    return $wc.DownloadString($url)
}

try {
    # 1) Find nyeste release-version via GitHub API
    Write-Step 'Finder nyeste version...'
    $rel   = (Get-Web "https://api.github.com/repos/$repo/releases/latest") | ConvertFrom-Json
    $tag   = $rel.tag_name                       # fx v1.0.9
    $zipA  = $rel.assets | Where-Object { $_.name -like '*.zip'         } | Select-Object -First 1
    $shaA  = $rel.assets | Where-Object { $_.name -like '*.zip.sha256'  } | Select-Object -First 1
    if (-not $zipA -or -not $shaA) { throw "Release $tag mangler .zip/.sha256" }
    Write-Host "    Nyeste release: $tag"

    # 2) Find installationsmappen (registry InstallLocation, ellers standard)
    Write-Step 'Finder installationsmappe...'
    $app = $null
    $keys = @(
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    foreach ($k in $keys) {
        $hit = Get-ItemProperty $k -ErrorAction SilentlyContinue |
               Where-Object { $_.DisplayName -like 'M*dev*rkt*j*' -and $_.InstallLocation } |
               Select-Object -First 1
        if ($hit) { $app = $hit.InstallLocation.TrimEnd('\'); break }
    }
    if (-not $app) {
        # Fallback: soeg efter mappen uden non-ASCII literal (ANSI-encoding-sikkert)
        $progs = Join-Path $env:LOCALAPPDATA 'Programs'
        $d = Get-ChildItem -LiteralPath $progs -Directory -Filter 'M*dev*rkt*j' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($d) { $app = $d.FullName }
    }
    if (-not $app -or -not (Test-Path -LiteralPath $app)) { throw "Fandt ikke installationsmappen" }
    Write-Host "    Install-mappe: $app"

    # 3) Luk koerende instanser
    Write-Step 'Lukker koerende app...'
    Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($app, [StringComparison]::OrdinalIgnoreCase) } |
        ForEach-Object { try { $_.CloseMainWindow() | Out-Null } catch {}; Start-Sleep -Milliseconds 800; if (-not $_.HasExited) { $_.Kill() } }
    Start-Sleep -Seconds 2

    # 4) Hent + verificer SHA256
    $zip = Join-Path $env:TEMP "mv-$tag.zip"
    Write-Step "Henter $($zipA.name) ..."
    Get-Web $zipA.browser_download_url $zip
    $expected = ((Get-Web $shaA.browser_download_url) -split '\s+')[0].ToLower()
    $actual   = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
    if ($expected -ne $actual) { throw "Checksum-mismatch! forventet $expected, fik $actual" }
    Write-Host '    Checksum OK'

    # 5) Pak ud
    Write-Step 'Pakker ud...'
    $new = Join-Path $env:TEMP "mv-$tag-new"
    if (Test-Path -LiteralPath $new) { Remove-Item -LiteralPath $new -Recurse -Force }
    Expand-Archive -LiteralPath $zip -DestinationPath $new -Force

    # 6) Backup + swap (Unicode-sikkert via Move-Item -LiteralPath) + rollback
    Write-Step 'Installerer...'
    $bak = "$app.ota-bak"
    if (Test-Path -LiteralPath $bak) { Remove-Item -LiteralPath $bak -Recurse -Force }
    Move-Item -LiteralPath $app -Destination $bak
    try {
        Move-Item -LiteralPath $new -Destination $app
        # Find exe uden non-ASCII literal (ANSI-encoding-sikkert): foerste .exe
        # i mappen som ikke er uninstalleren.
        $exe = Get-ChildItem -LiteralPath $app -Filter '*.exe' -ErrorAction SilentlyContinue |
               Where-Object { $_.Name -notlike 'unins*' } | Select-Object -First 1
        if (-not $exe) { throw 'ny exe mangler efter udpakning' }
        # Bevar uninstaller-filer, saa "Tilfoej/fjern programmer" stadig virker
        Get-ChildItem -LiteralPath $bak -Filter 'unins000.*' -ErrorAction SilentlyContinue |
            ForEach-Object { Copy-Item $_.FullName -Destination $app -Force -ErrorAction SilentlyContinue }
        Remove-Item -LiteralPath $bak -Recurse -Force -ErrorAction SilentlyContinue
        Write-Step "Opdateret til $tag. Starter appen..."
        Start-Process -FilePath $exe.FullName
    }
    catch {
        Write-Warning "Swap fejlede: $_  -> ruller tilbage."
        if (Test-Path -LiteralPath $app) { Remove-Item -LiteralPath $app -Recurse -Force }
        Move-Item -LiteralPath $bak -Destination $app
        throw
    }
    Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $new -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "`nFaerdig - $tag er installeret. Fremtidige OTA-opdateringer virker nu automatisk." -ForegroundColor Green
}
catch {
    Write-Host "`nFEJL: $_" -ForegroundColor Red
    exit 1
}
