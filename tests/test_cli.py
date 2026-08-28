import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from onramp import cli
from onramp.db import manager as db_manager_module
from onramp.db import migrations as migrations_module
from onramp.project import package_version


def test_is_port_in_use():
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("localhost", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert cli.is_port_in_use(port)

    assert not cli.is_port_in_use(port)


def test_find_next_available_port(monkeypatch):
    monkeypatch.setattr(cli, "is_port_in_use", lambda port: port in {8000, 8001})

    assert cli.find_next_available_port(8000) == 8002


def test_api_browser_waits_for_backend_and_opens_default_route(monkeypatch):
    opened = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        cli.socket,
        "create_connection",
        lambda address, timeout: Connection(),
    )
    monkeypatch.setattr(cli, "_open_api_url", opened.append)

    cli._open_api_when_ready(
        8123,
        SimpleNamespace(poll=lambda: None),
        timeout=0.1,
    )

    assert cli._api_url(8123) == "http://127.0.0.1:8123/api"
    assert opened == [8123]


def test_enable_backend_updates_setting_and_preserves_the_file(
    tmp_path,
    monkeypatch,
    capsys,
):
    settings_path = tmp_path / "app" / "settings.py"
    settings_path.parent.mkdir()
    settings_path.write_text(
        "from pathlib import Path\n\n"
        "BACKEND: bool = False  # Start only when enabled\n\n"
        "DATABASE = {'engine': 'sqlite'}\n"
    )
    monkeypatch.setattr(cli, "SETTINGS_PATH", str(settings_path))

    assert cli.enable_backend()
    assert settings_path.read_text() == (
        "from pathlib import Path\n\n"
        "BACKEND: bool = True  # Start only when enabled\n\n"
        "DATABASE = {'engine': 'sqlite'}\n"
    )
    assert "Backend enabled" in capsys.readouterr().out


def test_enable_backend_is_idempotent(tmp_path, monkeypatch, capsys):
    settings_path = tmp_path / "app" / "settings.py"
    settings_path.parent.mkdir()
    settings_path.write_text("BACKEND = True\n")
    monkeypatch.setattr(cli, "SETTINGS_PATH", str(settings_path))

    assert cli.enable_backend()
    assert settings_path.read_text() == "BACKEND = True\n"
    assert "already enabled" in capsys.readouterr().out


def test_disable_backend_updates_setting_and_preserves_the_file(
    tmp_path,
    monkeypatch,
    capsys,
):
    settings_path = tmp_path / "app" / "settings.py"
    settings_path.parent.mkdir()
    settings_path.write_text(
        "BACKEND: bool = True  # Start only when enabled\n"
        "DATABASE = {'engine': 'sqlite'}\n"
    )
    monkeypatch.setattr(cli, "SETTINGS_PATH", str(settings_path))

    assert cli.disable_backend()
    assert settings_path.read_text() == (
        "BACKEND: bool = False  # Start only when enabled\n"
        "DATABASE = {'engine': 'sqlite'}\n"
    )
    assert "Backend disabled" in capsys.readouterr().out


def test_disable_backend_is_idempotent(tmp_path, monkeypatch, capsys):
    settings_path = tmp_path / "app" / "settings.py"
    settings_path.parent.mkdir()
    settings_path.write_text("BACKEND = False\n")
    monkeypatch.setattr(cli, "SETTINGS_PATH", str(settings_path))

    assert cli.disable_backend()
    assert settings_path.read_text() == "BACKEND = False\n"
    assert "already disabled" in capsys.readouterr().out


def test_enable_backend_requires_project_settings(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "SETTINGS_PATH", str(tmp_path / "app" / "settings.py"))

    assert not cli.enable_backend()
    assert "project root" in capsys.readouterr().out


def test_project_files_have_real_metadata_and_ignore_native_outputs(tmp_path):
    cli.write_project_files(str(tmp_path), "My Great App")

    pyproject = (tmp_path / "pyproject.toml").read_text()
    gitignore = (tmp_path / ".gitignore").read_text()
    agents = (tmp_path / "AGENTS.md").read_text()

    assert 'name = "my-great-app"' in pyproject
    assert f'"onramp~={package_version()}"' in pyproject
    assert "build/ios/Pods/" in gitignore
    assert "build/" not in {
        line.strip() for line in gitignore.splitlines()
    }
    assert "build/ is the editable" in agents.replace(chr(96), "")


def test_generated_settings_make_committed_migrations_authoritative(tmp_path, monkeypatch):
    target = tmp_path / "api-app"
    monkeypatch.setattr(cli, "init_migrations", lambda _app_dir: True)

    assert cli.create_app_directory(
        "api-app",
        api_only=True,
        directory_path=str(target),
    )

    settings = (target / "app" / "settings.py").read_text()
    assert "ENVIRONMENT = 'development'" in settings
    assert "AUTO_GENERATE_SCHEMAS = False" in settings
    assert "migration history remains authoritative" in settings
    assert (target / "app" / "db" / "db_config.py").is_file()
    pyproject = (target / "pyproject.toml").read_text()
    assert "[tool.tortoise]" in pyproject
    assert "[tool.aerich]" not in pyproject


def test_create_app_directory_accepts_empty_target_and_skips_netlify_for_api(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "api-app"
    target.mkdir()
    monkeypatch.setattr(cli, "init_migrations", lambda _app_dir: True)

    assert cli.create_app_directory(
        "api-app",
        api_only=True,
        directory_path=str(target),
    )
    assert (target / "app" / "api" / "__init__.py").is_file()
    assert (target / "pyproject.toml").is_file()
    assert not (target / "netlify.toml").exists()


def test_create_new_project_keeps_empty_target_when_frontend_fails(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "example"
    target.mkdir()
    monkeypatch.setattr(cli, "PROJECT_ROOT", str(tmp_path))

    def fake_backend(_name, api_only=False, directory_path=None):
        Path(directory_path, "app").mkdir()
        return True

    monkeypatch.setattr(cli, "create_app_directory", fake_backend)
    monkeypatch.setattr(cli, "create_frontend", lambda *args, **kwargs: False)
    monkeypatch.setattr(cli, "ensure_node_env", lambda: {})

    assert not cli.create_new_project("example")
    assert target.is_dir()
    assert list(target.iterdir()) == []
    assert not list(tmp_path.glob(".example-onramp-*"))


def test_create_new_project_publishes_only_after_both_layers_succeed(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(cli, "PROJECT_ROOT", str(tmp_path))

    def fake_backend(_name, api_only=False, directory_path=None):
        Path(directory_path, "app").mkdir()
        Path(directory_path, "backend-ready").write_text("yes")
        return True

    def fake_frontend(_name, output, **_kwargs):
        Path(output).mkdir()
        Path(output, "frontend-ready").write_text("yes")
        return True

    monkeypatch.setattr(cli, "create_app_directory", fake_backend)
    monkeypatch.setattr(cli, "create_frontend", fake_frontend)
    monkeypatch.setattr(cli, "ensure_node_env", lambda: {})

    assert cli.create_new_project("example")
    assert (tmp_path / "example" / "backend-ready").is_file()
    assert (tmp_path / "example" / "build" / "frontend-ready").is_file()
    assert (tmp_path / "example" / ".onramp" / "project.toml").is_file()
    assert not list(tmp_path.glob(".example-onramp-*"))


def test_create_new_project_preserves_initialized_git_repository(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "example"
    git_directory = target / ".git"
    git_directory.mkdir(parents=True)
    (git_directory / "config").write_text("repository metadata")
    monkeypatch.setattr(cli, "PROJECT_ROOT", str(tmp_path))

    def fake_backend(_name, api_only=False, directory_path=None):
        Path(directory_path, "app").mkdir()
        Path(directory_path, "backend-ready").write_text("yes")
        return True

    def fake_frontend(_name, output, **_kwargs):
        Path(output).mkdir()
        Path(output, "frontend-ready").write_text("yes")
        return True

    monkeypatch.setattr(cli, "create_app_directory", fake_backend)
    monkeypatch.setattr(cli, "create_frontend", fake_frontend)
    monkeypatch.setattr(cli, "ensure_node_env", lambda: {})

    assert cli.create_new_project("example")
    assert (target / ".git" / "config").read_text() == "repository metadata"
    assert (target / "backend-ready").is_file()
    assert (target / "build" / "frontend-ready").is_file()


def test_create_new_project_leaves_git_repository_when_frontend_fails(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "example"
    git_directory = target / ".git"
    git_directory.mkdir(parents=True)
    (git_directory / "config").write_text("repository metadata")
    monkeypatch.setattr(cli, "PROJECT_ROOT", str(tmp_path))

    def fake_backend(_name, api_only=False, directory_path=None):
        Path(directory_path, "app").mkdir()
        return True

    monkeypatch.setattr(cli, "create_app_directory", fake_backend)
    monkeypatch.setattr(cli, "create_frontend", lambda *args, **kwargs: False)
    monkeypatch.setattr(cli, "ensure_node_env", lambda: {})

    assert not cli.create_new_project("example")
    assert (target / ".git" / "config").read_text() == "repository metadata"
    assert list(target.iterdir()) == [git_directory]
    assert not list(tmp_path.glob(".example-onramp-*"))


def test_create_new_project_restores_git_repository_when_publish_fails(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "example"
    git_directory = target / ".git"
    git_directory.mkdir(parents=True)
    (git_directory / "config").write_text("repository metadata")
    monkeypatch.setattr(cli, "PROJECT_ROOT", str(tmp_path))

    def fake_backend(_name, api_only=False, directory_path=None):
        Path(directory_path, "app").mkdir()
        return True

    def fake_frontend(_name, output, **_kwargs):
        Path(output).mkdir()
        return True

    original_replace = cli.os.replace

    def fail_publish(source, destination):
        if Path(destination) == target:
            raise OSError("publish failed")
        original_replace(source, destination)

    monkeypatch.setattr(cli, "create_app_directory", fake_backend)
    monkeypatch.setattr(cli, "create_frontend", fake_frontend)
    monkeypatch.setattr(cli, "ensure_node_env", lambda: {})
    monkeypatch.setattr(cli.os, "replace", fail_publish)

    assert not cli.create_new_project("example")
    assert (target / ".git" / "config").read_text() == "repository metadata"
    assert list(target.iterdir()) == [git_directory]
    assert not list(tmp_path.glob(".example-onramp-*"))


def test_create_new_project_refuses_nonempty_target(tmp_path, monkeypatch):
    target = tmp_path / "example"
    target.mkdir()
    (target / "owned.txt").write_text("keep")
    monkeypatch.setattr(cli, "PROJECT_ROOT", str(tmp_path))

    assert not cli.create_new_project("example")
    assert (target / "owned.txt").read_text() == "keep"


def test_main_returns_failure_when_new_project_fails(monkeypatch):
    monkeypatch.setattr(cli, "create_new_project", lambda *args, **kwargs: False)
    monkeypatch.setattr(cli.sys, "argv", ["onramp", "new", "example"])

    assert cli.main() == 1


@pytest.mark.parametrize(
    ("runner", "platform"),
    [
        (lambda: cli.run_web(port=9000), "web"),
        (lambda: cli.run_ios(port=9000), "ios"),
        (lambda: cli.run_android(port=9000), "android"),
        (lambda: cli.run_mobile(port=9000), "mobile"),
    ],
)
def test_frontend_with_backend_requests_api_browser(
    tmp_path,
    monkeypatch,
    runner,
    platform,
):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    process = SimpleNamespace(poll=lambda: None)
    captured = {}

    monkeypatch.setattr(cli, "BUILD_DIR", str(build_dir))
    monkeypatch.setattr(cli, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(cli, "settings", SimpleNamespace(BACKEND=True))
    monkeypatch.setattr(cli, "ensure_node_env", lambda: {"PATH": "test"})
    monkeypatch.setattr(
        cli,
        "start_frontend",
        lambda selected, *_args, **_kwargs: (
            captured.update(platform=selected) or process
        ),
    )
    monkeypatch.setattr(
        cli,
        "run_uvicorn_with_watch",
        lambda port, companion_process=None, open_browser=False: (
            captured.update(
                port=port,
                companion=companion_process,
                open_browser=open_browser,
            )
            or True
        ),
    )
    cli.spawned_processes.clear()

    assert runner()
    assert captured == {
        "platform": platform,
        "port": 9000,
        "companion": process,
        "open_browser": True,
    }
    cli.spawned_processes.clear()


def test_android_coordinates_backend_and_metro_port(tmp_path, monkeypatch):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    process = SimpleNamespace(poll=lambda: None)
    captured = {}

    monkeypatch.setattr(cli, "BUILD_DIR", str(build_dir))
    monkeypatch.setattr(cli, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(cli, "settings", SimpleNamespace(BACKEND=True))
    monkeypatch.setattr(cli, "ensure_node_env", lambda: {"PATH": "test"})

    def fake_start(platform, output, **kwargs):
        captured.update(platform=platform, output=output, **kwargs)
        return process

    monkeypatch.setattr(cli, "start_frontend", fake_start)
    monkeypatch.setattr(
        cli,
        "run_uvicorn_with_watch",
        lambda port, companion_process=None, open_browser=False: (
            captured.update(
                backend_port=port,
                backend_companion=companion_process,
                backend_opens_browser=open_browser,
            )
            or True
        ),
    )
    cli.spawned_processes.clear()

    assert cli.run_android(
        port=9000,
        metro_port=9090,
        watch_diagnostics=True,
    )
    assert captured["platform"] == "android"
    assert captured["metro_port"] == 9090
    assert captured["watch_diagnostics"] is True
    assert captured["backend_port"] == 9000
    assert captured["backend_companion"] is process
    assert captured["backend_opens_browser"] is True
    cli.spawned_processes.clear()


def test_mobile_coordinates_both_apps_with_one_backend(tmp_path, monkeypatch):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    process = SimpleNamespace(poll=lambda: None)
    captured = {}

    monkeypatch.setattr(cli, "BUILD_DIR", str(build_dir))
    monkeypatch.setattr(cli, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(cli, "settings", SimpleNamespace(BACKEND=True))
    monkeypatch.setattr(cli, "ensure_node_env", lambda: {"PATH": "test"})

    def fake_start(platform, output, **kwargs):
        captured.update(platform=platform, output=output, **kwargs)
        return process

    monkeypatch.setattr(cli, "start_frontend", fake_start)
    monkeypatch.setattr(
        cli,
        "run_uvicorn_with_watch",
        lambda port, companion_process=None, open_browser=False: (
            captured.update(
                backend_port=port,
                backend_companion=companion_process,
                backend_opens_browser=open_browser,
            )
            or True
        ),
    )
    cli.spawned_processes.clear()

    assert cli.run_mobile(port=9000, metro_port=9090, rebuild=True)
    assert captured["platform"] == "mobile"
    assert captured["metro_port"] == 9090
    assert captured["rebuild"] is True
    assert captured["backend_port"] == 9000
    assert captured["backend_companion"] is process
    assert captured["backend_opens_browser"] is True
    assert cli.spawned_processes == [process]
    cli.spawned_processes.clear()


def test_backend_watcher_ignores_database_and_directory_changes():
    assert cli._backend_source_filter(None, "/project/app/api/index.py")
    assert not cli._backend_source_filter(None, "/project/app")
    assert not cli._backend_source_filter(None, "/project/app/db/db.sqlite3")
    assert not cli._backend_source_filter(
        None,
        "/project/app/db/db.sqlite3-wal",
    )
    assert not cli._backend_source_filter(
        None,
        "/project/app/__pycache__/index.pyc",
    )


def test_backend_watcher_opens_api_on_selected_fallback_port(
    tmp_path,
    monkeypatch,
):
    class FrontendProcess:
        def __init__(self):
            self.statuses = iter([None, 0])

        def poll(self):
            return next(self.statuses)

    class BackendProcess:
        pid = 1234

        def __init__(self):
            self.terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

    frontend = FrontendProcess()
    backend = BackendProcess()
    scheduled = []

    def fake_watch(*_paths, **_options):
        yield set()

    monkeypatch.setattr(cli, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "is_port_in_use", lambda port: port == 8000)
    monkeypatch.setattr(cli, "find_next_available_port", lambda _port: 8123)
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    monkeypatch.setattr(cli, "_start_uvicorn_worker", lambda *_args: backend)
    monkeypatch.setattr(
        cli,
        "_schedule_api_browser",
        lambda port, process: scheduled.append((port, process)),
    )
    monkeypatch.setattr(cli, "cleanup_processes", lambda: None)
    monkeypatch.setattr(cli, "watch", fake_watch)

    assert cli.run_uvicorn_with_watch(
        8000,
        companion_process=frontend,
        open_browser=True,
    )
    assert scheduled == [(8123, backend)]
    assert backend.terminated


def test_backend_stops_and_fails_when_frontend_process_fails(
    tmp_path,
    monkeypatch,
    capsys,
):
    class FrontendProcess:
        def __init__(self):
            self.statuses = iter([None, 70])

        def poll(self):
            return next(self.statuses)

    class BackendProcess:
        def __init__(self):
            self.pid = 1234
            self.terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

    frontend = FrontendProcess()
    backend = BackendProcess()
    watch_options = {}

    def fake_watch(*paths, **options):
        watch_options.update(paths=paths, **options)
        yield set()

    monkeypatch.setattr(cli, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "is_port_in_use", lambda _port: False)
    monkeypatch.setattr(cli, "_start_uvicorn_worker", lambda *_args: backend)
    monkeypatch.setattr(cli, "cleanup_processes", lambda: None)
    monkeypatch.setattr(cli, "watch", fake_watch)

    assert not cli.run_uvicorn_with_watch(
        8000,
        companion_process=frontend,
    )
    assert backend.terminated
    assert watch_options["yield_on_timeout"] is True
    assert watch_options["rust_timeout"] == 500
    assert watch_options["watch_filter"] is cli._backend_source_filter
    assert "Frontend command failed with status 70; stopping backend." in (
        capsys.readouterr().out
    )


def test_uvicorn_worker_isolated_from_terminal_process_group(
    tmp_path,
    monkeypatch,
):
    captured = {}
    process = SimpleNamespace(pid=1234)

    def fake_popen(command, **options):
        captured.update(command=command, **options)
        return process

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cli, "_uvicorn_cmd", lambda port: ["uvicorn", str(port)])
    cli.spawned_processes.clear()

    assert cli._start_uvicorn_worker(str(tmp_path), 8123) is process
    assert captured["command"] == ["uvicorn", "8123"]
    assert captured["cwd"] == str(tmp_path)
    if os.name == "nt":
        assert captured["creationflags"] == subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert captured["start_new_session"] is True
    assert cli.spawned_processes == [process]
    cli.spawned_processes.clear()


def test_stop_process_escalates_and_reaps_after_timeout():
    calls = []

    class Process:
        pid = 1234

        def poll(self):
            return None

        def terminate(self):
            calls.append("terminate")

        def kill(self):
            calls.append("kill")

        def wait(self, timeout=None):
            calls.append(("wait", timeout))
            if calls.count(("wait", timeout)) == 1:
                raise subprocess.TimeoutExpired("worker", timeout)
            return -signal.SIGKILL

    cli._stop_process(Process(), timeout=0.01)

    assert calls == [
        "terminate",
        ("wait", 0.01),
        "kill",
        ("wait", 0.01),
    ]


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal lifecycle regression")
def test_ctrl_c_reaps_frontend_and_isolated_backend(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    frontend_pid_path = tmp_path / "frontend.pid"
    backend_pid_path = tmp_path / "backend.pid"
    source_root = Path(__file__).resolve().parents[1] / "src"
    child_code = (
        "import os, pathlib, sys, time; "
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
        "time.sleep(60)"
    )
    coordinator_code = f"""
import signal
import subprocess
import sys
from onramp import cli

cli.APP_DIR = {str(app_dir)!r}
cli.spawned_processes.clear()
cli.is_port_in_use = lambda _port: False
cli._uvicorn_cmd = lambda _port: [
    sys.executable,
    "-c",
    {child_code!r},
    {str(backend_pid_path)!r},
]
signal.signal(signal.SIGINT, cli.signal_handler)
frontend = subprocess.Popen([
    sys.executable,
    "-c",
    {child_code!r},
    {str(frontend_pid_path)!r},
])
cli.spawned_processes.append(frontend)
try:
    cli.run_uvicorn_with_watch(8123, companion_process=frontend)
except KeyboardInterrupt:
    raise SystemExit(130)
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(source_root), environment.get("PYTHONPATH")])
    )
    coordinator = subprocess.Popen(
        [sys.executable, "-c", coordinator_code],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        text=True,
    )
    child_pids = []

    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if frontend_pid_path.is_file() and backend_pid_path.is_file():
                break
            if coordinator.poll() is not None:
                break
            time.sleep(0.05)

        assert frontend_pid_path.is_file()
        assert backend_pid_path.is_file()
        child_pids = [
            int(frontend_pid_path.read_text()),
            int(backend_pid_path.read_text()),
        ]

        os.killpg(coordinator.pid, signal.SIGINT)
        output, _ = coordinator.communicate(timeout=10)

        assert coordinator.returncode == 130, output
        for pid in child_pids:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
    finally:
        if coordinator.poll() is None:
            coordinator.kill()
            coordinator.communicate(timeout=5)
        for pid in child_pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_main_dispatches_mobile_command(monkeypatch):
    captured = {}

    def fake_mobile(
        port,
        metro_port=None,
        watch_diagnostics=False,
        rebuild=False,
    ):
        captured.update(
            port=port,
            metro_port=metro_port,
            watch_diagnostics=watch_diagnostics,
            rebuild=rebuild,
        )
        return True

    monkeypatch.setattr(cli, "run_mobile", fake_mobile)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        [
            "onramp",
            "mobile",
            "--port",
            "9000",
            "--metro-port",
            "9090",
            "--watch-diagnostics",
            "--rebuild",
        ],
    )

    assert cli.main() == 0
    assert captured == {
        "port": 9000,
        "metro_port": 9090,
        "watch_diagnostics": True,
        "rebuild": True,
    }


def test_main_dispatches_backend_command(monkeypatch):
    called = []
    monkeypatch.setattr(cli, "enable_backend", lambda: called.append(True) or True)
    monkeypatch.setattr(cli.sys, "argv", ["onramp", "backend"])

    assert cli.main() == 0
    assert called == [True]


def test_main_dispatches_backend_off_command(monkeypatch):
    called = []
    monkeypatch.setattr(cli, "disable_backend", lambda: called.append(True) or True)
    monkeypatch.setattr(cli.sys, "argv", ["onramp", "backend", "off"])

    assert cli.main() == 0
    assert called == [True]


def test_main_rejects_unknown_backend_option(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "argv", ["onramp", "backend", "maybe"])

    assert cli.main() == 2
    assert "Usage: 'onramp backend [off]'" in capsys.readouterr().out


def test_main_dispatches_database_subcommands(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli,
        "create_migration",
        lambda name=None: calls.append(("make", name)) or True,
    )
    monkeypatch.setattr(
        cli,
        "apply_migrations",
        lambda: calls.append(("upgrade", None)) or True,
    )
    monkeypatch.setattr(
        cli,
        "check_migrations",
        lambda: calls.append(("check", None)) or True,
    )

    monkeypatch.setattr(cli.sys, "argv", ["onramp", "db", "make", "requests"])
    assert cli.main() == 0
    monkeypatch.setattr(cli.sys, "argv", ["onramp", "db", "upgrade"])
    assert cli.main() == 0
    monkeypatch.setattr(cli.sys, "argv", ["onramp", "db", "check"])
    assert cli.main() == 0

    assert calls == [
        ("make", "requests"),
        ("upgrade", None),
        ("check", None),
    ]


def test_main_dispatches_deploy_check_flag_and_compatibility_alias(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli,
        "check_deployment",
        lambda root, provider=None: calls.append((root, provider)) or True,
    )

    monkeypatch.setattr(cli.sys, "argv", ["onramp", "deploy", "--check"])
    assert cli.main() == 0
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["onramp", "deploy", "render", "--check"],
    )
    assert cli.main() == 0
    monkeypatch.setattr(cli.sys, "argv", ["onramp", "deploy", "check"])
    assert cli.main() == 0

    assert calls == [
        (cli.PROJECT_ROOT, None),
        (cli.PROJECT_ROOT, "render"),
        (cli.PROJECT_ROOT, None),
    ]


def test_production_server_uses_host_environment_and_proxy_settings(monkeypatch):
    monkeypatch.setenv("PORT", "9123")
    monkeypatch.setenv("ONRAMP_FORWARDED_ALLOW_IPS", "10.0.0.0/8")
    monkeypatch.setenv("ONRAMP_WORKERS", "2")

    command = cli._production_uvicorn_cmd(host="0.0.0.0")

    assert command[command.index("--host") + 1] == "0.0.0.0"
    assert command[command.index("--port") + 1] == "9123"
    assert command[command.index("--forwarded-allow-ips") + 1] == "10.0.0.0/8"
    assert command[command.index("--workers") + 1] == "2"


def test_development_migrate_refuses_to_generate_in_production(
    tmp_path,
    monkeypatch,
    capsys,
):
    app_dir = tmp_path / "app"
    (app_dir / "models").mkdir(parents=True)
    (app_dir / "settings.py").write_text(
        "DATABASE = {'engine': 'sqlite', 'name': 'db.sqlite3'}\n"
    )
    monkeypatch.setenv("ONRAMP_ENVIRONMENT", "production")
    migrations_module._migration_manager = None
    db_manager_module._db_manager = None

    manager = migrations_module.MigrationManager(str(app_dir))

    assert not manager.migrate_with_prep("unsafe")
    assert "onramp db upgrade" in capsys.readouterr().out


def test_initial_database_make_creates_portable_operations(tmp_path):
    app_dir = tmp_path / "app"
    models_dir = app_dir / "models"
    models_dir.mkdir(parents=True)
    (app_dir / "__init__.py").write_text("")
    (models_dir / "__init__.py").write_text("")
    (models_dir / "models.py").write_text(
        "from tortoise import fields, models\n"
        "class Request(models.Model):\n"
        "    id = fields.IntField(primary_key=True)\n"
    )
    (app_dir / "settings.py").write_text(
        "DATABASE = {'engine': 'sqlite', 'name': 'db.sqlite3'}\n"
    )
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'migration-test'\nversion = '0.1.0'\n"
    )
    migrations_module._migration_manager = None
    db_manager_module._db_manager = None
    manager = migrations_module.MigrationManager(str(app_dir))

    assert manager.init_migrations()
    migration_files = list((app_dir / "db" / "migrations").glob("*.py"))
    migration = next(path for path in migration_files if path.name != "__init__.py")
    content = migration.read_text()
    assert "ops.CreateModel" in content
    assert "SERIAL" not in content
    assert "AUTOINCREMENT" not in content
    assert (app_dir / "db" / "db.sqlite3").is_file()


def test_generated_tortoise_config_is_portable_and_replaces_aerich(tmp_path):
    app_dir = tmp_path / "app"
    (app_dir / "db").mkdir(parents=True)
    (app_dir / "models").mkdir()
    (app_dir / "models" / "__init__.py").write_text("")
    (app_dir / "settings.py").write_text(
        "DATABASE = {'engine': 'sqlite', 'name': 'db.sqlite3'}\n"
    )
    (tmp_path / "pyproject.toml").write_text(
        "[tool.aerich]\n"
        'tortoise_orm = "app.db.db_config.TORTOISE_ORM"\n'
    )

    db_manager_module._db_manager = None
    migrations_module._migration_manager = None
    migration_manager = migrations_module.MigrationManager(str(app_dir))
    migration_manager._ensure_tortoise_config()

    config = (app_dir / "db" / "db_config.py").read_text()
    assert "Path(__file__).resolve().parents[1]" in config
    assert str(tmp_path) not in config
    pyproject = (tmp_path / "pyproject.toml").read_text()
    assert "[tool.tortoise]" in pyproject
    assert "[tool.aerich]" not in pyproject


def test_tortoise_setup_preserves_existing_database_config(tmp_path):
    app_dir = tmp_path / "app"
    db_dir = app_dir / "db"
    db_dir.mkdir(parents=True)
    (app_dir / "models").mkdir()
    (app_dir / "models" / "__init__.py").write_text("")
    (app_dir / "settings.py").write_text(
        "DATABASE = {'engine': 'sqlite', 'name': 'db.sqlite3'}\n"
    )
    custom_config = "# Application-owned Tortoise configuration\n"
    (db_dir / "db_config.py").write_text(custom_config)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.aerich]\n"
        'tortoise_orm = "app.db.db_config.TORTOISE_ORM"\n'
    )

    db_manager_module._db_manager = None
    migrations_module._migration_manager = None
    manager = migrations_module.MigrationManager(str(app_dir))
    manager._ensure_tortoise_config()

    assert (db_dir / "db_config.py").read_text() == custom_config
