# Day 3 results (2026-11-24)

## What was built

The builder agent. The owner writes a request in plain English. Gemini (through the
Sprites connector) writes the script, does a dry run, and makes a proposal. The
owner approves it, and only then does Errand install the code.

- `errand_server/builder.py`: the agent loop. One turn = the owner's message, then
  model calls and tool calls until the model answers in text. At most 16 steps a turn.
  A Sprite task hold (`errand-build-<draft>`) keeps the Sprite awake during a turn.
- `errand_server/builder_tools.py`: the tools `list_connectors`, `peek_url`,
  `write_file`, `dry_run` (with optional `event_json` and `state_json`) and `propose`.
- `errand_server/codecheck.py`: rejects bad file names, syntax errors, and imports
  that go around the SDK (`subprocess`, `socket`, `urllib.request`, `http`,
  `requests`, ...). It is a guard rail, not a sandbox.
- `errand_server/drafts.py`: `drafts/<id>/draft.json` (conversation, transcript,
  proposal, dry runs), `drafts/<id>/code/`, `drafts/<id>/state.json`.
- `errand_server/prompts.py`: the system prompt, with the SDK reference.
- SDK: `ERRAND_DRY_RUN=1` makes `send_email()` and non-GET `call()` print instead of
  send. New `call(provider, method, path, ...)` for any connector. `generate()` is
  the raw Gemini call, with retry rounds and a model cooldown.
- API: `GET|POST /api/drafts`, `GET /api/drafts/<id>`, `POST /api/drafts/<id>/messages`,
  `POST /api/drafts/<id>/approve`. A turn runs in a thread; clients poll the draft.
- CLI: `build MESSAGE [--draft ID]`, `approve DRAFT`, `drafts`, `show DRAFT`.
- Setting `timezone` (the prompt tells the model the owner's timezone).

154 tests pass on the Sprite, 86% coverage.

## Real builds

| Request | Result | Time |
|---|---|---|
| Lagos weather email every day at 07:00 | peeked wttr.in, wrote 37 lines, dry run ok, proposed `daily 07:00 Africa/Lagos`; approved over HTTP, real run sent the email | 9 s |
| "Watch a page and tell me when it changes" | asked one question: which page, how often | 3 s |
| (reply) python.org downloads, every 6 h | wrote a regex scraper, dry run ok, proposed `every 6h` | 25 s |
| GitHub star webhook, email who starred | dry run with a sample event, proposed webhook; approved, real POST ran ok | ~2.5 min (model failures) |
| Python releases again, after the `state_json` change | found the RSS feed, dry run 1 = first run, dry run 2 from a saved state tested the email | 101 s (every model failed once) |

## Things found

- Gemini function calls work through the gateway. The model's `thoughtSignature`
  must come back unchanged, or the next call fails with 400. A part that another
  model wrote can carry `"thoughtSignature": "skip_thought_signature_validator"`;
  the builder does this when it falls back to another model in the middle of a turn.
- Free-tier models fail often and sometimes hang. One call hung for 60 s, another took
  53 s to return 503. Fixes: a 40 s timeout per call in the builder, and a 2-minute
  cooldown that tries a failed model last. A whole turn can still take 1-2 minutes
  when every model is busy. The UI (day 4) must show progress.
- A dry run proves the script works with the agent's own sample. The star webhook
  script accepts only the `started` action (GitHub's "watch" event). GitHub's "star"
  event sends `created`, so a real star would be skipped. Self-repair from real run
  output (day 4) is the answer.
- The first run of a "tell me when X changes" script often only saves state. The
  `state_json` input lets a second dry run test the send path; the prompt asks for it.
- The model said "approve in your Sprites dashboard". The prompt now says that
  Errand shows the Approve button.

## Known limits

- Scripts run as the same Unix user as the server, so a script could read
  `secrets.json`. The code check and the owner's review are the only guards now.
  A separate user, or a Sprite network policy (day 5), would make this stronger.
- The builder makes new automations only. Changes to an installed automation come
  with self-repair on day 4.
