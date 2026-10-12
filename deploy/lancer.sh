#!/bin/sh
# Lance la veille depuis la racine du projet ; utilisé par cron et launchd.
cd "$(dirname "$0")/.." || exit 1
PYTHON="${PYTHON:-.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3
exec "$PYTHON" -m veille_boamp "$@" >> veille.log 2>&1
