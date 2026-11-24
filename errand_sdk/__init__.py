"""Helpers for automation scripts. Every external call goes through a Sprites connector
or a plain public fetch; scripts never hold an API key.

    from errand_sdk import ask_llm, send_email, call, fetch_json, load_state, save_state, event
"""

from .connectors import call, connected_services
from .gateway import GatewayError
from .llm import LLMError, ask_llm
from .notify import NotifyError, send_email
from .runtime import is_dry_run
from .state import event, load_state, save_state
from .web import fetch_json, fetch_text

__all__ = [
    "GatewayError",
    "LLMError",
    "NotifyError",
    "ask_llm",
    "call",
    "connected_services",
    "event",
    "fetch_json",
    "fetch_text",
    "is_dry_run",
    "load_state",
    "save_state",
    "send_email",
]
