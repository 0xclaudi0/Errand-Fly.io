#!/bin/sh
# Day-1 probe: measure how fast the Sprite URL answers after idle gaps.
# Same pid as before = warm wake; new pid = cold wake (service restarted).
# Usage: spikes/wake_test.sh https://<sprite>.sprites.app "60 180 600 1200"

URL="${1:?sprite URL required}"
GAPS="${2:-60 180 600 1200}"

probe() {
  printf '%s idle=%ss ' "$(date +%H:%M:%S)" "$1"
  curl -s -m 30 -w ' -> HTTP %{http_code} in %{time_total}s\n' "$URL/tick?idle=$1"
}

probe 0
for gap in $GAPS; do
  sleep "$gap"
  probe "$gap"
done
