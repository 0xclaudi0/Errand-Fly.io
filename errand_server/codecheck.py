"""Static checks on code that the builder agent writes.

This is a guard rail for an honest model, not a sandbox. It keeps every
outside call inside the SDK, so the dry run can stop actions that send or
change things. The Sprite network policy (day 5) is the real wall.
"""

from __future__ import annotations

import ast
import re

FILE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,40}\.py$")
MAX_FILES = 6
MAX_FILE_BYTES = 100_000

BLOCKED_MODULES = {
    "subprocess": "scripts cannot start programs",
    "socket": "use the errand_sdk helpers for network calls",
    "ssl": "use the errand_sdk helpers for network calls",
    "http": "use fetch_text/fetch_json or call() from errand_sdk",
    "urllib.request": "use fetch_text/fetch_json or call() from errand_sdk",
    "requests": "use fetch_text/fetch_json or call() from errand_sdk",
    "httpx": "use fetch_text/fetch_json or call() from errand_sdk",
    "aiohttp": "use fetch_text/fetch_json or call() from errand_sdk",
    "smtplib": "use send_email() from errand_sdk",
    "ctypes": "not allowed",
    "multiprocessing": "not allowed",
    "pty": "not allowed",
    "shutil": "not allowed",
}
BLOCKED_CALLS = {"eval", "exec", "compile", "__import__"}
BLOCKED_OS_ATTRS = {"system", "popen", "spawnl", "spawnv", "execv", "execl", "fork",
                    "remove", "unlink", "rmdir", "removedirs", "kill"}


def check_file_name(name: str) -> str | None:
    if not FILE_NAME.fullmatch(name):
        return "file names must look like main.py or helpers.py (lowercase, no folders)"
    return None


def check_code(name: str, source: str) -> list[str]:
    """Return a list of problems. An empty list means the file is acceptable."""
    if len(source.encode()) > MAX_FILE_BYTES:
        return [f"{name} is larger than {MAX_FILE_BYTES} bytes"]
    try:
        tree = ast.parse(source, filename=name)
    except SyntaxError as err:
        return [f"{name}:{err.lineno}: syntax error: {err.msg}"]
    return [f"{name}:{line}: {text}" for line, text in _walk(tree)]


def _walk(tree: ast.AST) -> list[tuple[int, str]]:
    problems: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        for module in _imported_modules(node):
            reason = _blocked(module)
            if reason:
                problems.append((node.lineno, f"import of {module} is blocked: {reason}"))
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in BLOCKED_CALLS:
                problems.append((node.lineno, f"{func.id}() is blocked"))
            if (isinstance(func, ast.Attribute) and func.attr in BLOCKED_OS_ATTRS
                    and isinstance(func.value, ast.Name) and func.value.id == "os"):
                problems.append((node.lineno, f"os.{func.attr}() is blocked"))
    return problems


def _imported_modules(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module and not node.level:
        return [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
    return []


def _blocked(module: str) -> str | None:
    for blocked, reason in BLOCKED_MODULES.items():
        if module == blocked or module.startswith(blocked + "."):
            return reason
    return None
