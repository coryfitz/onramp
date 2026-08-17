from pathlib import Path
from types import SimpleNamespace

from onramp import cli
from onramp.db import manager as db_manager_module
from onramp.db import migrations as migrations_module


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


def test_project_files_have_real_metadata_and_ignore_native_outputs(tmp_path):
    cli.write_project_files(str(tmp_path), "My Great App")

    pyproject = (tmp_path / "pyproject.toml").read_text()
    gitignore = (tmp_path / ".gitignore").read_text()
    agents = (tmp_path / "AGENTS.md").read_text()

    assert 'name = "my-great-app"' in pyproject
    assert '"onramp~=0.4.0"' in pyproject
    assert "build/ios/Pods/" in gitignore
    assert "build/" not in {
        line.strip() for line in gitignore.splitlines()
    }
    assert "build/ is the editable" in agents.replace(chr(96), "")


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
        lambda port: captured.update(backend_port=port),
    )
    cli.spawned_processes.clear()

    assert cli.run_android(port=9000, metro_port=9090)
    assert captured["platform"] == "android"
    assert captured["metro_port"] == 9090
    assert captured["backend_port"] == 9000
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
        lambda port: captured.update(backend_port=port),
    )
    cli.spawned_processes.clear()

    assert cli.run_mobile(port=9000, metro_port=9090)
    assert captured["platform"] == "mobile"
    assert captured["metro_port"] == 9090
    assert captured["backend_port"] == 9000
    assert cli.spawned_processes == [process]
    cli.spawned_processes.clear()


def test_main_dispatches_mobile_command(monkeypatch):
    captured = {}

    def fake_mobile(port, metro_port=None):
        captured.update(port=port, metro_port=metro_port)
        return True

    monkeypatch.setattr(cli, "run_mobile", fake_mobile)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["onramp", "mobile", "--port", "9000", "--metro-port", "9090"],
    )

    assert cli.main() == 0
    assert captured == {"port": 9000, "metro_port": 9090}


def test_generated_aerich_config_is_portable(tmp_path):
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
    migration_manager._ensure_aerich_config()

    config = (app_dir / "db" / "db_config.py").read_text()
    assert "Path(__file__).resolve().parents[1]" in config
    assert str(tmp_path) not in config
