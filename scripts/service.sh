#!/bin/sh
# Runs inside the Sprite: create the errand service, or restart it if it exists.
set -eu
DEST=/home/sprite/errand
PY="$(command -v python3)"

if sprite-env services get errand >/dev/null 2>&1; then
  sprite-env services restart errand
else
  sprite-env services create errand --cmd "$PY" --args "-m,errand_server,serve" \
    --dir "$DEST" --env "ERRAND_HOME=/home/sprite/.errand" --http-port 8080 --no-stream
fi
sprite-env services get errand
