# Day 1 results (2026-11-23)

## Setup

- Sprite: `errand` in org `claudio-43`, URL `https://errand-bsype.sprites.app`.
- Image: Ubuntu 26.04.1 LTS, Python 3.13.17, Node 24.22.1, 8 CPUs, 15 GiB RAM, 99 GB disk.
- The `sprite` CLI has no Intel macOS build. `~/.local/bin/sprite` runs the
  linux-x86_64 build in Docker (Colima). Run `colima start` first.
- Use `sprite exec --no-stdin` in scripts. Without it, the CLI forwards stdin
  and turns off automatic reconnection.

## Test 3: wake on request

The probe service (`probe_server.py`) answered requests after idle gaps.
Run with `spikes/wake_test.sh`.

| Idle gap | Response time | Process |
|---|---|---|
| 0 s | 0.52 s | pid 298 |
| 60 s | 0.68 s | pid 298 |
| 180 s | 0.66 s | pid 298 |
| 600 s | 1.30 s | pid 298 |
| 1200 s | 1.13 s | pid 298 |

- The service never restarted in 35 minutes. Its memory survived each gap.
- After 10 minutes or more of idle time, a wake adds about 0.6 s.
- `sprite info` showed `Status: cold` after the test, but the process had the
  same pid. The CLI status label does not tell us if memory survives.
- `time.monotonic()` advanced at the same rate as wall time during idle
  gaps, so the server cannot use the clock to find out that it slept.

**Design rule:** `errand-server` keeps no important state in memory. All
state goes to SQLite and to files, because a cold wake can come at any time.

## Test 4: public URL

- Private (default, `--auth sprite`): a request with no login gets HTTP 302.
- Public (`sprite url update --auth public`): GET and POST to `/hooks/<id>`
  reach the HTTP service, about 0.5 s from Europe.
- The URL is private again after the test. Errand needs its own login and
  webhook secrets before the URL goes public.

## Test 5: Free plan

- $5 of usage each month, a maximum of 3 Sprites.
- Sprites: $0.0385 per CPU-hour, $0.021875 per GB-hour of memory, storage
  billed separately. An idle Sprite pays only for stored data.
- The pricing page is not clear about the credit card requirement.

## Connectors

Both connections use the access policy `name_prefix: errand`. Code in the
Sprite lists them with `GET https://api.sprites.dev/v1/gateway/list`.

| Provider | Connection ID | Gateway base URL |
|---|---|---|
| gemini | `jnfL8B4ykDU9xCv9YUfFrQ` | `https://api.sprites.dev/v1/gateway/gemini/jnfL8B4ykDU9xCv9YUfFrQ` |
| resend | `3n6VQEUv9g5arXTRggNfIA` | `https://api.sprites.dev/v1/gateway/resend/3n6VQEUv9g5arXTRggNfIA` |

There is no Anthropic key, so Errand uses Gemini for the builder agent.

## Test 1: LLM through the gateway (Gemini)

- Plain HTTP: `POST <base>/v1beta/models/<model>:generateContent` with no key.
- Python SDK: `google-genai` works with `api_key="sprites"` and
  `HttpOptions(base_url=<base>)`. One call took 1.1 s.
- Tool calls work. `write_file` came back with correct `path` and `content` args.

| Model alias | Served by | Result |
|---|---|---|
| `gemini-flash-lite-latest` | gemini-3.5-flash-lite | 200, tool call, 0.6 s |
| `gemini-3.5-flash` | gemini-3.6-flash | 200, tool call, 1.2 s |
| `gemini-3.1-flash-lite` | gemini-3.1-flash-lite | 200, tool call, 0.9 s |
| `gemini-flash-latest` | gemini-3.8-flash | one 200, then 503 "high demand" |
| `gemini-pro-latest` | gemini-3.1-pro | 429, the free tier has no quota |
| `gemini-2.5-flash` | none | 404, not available to new users |

- The first call to `gemini-flash-latest` failed with HTTP 502
  `failed to reach Google Gemini API` after 125 s.

**Design rules:**
- `ask_llm()` uses a model list: `gemini-3.5-flash` first, then
  `gemini-3.1-flash-lite`, then `gemini-flash-lite-latest`. On 429, 502 or 503
  it tries the next model.
- Each LLM call has a client timeout of 60 s. The gateway can hold a failed
  request for more than 2 minutes.
- Do not use Pro models on the free key.

## Test 2: email through the gateway (Resend)

- The account has no verified domain (`provider_info.domains` is empty).
- `POST <base>/emails` from `onboarding@resend.dev` to `delivered@resend.dev`
  returned HTTP 200 in 0.4 s. `GET <base>/emails/<id>` showed `delivered`.
- A send to `someone@example.com` returned HTTP 422 (`validation_error`).
- Not tested: a send to a real address. The Resend docs say that, with no
  verified domain, `onboarding@resend.dev` sends only to the account owner's
  address. That is enough for the demo. Other users need their own domain,
  or they use Pushover or Slack.
