@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Lance d'abord INSTALLER_ET_DEMARRER.bat pour installer les composants.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" app.py
pause
