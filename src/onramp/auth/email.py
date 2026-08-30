"""Development outbox and provider-based transactional email delivery."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from onramp.api import APIError

from .config import auth_config, import_callable
from .security import runtime_environment


RESEND_ENDPOINT = "https://api.resend.com/emails"


class EmailDeliveryError(APIError):
    def __init__(self, message: str = "Verification email is temporarily unavailable."):
        super().__init__(message, status=503, code="email_unavailable")


def _message(purpose: str, code: str, app_name: str) -> tuple[str, str]:
    actions = {
        "signup": f"create your {app_name} account",
        "signin": f"sign in to {app_name}",
        "delete_account": f"delete your {app_name} account",
    }
    action = actions.get(purpose, "verify your notification request")
    return (
        f"Your {app_name} verification code: {code}",
        f"Use {code} to {action}. This code expires shortly.\n\n"
        "If you did not request this code, you can ignore this email.",
    )


def development_outbox(app_dir: str | None = None) -> Path:
    if app_dir:
        root = Path(app_dir).resolve().parent
    else:
        root = Path.cwd()
    return root / ".onramp" / "dev-mail-outbox.jsonl"


def _write_development_message(message: dict, app_dir: str | None) -> None:
    destination = development_outbox(app_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    message = {"created_at": datetime.now(timezone.utc).isoformat(), **message}
    with destination.open("a", encoding="utf-8") as outbox:
        outbox.write(json.dumps(message, sort_keys=True) + "\n")
    print(
        f"OnRamp development mail: {message['to']} -> {message['code']} "
        f"({message['purpose']}); outbox: {destination}"
    )


def _send_with_resend(message: dict, config: dict) -> None:
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        raise EmailDeliveryError("RESEND_API_KEY is not configured")
    subject, body_text = _message(
        message["purpose"], message["code"], str(config["app_name"])
    )
    body = json.dumps(
        {
            "from": os.getenv("ONRAMP_EMAIL_FROM", str(config["email_from"])),
            "to": [message["to"]],
            "subject": subject,
            "text": body_text,
        }
    ).encode("utf-8")
    request = Request(
        RESEND_ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": message["idempotency_key"],
            "User-Agent": "OnRamp/0.5",
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            if not 200 <= response.status < 300:
                raise EmailDeliveryError(f"Email provider returned HTTP {response.status}")
    except HTTPError as error:
        raise EmailDeliveryError(f"Email provider returned HTTP {error.code}") from error
    except URLError as error:
        raise EmailDeliveryError("Could not reach the email provider") from error


async def send_verification_code(
    email: str,
    purpose: str,
    code: str,
    idempotency_key: str,
    *,
    app_dir: str | None = None,
) -> None:
    config = auth_config(app_dir)
    message = {
        "to": email,
        "purpose": purpose,
        "code": code,
        "idempotency_key": idempotency_key,
    }
    custom_sender = config.get("email_sender")
    if custom_sender:
        result = import_callable(str(custom_sender))(message)
        if hasattr(result, "__await__"):
            await result
        return
    if runtime_environment() in {"development", "test"}:
        await asyncio.to_thread(_write_development_message, message, app_dir)
        return
    await asyncio.to_thread(_send_with_resend, message, config)
