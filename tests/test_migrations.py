import asyncio
import os
from pathlib import Path
import sqlite3

import pytest

from onramp.db import manager as db_manager_module
from onramp.db import migrations as migrations_module


def create_migration_project(root: Path) -> Path:
    app_dir = root / "app"
    models_dir = app_dir / "models"
    models_dir.mkdir(parents=True)
    (app_dir / "__init__.py").write_text("")
    (models_dir / "__init__.py").write_text("")
    (models_dir / "models.py").write_text(
        "from tortoise import fields, models\n\n"
        "class Request(models.Model):\n"
        "    id = fields.IntField(primary_key=True)\n"
        "    email = fields.CharField(max_length=320)\n"
    )
    (app_dir / "settings.py").write_text(
        "ENVIRONMENT = 'development'\n"
        "AUTO_GENERATE_SCHEMAS = False\n"
        "DATABASE = {'engine': 'sqlite', 'name': 'db.sqlite3'}\n"
    )
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'migration-test'\nversion = '0.1.0'\n"
    )
    return app_dir


def migration_manager(app_dir: Path):
    migrations_module._migration_manager = None
    db_manager_module._db_manager = None
    return migrations_module.MigrationManager(str(app_dir))


def test_generated_table_descriptions_cannot_inject_sql_delimiters(tmp_path):
    migration = tmp_path / "0001_description.py"
    migration.write_text(
        "options={'table': 'sessions', 'table_description': "
        "'Opaque session; only a digest is stored.'}\n"
    )

    assert migrations_module._sanitize_migration_descriptions(migration)
    assert "session, only" in migration.read_text()
    assert "session; only" not in migration.read_text()


def test_native_migrations_create_and_update_sqlite_schema(tmp_path):
    app_dir = create_migration_project(tmp_path)
    manager = migration_manager(app_dir)

    assert manager.init_migrations()
    initial = manager.migration_files()[0].read_text()
    assert "ops.CreateModel" in initial
    assert "CREATE TABLE" not in initial

    database = app_dir / "db" / "db.sqlite3"
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"request", "tortoise_migrations"} <= tables

    models = app_dir / "models" / "models.py"
    models.write_text(
        models.read_text() + "    requested_at = fields.DatetimeField(null=True)\n"
    )
    assert manager.create_migration("add_requested_at")
    assert not manager.check_migrations()
    assert manager.apply_migrations()
    assert manager.check_migrations()

    with sqlite3.connect(database) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(request)")
        }
    assert "requested_at" in columns
    assert "ops.AddField" in manager.migration_files()[-1].read_text()


def test_initializing_an_empty_project_waits_for_its_first_model(tmp_path, capsys):
    app_dir = tmp_path / "app"
    models_dir = app_dir / "models"
    models_dir.mkdir(parents=True)
    (app_dir / "__init__.py").write_text("")
    (models_dir / "__init__.py").write_text("")
    (models_dir / "models.py").write_text(
        "from onramp.db import models\n\n"
        "# Application models will be added here.\n"
    )
    (app_dir / "settings.py").write_text(
        "ENVIRONMENT = 'development'\n"
        "AUTH = {'enabled': False}\n"
        "AUTO_GENERATE_SCHEMAS = False\n"
        "DATABASE = {'engine': 'sqlite', 'name': 'db.sqlite3'}\n"
    )
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'empty-migration-test'\nversion = '0.1.0'\n"
    )

    manager = migration_manager(app_dir)

    assert manager.init_migrations()
    assert manager.migration_files() == []
    assert "migration setup is ready" in capsys.readouterr().out


@pytest.mark.skipif(
    not os.environ.get("ONRAMP_TEST_POSTGRES_URL"),
    reason="ONRAMP_TEST_POSTGRES_URL is not configured",
)
def test_sqlite_generated_migration_applies_to_postgresql(tmp_path, monkeypatch):
    app_dir = create_migration_project(tmp_path)
    manager = migration_manager(app_dir)
    assert manager.init_migrations()

    migration = manager.migration_files()[0].read_text()
    assert "ops.CreateModel" in migration
    assert "AUTOINCREMENT" not in migration
    assert "SERIAL" not in migration

    database_url = os.environ["ONRAMP_TEST_POSTGRES_URL"]
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("ONRAMP_ENVIRONMENT", "production")
    manager = migration_manager(app_dir)
    assert manager.apply_migrations()
    assert manager.check_migrations()

    async def table_exists():
        import asyncpg

        connection = await asyncpg.connect(database_url)
        try:
            return await connection.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'request')"
            )
        finally:
            await connection.close()

    assert asyncio.run(table_exists())
