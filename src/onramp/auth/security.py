"""Normalization and one-way secret handling for OnRamp accounts."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
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


def client_request_digest(scope: str, client_host: str | None) -> str:
    """Domain-separated HMAC: raw client addresses never enter limiter storage."""
    host = str(client_host or "unavailable-client")[:255].strip().lower()
    try:
        address = ipaddress.ip_address(host)
        host = str(getattr(address, "ipv4_mapped", None) or address)
    except ValueError:
        # ASGI test clients and Unix-domain peers may have non-IP host names.
        pass
    message = json.dumps(
        ["onramp-client-request-v1", runtime_environment(), scope, host],
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(
        _secret("ONRAMP_AUTH_SECRET"), message, hashlib.sha256
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


def sign_action_token(action: str, subject: object) -> str:
    """Sign a non-secret identifier for a narrowly scoped public action."""
    normalized_action = str(action or "").strip()
    normalized_subject = str(subject or "").strip()
    if not normalized_action or not normalized_subject:
        raise ValueError("Action and subject are required.")
    if len(normalized_action) > 128 or len(normalized_subject) > 1_024:
        raise ValueError("Action or subject is too long.")
    encoded = base64.urlsafe_b64encode(normalized_subject.encode("utf-8")).decode(
        "ascii"
    ).rstrip("=")
    signature = hmac.new(
        _secret("ONRAMP_AUTH_SECRET"),
        f"{normalized_action}:{encoded}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{encoded}.{signature}"


def verify_action_token(action: str, token: object) -> str | None:
    """Return a signed action token's subject, or ``None`` when invalid."""
    normalized_action = str(action or "").strip()
    normalized_token = str(token or "").strip()
    if len(normalized_token) > 2_048:
        return None
    encoded, separator, supplied_signature = normalized_token.partition(".")
    if not normalized_action or not separator or not encoded or not supplied_signature:
        return None
    expected = hmac.new(
        _secret("ONRAMP_AUTH_SECRET"),
        f"{normalized_action}:{encoded}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(supplied_signature, expected):
        return None
    try:
        padding = "=" * (-len(encoded) % 4)
        return base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
