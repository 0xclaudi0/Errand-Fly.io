# Day 5 results (2026-11-24)

## Dark theme

The UI follows the system theme. Dark tokens: canvas `#111111` (the spec's near-black),
cards `#1a1a1a`, surfaces `#232325`, borders `#2e2e31`, text `#f4f4f5`, secondary text
`#a1a1aa`. Indigo buttons stay solid (`#4f46e5`, white text); indigo as text, focus and
dots becomes `#a5b4fc` so it keeps its contrast. New tokens: `--canvas`, `--accent`,
`--on-primary`, `--primary-hover`, `--error-hover`.

## Public URL

`sprite config update --url-auth public`. Checked from outside:

| Route | Result |
|---|---|
| `/healthz`, `/` | 200, with CSP, nosniff and no-referrer headers |
| `/api/automations`, `/api/session` | 401 |
| `/tick` with no token | 401 |
| `/login?code=wrong` | 303 to `/?login=expired` |
| `POST /hooks/<unknown>` | 404 |

## Egress policy

- The Sprite filters egress by DNS. **The container cannot change its own policy**; an
  update must come from outside with the owner's org login. So no errand and no agent
  can widen its own network access.
- `python -m errand_server egress` prints the policy that Errand needs:
  `{"include": "defaults"}` (platform hosts, connector gateway, package hosts) + hosts in
  the code of installed errands + hosts that the owner allowed (`egress_allow`).
- `scripts/egress.sh` (run on your computer) applies it: `sync`, `allow HOST...`,
  `show`, `open`.
- The Sprite did not mount `/.sprite/policy/network.json`, and the socket has no policy
  route. Errand detects the policy by DNS: a canary host (`www.example.org`) that no
  errand uses, and a lookup of each wanted host.
- Settings shows Open or Locked, the sites the errands use, and the exact command for
  any blocked site.
- The builder gets a `hint` when a fetch or dry run fails on DNS. The prompt tells it
  to stop and give the owner the command, with no workaround.

Live check after `scripts/egress.sh sync`:

| Check | Result |
|---|---|
| wttr.in, hacker-news.firebaseio.com, api.sprites.dev, pypi.org | resolve |
| en.wikipedia.org, www.example.org | `Temporary failure in name resolution` |
| Real run of lagos-weather-morning (wttr.in + Resend connector) | ok in 0.4 s |
| New build: weekly Wikipedia featured article | agent stopped: "run `scripts/egress.sh allow en.wikipedia.org`" |
| Ran that command, replied "continue" | peeked, wrote 34 lines, dry run passed (Gemini + Resend), proposed `weekly mon 09:00 Africa/Lagos` |

207 tests pass.

## On this Mac

`sprite api` needs curl, which the Docker wrapper's Alpine image does not have. A local
image `errand/sprite-cli:local` (alpine:3.20 + curl + socat) works:
`SPRITE_DOCKER_IMAGE=errand/sprite-cli:local scripts/egress.sh sync`.

## Scheduler (owner step, not done yet)

cron-job.org needs an account in the owner's name, and the tick token goes into its form.

1. Get the tick token:
   `sprite exec -- sh -c 'python3 -c "import json; print(json.load(open(\"/home/sprite/.errand/secrets.json\"))[\"tick_token\"])"'`
2. In cron-job.org, create a job:
   - URL: `https://errand-bsype.sprites.app/tick`
   - Schedule: every 5 minutes (every minute keeps the Sprite awake all the time)
   - Advanced, Headers: `X-Errand-Token: <tick token>` (a header, so the token is not in
     the URL or in logs)
   - Method: GET. A 202 answer is a success.
3. Check: `/tick` calls appear in the service log, and due schedules run.

Schedules are as exact as the cron interval: with every 5 minutes, a `daily 07:00` errand
runs between 07:00 and 07:05.
