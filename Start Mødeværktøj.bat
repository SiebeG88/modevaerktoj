@echo off
cd /d "%~dp0"
where py >nul 2>nul && (py -3 meeting_app.py) || (python meeting_app.py)
pause
