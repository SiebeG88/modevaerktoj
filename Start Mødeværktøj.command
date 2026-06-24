#!/bin/bash
# Dobbeltklik fra Finder for at starte mødeværktøjet.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

exec /opt/local/bin/python3.12 meeting_app.py
