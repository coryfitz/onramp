"""Configuration helpers shared by OnRamp account batteries."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from onramp.db.manager import get_db_manager


DEFAULT_AUTH = {
    "enabled": False,
    "app_name": "OnRamp",
    "email_from": "OnRamp <accounts@example.com>",
    "session_days": 30,
    "challenge_minutes": 10,
    "challenge_attempts": 6,
    "hourly_challenge_limit": 6,
    "resend_delay_seconds": 60,
    "deletion_hooks": [],
}


def auth_config(app_dir: str | None = None) -> dict[str, Any]:
    configured = get_db_manager(app_dir).settings.get("AUTH", {})
    return {**DEFAULT_AUTH, **dict(configured or {})}


def auth_enabled(app_dir: str | None = None) -> bool:
    return bool(auth_config(app_dir).get("enabled"))


def import_callable(reference: str):
    module_name, separator, attribute = str(reference).rpartition(".")
    if not separator:
        raise RuntimeError(
            f"OnRamp callable '{reference}' must use a full module.attribute path"
        )
    value = getattr(import_module(module_name), attribute)
    if not callable(value):
        raise RuntimeError(f"OnRamp callable '{reference}' is not callable")
    return value
