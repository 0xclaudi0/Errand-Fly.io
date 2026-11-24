import pytest

from errand_server.codecheck import MAX_FILE_BYTES, check_code, check_file_name


@pytest.mark.parametrize("name", ["main.py", "helpers.py", "feed_2.py"])
def test_good_file_names(name):
    assert check_file_name(name) is None


@pytest.mark.parametrize("name", ["../x.py", "a/b.py", "Main.py", "main.sh", ".py", "state.json"])
def test_bad_file_names(name):
    assert check_file_name(name)


def test_plain_sdk_script_passes():
    source = "import json\nfrom urllib.parse import quote\nfrom errand_sdk import ask_llm\nprint(1)\n"
    assert check_code("main.py", source) == []


@pytest.mark.parametrize("source, word", [
    ("import subprocess", "subprocess"),
    ("from urllib import request", "urllib.request"),
    ("import urllib.request as r", "urllib.request"),
    ("import http.client", "http.client"),
    ("import requests", "requests"),
    ("from socket import socket", "socket"),
    ("eval('1')", "eval()"),
    ("import os\nos.system('ls')", "os.system()"),
    ("import os\nos.remove('x')", "os.remove()"),
])
def test_blocked_code(source, word):
    problems = check_code("main.py", source)
    assert problems and word in problems[0]


def test_syntax_error_has_the_line():
    assert check_code("main.py", "x = (\n") [0].startswith("main.py:1: syntax error")


def test_large_file_is_refused():
    assert "larger than" in check_code("main.py", "#" * (MAX_FILE_BYTES + 1))[0]
