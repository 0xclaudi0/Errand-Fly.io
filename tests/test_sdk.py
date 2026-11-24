import json

import pytest

from errand_sdk import llm, state
from errand_sdk.gateway import GatewayError


def test_state_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("ERRAND_STATE_FILE", str(tmp_path / "state.json"))
    assert state.load_state({"seen": []}) == {"seen": []}
    state.save_state({"seen": [1, 2]})
    assert state.load_state() == {"seen": [1, 2]}
    assert list(tmp_path.iterdir()) == [tmp_path / "state.json"]


def test_event_reads_the_event_file(tmp_path, monkeypatch):
    path = tmp_path / "event.json"
    path.write_text(json.dumps({"action": "created"}))
    monkeypatch.setenv("ERRAND_EVENT_FILE", str(path))
    assert state.event() == {"action": "created"}
    monkeypatch.delenv("ERRAND_EVENT_FILE")
    assert state.event() is None


def gemini_reply(text):
    return {"candidates": [{"content": {"parts": [{"thought": True, "text": "hmm"}, {"text": text}]}}]}


def test_ask_llm_returns_text_without_thought_parts():
    calls = []

    def post(url, body, timeout):
        calls.append((url, body))
        return gemini_reply("hello")

    out = llm.ask_llm("hi", system="be brief", base="https://gw/gemini/c1", models=["m1"], post=post)
    assert out == "hello"
    url, body = calls[0]
    assert url == "https://gw/gemini/c1/v1beta/models/m1:generateContent"
    assert body["systemInstruction"]["parts"][0]["text"] == "be brief"


def test_ask_llm_falls_back_on_busy_models(capsys):
    tried = []

    def post(url, body, timeout):
        model = url.rsplit("/", 1)[1].split(":")[0]
        tried.append(model)
        if model != "m3":
            raise GatewayError(503 if model == "m1" else 429, "busy")
        return gemini_reply("third time")

    out = llm.ask_llm("hi", base="b", models=["m1", "m2", "m3"], post=post)
    assert out == "third time"
    assert tried == ["m1", "m2", "m3"]
    assert "m1 failed (HTTP 503: busy)" in capsys.readouterr().err


def test_ask_llm_does_not_retry_a_bad_request():
    tried = []

    def post(url, body, timeout):
        tried.append(url)
        raise GatewayError(400, "bad prompt")

    with pytest.raises(llm.LLMError, match="bad prompt"):
        llm.ask_llm("hi", base="b", models=["m1", "m2"], post=post)
    assert len(tried) == 1


def test_ask_llm_json_mode_parses_the_reply():
    def post(url, body, timeout):
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        return gemini_reply('{"ok": true}')

    assert llm.ask_llm("hi", base="b", models=["m"], post=post, json_output=True) == {"ok": True}
