from pathlib import Path
from types import SimpleNamespace

from onramp import frontend


def test_python_bridge_marks_child_environment():
    child = frontend._frontend_env({"PATH": "test"})

    assert child["PATH"] == "test"
    assert child["ONRAMP_PYTHON_WRAPPER"] == "1"


def test_installed_python_package_uses_current_published_frontend(monkeypatch):
    monkeypatch.setattr(
        frontend,
        "_local_frontend_bin",
        lambda: Path("/missing/onramp-js.js"),
    )

    assert frontend._frontend_command(["--version"]) == [
        "npx",
        "--yes",
        "onramp-js@0.5.2",
        "--version",
    ]


def test_run_frontend_forwards_metro_port(tmp_path, monkeypatch):
    captured = {}

    def fake_run(command, cwd, env, action):
        captured.update(command=command, cwd=cwd, env=env, action=action)
        return True

    monkeypatch.setattr(frontend, "_run_frontend_command", fake_run)

    assert frontend.run_frontend(
        "ios",
        tmp_path,
        app_name="Example",
        metro_port=9090,
    )
    assert captured["command"][-2:] == ["--metro-port", "9090"]


def test_run_frontend_forwards_watch_diagnostics(tmp_path, monkeypatch):
    captured = {}

    def fake_run(command, cwd, env, action):
        captured.update(command=command, cwd=cwd, env=env, action=action)
        return True

    monkeypatch.setattr(frontend, "_run_frontend_command", fake_run)

    assert frontend.run_frontend(
        "ios",
        tmp_path,
        watch_diagnostics=True,
    )
    assert captured["command"][-1] == "--watch-diagnostics"


def test_mobile_is_forwarded_through_the_python_bridge(tmp_path, monkeypatch):
    captured = {}

    def fake_run(command, cwd, env, action):
        captured.update(command=command, cwd=cwd, env=env, action=action)
        return True

    monkeypatch.setattr(frontend, "_run_frontend_command", fake_run)

    assert frontend.run_frontend("mobile", tmp_path, app_name="Example")
    assert captured["command"][:2] == ["run", "mobile"]


def test_repair_preserves_lock_unless_fresh_is_explicit(tmp_path, monkeypatch):
    commands = []

    def fake_run(command, cwd, env, action):
        commands.append(command)
        return True

    monkeypatch.setattr(frontend, "_run_frontend_command", fake_run)

    assert frontend.repair_frontend("ios", tmp_path)
    assert "--fresh" not in commands[-1]

    assert frontend.repair_frontend("ios", tmp_path, fresh=True)
    assert commands[-1][-1] == "--fresh"


def test_frontend_failure_is_reported_to_caller(tmp_path, monkeypatch):
    def fail(*_args, **_kwargs):
        raise frontend.subprocess.CalledProcessError(7, ["node"])

    monkeypatch.setattr(frontend.subprocess, "run", fail)
    monkeypatch.setattr(frontend, "_frontend_command", lambda args: ["node", *args])

    assert not frontend.create_frontend("Example", tmp_path / "build")


def test_frontend_upgrade_forwards_non_mutating_mode(tmp_path, monkeypatch):
    commands = []

    def fake_run(command, cwd, env, action):
        commands.append(command)
        return True

    monkeypatch.setattr(frontend, "_run_frontend_command", fake_run)

    assert frontend.upgrade_frontend(tmp_path, check=True)
    assert commands[-1][-1] == "--check"
