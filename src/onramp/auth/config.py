"""Configuration helpers shared by OnRamp account batteries."""

from __future__ import annotations

from importlib import import_module
import os
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from onramp.db.manager import get_db_manager


DEFAULT_AUTH = {
    "enabled": False,
    "app_name": "OnRamp",
    "email_from": "OnRamp <accounts@example.com>",
    "verification_email_renderer": None,
    "session_days": 30,
    "challenge_minutes": 10,
    "challenge_attempts": 6,
    "hourly_challenge_limit": 6,
    "resend_delay_seconds": 60,
    "auth_ip_hourly_limit": 120,
    "notification_ip_hourly_limit": 120,
    "notification_metadata_bytes": 16_384,
    "notification_request_bytes": 16_384,
    "notification_contact_token_days": None,
    "notification_subscription_validator": None,
    "notification_subscription_ready_hook": None,
    "unverified_subscription_days": 30,
    "public_url": "",
    "deletion_hooks": [],
}


def auth_config(app_dir: str | None = None) -> dict[str, Any]:
    configured = get_db_manager(app_dir).settings.get("AUTH", {})
    return {**DEFAULT_AUTH, **dict(configured or {})}


def auth_enabled(app_dir: str | None = None) -> bool:
    return bool(auth_config(app_dir).get("enabled"))


def application_public_url(app_dir: str | None = None) -> str:
    """Return the externally reachable application URL used in email actions."""
    from .security import runtime_environment

    environment = runtime_environment()
    configured = (
        os.getenv("ONRAMP_PUBLIC_URL", "").strip()
        or str(auth_config(app_dir).get("public_url") or "").strip()
    )
    if not configured and environment in {"development", "test"}:
        configured = "http://127.0.0.1:8000"
    try:
        parts = urlsplit(configured)
    except ValueError as error:
        raise RuntimeError("ONRAMP_PUBLIC_URL is not a valid URL.") from error
    if parts.scheme not in {"http", "https"} or not parts.netloc or not parts.hostname:
        raise RuntimeError(
            "ONRAMP_PUBLIC_URL (or AUTH.public_url) must be configured before "
            "sending notification email."
        )
    if parts.username is not None or parts.password is not None:
        raise RuntimeError("ONRAMP_PUBLIC_URL cannot contain credentials.")
    if parts.query or parts.fragment:
        raise RuntimeError("ONRAMP_PUBLIC_URL cannot contain a query or fragment.")
    try:
        parts.port
    except ValueError as error:
        raise RuntimeError("ONRAMP_PUBLIC_URL contains an invalid port.") from error
    if environment in {"staging", "production"} and parts.scheme != "https":
        raise RuntimeError("ONRAMP_PUBLIC_URL must use HTTPS outside development.")
    if (
        parts.scheme == "http"
        and environment in {"development", "test"}
        and parts.hostname not in {"localhost", "127.0.0.1", "::1"}
    ):
        raise RuntimeError(
            "HTTP ONRAMP_PUBLIC_URL values are limited to localhost in development."
        )
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


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
