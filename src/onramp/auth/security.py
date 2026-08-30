"""Normalization and one-way secret handling for OnRamp accounts."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets


EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
VALID_ENVIRONMENTS = {"development", "test", "staging", "production"}


def runtime_environment() -> str:
    value = os.getenv("ONRAMP_ENVIRONMENT", "development").strip().lower()
    if value not in VALID_ENVIRONMENTS:
        raise RuntimeError(f"Unsupported ONRAMP_ENVIRONMENT: {value}")
    return value


def _secret(name: str) -> bytes:
    value = os.getenv(name, "").strip()
    environment = runtime_environment()
    if not value and environment in {"development", "test"}:
        value = f"onramp-local-development-only:{name}"
    if not value:
        raise RuntimeError(f"{name} must be configured outside development")
    if len(value) < 32 and environment not in {"development", "test"}:
        raise RuntimeError(f"{name} must contain at least 32 characters")
    return value.encode("utf-8")


def normalize_email(value: object) -> str:
    email = str(value or "").strip().lower()
    if len(email) > 255 or not EMAIL_PATTERN.fullmatch(email):
        raise ValueError("Enter a valid email address.")
    return email


def email_digest(email: str) -> str:
    return hmac.new(
        _secret("ONRAMP_IDENTITY_SECRET"),
        normalize_email(email).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def challenge_digest(email: str, purpose: str, code: str) -> str:
    message = f"{normalize_email(email)}:{purpose}:{code}".encode("utf-8")
    return hmac.new(
        _secret("ONRAMP_AUTH_SECRET"), message, hashlib.sha256
    ).hexdigest()


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def new_session_token() -> str:
    return f"or_{secrets.token_urlsafe(32)}"
