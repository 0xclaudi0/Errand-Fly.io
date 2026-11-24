"""ask_llm(): one call to Gemini through the Sprites connector, with model fallback.

Day-1 tests showed that free-tier models fail often (429, 502, 503), so a
busy model is skipped and the next one is tried.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

from . import gateway
from .gateway import GatewayError

# Lite models answer in about 2 s; gemini-3.5-flash thinks and can take 10-60 s.
DEFAULT_MODELS = ("gemini-3.1-flash-lite", "gemini-flash-lite-latest", "gemini-3.5-flash")
RETRY_STATUSES = {0, 429, 500, 502, 503, 504}
TIMEOUT_S = 60

Post = Callable[[str, dict, float], dict]


class LLMError(Exception):
    pass


def ask_llm(prompt: str, *, system: str | None = None, json_output: bool = False,
            models: list[str] | tuple[str, ...] = DEFAULT_MODELS,
            base: str | None = None, post: Post | None = None) -> Any:
    """Return the reply text, or a parsed object when json_output=True."""
    base = base or gateway.base_url("gemini")
    post = post or _post
    body = _request_body(prompt, system, json_output)
    last: GatewayError | None = None
    for model in models:
        url = f"{base}/v1beta/models/{model}:generateContent"
        try:
            text = _reply_text(post(url, body, TIMEOUT_S))
        except GatewayError as err:
            if err.status not in RETRY_STATUSES:
                raise LLMError(f"{model}: {err.message}") from err
            last = err
            print(f"errand: {model} failed ({err}), trying the next model", file=sys.stderr)
            continue
        return json.loads(text) if json_output else text
    raise LLMError(f"every model failed; last error: {last}")


def _post(url: str, body: dict, timeout: float) -> dict:
    return gateway.request_json("POST", url, body, timeout=timeout)


def _request_body(prompt: str, system: str | None, json_output: bool) -> dict:
    body: dict[str, Any] = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if json_output:
        body["generationConfig"] = {"responseMimeType": "application/json"}
    return body


def _reply_text(reply: dict) -> str:
    try:
        parts = reply["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError) as err:
        raise GatewayError(502, f"reply has no content: {str(reply)[:300]}") from err
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text.strip():
        raise GatewayError(502, "reply was empty")
    return text.strip()
