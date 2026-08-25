import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import tomllib
from types import SimpleNamespace
from urllib.error import URLError
from urllib.request import Request, urlopen

import pytest

from onramp.db import manager as db_manager_module


def create_app_dir(tmp_path, settings=""):
    app_dir = tmp_path / "app"
    (app_dir / "api").mkdir(parents=True)
    (app_dir / "models").mkdir()
    (app_dir / "__init__.py").write_text("")
    (app_dir / "api" / "__init__.py").write_text("")
    (app_dir / "models" / "__init__.py").write_text("")
    (app_dir / "settings.py").write_text(
        settings
        or (
            "ENVIRONMENT = 'development'\n"
            "AUTO_GENERATE_SCHEMAS = True\n"
            "DATABASE = {'engine': 'sqlite', 'name': 'db.sqlite3'}\n"
        )
    )
    return app_dir


def fake_tortoise(calls, generate_error=None):
    async def init(config):
        calls.append(("init", config))

    async def generate_schemas():
        calls.append(("generate", None))
        if generate_error:
            raise generate_error

    async def close_connections():
        calls.append(("close", None))

    return SimpleNamespace(
        init=init,
        generate_schemas=generate_schemas,
        close_connections=close_connections,
    )


def run_lifespan(lifespan):
    async def exercise():
        async with lifespan(SimpleNamespace()):
            return "running"

    return asyncio.run(exercise())


def test_database_lifespan_initializes_generates_and_closes_in_development(
    tmp_path,
    monkeypatch,
):
    app_dir = create_app_dir(tmp_path)
    calls = []
    monkeypatch.setattr(db_manager_module, "Tortoise", fake_tortoise(calls))
    db_manager_module._db_manager = None

    result = run_lifespan(db_manager_module.database_lifespan(str(app_dir)))

    assert result == "running"
    assert [call[0] for call in calls] == ["init", "generate", "close"]


@pytest.mark.parametrize(
    "settings",
    [
        (
            "ENVIRONMENT = 'production'\n"
            "AUTO_GENERATE_SCHEMAS = True\n"
            "DATABASE = {'engine': 'sqlite', 'name': 'db.sqlite3'}\n"
        ),
        (
            "ENVIRONMENT = 'development'\n"
            "AUTO_GENERATE_SCHEMAS = False\n"
            "DATABASE = {'engine': 'sqlite', 'name': 'db.sqlite3'}\n"
        ),
    ],
)
def test_database_lifespan_skips_schema_generation_outside_opted_in_development(
    tmp_path,
    monkeypatch,
    settings,
):
    app_dir = create_app_dir(tmp_path, settings)
    calls = []
    monkeypatch.setattr(db_manager_module, "Tortoise", fake_tortoise(calls))
    db_manager_module._db_manager = None

    run_lifespan(db_manager_module.database_lifespan(str(app_dir)))

    assert [call[0] for call in calls] == ["init", "close"]


def test_environment_override_disables_automatic_schema_generation(
    tmp_path,
    monkeypatch,
):
    app_dir = create_app_dir(tmp_path)
    calls = []
    monkeypatch.setenv("ONRAMP_ENVIRONMENT", "production")
    monkeypatch.setattr(db_manager_module, "Tortoise", fake_tortoise(calls))
    db_manager_module._db_manager = None

    run_lifespan(db_manager_module.database_lifespan(str(app_dir)))

    assert [call[0] for call in calls] == ["init", "close"]


def test_database_lifespan_closes_after_schema_startup_failure(
    tmp_path,
    monkeypatch,
):
    app_dir = create_app_dir(tmp_path)
    calls = []
    monkeypatch.setattr(
        db_manager_module,
        "Tortoise",
        fake_tortoise(calls, RuntimeError("schema failed")),
    )
    db_manager_module._db_manager = None

    with pytest.raises(RuntimeError, match="schema failed"):
        run_lifespan(db_manager_module.database_lifespan(str(app_dir)))

    assert [call[0] for call in calls] == ["init", "generate", "close"]


def available_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


def test_uvicorn_starts_current_onramp_backend_with_database_lifespan(tmp_path):
    app_dir = create_app_dir(
        tmp_path,
        (
            "ENVIRONMENT = 'development'\n"
            "AUTO_GENERATE_SCHEMAS = True\n"
            "DATABASE = {'engine': 'sqlite', 'name': 'db.sqlite3'}\n"
        ),
    )
    (app_dir / "api" / "index.py").write_text(
        "def get():\n"
        "    \"\"\"Check the backend status.\"\"\"\n"
        "    return {'status': 'ok'}\n"
    )
    port = available_port()
    source_root = Path(__file__).resolve().parents[1] / "src"
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONUNBUFFERED"] = "1"
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(source_root), environment.get("PYTHONPATH")])
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "onramp.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--lifespan",
            "on",
        ],
        cwd=tmp_path,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    response_body = None
    explorer_body = None
    logo_body = None
    openapi_document = None
    output = ""
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                with urlopen(f"http://127.0.0.1:{port}/api", timeout=0.5) as response:
                    response_body = response.read().decode("utf-8")
                    break
            except (OSError, URLError):
                time.sleep(0.1)

        if response_body is not None:
            explorer_request = Request(
                f"http://127.0.0.1:{port}/api",
                headers={"Accept": "text/html"},
            )
            with urlopen(explorer_request, timeout=2) as response:
                assert response.headers.get_content_type() == "text/html"
                explorer_body = response.read().decode("utf-8")
            with urlopen(
                f"http://127.0.0.1:{port}/api/openapi.json",
                timeout=2,
            ) as response:
                openapi_document = json.loads(response.read().decode("utf-8"))
            with urlopen(
                f"http://127.0.0.1:{port}/api/onramp-logo.png",
                timeout=2,
            ) as response:
                assert response.headers.get_content_type() == "image/png"
                logo_body = response.read()
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
        try:
            remaining, _ = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            remaining, _ = process.communicate(timeout=5)
        output += remaining

    if response_body is None:
        pytest.fail(f"Uvicorn did not serve /api:\n{output}")
    assert process.returncode == 0
    assert json.loads(response_body) == {"status": "ok"}
    assert "Explore your API." in explorer_body
    assert "OpenAPI JSON" in explorer_body
    assert logo_body.startswith(b"\x89PNG\r\n\x1a\n")
    assert openapi_document["openapi"] == "3.1.0"
    assert openapi_document["paths"]["/api"]["get"]["summary"] == (
        "Check the backend status."
    )
    assert "Database initialized:" in output
    assert "Database connections closed" in output
    assert "Application startup complete" in output
    assert "AttributeError" not in output
    assert (app_dir / "db" / "db.sqlite3").is_file()


def test_runtime_dependencies_support_starlette_lifespan_without_name_collision():
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject_path.open("rb") as pyproject_file:
        dependencies = tomllib.load(pyproject_file)["project"]["dependencies"]

    assert "starlette>=0.47.3,<2" in dependencies
    assert "tortoise-orm>=1.1.8,<2" in dependencies
    assert not any(
        dependency.split("<", 1)[0].split(">", 1)[0] == "tortoise"
        for dependency in dependencies
    )
