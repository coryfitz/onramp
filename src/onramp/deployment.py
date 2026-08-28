"""Provider-neutral production preparation and deployment helpers."""

from __future__ import annotations

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
SUPPORTED_PROVIDERS = {"container", "render"}


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
    required = [".env", ".env.*", "!.env.example"]
    existing = {line.strip() for line in content.splitlines()}
    missing = [line for line in required if line not in existing]
    if not missing:
        return
    suffix = "\n# Local deployment secrets\n" + "\n".join(missing) + "\n"
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


def _deploy_config(provider: str, image: str) -> str:
    return f"""[deploy]
provider = {json.dumps(provider)}
environment = "production"
health_path = "/health/ready"
image = {json.dumps(image)}
# test_command = "python -m pytest"
# render_service = "srv-..."
"""


def _render_blueprint(name: str) -> str:
    database_name = f"{name}-db"
    service_name = f"{name}-api"
    return f"""services:
  - type: web
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

databases:
  - name: {database_name}
    plan: basic-256mb
"""


def initialize_deployment(project_root: str | Path, provider: str = "render") -> bool:
    """Create portable production files without overwriting application files."""
    root = Path(project_root).resolve()
    provider = provider.strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        print(
            f"Unsupported deployment provider: {provider}. Choose render or container."
        )
        return False
    if not (root / "app" / "settings.py").is_file():
        print("app/settings.py not found. Run this from an OnRamp project root.")
        return False

    name = _slug(root.name)
    _write_once(root / "Dockerfile", _dockerfile())
    _write_once(root / ".dockerignore", _dockerignore())
    _write_once(root / ".env.example", _environment_example())
    _write_once(root / DEPLOY_CONFIG, _deploy_config(provider, f"{name}-api"))
    if provider == "render":
        _write_once(root / "render.yaml", _render_blueprint(name))
    _append_gitignore(root)

    print("Deployment preparation complete.")
    print("Add production secrets in your hosting provider, never in onramp.toml.")
    if provider == "render":
        print(
            "For the first deployment, connect render.yaml as a Blueprint in "
            "Render; later runs can deploy the selected service directly."
        )
    print("Next: onramp deploy check")
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
    return [path for path in migrations.rglob("*.py") if path.name != "__init__.py"]


def _migration_dialect(paths: list[Path]) -> str | None:
    """Recognize SQL markers that make Aerich migrations engine-specific."""
    content = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore") for path in paths
    )
    if "AUTOINCREMENT" in content or "PRAGMA " in content:
        return "sqlite"
    if "SERIAL" in content or "TIMESTAMPTZ" in content:
        return "postgresql"
    if "`" in content or "AUTO_INCREMENT" in content:
        return "mysql"
    return None


def _target_database_engine(provider: str, manager: DatabaseManager) -> str | None:
    if provider == "render":
        return "postgresql"
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if database_url:
        scheme = database_url.split(":", 1)[0].lower()
        return "postgresql" if scheme in {"postgres", "postgresql", "asyncpg"} else scheme
    if provider == "container":
        return None
    return manager._database_config().get("engine")


def check_deployment(
    project_root: str | Path,
    provider_override: str | None = None,
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
    failures: list[str] = []
    notices: list[str] = []

    if provider not in SUPPORTED_PROVIDERS:
        failures.append(f"unsupported provider '{provider}'")
    if config.get("environment") != "production":
        failures.append("[deploy].environment must be 'production'")
    for relative_path in ("Dockerfile", ".dockerignore", ".env.example"):
        if not (root / relative_path).is_file():
            failures.append(f"missing {relative_path}")
    if provider == "render" and not (root / "render.yaml").is_file():
        failures.append("missing render.yaml")
    if not (root / "app" / "settings.py").is_file():
        failures.append("missing app/settings.py")

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
    migration_files = _migration_files(root)
    if not migration_files:
        instruction = (
            "ONRAMP_DATABASE_ENGINE=postgresql onramp db make"
            if provider == "render"
            else "onramp db make"
        )
        failures.append(
            "no committed database migration files were found; run " + instruction
        )
    else:
        migration_dialect = _migration_dialect(migration_files)
        target_dialect = _target_database_engine(provider, manager)
        if (
            migration_dialect
            and target_dialect
            and migration_dialect != target_dialect
        ):
            failures.append(
                f"migrations were generated for {migration_dialect}, but {provider} "
                f"uses {target_dialect}; generate migrations against the production "
                "database engine"
            )

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
        notices.append("DATABASE_URL must be set in the production secret environment")

    if failures:
        print("Deployment check failed:")
        for failure in failures:
            print(f"  ✗ {failure}")
        for notice in notices:
            print(f"  • {notice}")
        return False

    print("Deployment check passed:")
    print(f"  ✓ provider: {provider}")
    print("  ✓ production container files")
    print("  ✓ committed database migrations")
    print("  ✓ no tracked local secret files")
    for notice in notices:
        print(f"  • {notice}")
    return True


def _run_project_checks(root: Path, config: dict) -> bool:
    configured = str(config.get("test_command", "")).strip()
    if configured:
        command = shlex.split(configured)
        label = configured
    elif (root / "tests").is_dir():
        command = [sys.executable, "-m", "pytest"]
        label = "python -m pytest"
    else:
        command = [sys.executable, "-m", "compileall", "-q", "app"]
        label = "python -m compileall -q app"
    print(f"Running production checks: {label}")
    return subprocess.run(command, cwd=root, check=False).returncode == 0


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


def deploy_project(
    project_root: str | Path,
    provider_override: str | None = None,
) -> bool:
    """Check, build, and deploy the configured production artifact."""
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
    if not check_deployment(root, provider_override=provider):
        return False
    if not _run_project_checks(root, config):
        print("Production checks failed; deployment stopped.")
        return False
    if provider == "render" and not _git_is_clean(root):
        print(
            "Render deploys committed source, but this project has uncommitted "
            "changes. Commit and push them before deploying."
        )
        return False
    if not shutil.which("docker"):
        print("Docker is required to build the portable production image.")
        return False

    image = str(config.get("image") or f"{_slug(root.name)}-api")
    print(f"Building production image {image}...")
    if subprocess.run(
        ["docker", "build", "--tag", image, "."],
        cwd=root,
        check=False,
    ).returncode != 0:
        print("Production image build failed.")
        return False

    if provider == "container":
        print(f"✓ Production image ready: {image}")
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

    service = str(
        os.environ.get("ONRAMP_RENDER_SERVICE")
        or config.get("render_service")
        or ""
    ).strip()
    command = [render, "deploys", "create"]
    if service:
        command.append(service)
    command.append("--wait")
    print("Starting Render deployment...")
    result = subprocess.run(command, cwd=root, check=False)
    if result.returncode != 0:
        print("Render deployment failed.")
        return False
    print("✓ Render deployment completed and passed its health check")
    return True
