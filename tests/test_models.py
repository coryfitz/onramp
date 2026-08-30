import asyncio
import sys

from tortoise import Tortoise

from onramp.app import OnRamp
from onramp.db.manager import DatabaseManager
from onramp.db import models


class GetOrCreateRecord(models.Model):
    id = models.IntegerField(primary_key=True)
    name = models.CharField(max_length=100, unique=True)
    label = models.CharField(max_length=100)

    class Meta:
        table = "get_or_create_records"


def test_model_get_or_create_uses_tortoise_implementation():
    async def scenario():
        await Tortoise.init(
            db_url="sqlite://:memory:",
            modules={"models": [__name__]},
        )
        await Tortoise.generate_schemas()
        try:
            created, was_created = await GetOrCreateRecord.get_or_create(
                name="first",
                defaults={"label": "created"},
            )
            existing, was_created_again = await GetOrCreateRecord.get_or_create(
                name="first",
                defaults={"label": "ignored"},
            )
            assert was_created is True
            assert was_created_again is False
            assert existing.id == created.id
            assert existing.label == "created"
        finally:
            await Tortoise.close_connections()

    asyncio.run(scenario())


def _application_tree(tmp_path):
    app_dir = tmp_path / "app"
    (app_dir / "api").mkdir(parents=True)
    (app_dir / "models").mkdir()
    for package in (app_dir, app_dir / "api", app_dir / "models"):
        (package / "__init__.py").write_text("")
    (app_dir / "api" / "index.py").write_text("def get():\n    return {}\n")
    (app_dir / "models" / "models.py").write_text(
        "from onramp.db import models\n\n"
        "class Example(models.Model):\n"
        "    id = models.IntegerField(primary_key=True)\n"
    )
    (app_dir / "settings.py").write_text(
        "DATABASE = {'engine': 'sqlite', 'name': 'db.sqlite3'}\n"
    )
    return app_dir


def test_application_directory_is_not_added_as_top_level_import_root(
    tmp_path,
    monkeypatch,
):
    app_dir = _application_tree(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))

    DatabaseManager(str(app_dir)).discover_models()
    OnRamp(str(app_dir)).discover_file_routes()

    assert str(tmp_path) in sys.path
    assert str(app_dir) not in sys.path
