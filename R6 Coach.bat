@echo off
rem One-click start for R6 Coach: runs the coach with its tray icon and opens the dashboard.
rem Double-click this file, or make a desktop shortcut to it. Quit from the tray icon.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo R6 Coach is not set up yet. See README.md, "Setup".
  pause
  exit /b 1
)
rem If a coach is already running, just open its dashboard.
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if %errorlevel%==0 (
  start "" http://127.0.0.1:8765/
  exit /b 0
)
if not exist "data" mkdir data
rem Arguments pass through: "R6 Coach.bat" -v writes the debug log (or set R6COACH_DEBUG=1).
start "R6 Coach" /b ".venv\Scripts\pythonw.exe" -m r6coach %* >> "data\coach.log" 2>&1
