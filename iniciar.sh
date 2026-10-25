#!/usr/bin/env bash
# Cruce de afiliados - arranque en Linux/Mac (solo este equipo: http://localhost:8080)
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; }
exec .venv/bin/python app.py
