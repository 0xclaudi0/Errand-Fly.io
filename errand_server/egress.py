"""The Sprite network policy that Errand needs.

The Sprite enforces egress with a DNS filter, and the container cannot change its
own policy: an update must come from outside (scripts/egress.sh, with the owner's
org login). So no errand, and no builder agent, can widen its own network access.

Errand only computes what it needs:
    {"include": "defaults"}   platform hosts, the connector gateway, package hosts
  + hosts in the code of installed errands (https://host/... string literals)
  + hosts the owner allowed by hand (setting egress_allow)
"""

from __future__ import annotations

import json
import re
import socket
from pathlib import Path
from typing import Callable

from . import settings
from .config import Paths

ACTIVE_POLICY = Path("/.sprite/policy/network.json")
URL_HOST = re.compile(r"https?://([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
HOST = re.compile(r"^(?:\*\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+$")
IGNORED_HOSTS = {"localhost", "example.com", "www.example.com"}
# No errand uses this host. If it does not resolve, a policy is active. (The Sprite does
# not always mount network.json, and its socket has no route for the policy.)
CANARY_HOST = "www.example.org"
DNS_ERRORS = ("Name or service not known", "Temporary failure in name resolution",
              "nodename nor servname", "No address associated with hostname")


def hosts_in_code(paths: Paths) -> set[str]:
    hosts: set[str] = set()
    for source in paths.automations.glob("*/*.py"):
        for match in URL_HOST.finditer(source.read_text(errors="replace")):
            host = match.group(1).lower()
            if host not in IGNORED_HOSTS:
                hosts.add(host)
    return hosts


def allowed_by_hand(paths: Paths) -> set[str]:
    raw = settings.load(paths.settings).get("egress_allow", "")
    return {h for h in raw.split(",") if h}


def wanted_hosts(paths: Paths) -> list[str]:
    return sorted(hosts_in_code(paths) | allowed_by_hand(paths))


def wanted_policy(paths: Paths) -> dict:
    rules: list[dict] = [{"include": "defaults"}]
    rules += [{"domain": host, "action": "allow"} for host in wanted_hosts(paths)]
    return {"rules": rules}


def allow(paths: Paths, host: str) -> list[str]:
    host = host.strip().lower()
    if not HOST.fullmatch(host):
        raise ValueError(f"{host!r} is not a host name like api.example.com or *.example.com")
    hosts = sorted(allowed_by_hand(paths) | {host})
    settings.update(paths.settings, {"egress_allow": ",".join(hosts)})
    return hosts


def active_policy(path: Path = ACTIVE_POLICY) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, 443)
    except OSError:
        return False
    return True


def status(paths: Paths, active: dict | None,
           resolve: Callable[[str], bool] = resolves) -> dict:
    """What the settings page shows: is egress locked, and what does it still block?"""
    wanted = wanted_hosts(paths)
    if active is None:  # no mounted file: find out by DNS
        if resolve(CANARY_HOST):
            return {"mode": "open", "wanted": wanted, "blocked": []}
        blocked = [h for h in wanted if not h.startswith("*.") and not resolve(h)]
        return {"mode": "locked", "wanted": wanted, "blocked": blocked}
    rules = active.get("rules") or []
    if not rules:
        return {"mode": "open", "wanted": wanted, "blocked": []}
    return {"mode": "locked", "wanted": wanted,
            "blocked": [h for h in wanted if not _allows(rules, h)]}


def dns_hint(text: str, host: str | None = None) -> str | None:
    """A builder hint when a fetch failed because the policy blocks the host."""
    if not any(marker in text for marker in DNS_ERRORS):
        return None
    target = host or "HOST"
    return (f"DNS failed. The Sprite network policy probably blocks {target}. Do not try "
            f"another way around it. Tell the owner to run `scripts/egress.sh allow {target}` "
            "on their computer, then reply 'continue'.")


def _allows(rules: list[dict], host: str) -> bool:
    for rule in rules:
        domain = rule.get("domain")
        if not domain or rule.get("action") != "allow":
            continue
        if domain in ("*", host) or (domain.startswith("*.") and host.endswith(domain[1:])):
            return True
    return False
