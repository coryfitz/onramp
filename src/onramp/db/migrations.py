"""Portable migration management using Tortoise ORM's native operations."""

from __future__ import annotations

import os
from pathlib import Path
import re
import ast
import subprocess
import sys
from typing import Optional

from .manager import get_db_manager


TORTOISE_CONFIG = "app.db.db_config.TORTOISE_ORM"
TORTOISE_TOOL_SECTION = (
    "[tool.tortoise]\n"
    f'tortoise_orm = "{TORTOISE_CONFIG}"\n'
)


_TABLE_DESCRIPTION = re.compile(
    r"('table_description'\s*:\s*)('(?:\\.|[^'\\])*')"
)


def _sanitize_migration_descriptions(path: Path) -> bool:
    """Keep generated documentation from becoming executable SQL delimiters."""
    content = path.read_text(encoding="utf-8")

    def sanitize(match: re.Match) -> str:
        description = ast.literal_eval(match.group(2))
        safe = str(description).replace(";", ",")
        return match.group(1) + repr(safe)

    updated = _TABLE_DESCRIPTION.sub(sanitize, content)
    if updated == content:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def _replace_legacy_tool_config(content: str) -> str:
    """Replace Aerich's project configuration with Tortoise's native CLI config."""
    updated = re.sub(
        r"(?ms)^\[tool\.aerich\]\n.*?(?=^\[|\Z)",
        "",
        content,
    ).rstrip()
    if "[tool.tortoise]" not in updated:
        updated += "\n\n" + TORTOISE_TOOL_SECTION.rstrip()
    return updated + "\n"


class MigrationManager:
    """Manage backend-neutral migrations through Tortoise ORM."""

    def __init__(self, app_dir: str = None):
        self.app_dir = app_dir
        self.db_manager = get_db_manager(app_dir)
        self.project_root = Path(self.db_manager.app_dir).resolve().parent
        self.db_dir = Path(self.db_manager.app_dir).resolve() / "db"
        self.migrations_dir = self.db_dir / "migrations"

    def _ensure_tortoise_config(self) -> None:
        """Create the portable Tortoise config and migration package if absent."""
        self.db_dir.mkdir(parents=True, exist_ok=True)
        init_path = self.db_dir / "__init__.py"
        if not init_path.exists():
            init_path.write_text("# Database package\n", encoding="utf-8")

        db_config_path = self.db_dir / "db_config.py"
        if not db_config_path.exists():
            db_config_path.write_text(
                """# Database configuration for Tortoise ORM and its migration CLI.
from pathlib import Path

from onramp.db.manager import DatabaseManager


APP_DIR = Path(__file__).resolve().parents[1]
TORTOISE_ORM = DatabaseManager(str(APP_DIR)).get_tortoise_config()
""",
                encoding="utf-8",
            )

        self.migrations_dir.mkdir(parents=True, exist_ok=True)
        migrations_init = self.migrations_dir / "__init__.py"
        if not migrations_init.exists():
            migrations_init.write_text("", encoding="utf-8")

        pyproject_path = self.project_root / "pyproject.toml"
        if pyproject_path.exists():
            content = pyproject_path.read_text(encoding="utf-8")
            updated = _replace_legacy_tool_config(content)
            if updated != content:
                pyproject_path.write_text(updated, encoding="utf-8")
        else:
            pyproject_path.write_text(
                """[build-system]
requires = ["setuptools>=77.0.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "onramp-app"
version = "0.1.0"

"""
                + TORTOISE_TOOL_SECTION,
                encoding="utf-8",
            )

    def migration_files(self) -> list[Path]:
        """Return native Tortoise migration modules, excluding legacy subfolders."""
        if not self.migrations_dir.is_dir():
            return []
        return sorted(
            path
            for path in self.migrations_dir.glob("*.py")
            if path.name != "__init__.py"
        )

    def _run_tortoise_command_result(self, command: list[str]):
        full_command = [
            sys.executable,
            "-m",
            "tortoise",
            "-c",
            TORTOISE_CONFIG,
            *command,
        ]
        return subprocess.run(
            full_command,
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=False,
        )

    def _run_tortoise_command(self, command: list[str]) -> bool:
        result = self._run_tortoise_command_result(command)
        if result.stdout:
            print(result.stdout.rstrip())
        if result.stderr:
            print(result.stderr.rstrip())
        if result.returncode == 0:
            return True
        print(f"Error running Tortoise migration command: {' '.join(command)}")
        return False

    def init_migrations(self) -> bool:
        """Create and apply the initial portable migration for a new project."""
        print("Setting up portable Tortoise migrations...")
        self._ensure_tortoise_config()
        if self.migration_files():
            print("Migration system already initialized")
            return True
        if not self.create_migration("initial"):
            return False
        if not self.apply_migrations():
            return False
        print("Portable migration system initialized")
        return True

    def create_migration(self, name: Optional[str] = None) -> bool:
        """Create a backend-neutral migration from model changes."""
        if self.db_manager.environment() != "development":
            print(
                "Migration creation is available only in development. "
                "Use 'onramp db upgrade' to apply committed migrations."
            )
            return False
        print("Creating portable migration...")
        self._ensure_tortoise_config()
        command = ["makemigrations"]
        if name:
            command.extend(["--name", name])
        before = set(self.migration_files())
        if not self._run_tortoise_command(command):
            return False
        for migration_path in set(self.migration_files()) - before:
            if _sanitize_migration_descriptions(migration_path):
                print(
                    f"Sanitized SQL-delimiter punctuation in {migration_path.name}"
                )
        return True

    def apply_migrations(self) -> bool:
        """Apply every pending portable migration."""
        print("Applying migrations...")
        self.db_manager.validate_runtime_configuration()
        self._ensure_tortoise_config()
        if not self.migration_files():
            print(
                "No committed migration files were found. Run 'onramp migrate' "
                "in development first."
            )
            return False
        return self._run_tortoise_command(["upgrade"])

    def check_migrations(self) -> bool:
        """Return whether the configured database has no pending migrations."""
        self.db_manager.validate_runtime_configuration()
        self._ensure_tortoise_config()
        if not self.migration_files():
            print("No portable migration files were found.")
            return False

        result = self._run_tortoise_command_result(["upgrade", "--dry-run"])
        output = "\n".join(part for part in (result.stdout, result.stderr) if part)
        if result.returncode != 0:
            print("Could not check database migrations.")
            if output.strip():
                print(output.rstrip())
            return False
        if "No migrations to apply" in output:
            print("Database migrations are up to date.")
            return True
        print("Pending database migrations:")
        print(output.rstrip())
        return False

    def migrate_with_prep(self, name: Optional[str] = None) -> bool:
        """Create and apply migrations in one development command."""
        if self.db_manager.environment() != "development":
            print(
                "'onramp migrate' creates migration files and is available only "
                "in development. Use 'onramp db upgrade' in deployments."
            )
            return False
        print("Preparing and applying migrations...")
        if not self.create_migration(name):
            return False
        return self.apply_migrations()


_migration_manager = None


def get_migration_manager(app_dir: str = None):
    """Get or create the manager associated with the requested app directory."""
    global _migration_manager
    requested_app_dir = os.path.abspath(app_dir) if app_dir else None
    current_app_dir = (
        os.path.abspath(_migration_manager.app_dir)
        if _migration_manager is not None and _migration_manager.app_dir
        else None
    )
    if _migration_manager is None or (
        requested_app_dir is not None and requested_app_dir != current_app_dir
    ):
        _migration_manager = MigrationManager(app_dir)
    return _migration_manager


def create_migration(name: Optional[str] = None, app_dir: str = None):
    return get_migration_manager(app_dir).create_migration(name)


def migrate(name: Optional[str] = None, app_dir: str = None):
    return get_migration_manager(app_dir).migrate_with_prep(name)


def apply_migrations(app_dir: str = None):
    return get_migration_manager(app_dir).apply_migrations()


def check_migrations(app_dir: str = None):
    return get_migration_manager(app_dir).check_migrations()


def init_migrations(app_dir: str = None):
    return get_migration_manager(app_dir).init_migrations()
