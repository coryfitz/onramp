"""Provider-neutral production preparation and deployment helpers."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tomllib

from .db.manager import DatabaseManager
from .project import atomic_write


DEPLOY_CONFIG = Path("onramp.toml")
DEPLOY_STATE = Path(".onramp/deploy-state.json")
SUPPORTED_PROVIDERS = {"container", "render"}
SUPPORTED_TARGET_KINDS = {"container", "static"}


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "onramp-app"


def _write_once(path: Path, content: str) -> bool:
    if path.exists():
        print(f"Leaving existing {path.name} unchanged.")
        return False
    atomic_write(path, content)
    print(f"✓ Created {path.name}")
    return True


def _append_gitignore(root: Path) -> None:
    gitignore = root / ".gitignore"
    content = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
    required = [
        ".env",
        ".env.*",
        "!.env.example",
        DEPLOY_STATE.as_posix(),
    ]
    existing = {line.strip() for line in content.splitlines()}
    missing = [line for line in required if line not in existing]
    if not missing:
        return
    suffix = "\n# Local deployment secrets and state\n" + "\n".join(missing) + "\n"
    atomic_write(gitignore, content.rstrip() + suffix)
    print("✓ Updated .gitignore for local secret files")


def _dockerfile() -> str:
    return """FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system onramp && adduser --system --ingroup onramp onramp

COPY --chown=onramp:onramp pyproject.toml README.md ./
COPY --chown=onramp:onramp app ./app
RUN pip install --no-cache-dir .

USER onramp

CMD ["onramp", "start"]
"""


def _dockerignore() -> str:
    return """.git
.github
.onramp/backups
.venv
__pycache__
*.py[cod]
*.sqlite3
*.sqlite3-*
.env
.env.*
!.env.example
.pytest_cache
.coverage
htmlcov
build/node_modules
build/dist
build/ios
build/android
"""


def _environment_example() -> str:
    return """ONRAMP_ENVIRONMENT=production
# DATABASE_URL=postgresql://user:password@host:5432/database
# ONRAMP_DATABASE_SSL=true
# ONRAMP_DATABASE_POOL_MIN_SIZE=1
# ONRAMP_DATABASE_POOL_MAX_SIZE=5
# ONRAMP_DATABASE_CONNECT_TIMEOUT=10
# ONRAMP_ALLOWED_HOSTS=api.example.com
# ONRAMP_CORS_ALLOWED_ORIGINS=https://example.com
# ONRAMP_FORWARDED_ALLOW_IPS=127.0.0.1
"""


def _project_components(root: Path) -> tuple[bool, bool]:
    has_backend = (root / "app" / "settings.py").is_file()
    package_path = root / "build" / "package.json"
    has_web = False
    if package_path.is_file():
        try:
            package = json.loads(package_path.read_text(encoding="utf-8"))
            has_web = "build:web" in dict(package.get("scripts", {}))
        except (OSError, ValueError, TypeError):
            pass
    return has_backend, has_web


def _deploy_config(
    provider: str,
    image: str,
    *,
    has_backend: bool,
    has_web: bool,
) -> str:
    default_targets = [
        name
        for name, available in (("backend", has_backend), ("web", has_web))
        if available
    ]
    lines = [
        "[deploy]",
        f"provider = {json.dumps(provider)}",
        'environment = "production"',
        f"default_targets = {json.dumps(default_targets)}",
    ]
    if has_backend:
        lines.extend(
            [
                "",
                "[deploy.targets.backend]",
                'kind = "container"',
                'components = ["backend"]',
                f"image = {json.dumps(image)}",
                'dockerfile = "Dockerfile"',
                'context = "."',
                'health_path = "/health/ready"',
                '# render_service = "srv-..."',
                '# test_command = "python -m pytest"',
            ]
        )
    if has_web:
        lines.extend(
            [
                "",
                "[deploy.targets.web]",
                'kind = "static"',
                'components = ["web"]',
                'root = "build"',
                'build_command = "npm ci && npm run build:web"',
                'local_build_command = "npm run build:web"',
                'publish_directory = "dist"',
                '# render_service = "srv-..."',
            ]
        )
    lines.extend(
        [
            "",
            "# A combined container can instead be represented by one target:",
            "# [deploy.targets.app]",
            '# kind = "container"',
            '# components = ["backend", "web"]',
            '# image = "my-app"',
            '# dockerfile = "Dockerfile.fullstack"',
            '# context = "."',
            "",
        ]
    )
    return "\n".join(lines)


def _render_blueprint(name: str, *, has_backend: bool, has_web: bool) -> str:
    database_name = f"{name}-db"
    service_name = f"{name}-api"
    services = "services:\n"
    if has_backend:
        services += f"""  - type: web
    name: {service_name}
    runtime: docker
    plan: starter
    dockerfilePath: ./Dockerfile
    healthCheckPath: /health/ready
    preDeployCommand: onramp db upgrade
    maxShutdownDelaySeconds: 30
    autoDeployTrigger: checksPass
    envVars:
      - key: ONRAMP_ENVIRONMENT
        value: production
      - key: DATABASE_URL
        fromDatabase:
          name: {database_name}
          property: connectionString
"""
    if has_web:
        services += f"""  - type: web
    name: {name}-web
    runtime: static
    rootDir: build
    buildCommand: npm ci && npm run build:web
    staticPublishPath: ./dist
    autoDeployTrigger: checksPass
    routes:
      - type: rewrite
        source: /*
        destination: /index.html
"""
    if has_backend:
        services += f"""
databases:
  - name: {database_name}
    plan: basic-256mb
"""
    return services


def initialize_deployment(project_root: str | Path, provider: str = "render") -> bool:
    """Create portable production files without overwriting application files."""
    root = Path(project_root).resolve()
    provider = provider.strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        print(
            f"Unsupported deployment provider: {provider}. Choose render or container."
        )
        return False
    has_backend, has_web = _project_components(root)
    if not has_backend and not has_web:
        print(
            "No deployable backend or web frontend was found. Run this from "
            "an OnRamp project root."
        )
        return False

    name = _slug(root.name)
    if has_backend:
        _write_once(root / "Dockerfile", _dockerfile())
        _write_once(root / ".dockerignore", _dockerignore())
    _write_once(root / ".env.example", _environment_example())
    _write_once(
        root / DEPLOY_CONFIG,
        _deploy_config(
            provider,
            f"{name}-api",
            has_backend=has_backend,
            has_web=has_web,
        ),
    )
    if provider == "render":
        _write_once(
            root / "render.yaml",
            _render_blueprint(name, has_backend=has_backend, has_web=has_web),
        )
    _append_gitignore(root)

    print("Deployment preparation complete.")
    labels = []
    if has_backend:
        labels.append("backend")
    if has_web:
        labels.append("web frontend")
    print("Configured deployment targets: " + " and ".join(labels) + ".")
    print("Add production secrets in your hosting provider, never in onramp.toml.")
    if provider == "render":
        print(
            "For the first deployment, connect render.yaml as a Blueprint in "
            "Render; later runs can deploy the selected service directly."
        )
    print("Next: onramp deploy --check")
    return True


def load_deployment_config(project_root: str | Path) -> dict | None:
    root = Path(project_root).resolve()
    path = root / DEPLOY_CONFIG
    if not path.is_file():
        return None
    try:
        with path.open("rb") as config_file:
            return dict(tomllib.load(config_file).get("deploy", {}))
    except (OSError, tomllib.TOMLDecodeError) as error:
        print(f"Could not read {DEPLOY_CONFIG}: {error}")
        return None


def deployment_targets(project_root: str | Path, config: dict) -> dict[str, dict]:
    """Return configured targets, inferring the original backend-only format."""
    root = Path(project_root).resolve()
    configured = config.get("targets")
    if isinstance(configured, dict) and configured:
        targets: dict[str, dict] = {}
        for name, value in configured.items():
            if isinstance(value, dict):
                target = dict(value)
                target.setdefault("kind", "container")
                components = target.get("components")
                if not isinstance(components, list) or not components:
                    target["components"] = [name] if name in {"backend", "web"} else []
                targets[str(name)] = target
        return targets

    has_backend, has_web = _project_components(root)
    if has_backend:
        return {
            "backend": {
                "kind": "container",
                "components": ["backend"],
                "image": config.get("image") or f"{_slug(root.name)}-api",
                "dockerfile": "Dockerfile",
                "context": ".",
                "health_path": config.get("health_path") or "/health/ready",
                "render_service": config.get("render_service") or "",
            }
        }
    if has_web:
        return {
            "web": {
                "kind": "static",
                "components": ["web"],
                "root": "build",
                "build_command": "npm ci && npm run build:web",
                "local_build_command": "npm run build:web",
                "publish_directory": "dist",
                "render_service": config.get("render_service") or "",
            }
        }
    return {}


def _target_label(name: str, target: dict) -> str:
    components = set(target.get("components", []))
    if components == {"backend", "web"}:
        return "Full application"
    if components == {"backend"}:
        return "Backend"
    if components == {"web"}:
        return "Web frontend"
    return name.replace("-", " ").replace("_", " ").title()


def _valid_target_names(
    names: object,
    targets: dict[str, dict],
) -> list[str]:
    if not isinstance(names, list):
        return []
    requested = [str(name) for name in names]
    if not requested or any(name not in targets for name in requested):
        return []
    return list(dict.fromkeys(requested))


def _load_last_targets(root: Path, targets: dict[str, dict]) -> list[str]:
    state_path = root / DEPLOY_STATE
    if not state_path.is_file():
        return []
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    return _valid_target_names(state.get("targets"), targets)


def _save_last_targets(root: Path, names: list[str]) -> None:
    state_path = root / DEPLOY_STATE
    state_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(state_path, json.dumps({"targets": names}, indent=2) + "\n")


def _interactive_terminal() -> bool:
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def select_deployment_targets(
    project_root: str | Path,
    config: dict,
    *,
    interactive: bool | None = None,
    input_fn=None,
    remember: bool = False,
) -> list[str] | None:
    """Choose a deployment scope interactively or from committed CI defaults."""
    root = Path(project_root).resolve()
    if input_fn is None:
        input_fn = input
    targets = deployment_targets(root, config)
    if not targets:
        print("No deployment targets are configured in onramp.toml.")
        return None
    if len(targets) == 1:
        names = list(targets)
        print(f"Deployment target: {_target_label(names[0], targets[names[0]])}")
        return names

    configured_default = _valid_target_names(config.get("default_targets"), targets)
    last_targets = _load_last_targets(root, targets)
    suggested = last_targets or configured_default
    is_interactive = _interactive_terminal() if interactive is None else interactive
    if not is_interactive:
        if not configured_default:
            print(
                "Multiple deployment targets are configured, but this terminal "
                "cannot prompt. Set [deploy].default_targets in onramp.toml."
            )
            return None
        labels = ", ".join(_target_label(name, targets[name]) for name in configured_default)
        print(f"Deployment targets from onramp.toml: {labels}")
        return configured_default

    target_names = list(targets)
    backend = next(
        (
            name
            for name in target_names
            if set(targets[name].get("components", [])) == {"backend"}
        ),
        None,
    )
    web = next(
        (
            name
            for name in target_names
            if set(targets[name].get("components", [])) == {"web"}
        ),
        None,
    )
    if backend and web and len(targets) == 2:
        options = [
            ([backend], "Backend"),
            ([web], "Web frontend"),
            ([backend, web], "Both"),
        ]
    else:
        options = [([name], _target_label(name, targets[name])) for name in target_names]
        options.append((target_names, "All configured targets"))

    suggested_set = set(suggested)
    default_index = next(
        (
            index
            for index, (names, _label) in enumerate(options, start=1)
            if set(names) == suggested_set
        ),
        len(options),
    )
    print("What would you like to deploy?")
    for index, (_names, label) in enumerate(options, start=1):
        recommendation = " (recommended)" if len(options) == 3 and index == 3 else ""
        print(f"  {index}. {label}{recommendation}")
    while True:
        try:
            answer = input_fn(f"Selection [{default_index}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("Deployment cancelled.")
            return None
        if not answer:
            choice = default_index
        else:
            try:
                choice = int(answer)
            except ValueError:
                choice = 0
        if 1 <= choice <= len(options):
            names = options[choice - 1][0]
            if remember:
                _save_last_targets(root, names)
            return names
        print(f"Enter a number from 1 to {len(options)}.")


def _tracked_secret_files(root: Path) -> list[str]:
    if not (root / ".git").exists() or not shutil.which("git"):
        return []
    result = subprocess.run(
        ["git", "ls-files", ".env", ".env.*"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() and line.strip() != ".env.example"
    ]


def _migration_files(root: Path) -> list[Path]:
    migrations = root / "app" / "db" / "migrations"
    if not migrations.is_dir():
        return []
    return sorted(
        path
        for path in migrations.glob("*.py")
        if path.name != "__init__.py"
    )


def _raw_sql_migrations(paths: list[Path]) -> list[str]:
    """Find migration modules using explicitly backend-specific SQL operations."""
    raw_sql = []
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            continue
        if any(
            isinstance(node, ast.Call)
            and (
                isinstance(node.func, ast.Name) and node.func.id == "RunSQL"
                or isinstance(node.func, ast.Attribute) and node.func.attr == "RunSQL"
            )
            for node in ast.walk(tree)
        ):
            raw_sql.append(path.name)
    return raw_sql


def check_deployment(
    project_root: str | Path,
    provider_override: str | None = None,
    target_names: list[str] | None = None,
    *,
    interactive: bool | None = None,
) -> bool:
    """Perform a read-only production preflight."""
    root = Path(project_root).resolve()
    config = load_deployment_config(root)
    if config is None:
        print("onramp.toml not found. Run 'onramp deploy init' first.")
        return False

    provider = str(
        provider_override or config.get("provider", "container")
    ).lower()
    targets = deployment_targets(root, config)
    if target_names is None:
        target_names = select_deployment_targets(
            root,
            config,
            interactive=interactive,
            remember=False,
        )
        if target_names is None:
            return False
    elif not target_names or any(name not in targets for name in target_names):
        print("The requested deployment target is not configured in onramp.toml.")
        return False
    selected = {name: targets[name] for name in target_names}
    components = {
        component
        for target in selected.values()
        for component in target.get("components", [])
    }
    failures: list[str] = []
    notices: list[str] = []

    if provider not in SUPPORTED_PROVIDERS:
        failures.append(f"unsupported provider '{provider}'")
    if config.get("environment") != "production":
        failures.append("[deploy].environment must be 'production'")
    if not (root / ".env.example").is_file():
        failures.append("missing .env.example")
    if provider == "render" and not (root / "render.yaml").is_file():
        failures.append("missing render.yaml")

    for name, target in selected.items():
        kind = str(target.get("kind", "")).lower()
        target_components = set(target.get("components", []))
        if kind not in SUPPORTED_TARGET_KINDS:
            failures.append(f"target '{name}' has unsupported kind '{kind}'")
        if not target_components or not target_components <= {"backend", "web"}:
            failures.append(
                f"target '{name}' must declare backend, web, or both components"
            )
        if kind == "static" and "backend" in target_components:
            failures.append(f"target '{name}' cannot deploy a backend as static files")
        if kind == "container":
            dockerfile = str(target.get("dockerfile") or "Dockerfile")
            context = str(target.get("context") or ".")
            if not (root / dockerfile).is_file():
                failures.append(f"target '{name}' is missing {dockerfile}")
            if not (root / context).is_dir():
                failures.append(f"target '{name}' is missing context directory {context}")
            if not (root / ".dockerignore").is_file():
                failures.append("missing .dockerignore")
        if "web" in target_components:
            web_root = root / str(target.get("root") or "build")
            package_path = web_root / "package.json"
            if not package_path.is_file():
                failures.append(f"target '{name}' is missing {package_path.relative_to(root)}")
            else:
                try:
                    package = json.loads(package_path.read_text(encoding="utf-8"))
                    scripts = dict(package.get("scripts", {}))
                except (OSError, ValueError, TypeError):
                    scripts = {}
                    failures.append(
                        f"target '{name}' has an unreadable {package_path.relative_to(root)}"
                    )
                if "build:web" not in scripts:
                    failures.append(
                        f"target '{name}' requires a build:web package script"
                    )

    if "backend" in components:
        settings_path = root / "app" / "settings.py"
        if not settings_path.is_file():
            failures.append("missing app/settings.py")
        else:
            manager = DatabaseManager(str(root / "app"))
            committed_database = dict(manager.settings.get("DATABASE", {}))
            if committed_database.get("password"):
                failures.append(
                    "app/settings.py contains a database password; use DATABASE_URL instead"
                )
    tracked_secrets = _tracked_secret_files(root)
    if tracked_secrets:
        failures.append(
            "secret environment files are tracked by Git: " + ", ".join(tracked_secrets)
        )
    if "backend" in components:
        migration_files = _migration_files(root)
        if not migration_files:
            failures.append(
                "no committed portable migration files were found; run onramp db make"
            )
        else:
            raw_sql = _raw_sql_migrations(migration_files)
            if raw_sql:
                notices.append(
                    "review database portability for RunSQL migrations: "
                    + ", ".join(raw_sql)
                )

    if "backend" in components:
        database_url = os.environ.get("DATABASE_URL", "").strip()
        if database_url:
            notices.append("DATABASE_URL is available for deployment checks")
        elif (
            provider == "render"
            and (root / "render.yaml").is_file()
            and "fromDatabase:" in (root / "render.yaml").read_text(encoding="utf-8")
        ):
            notices.append("Render will inject DATABASE_URL from its managed database")
        else:
            notices.append(
                "DATABASE_URL must be set in the production secret environment"
            )

    if failures:
        print("Deployment check failed:")
        for failure in failures:
            print(f"  ✗ {failure}")
        for notice in notices:
            print(f"  • {notice}")
        return False

    print("Deployment check passed:")
    print(f"  ✓ provider: {provider}")
    print(
        "  ✓ targets: "
        + ", ".join(_target_label(name, selected[name]) for name in target_names)
    )
    if any(target.get("kind") == "container" for target in selected.values()):
        print("  ✓ production container files")
    if "web" in components:
        print("  ✓ production web build configuration")
    if "backend" in components:
        print("  ✓ committed database migrations")
    print("  ✓ no tracked local secret files")
    for notice in notices:
        print(f"  • {notice}")
    return True


def _run_command(root: Path, command_text: str, label: str | None = None) -> bool:
    command = shlex.split(command_text)
    if not command:
        print("A configured production command is empty.")
        return False
    print(f"Running production checks: {label or command_text}")
    try:
        return subprocess.run(command, cwd=root, check=False).returncode == 0
    except OSError as error:
        print(f"Could not run {command[0]}: {error}")
        return False


def _run_backend_checks(root: Path, config: dict) -> bool:
    configured = str(config.get("test_command", "")).strip()
    if configured:
        return _run_command(root, configured)
    if (root / "tests").is_dir():
        print("Running production checks: python -m pytest")
        return (
            subprocess.run(
                [sys.executable, "-m", "pytest"],
                cwd=root,
                check=False,
            ).returncode
            == 0
        )
    print("Running production checks: python -m compileall -q app")
    return (
        subprocess.run(
            [sys.executable, "-m", "compileall", "-q", "app"],
            cwd=root,
            check=False,
        ).returncode
        == 0
    )


def _run_web_checks(root: Path, target: dict, *, custom_test: bool) -> bool:
    web_root = root / str(target.get("root") or "build")
    try:
        package = json.loads((web_root / "package.json").read_text(encoding="utf-8"))
        scripts = dict(package.get("scripts", {}))
    except (OSError, ValueError, TypeError) as error:
        print(f"Could not read web package configuration: {error}")
        return False

    if not custom_test:
        for script in ("test", "typecheck"):
            if script not in scripts:
                continue
            label = f"npm run {script}" if script != "test" else "npm test"
            command = ["npm", "run", script] if script != "test" else ["npm", "test"]
            print(f"Running production checks: {label}")
            try:
                result = subprocess.run(command, cwd=web_root, check=False)
            except OSError as error:
                print(f"Could not run npm: {error}")
                return False
            if result.returncode != 0:
                return False

    build_command = str(
        target.get("local_build_command") or "npm run build:web"
    ).strip()
    return _run_command(web_root, build_command, "web production build")


def _run_project_checks(
    root: Path,
    config: dict,
    targets: dict[str, dict] | None = None,
) -> bool:
    selected = targets or deployment_targets(root, config)
    custom_components: set[str] = set()
    ran_commands: set[str] = set()
    for target in selected.values():
        configured = str(target.get("test_command", "")).strip()
        if configured and configured not in ran_commands:
            target_components = set(target.get("components", []))
            command_root = (
                root / str(target.get("root") or "build")
                if target_components == {"web"}
                else root
            )
            if not _run_command(command_root, configured):
                return False
            ran_commands.add(configured)
            custom_components.update(target.get("components", []))

    components = {
        component
        for target in selected.values()
        for component in target.get("components", [])
    }
    if "backend" in components and "backend" not in custom_components:
        if not _run_backend_checks(root, config):
            return False
    if "web" in components:
        web_target = next(
            target
            for target in selected.values()
            if "web" in target.get("components", [])
        )
        if not _run_web_checks(
            root,
            web_target,
            custom_test="web" in custom_components,
        ):
            return False
    return True


def _git_is_clean(root: Path) -> bool:
    if not (root / ".git").exists() or not shutil.which("git"):
        return True
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and not result.stdout.strip()


def _build_target_artifacts(
    root: Path,
    selected: dict[str, dict],
) -> bool:
    container_targets = {
        name: target
        for name, target in selected.items()
        if target.get("kind") == "container"
    }
    if container_targets and not shutil.which("docker"):
        print("Docker is required to build the configured production container.")
        return False

    for name, target in container_targets.items():
        image = str(target.get("image") or f"{_slug(root.name)}-{name}")
        dockerfile = str(target.get("dockerfile") or "Dockerfile")
        context = str(target.get("context") or ".")
        command = ["docker", "build", "--tag", image]
        if dockerfile != "Dockerfile":
            command.extend(["--file", dockerfile])
        command.append(context)
        print(f"Building production image {image}...")
        if subprocess.run(command, cwd=root, check=False).returncode != 0:
            print(f"Production image build failed for {_target_label(name, target)}.")
            return False

    for name, target in selected.items():
        if target.get("kind") != "static":
            continue
        web_root = root / str(target.get("root") or "build")
        publish = web_root / str(target.get("publish_directory") or "dist")
        if not publish.is_dir():
            print(
                f"Web production build did not create {publish.relative_to(root)} "
                f"for {_target_label(name, target)}."
            )
            return False
        print(f"✓ Web production artifact ready: {publish.relative_to(root)}")
    return True


def _render_service_for_target(
    config: dict,
    name: str,
    target: dict,
    selected_count: int,
) -> str:
    environment_name = re.sub(r"[^A-Za-z0-9]+", "_", name).upper()
    service = str(
        os.environ.get(f"ONRAMP_RENDER_{environment_name}_SERVICE")
        or target.get("render_service")
        or ""
    ).strip()
    if service:
        return service
    if selected_count == 1:
        return str(
            os.environ.get("ONRAMP_RENDER_SERVICE")
            or config.get("render_service")
            or ""
        ).strip()
    return ""


def deploy_project(
    project_root: str | Path,
    provider_override: str | None = None,
    *,
    interactive: bool | None = None,
) -> bool:
    """Select, check, build, and deploy configured production targets."""
    root = Path(project_root).resolve()
    config = load_deployment_config(root)
    if config is None:
        provider = provider_override or "render"
        print(f"No deployment configuration found; initializing {provider}.")
        if not initialize_deployment(root, provider):
            return False
        config = load_deployment_config(root) or {}

    provider = str(
        provider_override or config.get("provider", "container")
    ).lower()
    target_names = select_deployment_targets(
        root,
        config,
        interactive=interactive,
        remember=True,
    )
    if target_names is None:
        return False
    targets = deployment_targets(root, config)
    selected = {name: targets[name] for name in target_names}
    if not check_deployment(
        root,
        provider_override=provider,
        target_names=target_names,
        interactive=False,
    ):
        return False
    if not _run_project_checks(root, config, selected):
        print("Production checks failed; deployment stopped.")
        return False
    if not _build_target_artifacts(root, selected):
        return False
    if provider == "render" and not _git_is_clean(root):
        print(
            "Render deploys committed source, but this project has uncommitted "
            "changes. Commit and push them before deploying."
        )
        return False
    if provider == "container":
        print("✓ All selected production artifacts are ready")
        return True

    render = shutil.which("render")
    if not render:
        print(
            "The Render CLI is required for the final deployment step. Install it "
            "from https://render.com/docs/cli and rerun 'onramp deploy'."
        )
        return False

    validation = subprocess.run(
        [render, "blueprints", "validate", "render.yaml"],
        cwd=root,
        check=False,
    )
    if validation.returncode != 0:
        print("Render rejected render.yaml; deployment stopped.")
        return False

    is_interactive = _interactive_terminal() if interactive is None else interactive
    ordered_names = sorted(
        target_names,
        key=lambda name: (
            0 if "backend" in selected[name].get("components", []) else 1,
            target_names.index(name),
        ),
    )
    services = {
        name: _render_service_for_target(
            config,
            name,
            selected[name],
            len(selected),
        )
        for name in ordered_names
    }
    if len(selected) > 1 and not is_interactive:
        missing = [name for name, service in services.items() if not service]
        if missing:
            variables = ", ".join(
                "ONRAMP_RENDER_"
                + re.sub(r"[^A-Za-z0-9]+", "_", name).upper()
                + "_SERVICE"
                for name in missing
            )
            print(
                "Noninteractive multi-target Render deployment requires service "
                f"IDs in onramp.toml or {variables}."
            )
            return False

    for name in ordered_names:
        target = selected[name]
        command = [render, "deploys", "create"]
        if services[name]:
            command.append(services[name])
        command.append("--wait")
        print(f"Starting Render deployment: {_target_label(name, target)}...")
        result = subprocess.run(command, cwd=root, check=False)
        if result.returncode != 0:
            print(f"Render deployment failed for {_target_label(name, target)}.")
            return False
        if "backend" in target.get("components", []):
            print(
                f"✓ {_target_label(name, target)} deployment passed its health check"
            )
        else:
            print(f"✓ {_target_label(name, target)} deployment completed")
    print("✓ All selected Render deployments completed")
    return True
