"""Shared Tortoise ORM configuration for runtime and migration commands."""

from pathlib import Path

from onramp.db.manager import DatabaseManager


APP_DIR = Path(__file__).resolve().parents[1]
TORTOISE_ORM = DatabaseManager(str(APP_DIR)).get_tortoise_config()
