import pytest

from errand_server import settings
from errand_server.auth import Auth, cookie_value, session_cookie


@pytest.fixture
def clock():
    return [1000.0]


@pytest.fixture
def auth(tmp_path, clock):
    return Auth(tmp_path / "auth.json", clock=lambda: clock[0])


def test_a_code_works_once(auth, tmp_path):
    code = auth.new_login_code()
    token = auth.redeem(code)
    assert token and auth.is_session(token)
    assert auth.redeem(code) is None
    assert code not in (tmp_path / "auth.json").read_text()
    assert token not in (tmp_path / "auth.json").read_text()
    assert oct((tmp_path / "auth.json").stat().st_mode & 0o777) == "0o600"


def test_codes_and_sessions_expire(auth, clock):
    code = auth.new_login_code()
    clock[0] += 601
    assert auth.redeem(code) is None
    token = auth.redeem(auth.new_login_code())
    clock[0] += 31 * 24 * 3600
    assert auth.is_session(token) is False


def test_logout_ends_the_session(auth):
    token = auth.redeem(auth.new_login_code())
    auth.end_session(token)
    assert auth.is_session(token) is False
    assert auth.is_session(None) is False and auth.redeem("") is None
    auth.end_session(None)


def test_cookie_helpers():
    assert "HttpOnly" in session_cookie("t") and "SameSite=Strict" in session_cookie("t")
    assert cookie_value("a=1; errand_session=xyz; b=2") == "xyz"
    assert cookie_value("errand_session=") is None and cookie_value(None) is None


@pytest.mark.parametrize("key, value", [
    ("notify_email", "not-an-email"), ("timezone", "Mars/Olympus"), ("timezone", "../etc"),
    ("auto_repair", "maybe"), ("secret", "x"), ("notify_email", 5), ("email_from", "x"),
])
def test_bad_settings_are_refused(tmp_path, key, value):
    with pytest.raises(ValueError):
        settings.update(tmp_path / "s.json", {key: value})
    assert settings.load(tmp_path / "s.json") == {}


def test_good_settings_merge(tmp_path):
    path = tmp_path / "s.json"
    settings.update(path, {"notify_email": "me@example.com"})
    settings.update(path, {"timezone": "Africa/Lagos", "auto_repair": "off",
                           "email_from": "Me <me@example.com>"})
    assert settings.load(path) == {"notify_email": "me@example.com", "timezone": "Africa/Lagos",
                                   "auto_repair": "off", "email_from": "Me <me@example.com>"}
    path.write_text("[1]")
    assert settings.load(path) == {}
