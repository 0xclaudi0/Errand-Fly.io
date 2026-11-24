import json

import pytest

from errand_sdk import connectors, gateway, llm, notify
from errand_sdk.gateway import GatewayError
from tests.test_sdk_io import Recorder, fake_gateway, listing  # noqa: F401 (fixture)


def test_dry_run_email_prints_and_sends_nothing(fake_gateway, tmp_path, monkeypatch, capsys):
    Recorder.routes["/list"] = listing(fake_gateway, "resend")
    (tmp_path / "settings.json").write_text(json.dumps({"notify_email": "me@test.dev"}))
    monkeypatch.setenv("ERRAND_DRY_RUN", "1")
    assert notify.send_email("Hi", "Body") == "dry-run"
    out = capsys.readouterr().out
    assert "[dry run] Email was not done." in out and "Subject: Hi" in out
    assert Recorder.seen == []  # a dry run does not even look up the connector


def test_call_sends_reads_and_blocks_writes_in_a_dry_run(fake_gateway, monkeypatch, capsys):
    Recorder.routes["/list"] = listing(fake_gateway, "github")
    Recorder.routes["/github/c-github/repos/a/b?state=open"] = (200, [{"n": 1}])
    Recorder.routes["/github/c-github/repos/a/b/issues"] = (201, {"n": 2})
    assert connectors.call("github", "GET", "/repos/a/b", query={"state": "open"}) == [{"n": 1}]
    assert connectors.call("github", "post", "repos/a/b/issues", {"title": "t"}) == {"n": 2}
    monkeypatch.setenv("ERRAND_DRY_RUN", "1")
    assert connectors.call("github", "POST", "/repos/a/b/issues", {"title": "t"}) == {"dry_run": True}
    assert "[dry run] POST github /repos/a/b/issues was not done." in capsys.readouterr().out
    assert [m for m, _, _, _ in Recorder.seen].count("POST") == 1


def test_providers_lists_unique_names(fake_gateway):
    Recorder.routes["/list"] = listing(fake_gateway, "resend", "gemini", "resend")
    assert gateway.providers() == ["gemini", "resend"]
    assert connectors.connected_services() == ["gemini", "resend"]


def test_generate_tries_a_second_round_after_a_pause():
    tried, pauses = [], []

    def post(url, body, timeout):
        tried.append(url.rsplit("/", 1)[1])
        if len(tried) < 3:
            raise GatewayError(503, "busy")
        return {"candidates": [{"content": {"role": "model", "parts": [{"text": "late"}]}}]}

    model, content = llm.generate(lambda m: {}, models=["m1", "m2"], base="b", post=post,
                                  sleep=pauses.append)
    assert model == "m1" and content["parts"][0]["text"] == "late"
    assert len(tried) == 3 and pauses == [llm.PAUSE_S]


def test_generate_accepts_a_reply_with_only_a_function_call():
    def post(url, body, timeout):
        return {"candidates": [{"content": {"parts": [{"functionCall": {"name": "x"}}]}}]}

    assert llm.generate(lambda m: {}, models=["m"], base="b", post=post)[0] == "m"


def test_generate_treats_an_empty_reply_as_busy():
    replies = [{"candidates": [{"content": {"parts": [{"text": "  "}]}}]},
               {"candidates": []},
               {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}]

    def post(url, body, timeout):
        return replies.pop(0)

    assert llm.generate(lambda m: {}, models=["a", "b", "c"], base="b", post=post)[0] == "c"


def test_a_failed_model_is_tried_last_until_its_cooldown_ends():
    now = [0.0]
    health = llm.ModelHealth(cooldown_s=60, clock=lambda: now[0])
    health.failed("m1")
    assert health.order(["m1", "m2"]) == ["m2", "m1"]
    now[0] = 61
    assert health.order(["m1", "m2"]) == ["m1", "m2"]
    health.failed("m2")
    health.worked("m2")
    assert health.order(["m1", "m2"]) == ["m1", "m2"]


def test_generate_skips_a_cooling_model_on_the_next_call():
    tried = []
    health = llm.ModelHealth()

    def post(url, body, timeout):
        model = url.rsplit("/", 1)[1].split(":")[0]
        tried.append((model, timeout))
        if model == "m1":
            raise GatewayError(0, "timed out")
        return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    for _ in range(2):
        llm.generate(lambda m: {}, models=["m1", "m2"], base="b", post=post, health=health,
                     timeout_s=7)
    assert tried == [("m1", 7), ("m2", 7), ("m2", 7)]
