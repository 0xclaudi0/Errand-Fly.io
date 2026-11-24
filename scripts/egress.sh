#!/bin/sh
# Apply the network policy that Errand needs. Run this on YOUR computer: a Sprite
# cannot change its own policy, so no errand or agent can widen its own access.
#
#   scripts/egress.sh            same as sync
#   scripts/egress.sh sync       allow the platform defaults + the hosts installed errands use
#   scripts/egress.sh allow HOST [HOST...]   allow more hosts by hand, then sync
#   scripts/egress.sh show       print the active policy
#   scripts/egress.sh open       remove the policy (no egress limits)
set -e
cd "$(dirname "$0")/.."
SPRITE="${SPRITE_NAME:-$(sed -n 's/.*"sprite": *"\([^"]*\)".*/\1/p' .sprite 2>/dev/null)}"
SPRITE="${SPRITE:-errand}"
ERRAND="cd ~/errand && ERRAND_HOME=~/.errand python3 -m errand_server egress"

post() {
  sprite api "/v1/sprites/$SPRITE/policy/network" -- -s -X POST \
    -H 'Content-Type: application/json' -d "$1"
  echo
}

case "${1:-sync}" in
  show)
    sprite api "/v1/sprites/$SPRITE/policy/network" -- -s; echo ;;
  allow)
    shift
    [ $# -gt 0 ] || { echo "usage: $0 allow HOST [HOST...]" >&2; exit 2; }
    args=""
    for host in "$@"; do args="$args --allow $host"; done
    policy=$(sprite exec --no-stdin -- sh -c "$ERRAND $args")
    echo "applying: $policy"; post "$policy" ;;
  sync)
    policy=$(sprite exec --no-stdin -- sh -c "$ERRAND")
    echo "applying: $policy"; post "$policy" ;;
  open)
    printf 'Remove every egress limit on %s? [y/N] ' "$SPRITE"; read -r answer
    [ "$answer" = "y" ] && post '{"rules": []}' ;;
  *)
    sed -n '2,10p' "$0"; exit 2 ;;
esac
