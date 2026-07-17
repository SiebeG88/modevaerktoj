@echo off
REM Dobbeltklik for at reparere OTA paa denne maskine (installerer nyeste version).
REM Selve arbejdet sker i PowerShell (Unicode-sikkert - en ren .bat kan ikke
REM haandtere 'oe'/'ae' i installationsstien).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Reparer-Modevaerktoj-OTA.ps1"
echo.
pause
