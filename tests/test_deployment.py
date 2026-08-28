from pathlib import Path
from types import SimpleNamespace

from onramp import deployment


def create_project(tmp_path: Path) -> Path:
    app = tmp_path / "app"
    migrations = app / "db" / "migrations"
    migrations.mkdir(parents=True)
    (app / "settings.py").write_text(
        "ENVIRONMENT = 'development'\n"
        "DATABASE = {'engine': 'sqlite', 'name': 'db.sqlite3'}\n"
    )
    (migrations / "0001_initial.py").write_text(
        "from tortoise.migrations import operations as ops\n\n"
        "operations = [ops.CreateModel(name='Example', fields=[])]\n"
    )
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'example'\nversion = '0.1.0'\n"
    )
    (tmp_path / "README.md").write_text("# Example\n")
    (tmp_path / ".gitignore").write_text(".venv/\n")
    return tmp_path


def test_deploy_init_creates_portable_and_render_files(tmp_path):
    root = create_project(tmp_path)

    assert deployment.initialize_deployment(root, "render")

    assert "CMD [\"onramp\", \"start\"]" in (root / "Dockerfile").read_text()
    assert "provider = \"render\"" in (root / "onramp.toml").read_text()
    blueprint = (root / "render.yaml").read_text()
    assert "preDeployCommand: onramp db upgrade" in blueprint
    assert "healthCheckPath: /health/ready" in blueprint
    assert "fromDatabase:" in blueprint
    assert ".env" in (root / ".gitignore").read_text()
    assert "password@host" in (root / ".env.example").read_text()


def test_deploy_init_never_overwrites_existing_files(tmp_path):
    root = create_project(tmp_path)
    (root / "Dockerfile").write_text("user-owned\n")

    assert deployment.initialize_deployment(root, "container")

    assert (root / "Dockerfile").read_text() == "user-owned\n"
    assert not (root / "render.yaml").exists()


def test_deploy_check_accepts_render_managed_database(tmp_path, capsys):
    root = create_project(tmp_path)
    deployment.initialize_deployment(root, "render")

    assert deployment.check_deployment(root)

    output = capsys.readouterr().out
    assert "Deployment check passed" in output
    assert "Render will inject DATABASE_URL" in output


def test_deploy_check_reports_missing_render_blueprint(tmp_path, capsys):
    root = create_project(tmp_path)
    deployment.initialize_deployment(root, "render")
    (root / "render.yaml").unlink()

    assert not deployment.check_deployment(root)

    assert "missing render.yaml" in capsys.readouterr().out


def test_deploy_check_rejects_committed_database_password(tmp_path, capsys):
    root = create_project(tmp_path)
    deployment.initialize_deployment(root, "render")
    (root / "app" / "settings.py").write_text(
        "DATABASE = {'engine': 'postgresql', 'password': 'secret'}\n"
    )

    assert not deployment.check_deployment(root)

    assert "contains a database password" in capsys.readouterr().out


def test_deploy_check_accepts_portable_migrations_and_flags_raw_sql(tmp_path, capsys):
    root = create_project(tmp_path)
    deployment.initialize_deployment(root, "render")
    migration = next((root / "app" / "db" / "migrations").glob("*.py"))
    migration.write_text(
        "from tortoise.migrations import operations as ops\n\n"
        "operations = [ops.RunSQL('CREATE INDEX example_idx ON example (id)')]\n"
    )

    assert deployment.check_deployment(root)

    output = capsys.readouterr().out
    assert "review database portability for RunSQL migrations" in output


def test_container_deploy_checks_and_builds_image(tmp_path, monkeypatch):
    root = create_project(tmp_path)
    deployment.initialize_deployment(root, "container")
    commands = []

    monkeypatch.setattr(deployment, "_run_project_checks", lambda *_args: True)
    monkeypatch.setattr(
        deployment.shutil,
        "which",
        lambda command: "/usr/local/bin/docker" if command == "docker" else None,
    )

    def run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(deployment.subprocess, "run", run)

    assert deployment.deploy_project(root)
    assert commands == [
        ["docker", "build", "--tag", f"{deployment._slug(root.name)}-api", "."]
    ]
