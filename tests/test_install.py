import subprocess

import pytest

from errand_server.install import install


@pytest.fixture
def source(tmp_path):
    folder = tmp_path / "src"
    folder.mkdir()
    (folder / "main.py").write_text("print('v1')\n")
    return folder


def git_log(folder):
    out = subprocess.run(["git", "log", "--format=%s"], cwd=folder, capture_output=True, text=True)
    return out.stdout.split("\n")[:-1]


def test_install_schedule_sets_the_first_run(repo, paths, source):
    a = install(repo, paths, source, automation_id="digest", name="Digest",
                trigger="schedule", schedule="every 1h")
    assert a.next_run_at is not None and a.hook_id is None
    assert (paths.automation_dir("digest") / "main.py").read_text() == "print('v1')\n"
    assert git_log(paths.automation_dir("digest")) == ["install digest"]


def test_reinstall_keeps_state_hook_and_history(repo, paths, source):
    first = install(repo, paths, source, automation_id="gh", name="GH", trigger="webhook")
    target = paths.automation_dir("gh")
    (target / "state.json").write_text('{"n": 1}')
    (source / "main.py").write_text("print('v2')\n")
    second = install(repo, paths, source, automation_id="gh", name="GH", trigger="webhook")
    assert second.hook_id == first.hook_id
    assert (target / "state.json").read_text() == '{"n": 1}'
    assert (target / "main.py").read_text() == "print('v2')\n"
    assert git_log(target) == ["update gh", "install gh"]


@pytest.mark.parametrize(
    "kwargs, message",
    [
        (dict(automation_id="../x", trigger="manual"), "invalid automation id"),
        (dict(automation_id="ok", trigger="schedule"), "needs a schedule"),
        (dict(automation_id="ok", trigger="cron"), "unknown trigger"),
        (dict(automation_id="ok", trigger="manual", timeout_s=0), "timeout"),
    ],
)
def test_install_rejects_bad_input(repo, paths, source, kwargs, message):
    with pytest.raises(ValueError, match=message):
        install(repo, paths, source, name="x", **kwargs)
    assert repo.list_automations() == []


def test_install_needs_main_py(repo, paths, tmp_path):
    with pytest.raises(ValueError, match="main.py"):
        install(repo, paths, tmp_path, automation_id="x", name="x", trigger="manual")


def test_versions_and_roll_back(repo, paths, source):
    from errand_server import history

    install(repo, paths, source, automation_id="d", name="D", trigger="manual")
    (source / "main.py").write_text("print('v2')\n")
    (source / "extra.py").write_text("X = 1\n")
    install(repo, paths, source, automation_id="d", name="D", trigger="manual")
    folder = paths.automation_dir("d")
    (folder / "state.json").write_text('{"keep": true}')
    first, second = history.versions(folder)[::-1]
    assert [first["message"], second["message"]] == ["install d", "update d"]

    history.roll_back(folder, first["sha"][:7])
    assert (folder / "main.py").read_text() == "print('v1')\n"
    assert not (folder / "extra.py").exists()
    assert (folder / "state.json").read_text() == '{"keep": true}'
    assert history.versions(folder)[0]["message"] == f"roll back to {first['sha'][:7]}"
    with pytest.raises(ValueError, match="current one"):
        history.roll_back(folder, first["sha"])
    with pytest.raises(ValueError):
        history.roll_back(folder, "not-a-sha")
    assert history.versions(paths.automation_dir("missing")) == []
