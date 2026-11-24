# Day 4 results (2026-11-24)

## What was built

- **Web UI** (`errand_server/static/`): plain HTML, CSS and JavaScript, no build step.
  - Home: a box for a new request, the drafts in progress, and the list of errands.
  - Draft: the chat, the steps, the proposal card (Approve / Discard), the last dry run
    and the code. The page polls while the agent works.
  - Errand: Run now, Pause/Resume, Delete, the webhook URL, "Ask for a change", the runs
    (output and a "fix this run" form), and the versions with Roll back.
  - Settings: email, timezone, self-repair on/off, the checkpoints with Restore, log out.
  - All text goes in with `textContent`. The CSP allows only scripts and styles from
    this site, so inline `style` attributes are set through CSSOM.
- **Login** (`auth.py`): no password. `python -m errand_server login-link` prints a
  one-time link (10 minutes). It sets an `HttpOnly; Secure; SameSite=Strict` session
  cookie for 30 days. Only SHA-256 hashes of codes and tokens are stored (`auth.json`,
  mode 0600). An API call with the cookie that changes data must also send
  `X-Errand-Client: web`, and a cross-site form cannot set that header.
- **Checkpoints** (`sprite_api.py`): the Sprite socket has `POST /v1/checkpoint`,
  `GET /v1/checkpoints` and `POST /v1/checkpoints/<id>/restore`. A checkpoint takes
  about 90 ms, so Errand makes one before each install, update and delete.
- **Versions** (`history.py`): the git log of each errand, and roll back to a version
  (a new commit; `state.json` stays).
- **Change and repair drafts**: `Builder.start_edit()` copies the installed code into a
  draft. The model gets the code, the run output, the last webhook payload and the state.
  `propose` keeps the installed id, and approval updates the errand in place.
- **Self-repair** (`repair.py`): after a failed run, Errand starts a repair draft. It
  keeps at most one open repair draft for each errand. It sends one email for each run
  of failures, with a link to the fix. The owner still approves the fix.
- **API** (`api_routes.py`): settings, detail, pause, roll back, edit, delete,
  checkpoints, and discard of a draft.

196 tests pass on the Sprite, 88% coverage.

## Bug found and fixed

`save_automation` used `INSERT OR REPLACE`. REPLACE deletes the old row, and
`ON DELETE CASCADE` then deleted every run of that errand. So each update lost the run
history. Now it is an upsert (`ON CONFLICT(id) DO UPDATE`), with a regression test.

## End-to-end check (browser through `sprite proxy`)

| Step | Result |
|---|---|
| Open `/` with no session | login screen with the command |
| Open the login link | 303 to `/`, cookie set, home view loads |
| POST a real GitHub `star` payload (`action: created`) | run 7 ok, but the script skipped it (the day-3 bug) |
| POST a JSON list | run 8 `error` (AttributeError); a repair draft started by itself |
| Reply in the draft: also handle `created` | new code, dry run ok, proposal in about 10 s |
| Approve and update | checkpoint `v23`, version "update github-star-alert" |
| POST the real star payload again | run 9 ok, email sent |
| Runs, versions, settings, phone width | render correctly |

A real checkpoint **restore** was not tried, because it restarts the Sprite and drops
everything after the checkpoint. The unit tests cover the call.

## Things found

- On this Mac, `sprite proxy` runs in Docker and listens on the container's own
  127.0.0.1. `~/.sprites-docker/proxy.sh` adds `socat` in the container to publish it.
  The preview config is in `~/Desktop/.claude/launch.json` (`errand-sprite`).
- A server restart during a builder turn marks the draft as stopped. A new message
  retries it. Deploys during a turn cause this.
- The UI must not print `false`: `replaceChildren()` turns `false` into text, and the
  CSS `display: flex` wins over the `hidden` attribute. Both are fixed.
- Through the proxy, a page load takes about 5 s. The Sprite URL should be faster.

## UI design

A first redesign used the `frontend-design` skill (a "pad of work orders" look). The
owner then gave a design spec, and the UI now follows it:

- Colors: white canvas, near-black `#111111` type, slate `#6b7280` secondary text,
  one indigo accent `#4338ca` for key actions, `#e5e7eb` borders, `#f8f8f8` surfaces,
  `#16a34a` / `#dc2626` only for status dots and errors.
- Type: one `ui-sans-serif` stack. Home question 54 px (headline-md), page titles 40 px
  (headline-sm), sections 18 px / 500, body 16 px. Smaller headings on phones.
- Components: pill buttons 44 px high (primary indigo, secondary white with a border,
  tertiary text only), pill status chips with a colored dot, 8 px cards with a thin
  border, 8 px inputs with an indigo focus border. No heavy shadows or gradients.
- Layout: centered hero on the home page, a 960 px central column, spacing from the
  spec scale (6 / 22 / 40 / 64 / 96 px).
- The spec has no dark theme, so the UI is light only. Errand keeps its own name and
  check icon; nothing from the spec's source brand is used.
- Checked in the browser at 1280 px and at phone width. No console errors.
