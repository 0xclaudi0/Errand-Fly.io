# Day 2 results (2026-11-24)

## What runs on the Sprite

- Service `errand`: `python3 -m errand_server serve` in `/home/sprite/errand`,
  `ERRAND_HOME=/home/sprite/.errand`, HTTP port 8080. The `probe` service is gone.
- State: `errand.db` (SQLite, WAL), `automations/<id>/` (code + `state.json`,
  git history of the code), `secrets.json` (tick and admin tokens, mode 0600),
  `settings.json` (`notify_email`, `email_from`).
- Installed: `hn-digest` (`daily 07:00` UTC) and `webhook-log` (webhook).
  `notify_email` is `delivered@resend.dev` (Resend test inbox) for now.

## Scripts

- `scripts/deploy.sh [--restart]` copies the code to the Sprite and restarts the service.
- `scripts/test.sh` runs pytest in the Sprite (Python 3.13). 90 tests pass, 86% coverage.
  `__main__.py` has no unit tests; the end-to-end run below checks it.

## End-to-end checks

| Check | Result |
|---|---|
| `/tick` with no token | 401 |
| `/tick` with token, nothing due | 202, `started: []` |
| `/tick` with `hn-digest` due | 202, `started: ["hn-digest"]`; run `ok` in 4.7 s, email sent |
| Same tick again at once | 202, `started: []` (claim is atomic) |
| Task hold during the run | `errand-run-3` present; gone after the run |
| `next_run_at` after the tick | next 07:00 UTC |
| `POST /hooks/<id>` with JSON | 202; run `ok`, payload in `state.json` |
| Unknown hook id | 404 |
| Tick token in the service log | 0 matches (the log drops query strings) |

## Things found

- Task API: `POST /v1/tasks` (201), `PUT /v1/tasks/<name>` extends (200),
  `DELETE /v1/tasks/<name>` (204). A second POST with the same name fails.
- `gemini-3.5-flash` took 12 s to 60 s+. The lite models take about 2 s, so
  `ask_llm()` now tries the lite models first. Each fallback prints a note to
  the run output. The builder agent (day 3) can ask for the stronger model.
- `sprite exec --stdin -- sh -s < script.sh` runs a local script in the Sprite.
