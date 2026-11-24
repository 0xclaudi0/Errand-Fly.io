#!/bin/sh
# Copy the Errand code to the Sprite. With --restart, also (re)start the service.
# Usage: scripts/deploy.sh [--restart]
set -eu
cd "$(dirname "$0")/.."

DEST=/home/sprite/errand
FILES="errand_server errand_sdk examples tests scripts pyproject.toml"

COPYFILE_DISABLE=1 tar czf - --exclude __pycache__ --exclude .pytest_cache $FILES \
  | sprite exec --stdin -- sh -c "rm -rf $DEST.new && mkdir -p $DEST.new \
      && tar xzf - -C $DEST.new && rm -rf $DEST && mv $DEST.new $DEST"
echo "deploy: code copied to $DEST"

if [ "${1:-}" = "--restart" ]; then
  sprite exec --no-stdin -- sh "$DEST/scripts/service.sh"
fi
