import time
from pathlib import Path

from errand_server.runner import execute


def script(tmp_path: Path, body: str) -> Path:
    folder = tmp_path / "auto"
    folder.mkdir(exist_ok=True)
    (folder / "main.py").write_text(body)
    return folder


def test_success_captures_output(tmp_path):
    outcome = execute(script(tmp_path, "print('hi')"), {}, timeout_s=10)
    assert (outcome.status, outcome.exit_code) == ("ok", 0)
    assert outcome.output.strip() == "hi"


def test_failure_captures_stderr(tmp_path):
    outcome = execute(script(tmp_path, "raise SystemExit('bad thing')"), {}, timeout_s=10)
    assert outcome.status == "error"
    assert outcome.exit_code == 1
    assert "bad thing" in outcome.output


def test_timeout_kills_the_process_group(tmp_path):
    body = (
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print('started', flush=True)\n"
        "time.sleep(60)\n"
    )
    start = time.monotonic()
    outcome = execute(script(tmp_path, body), {}, timeout_s=1)
    assert outcome.status == "timeout"
    assert time.monotonic() - start < 10
    assert "started" in outcome.output


def test_output_keeps_only_the_tail(tmp_path):
    body = "print('A' * 100000); print('END')"
    outcome = execute(script(tmp_path, body), {}, timeout_s=10, max_output=1000)
    assert len(outcome.output) <= 1000 + 100
    assert outcome.output.rstrip().endswith("END")
    assert "truncated" in outcome.output


def test_only_the_given_env_reaches_the_script(tmp_path, monkeypatch):
    monkeypatch.setenv("ERRAND_SECRET_SHOULD_NOT_LEAK", "x")
    body = "import os; print(os.environ.get('ERRAND_SECRET_SHOULD_NOT_LEAK'), os.environ['MINE'])"
    outcome = execute(script(tmp_path, body), {"MINE": "yes"}, timeout_s=10)
    assert outcome.output.strip() == "None yes"


def test_missing_folder_is_an_error_not_a_crash(tmp_path):
    outcome = execute(tmp_path / "missing", {}, timeout_s=5)
    assert outcome.status == "error"
    assert "main.py" in outcome.output
