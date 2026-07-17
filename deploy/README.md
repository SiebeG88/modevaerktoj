# deploy/ — engangs-reparation af OTA

Maskiner der allerede koerer en **gammel build med den defekte OTA** (foer
v1.0.9) kan ikke opdatere sig selv — den gamle updater kunne ikke swappe
app-mappen paa en sti med `ø`/`æ` (`…\Programs\Mødeværktøj`). De skal derfor
have den nye version installeret **manuelt én gang**. Derefter virker fremtidige
OTA-opdateringer af sig selv.

## Brug

Kopiér **begge** filer til maskinen (samme mappe) og dobbeltklik paa `.bat`:

- `Reparer-Modevaerktoj-OTA.bat` — tynd launcher (dobbeltklik-venlig)
- `Reparer-Modevaerktoj-OTA.ps1` — selve arbejdet (skal ligge ved siden af)

Scriptet:

1. finder nyeste release paa GitHub via API,
2. finder installationsmappen (registry `InstallLocation`, ellers `%LOCALAPPDATA%\Programs\...`),
3. lukker en koerende app, henter zip'en og verificerer **SHA256**,
4. bytter app-mappen **Unicode-sikkert** med **backup + rollback** (fejler noget, rulles tilbage til den gamle version — maskinen ender aldrig uden app),
5. bevarer uninstaller-filerne og genstarter appen.

## Hvorfor PowerShell og ikke en ren .bat

En batch-`.cmd` kan ikke haandtere `ø`/`æ` i stien paalideligt (selv med
`chcp 65001`). Al fil-logik ligger derfor i PowerShell, og scriptet undgaar
non-ASCII string-literaler (registry-wildcard `M*dev*rkt*j`, exe findes via
`Get-ChildItem`), saa `.ps1`-filens encoding er ligegyldig. Samme aarsag til at
den rettede OTA (`updater.apply_and_restart`) bruger en PowerShell-helper via
`-EncodedCommand` i stedet for en `.cmd`.
