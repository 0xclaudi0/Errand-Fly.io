"""ask_llm(): one call to Gemini through the Sprites connector, with model fallback.

Free-tier models fail often (429, 502, 503), so a busy model is skipped and
the next one is tried. If every model is busy, the whole list is tried again
after a short pause.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from typing import Any, Callable

from . import gateway
from .gateway import GatewayError

# Lite models answer in about 1-2 s; gemini-3.5-flash thinks and can take 10-60 s.
DEFAULT_MODELS = ("gemini-flash-lite-latest", "gemini-3.1-flash-lite", "gemini-3.5-flash")
RETRY_STATUSES = {0, 429, 500, 502, 503, 504}
TIMEOUT_S = 60
ROUNDS = 2
PAUSE_S = 5.0
COOLDOWN_S = 120.0

Post = Callable[[str, dict, float], dict]
BodyFor = Callable[[str], dict]


class LLMError(Exception):
    pass


class ModelHealth:
    """Remember which models failed recently, so the next call tries them last."""

    def __init__(self, cooldown_s: float = COOLDOWN_S,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._failed_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def order(self, models: list[str] | tuple[str, ...]) -> list[str]:
        now = self._clock()
        with self._lock:
            cooling = {m for m, at in self._failed_at.items() if now - at < self._cooldown_s}
        return [m for m in models if m not in cooling] + [m for m in models if m in cooling]

    def failed(self, model: str) -> None:
        with self._lock:
            self._failed_at[model] = self._clock()

    def worked(self, model: str) -> None:
        with self._lock:
            self._failed_at.pop(model, None)


HEALTH = ModelHealth()


def ask_llm(prompt: str, *, system: str | None = None, json_output: bool = False,
            models: list[str] | tuple[str, ...] = DEFAULT_MODELS,
            base: str | None = None, post: Post | None = None) -> Any:
    """Return the reply text, or a parsed object when json_output=True."""
    body = _request_body(prompt, system, json_output)
    _, content = generate(lambda _model: body, models=models, base=base, post=post)
    text = reply_text(content)
    return json.loads(text) if json_output else text


def generate(body_for: BodyFor, *, models: list[str] | tuple[str, ...] = DEFAULT_MODELS,
             base: str | None = None, post: Post | None = None,
             rounds: int = ROUNDS, pause_s: float = PAUSE_S, timeout_s: float = TIMEOUT_S,
             health: ModelHealth | None = None,
             sleep: Callable[[float], None] = time.sleep) -> tuple[str, dict]:
    """Call generateContent and return (model, content) from the first model that answers.

    `body_for(model)` builds the request body, so a caller can adapt it per model
    (the builder rewrites thought signatures that came from another model).
    """
    base = base or gateway.base_url("gemini")
    post = post or _post
    health = health or HEALTH
    last: GatewayError | None = None
    for round_no in range(rounds):
        if round_no:
            sleep(pause_s * round_no)
        for model in health.order(models):
            url = f"{base}/v1beta/models/{model}:generateContent"
            try:
                content = _content(post(url, body_for(model), timeout_s))
            except GatewayError as err:
                if err.status not in RETRY_STATUSES:
                    raise LLMError(f"{model}: {err.message}") from err
                last = err
                health.failed(model)
                print(f"errand: {model} failed ({err}), trying the next model", file=sys.stderr)
                continue
            health.worked(model)
            return model, content
    raise LLMError(f"every model failed; last error: {last}")


def reply_text(content: dict) -> str:
    """Join the text parts of a reply, without the model's thought parts."""
    text = "".join(p.get("text", "") for p in content["parts"] if not p.get("thought"))
    return text.strip()


def _post(url: str, body: dict, timeout: float) -> dict:
    return gateway.request_json("POST", url, body, timeout=timeout)


def _request_body(prompt: str, system: str | None, json_output: bool) -> dict:
    body: dict[str, Any] = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if json_output:
        body["generationConfig"] = {"responseMimeType": "application/json"}
    return body


def _content(reply: dict) -> dict:
    try:
        content = reply["candidates"][0]["content"]
        parts = content["parts"]
    except (KeyError, IndexError, TypeError) as err:
        raise GatewayError(502, f"reply has no content: {str(reply)[:300]}") from err
    if not any(p.get("functionCall") or (p.get("text") or "").strip()
               for p in parts if not p.get("thought")):
        raise GatewayError(502, "reply was empty")
    return content
