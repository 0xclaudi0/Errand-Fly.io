# Errand

Plain-English automations that run on your own [Sprite](https://sprites.dev).

You write what you want, for example *"Every weekday at 8:00, email me the top 5 Hacker
News stories with one line on each."* An agent writes a small Python script, tests it
with a dry run, and shows you a proposal. Errand installs the script only after you
approve it. When a run fails later, Errand prepares a fix and asks you again.

Built for Fly.io Hacksgiving 2026.

## What it can do

Each errand is a short script with one of three triggers:

- **Schedule:** `every 30m`, `daily 07:00 Africa/Lagos`, `weekly mon 09:00 Europe/London`
- **Webhook:** a private URL that GitHub, Stripe or any other service can call
- **Manual:** it runs when you click Run now

Errands can call any service that you connect to the Sprite with a Sprites connector
(Gemini and Resend are the minimum), and read public web pages and JSON APIs. Examples
from the demo Sprite (the agent built the last three from one sentence each; the HN
digest is a hand-written example):

| Errand | Trigger | Uses |
|---|---|---|
| Hacker News morning digest | `daily 07:00` | HN API, Gemini, Resend |
| Lagos weather and umbrella note | `daily 07:00 Africa/Lagos` | wttr.in, Resend |
| GitHub star alert | webhook | GitHub payload, Resend |
| Wikipedia featured article | `weekly mon 09:00` | Wikipedia, Gemini, Resend |

## How it works

```
 you ──"email me ..."──▶  builder agent (Gemini through the connector)
                            │  list_connectors, peek_url, write_file, dry_run, propose
                            ▼
                         proposal ──you approve──▶ checkpoint ─▶ install (git commit)
                                                                    │
 cron-job.org ──GET /tick──▶ errand-server ──runs due errands──▶ main.py
 GitHub ───POST /hooks/<secret>──┘             │                    │
                                               │ failed run         ▼
                                               └──▶ repair draft ─▶ email with a link to the fix
```

1. **Build.** The builder agent writes `main.py` with the `errand_sdk` helpers. A dry
   run executes the script for real, but `send_email()` and any connector call that is
   not a GET only print what they would send.
2. **Approve.** The proposal shows when the errand runs, what it uses, the time limit,
   the dry-run output and the code. Approve saves a Sprite checkpoint, then installs.
3. **Run.** An outside cron calls `/tick`; Errand starts each due errand in its own
   process with a hard time limit. A Sprite task keeps the Sprite awake during a run.
4. **Repair.** If a run fails, Errand starts a repair draft with the code, the output
   and the webhook payload. You get one email for each run of failures, with a link.
   Nothing changes until you approve the fix.
5. **Undo.** Every install, update and delete saves a checkpoint of the whole Sprite.
   Each errand also has a git history, and you can roll back to an older version.

## Install

You need a Fly.io account, the [Sprites CLI](https://sprites.dev) (`sprite login`), and
two connectors on the Sprite: **Gemini** (a free API key from aistudio.google.com) and
**Resend** (an API key from resend.com).

```bash
git clone https://github.com/0xclaudi0/Errand-Fly.io.git errand
cd errand
./install.sh
```

The installer creates (or reuses) the Sprite, copies the code, checks the connectors,
asks for your email and timezone, starts the service, makes the URL public (if you
agree), prints the scheduler settings, and prints your first login link. Run it again
to update; your errands, runs and settings stay.

**Email:** without your own domain in Resend, Resend delivers only to the email address
of your Resend account, so use that address.

**Scheduler:** create a free job on [cron-job.org](https://cron-job.org) that calls the
`/tick` URL every 5 minutes with the `X-Errand-Token` header that the installer prints.
(A call every minute keeps the Sprite awake all the time.)

## Use it

Open your Sprite URL. The login page emails you a one-time link.

- **Home:** write a request. Drafts that wait for you and your errands are below.
- **Draft:** the conversation, each step of the agent, the proposal, the last dry run
  and the code. Reply to ask for changes before you approve.
- **Errand:** Run now, Pause, Delete, the webhook URL, "Ask for a change", each run
  with its output (and "fix this run"), and the versions with Roll back.
- **Settings:** email, timezone, self-repair on or off, the network status, and the
  checkpoints with Restore.

The same things work from the command line on the Sprite:

```bash
sprite exec -- sh -c 'cd ~/errand && ERRAND_HOME=~/.errand python3 -m errand_server --help'
```

Commands: `build`, `approve`, `drafts`, `show`, `list`, `run`, `install`, `set`,
`login-link`, `egress`, `connectors`, `tick-info`, `serve`.

## Security

- **No API keys on the Sprite.** Scripts call the Sprites connector gateway, which adds
  the credential. The dry run and the code check make every outside call go through the
  SDK, so the dry run can stop actions that send or change things.
- **You approve all code.** The agent cannot install anything. A proposal needs a dry
  run that passed after the last change to the code.
- **Login:** no password. A one-time link (10 minutes from the CLI, 15 minutes by email)
  sets an `HttpOnly; Secure; SameSite=Strict` cookie. Only hashes of codes and sessions
  are stored. A request with the cookie that changes data also needs the
  `X-Errand-Client` header. Login emails go only to the address in Settings, use the
  Sprite URL (never the request's `Host`), and are limited to one a minute and five an
  hour.
- **Separate keys:** the admin token opens `/api`, the tick token opens only `/tick`,
  and each webhook URL starts only its own errand.
- **Network:** `scripts/egress.sh sync` locks the Sprite to the platform defaults, the
  connector gateway and the sites that your errands use. The Sprite cannot change its
  own policy, so an errand or the agent cannot widen it. When a site is blocked, the
  builder stops and gives you the exact command.
- **Headers:** a strict Content-Security-Policy (scripts and styles from the site only),
  `nosniff`, `no-referrer`, and no framing.

**Known limits.** Scripts run as the same Unix user as the server, so a script could
read `secrets.json`. The code check, your review and the network policy limit this; a
separate user for scripts would be stronger. Free-tier Gemini models are often busy, so
a build can take one or two minutes when every model is slow.

## Sprites features that Errand uses

| Feature | How Errand uses it |
|---|---|
| Services | `errand-server` runs as the service `errand`, on the HTTP port |
| Connectors and the gateway | Gemini for the agent and for `ask_llm()`, Resend for email, `call()` for any other connector |
| Tasks API (`/.sprite/api.sock`) | a hold keeps the Sprite awake during each run and each build |
| Checkpoints | one before every install, update and delete; restore from Settings |
| Network policy | the egress lock, which only the owner can change |
| Public URL | webhooks, the scheduler and the web UI |

## Development

Everything runs inside the Sprite (Python 3.13, standard library only).

```bash
scripts/deploy.sh --restart                                # copy the code and restart
sprite exec -- sh /home/sprite/errand/scripts/test.sh      # 214 tests, about 88% coverage
```

```
errand_server/   HTTP server, scheduler, runner, builder agent, auth, egress, web UI
errand_sdk/      helpers that errand scripts import: ask_llm, send_email, call, fetch_*, state
examples/        hn-digest and webhook-log
scripts/         deploy.sh, service.sh, test.sh, egress.sh
spikes/          notes from each day of the build, and the demo runbook
tests/           pytest suite
```
