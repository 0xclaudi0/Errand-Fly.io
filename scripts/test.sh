#!/bin/sh
# Runs inside the Sprite: run the test suite in a dev venv.
set -eu
VENV=/home/sprite/.errand-dev
[ -x "$VENV/bin/pytest" ] || { python3 -m venv "$VENV" && "$VENV/bin/pip" install -q pytest pytest-cov; }
cd /home/sprite/errand
"$VENV/bin/pytest" "$@"
