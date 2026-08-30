from pathlib import Path
from types import SimpleNamespace
import tomllib

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


def add_web_frontend(root: Path) -> None:
    build = root / "build"
    build.mkdir()
    (build / "package.json").write_text(
        '{"scripts":{"test":"jest","typecheck":"tsc --noEmit",'
        '"build:web":"webpack --mode production"}}\n'
    )


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


def test_deploy_init_configures_backend_and_web_targets(tmp_path):
    root = create_project(tmp_path)
    add_web_frontend(root)

    assert deployment.initialize_deployment(root, "render")

    with (root / "onramp.toml").open("rb") as config_file:
        config = tomllib.load(config_file)["deploy"]
    assert config["default_targets"] == ["backend", "web"]
    assert config["targets"]["backend"]["components"] == ["backend"]
    assert config["targets"]["web"]["components"] == ["web"]
    blueprint = (root / "render.yaml").read_text()
    assert "runtime: docker" in blueprint
    assert "runtime: static" in blueprint
    assert "rootDir: build" in blueprint
    assert "staticPublishPath: ./dist" in blueprint
    assert ".onramp/deploy-state.json" in (root / ".gitignore").read_text()


def test_deploy_init_supports_a_web_only_project(tmp_path):
    root = tmp_path
    (root / ".gitignore").write_text("")
    add_web_frontend(root)

    assert deployment.initialize_deployment(root, "render")

    assert not (root / "Dockerfile").exists()
    assert not (root / ".dockerignore").exists()
    blueprint = (root / "render.yaml").read_text()
    assert "runtime: static" in blueprint
    assert "runtime: docker" not in blueprint
    assert "databases:" not in blueprint


def test_interactive_target_selection_remembers_the_previous_choice(tmp_path):
    root = create_project(tmp_path)
    add_web_frontend(root)
    deployment.initialize_deployment(root, "container")
    config = deployment.load_deployment_config(root)

    assert deployment.select_deployment_targets(
        root,
        config,
        interactive=True,
        input_fn=lambda _prompt: "1",
        remember=True,
    ) == ["backend"]
    assert deployment.select_deployment_targets(
        root,
        config,
        interactive=True,
        input_fn=lambda _prompt: "",
    ) == ["backend"]
    assert '"backend"' in (root / deployment.DEPLOY_STATE).read_text()


def test_noninteractive_selection_uses_committed_defaults(tmp_path):
    root = create_project(tmp_path)
    add_web_frontend(root)
    deployment.initialize_deployment(root, "container")
    config = deployment.load_deployment_config(root)

    assert deployment.select_deployment_targets(
        root,
        config,
        interactive=False,
    ) == ["backend", "web"]

    config.pop("default_targets")
    assert (
        deployment.select_deployment_targets(root, config, interactive=False)
        is None
    )


def test_legacy_config_remains_backend_only(tmp_path):
    root = create_project(tmp_path)
    add_web_frontend(root)
    config = {
        "provider": "container",
        "environment": "production",
        "image": "legacy-api",
    }

    targets = deployment.deployment_targets(root, config)

    assert list(targets) == ["backend"]
    assert targets["backend"]["image"] == "legacy-api"


def test_combined_container_is_one_full_application_target(tmp_path):
    root = create_project(tmp_path)
    add_web_frontend(root)
    config = {
        "environment": "production",
        "provider": "container",
        "default_targets": ["app"],
        "targets": {
            "app": {
                "kind": "container",
                "components": ["backend", "web"],
                "dockerfile": "Dockerfile",
            }
        },
    }

    assert deployment.select_deployment_targets(
        root,
        config,
        interactive=True,
        input_fn=lambda _prompt: (_ for _ in ()).throw(AssertionError("prompted")),
    ) == ["app"]


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


def test_deploy_check_accepts_explicit_staging_environment(tmp_path, capsys):
    root = create_project(tmp_path)
    deployment.initialize_deployment(root, "render")

    assert deployment.check_deployment(root, environment_override="staging")

    assert "environment: staging" in capsys.readouterr().out


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


def test_web_only_check_does_not_require_backend_migrations(tmp_path):
    root = create_project(tmp_path)
    add_web_frontend(root)
    deployment.initialize_deployment(root, "render")
    for migration in (root / "app" / "db" / "migrations").glob("*.py"):
        migration.unlink()

    assert deployment.check_deployment(
        root,
        target_names=["web"],
        interactive=False,
    )


def test_interactive_check_does_not_write_last_selection(tmp_path, monkeypatch):
    root = create_project(tmp_path)
    add_web_frontend(root)
    deployment.initialize_deployment(root, "render")
    monkeypatch.setattr("builtins.input", lambda _prompt: "2")

    assert deployment.check_deployment(root, interactive=True)
    assert not (root / deployment.DEPLOY_STATE).exists()


def test_both_target_checks_include_backend_tests_and_web_builds(
    tmp_path,
    monkeypatch,
):
    root = create_project(tmp_path)
    add_web_frontend(root)
    deployment.initialize_deployment(root, "container")
    config = deployment.load_deployment_config(root)
    targets = deployment.deployment_targets(root, config)
    commands = []

    def run(command, **kwargs):
        commands.append((command, kwargs["cwd"]))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(deployment.subprocess, "run", run)

    assert deployment._run_project_checks(root, config, targets)
    assert commands == [
        ([deployment.sys.executable, "-m", "compileall", "-q", "app"], root),
        (["npm", "test"], root / "build"),
        (["npm", "run", "typecheck"], root / "build"),
        (["npm", "run", "build:web"], root / "build"),
    ]


def test_both_targets_deploy_backend_before_web(tmp_path, monkeypatch):
    root = create_project(tmp_path)
    add_web_frontend(root)
    deployment.initialize_deployment(root, "render")
    calls = []

    monkeypatch.setattr("builtins.input", lambda _prompt: "3")
    monkeypatch.setattr(
        deployment,
        "check_deployment",
        lambda *_args, **kwargs: calls.append(
            ("check", tuple(kwargs["target_names"]))
        )
        or True,
    )
    monkeypatch.setattr(
        deployment,
        "_run_project_checks",
        lambda *_args: calls.append(("tests",)) or True,
    )
    monkeypatch.setattr(
        deployment,
        "_build_target_artifacts",
        lambda *_args: calls.append(("build",)) or True,
    )
    monkeypatch.setattr(deployment, "_git_is_clean", lambda _root: True)
    monkeypatch.setattr(
        deployment.shutil,
        "which",
        lambda command: "/usr/local/bin/render" if command == "render" else None,
    )
    monkeypatch.setenv("ONRAMP_RENDER_BACKEND_SERVICE", "srv-backend")
    monkeypatch.setenv("ONRAMP_RENDER_WEB_SERVICE", "srv-web")

    def run(command, **_kwargs):
        calls.append(tuple(command))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(deployment.subprocess, "run", run)

    assert deployment.deploy_project(root, interactive=True)
    assert calls == [
        ("check", ("backend", "web")),
        ("tests",),
        ("build",),
        ("/usr/local/bin/render", "blueprints", "validate", "render.yaml"),
        (
            "/usr/local/bin/render",
            "deploys",
            "create",
            "srv-backend",
            "--wait",
        ),
        ("/usr/local/bin/render", "deploys", "create", "srv-web", "--wait"),
    ]


def test_staging_deploy_uses_environment_services_and_restores_process_env(
    tmp_path, monkeypatch
):
    root = create_project(tmp_path)
    deployment.initialize_deployment(root, "render")
    seen_environments = []
    commands = []

    monkeypatch.delenv("ONRAMP_ENVIRONMENT", raising=False)
    monkeypatch.setenv("ONRAMP_RENDER_STAGING_BACKEND_SERVICE", "srv-staging")
    monkeypatch.setattr(
        deployment,
        "_run_project_checks",
        lambda *_args: seen_environments.append(
            deployment.os.environ.get("ONRAMP_ENVIRONMENT")
        )
        or True,
    )
    monkeypatch.setattr(deployment, "_build_target_artifacts", lambda *_args: True)
    monkeypatch.setattr(deployment, "_git_is_clean", lambda _root: True)
    monkeypatch.setattr(
        deployment.shutil,
        "which",
        lambda command: "/usr/local/bin/render" if command == "render" else None,
    )

    def run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(deployment.subprocess, "run", run)

    assert deployment.deploy_project(
        root,
        environment_override="staging",
        interactive=False,
    )
    assert seen_environments == ["staging"]
    assert commands[-1] == [
        "/usr/local/bin/render",
        "deploys",
        "create",
        "srv-staging",
        "--wait",
    ]
    assert "ONRAMP_ENVIRONMENT" not in deployment.os.environ


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
