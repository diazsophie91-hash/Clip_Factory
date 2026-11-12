@echo off
setlocal
cd /d "%~dp0" || (echo Erreur : impossible d'acceder au dossier Clip Factory.& exit /b 1)

if not exist "requirements.txt" (
  echo Erreur : requirements.txt est introuvable dans %~dp0
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  where py.exe >nul 2>nul
  if errorlevel 1 (
    echo Erreur : Python Launcher introuvable. Installe Python 3.14 x64 puis relance ce script.
    exit /b 1
  )
  py -3.14 -m venv .venv
  if errorlevel 1 (
    echo Erreur : impossible de creer l'environnement Python 3.14.
    exit /b 1
  )
)

".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo Erreur : installation des dependances video/transcription echouee. Les donnees existantes n'ont pas ete modifiees.
  exit /b 1
)

call start_clip_factory.bat
exit /b %errorlevel%
