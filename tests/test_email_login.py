import re

import pytest

from errand_server import settings
from tests.test_api_routes import Site, site  # noqa: F401 (fixture)

WEB = {"X-Errand-Client": "web"}


@pytest.fixture
def mailbox(site):
    sent = []
    app_notify = lambda subject, text, to=None: sent.append((subject, text, to))  # noqa: E731
    site.app.notify = app_notify
    return sent


def test_no_email_set_explains_the_fallback(site, mailbox):
    status, body, _ = site.call("POST", "/login/email", {}, WEB)
    assert status == 409 and "Settings" in body["error"] and mailbox == []


def test_the_link_goes_to_the_owner_and_logs_in(site, mailbox, paths):
    settings.update(paths.settings, {"notify_email": "owner@example.net"})
    status, body, _ = site.call("POST", "/login/email", {"to": "attacker@evil.test"},
                                {**WEB, "Host": "evil.test"})
    assert status == 202 and body["data"] == {"sent_to": "o•••@example.net"}
    (subject, text, to), = mailbox
    assert to == "owner@example.net" and subject == "Your Errand login link"
    link = re.search(r"https://e\.sprites\.app/login\?code=\S+", text).group(0)
    status, _, resp = site.call("GET", link.removeprefix("https://e.sprites.app"))
    assert status == 303 and "errand_session=" in resp.getheader("Set-Cookie")

    status, body, _ = site.call("POST", "/login/email", {}, WEB)
    assert status == 429 and "try again in" in body["error"] and len(mailbox) == 1


def test_the_client_header_is_required(site, mailbox, paths):
    settings.update(paths.settings, {"notify_email": "owner@example.net"})
    assert site.call("POST", "/login/email", {})[0] == 403
    assert mailbox == []


def test_a_send_failure_is_502_without_details(site, paths):
    settings.update(paths.settings, {"notify_email": "owner@example.net"})

    def broken(subject, text, to=None):
        raise RuntimeError("resend api key re_secret")

    site.app.notify = broken
    status, body, _ = site.call("POST", "/login/email", {}, WEB)
    assert status == 502 and "re_secret" not in body["error"]


def test_no_known_url_is_503(site, mailbox, paths):
    settings.update(paths.settings, {"notify_email": "owner@example.net"})
    site.app.base_url = lambda: ""
    assert site.call("POST", "/login/email", {}, WEB)[0] == 503 and mailbox == []
