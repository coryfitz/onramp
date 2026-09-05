import json
from pathlib import Path
from types import SimpleNamespace

import pytest

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
    monkeypatch.setattr(
        frontend,
        "_frontend_package_version",
        lambda: "9.8.7",
    )
    monkeypatch.setattr(
        frontend,
        "_frontend_exec_prefix",
        lambda: Path("/isolated/onramp-npm-exec"),
    )

    assert frontend._frontend_command(["--version"]) == [
        "npm",
        "exec",
        "--yes",
        "--prefix",
        "/isolated/onramp-npm-exec",
        "--package",
        "onramp-js@9.8.7",
        "--",
        "onramp-js",
        "--version",
    ]


def test_installed_frontend_command_isolated_from_project_dependencies(
    tmp_path,
    monkeypatch,
):
    project = tmp_path / "project"
    project_local_bin = project / "node_modules" / ".bin" / "onramp-js"
    project_local_bin.parent.mkdir(parents=True)
    project_local_bin.write_text("old project-local frontend\n")
    isolated_prefix = tmp_path / "isolated-prefix"

    monkeypatch.setattr(
        frontend,
        "_local_frontend_bin",
        lambda: Path("/missing/onramp-js.js"),
    )
    monkeypatch.setattr(
        frontend,
        "_frontend_package_version",
        lambda: "9.8.7",
    )
    monkeypatch.setattr(
        frontend,
        "_frontend_exec_prefix",
        lambda: isolated_prefix,
    )

    command = frontend._frontend_command(["upgrade", "--output", str(project)])

    assert str(project_local_bin) not in command
    assert command[3:7] == [
        "--prefix",
        str(isolated_prefix),
        "--package",
        "onramp-js@9.8.7",
    ]
    assert command[7:10] == ["--", "onramp-js", "upgrade"]


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


def test_run_frontend_forwards_rebuild(tmp_path, monkeypatch):
    captured = {}

    def fake_run(command, cwd, env, action):
        captured.update(command=command, cwd=cwd, env=env, action=action)
        return True

    monkeypatch.setattr(frontend, "_run_frontend_command", fake_run)

    assert frontend.run_frontend("android", tmp_path, rebuild=True)
    assert captured["command"][-1] == "--rebuild"


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


@pytest.mark.parametrize("has_changes", [True, False])
def test_frontend_check_reads_structured_status_outside_project(
    tmp_path, monkeypatch, has_changes,
):
    original_env = {"PATH": "test-path", "CUSTOM": "preserved"}
    reports = []

    def fake_run(command, cwd, env, action):
        assert command[-1] == "--check"
        assert cwd == tmp_path
        assert env["CUSTOM"] == "preserved"
        report = Path(env["ONRAMP_UPGRADE_CHECK_RESULT"])
        assert not report.is_relative_to(tmp_path)
        reports.append(report)
        report.write_text(json.dumps({
            "schemaVersion": 1, "success": True, "hasChanges": has_changes,
        }))
        return True

    monkeypatch.setattr(frontend, "_run_frontend_command", fake_run)

    assert frontend.check_frontend_upgrade(tmp_path, original_env) == (
        frontend.FrontendUpgradeCheck(True, not has_changes)
    )
    assert original_env == {"PATH": "test-path", "CUSTOM": "preserved"}
    assert all(not report.exists() for report in reports)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("report", [
    None, "invalid json", "[]", "{}",
    '{"schemaVersion":1,"success":true,"hasChanges":"false"}',
    '{"schemaVersion":true,"success":true,"hasChanges":false}',
    '{"schemaVersion":2,"success":true,"hasChanges":false}',
])
def test_frontend_check_does_not_infer_current_state_from_missing_or_invalid_report(
    tmp_path, monkeypatch, report,
):
    def fake_run(command, cwd, env, action):
        if report is not None:
            Path(env["ONRAMP_UPGRADE_CHECK_RESULT"]).write_text(report)
        return True

    monkeypatch.setattr(frontend, "_run_frontend_command", fake_run)
    assert frontend.check_frontend_upgrade(tmp_path) == frontend.FrontendUpgradeCheck(True)


@pytest.mark.parametrize("process_success", [True, False])
def test_frontend_check_reports_failure_even_if_other_status_indicates_success(
    tmp_path, monkeypatch, process_success,
):
    def fake_run(command, cwd, env, action):
        Path(env["ONRAMP_UPGRADE_CHECK_RESULT"]).write_text(json.dumps({
            "schemaVersion": 1, "success": not process_success, "hasChanges": False,
        }))
        return process_success

    monkeypatch.setattr(frontend, "_run_frontend_command", fake_run)
    assert frontend.check_frontend_upgrade(tmp_path) == frontend.FrontendUpgradeCheck(False)
