import json

import pytest

from errand_server import egress
from errand_server.builder import Builder
from errand_server.builder_tools import Toolbox
from errand_server.drafts import DraftStore
from errand_server.runner import RunOutcome
from tests.test_builder import FakeExecute, ScriptedGemini, call, reply
from tests.test_jobs import FakeAwake

DNS = "URLError: <urlopen error [Errno -2] Name or service not known>"


@pytest.fixture
def installed(paths):
    folder = paths.automation_dir("weather")
    folder.mkdir(parents=True)
    (folder / "main.py").write_text(
        'URL = "https://wttr.in/Lagos?format=j1"\n# see http://localhost:8080 and https://example.com\n'
        'API = "https://Hacker-News.firebaseio.com/v0/topstories.json"\n')
    return paths


def test_hosts_come_from_code_and_from_the_owner(installed):
    assert egress.hosts_in_code(installed) == {"wttr.in", "hacker-news.firebaseio.com"}
    egress.allow(installed, "API.GitHub.com")
    egress.allow(installed, "*.python.org")
    assert egress.wanted_hosts(installed) == [
        "*.python.org", "api.github.com", "hacker-news.firebaseio.com", "wttr.in"]
    policy = egress.wanted_policy(installed)
    assert policy["rules"][0] == {"include": "defaults"}
    assert {"domain": "wttr.in", "action": "allow"} in policy["rules"]


@pytest.mark.parametrize("bad", ["not a host", "http://x.com", "localhost", "a..b.com"])
def test_allow_refuses_bad_hosts(paths, bad):
    with pytest.raises(ValueError):
        egress.allow(paths, bad)


def test_status_by_dns_when_no_policy_file_is_mounted(installed):
    open_dns = egress.status(installed, None, resolve=lambda host: True)
    assert open_dns["mode"] == "open"
    allowed = {"hacker-news.firebaseio.com"}
    locked = egress.status(installed, None, resolve=lambda host: host in allowed)
    assert locked == {"mode": "locked", "wanted": ["hacker-news.firebaseio.com", "wttr.in"],
                      "blocked": ["wttr.in"]}


def test_status_open_locked_and_blocked(installed):
    assert egress.status(installed, {"rules": []})["blocked"] == []
    locked = {"rules": [{"include": "defaults"}, {"domain": "*.firebaseio.com", "action": "allow"},
                        {"domain": "wttr.in", "action": "deny"}]}
    result = egress.status(installed, locked)
    assert result == {"mode": "locked", "wanted": ["hacker-news.firebaseio.com", "wttr.in"],
                      "blocked": ["wttr.in"]}
    assert egress.status(installed, {"rules": [{"domain": "*", "action": "allow"}]})["blocked"] == []


def test_active_policy_reads_the_mounted_file(tmp_path):
    path = tmp_path / "network.json"
    assert egress.active_policy(path) is None
    path.write_text(json.dumps({"rules": [{"include": "defaults"}]}))
    assert egress.active_policy(path)["rules"] == [{"include": "defaults"}]


def test_dns_hint_names_the_command():
    assert "scripts/egress.sh allow wttr.in" in egress.dns_hint(DNS, "wttr.in")
    assert egress.dns_hint("Traceback: KeyError") is None


def test_builder_gets_a_hint_when_the_policy_blocks_a_site(repo, paths):
    store = DraftStore(paths)

    def blocked_fetch(url):
        raise OSError(DNS)

    execute = FakeExecute(RunOutcome("error", 1, f"Traceback ...\n{DNS}\n"))
    tools = Toolbox(store, paths, repo, execute=execute, providers=lambda: [], fetch=blocked_fetch)
    gemini = ScriptedGemini(
        reply(call("peek_url", url="https://wttr.in/Lagos")),
        reply(call("write_file", name="main.py", content="print(1)\n")),
        reply(call("dry_run")),
        reply({"text": "Run scripts/egress.sh allow wttr.in, then reply."}),
    )
    builder = Builder(store, paths, repo, tools, post=gemini, models=("m1",),
                      keep_awake=FakeAwake(), background=False, sleep=lambda s: None)
    builder.start("weather")
    peek = gemini.requests[1][1]["contents"][-1]["parts"][0]["functionResponse"]["response"]
    dry = gemini.requests[3][1]["contents"][-1]["parts"][0]["functionResponse"]["response"]
    assert "egress.sh allow wttr.in" in peek["hint"] and "Name or service" in peek["error"]
    assert "egress.sh allow HOST" in dry["hint"]
