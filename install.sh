#!/bin/sh
# Install Errand on a Sprite. Run this from the errand folder on your computer:
#
#   ./install.sh
#
# It uses your own `sprite` CLI login. It asks a few questions; for a script, set
#   ERRAND_SPRITE=name  ERRAND_EMAIL=you@example.com  ERRAND_TZ=Europe/London
#   ERRAND_PUBLIC=yes|no  ERRAND_LOCK=yes|no
# Running it again updates the code and keeps your errands, runs and settings.
set -eu
cd "$(dirname "$0")"

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
note() { printf '  %s\n' "$*"; }
die() { printf '\nerror: %s\n' "$*" >&2; exit 1; }

# ask VAR "Question" "default": use $VAR if set, else read the answer
ask() {
  eval "current=\${$1:-}"
  if [ -n "$current" ]; then return; fi
  if [ -n "$3" ]; then printf '%s [%s] ' "$2" "$3"; else printf '%s ' "$2"; fi
  read -r answer || answer=""
  eval "$1=\${answer:-\$3}"
}

yes_no() { case "$1" in [Yy]*) return 0 ;; *) return 1 ;; esac; }

# errand ARGS...: run the Errand CLI on the Sprite. Arguments pass as a list, no shell.
errand() {
  sprite exec --no-stdin -- env ERRAND_HOME=/home/sprite/.errand PYTHONPATH=/home/sprite/errand \
    python3 -m errand_server "$@"
}

local_timezone() {
  if [ -L /etc/localtime ]; then
    readlink /etc/localtime | sed 's#.*/zoneinfo/##'
  elif [ -r /etc/timezone ]; then
    cat /etc/timezone
  else
    echo UTC
  fi
}

# ---- 0. the sprite CLI and the Sprite ----------------------------------------

command -v sprite >/dev/null 2>&1 || die "install the Sprites CLI first (https://sprites.dev), then run: sprite login"
sprite list >/dev/null 2>&1 || die "the sprite CLI is not logged in. Run: sprite login"

bold "Errand installer"
note "Errand runs on a Sprite in your Fly.io account. Nothing is sent anywhere else."
ERRAND_SPRITE="${ERRAND_SPRITE:-}"
ask ERRAND_SPRITE "Sprite name:" "errand"

if sprite list 2>/dev/null | grep -qx "$ERRAND_SPRITE"; then
  note "Using the Sprite '$ERRAND_SPRITE' that already exists."
else
  note "Creating the Sprite '$ERRAND_SPRITE'."
  sprite create --skip-console "$ERRAND_SPRITE" >/dev/null
fi
sprite use "$ERRAND_SPRITE" >/dev/null

# ---- 1. code -------------------------------------------------------------------

bold "1/6  Copying Errand to the Sprite"
scripts/deploy.sh >/dev/null
note "Done."

# ---- 2. connectors ---------------------------------------------------------------

bold "2/6  Checking connectors"
while :; do
  found=$(errand connectors 2>/dev/null || true)
  missing=""
  for provider in gemini resend; do
    case " $found " in *" $provider "*) ;; *) missing="$missing $provider" ;; esac
  done
  if [ -z "$missing" ]; then
    note "Gemini and Resend are connected. Errand never sees their API keys."
    break
  fi
  note "Missing:$missing"
  note "In the Sprites dashboard, open the Sprite '$ERRAND_SPRITE' and add a connector for"
  note "each one (Gemini: an API key from aistudio.google.com; Resend: an API key from resend.com)."
  [ -t 0 ] || die "connectors missing; add them, then run ./install.sh again"
  printf '  Press Enter when they are added (or type skip): '
  read -r answer || answer=""
  [ "$answer" = "skip" ] && { note "Skipped. The builder needs Gemini, and emails need Resend."; break; }
done

# ---- 3. owner settings -------------------------------------------------------------

bold "3/6  Your email and timezone"
note "Errand sends results, failure alerts and login links to this address."
note "Without your own domain in Resend, use the email address of your Resend account."
ERRAND_EMAIL="${ERRAND_EMAIL:-}"
while :; do
  ask ERRAND_EMAIL "Email:" ""
  if [ -n "$ERRAND_EMAIL" ] && errand set notify_email "$ERRAND_EMAIL" >/dev/null 2>&1; then break; fi
  note "That does not look like one email address. Try again."
  [ -t 0 ] || die "ERRAND_EMAIL is not a valid email address"
  ERRAND_EMAIL=""
done
ERRAND_TZ="${ERRAND_TZ:-}"
ask ERRAND_TZ "Timezone:" "$(local_timezone)"
errand set timezone "$ERRAND_TZ" >/dev/null 2>&1 || { note "Unknown timezone '$ERRAND_TZ'; using UTC."; errand set timezone UTC >/dev/null; }

# ---- 4. service ---------------------------------------------------------------------

bold "4/6  Starting Errand"
sprite exec --no-stdin -- sh /home/sprite/errand/scripts/service.sh >/dev/null
note "The service 'errand' runs on port 8080."

# ---- 5. public URL and scheduler ---------------------------------------------------------

bold "5/6  Public URL"
note "Webhooks and the scheduler must reach Errand from the internet. Every page that"
note "shows or changes data still needs your login; webhook URLs contain a secret."
ERRAND_PUBLIC="${ERRAND_PUBLIC:-}"
ask ERRAND_PUBLIC "Make the URL public? [Y/n]" "y"
if yes_no "$ERRAND_PUBLIC"; then
  sprite config update --url-auth public >/dev/null
  note "Public."
  echo
  note "Scheduled errands need a call to /tick every few minutes. A free cron-job.org"
  note "job works: every 5 minutes, method GET, with this URL and header:"
  errand tick-info | sed 's/^/    /'
  note "(The header keeps the token out of URLs and logs. Treat it like a password.)"
else
  note "Kept private. Only your Fly.io org can open it; webhooks and schedules will not run."
fi

# ---- 6. network policy -----------------------------------------------------------------

bold "6/6  Network"
note "You can lock the Sprite's network to the platform, your connectors and the sites"
note "your errands use. The Sprite cannot change this policy itself."
ERRAND_LOCK="${ERRAND_LOCK:-}"
ask ERRAND_LOCK "Lock the network now? [y/N]" "n"
if yes_no "$ERRAND_LOCK"; then
  scripts/egress.sh sync >/dev/null && note "Locked. Allow a site later with: scripts/egress.sh allow HOST"
else
  note "Not changed. To lock it later: scripts/egress.sh sync"
fi

# ---- done ---------------------------------------------------------------------------------

bold "Errand is ready. Open this link to log in (it works once, for 10 minutes):"
echo
errand login-link 2>/dev/null | sed 's/^/  /'
echo
note "Next time, the login page emails you a link. Run ./install.sh again to update."
