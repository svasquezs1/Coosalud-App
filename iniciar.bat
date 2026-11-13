@echo off
rem Cruce de afiliados - arranque en Windows (solo este equipo: http://localhost:8080)
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  if errorlevel 1 goto error
)
.venv\Scripts\python -m pip install -r requirements.txt
if errorlevel 1 goto error
.venv\Scripts\python app.py
pause
exit /b

:error
echo No se pudieron instalar las dependencias. Revise el mensaje anterior.
pause
exit /b 1
