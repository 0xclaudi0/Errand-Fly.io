"""Helpers for automation scripts. Every external call goes through a Sprites connector
or a plain public fetch; scripts never hold an API key.

    from errand_sdk import ask_llm, send_email, fetch_json, load_state, save_state, event
"""

from .llm import LLMError, ask_llm
from .notify import NotifyError, send_email
from .state import event, load_state, save_state
from .web import fetch_json, fetch_text

__all__ = [
    "LLMError",
    "NotifyError",
    "ask_llm",
    "event",
    "fetch_json",
    "fetch_text",
    "load_state",
    "save_state",
    "send_email",
]
