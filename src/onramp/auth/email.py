"""Development outbox and provider-based transactional email delivery."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from email.headerregistry import HeaderRegistry
from http.client import HTTPException
import json
import os
from pathlib import Path
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from onramp.api import APIError

from .config import application_public_url, auth_config, import_callable
from .email_templates import (
    VerificationEmailContext,
    default_verification_email,
    validate_email_template,
    validate_verification_context,
)
from .security import normalize_email, runtime_environment, sign_action_token


RESEND_ENDPOINT = "https://api.resend.com/emails"


class EmailDeliveryError(APIError):
    def __init__(self, message: str = "Email is temporarily unavailable."):
        super().__init__(message, status=503, code="email_unavailable")


@dataclass(frozen=True)
class EmailSendResult:
    """Provider-neutral result from a transactional email attempt."""

    provider: str
    message_id: str | None = None


def development_outbox(app_dir: str | None = None) -> Path:
    if app_dir:
        root = Path(app_dir).resolve().parent
    else:
        root = Path.cwd()
    return root / ".onramp" / "dev-mail-outbox.jsonl"


def _write_development_message(message: dict, app_dir: str | None) -> EmailSendResult:
    destination = development_outbox(app_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stored_message = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        **message,
    }
    with destination.open("a", encoding="utf-8") as outbox:
        outbox.write(json.dumps(stored_message, sort_keys=True) + "\n")
    description = (
        f"{message['code']} ({message['purpose']})"
        if message.get("code") and message.get("purpose")
        else message["subject"]
    )
    try:
        print(
            f"OnRamp development mail: {message['to']} -> {description}; "
            f"outbox: {destination}"
        )
    except Exception:
        # The outbox is the durable development delivery. A detached or closed
        # terminal must not turn that successful write into an API failure.
        pass
    return EmailSendResult(
        provider="development",
        message_id=f"dev/{message['idempotency_key']}",
    )


def email_sender_address(config: dict) -> str:
    """Resolve one safe From mailbox without exposing configuration in errors.

    This checks syntax, not DNS or provider approval of the sending domain.
    A nonempty environment override takes precedence over application settings.
    """
    sender = (
        os.getenv("ONRAMP_EMAIL_FROM", "").strip()
        or str(config.get("email_from") or "").strip()
    )
    error_message = (
        "ONRAMP_EMAIL_FROM (or AUTH.email_from) must contain one valid sender "
        "email address, optionally with a display name."
    )
    if (
        not sender
        or len(sender) > 998
        or any(ord(char) < 32 or ord(char) == 127 for char in sender)
    ):
        raise EmailDeliveryError(error_message)
    try:
        header = HeaderRegistry()("From", sender)
        if (
            header.defects
            or len(header.addresses) != 1
            or any(group.display_name is not None for group in header.groups)
        ):
            raise ValueError("Invalid sender mailbox")
        normalize_email(header.addresses[0].addr_spec)
    except (ValueError, TypeError, IndexError):
        raise EmailDeliveryError(error_message) from None
    return sender


def _resend_http_error(status: int) -> EmailDeliveryError:
    """Use fixed diagnostics; provider bodies can contain recipient data."""
    if status == 401:
        guidance = "Check the server's RESEND_API_KEY."
    elif status == 403:
        guidance = (
            "Check the Resend API key's sending permission and the verified "
            "domain for ONRAMP_EMAIL_FROM. Resend's test sender restricts recipients."
        )
    elif status == 429:
        guidance = "Check the Resend sending quota and rate limit before retrying."
    elif status == 409:
        guidance = (
            "Check for an in-flight request or a changed payload using the same "
            "idempotency key; retry the unchanged request after checking its status."
        )
    elif status >= 500:
        guidance = "The email provider is temporarily unavailable; try again later."
    else:
        guidance = "Check the email sender and message configuration in the server."
    return EmailDeliveryError(f"Email provider returned HTTP {status}. {guidance}")


def _send_with_resend(message: dict, config: dict) -> EmailSendResult:
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        raise EmailDeliveryError("RESEND_API_KEY is not configured")
    if any(ord(char) < 33 or ord(char) > 126 for char in api_key):
        raise EmailDeliveryError("RESEND_API_KEY must be a valid single-line API key.")
    payload = {
        "from": email_sender_address(config),
        "to": [message["to"]],
        "subject": message["subject"],
        "text": message["text"],
    }
    if message.get("html"):
        payload["html"] = message["html"]
    body = json.dumps(payload).encode("utf-8")
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
                raise _resend_http_error(response.status)
            response_body = response.read()
    except HTTPError as error:
        status = error.code
        error.close()
        raise _resend_http_error(status) from None
    except TimeoutError:
        raise EmailDeliveryError(
            "The email provider timed out. Delivery could not be confirmed."
        ) from None
    except (URLError, OSError, HTTPException):
        raise EmailDeliveryError("Could not reach the email provider.") from None

    try:
        response_payload = json.loads(response_body)
        message_id = (
            response_payload.get("id") if isinstance(response_payload, dict) else None
        )
    except (TypeError, ValueError, UnicodeDecodeError):
        message_id = None
    if (
        not isinstance(message_id, str)
        or not message_id.strip()
        or len(message_id) > 255
        or any(ord(char) < 33 or ord(char) > 126 for char in message_id)
    ):
        raise EmailDeliveryError(
            "The email provider returned an invalid response; delivery could not "
            "be confirmed."
        )
    return EmailSendResult(provider="resend", message_id=message_id)


def _custom_result(value: object) -> EmailSendResult:
    if isinstance(value, EmailSendResult):
        return value
    if isinstance(value, dict):
        return EmailSendResult(
            provider=str(value.get("provider") or "custom"),
            message_id=str(value.get("message_id") or value.get("id") or "").strip()
            or None,
        )
    if isinstance(value, str):
        return EmailSendResult(provider="custom", message_id=value or None)
    return EmailSendResult(provider="custom")


async def send_transactional_email(
    to_email: object,
    subject: object,
    text_body: object,
    html_body: object | None = None,
    idempotency_key: str | None = None,
    *,
    app_dir: str | None = None,
    message_fields: dict | None = None,
) -> EmailSendResult:
    """Send through a custom sender, the development outbox, or Resend.

    Custom senders receive the complete message dictionary and must honor its
    ``idempotency_key`` when they communicate with an external provider.
    """

    email = normalize_email(to_email)
    normalized_subject = str(subject or "").strip()
    normalized_text = str(text_body or "").strip()
    normalized_html = str(html_body or "").strip() or None
    if (
        not normalized_subject
        or len(normalized_subject) > 998
        or "\r" in normalized_subject
        or "\n" in normalized_subject
    ):
        raise EmailDeliveryError("Email subject must contain 1–998 characters.")
    if not normalized_text:
        raise EmailDeliveryError("Email text body is required.")
    key = str(idempotency_key or f"transactional/{uuid.uuid4()}").strip()
    if not key or len(key) > 255:
        raise EmailDeliveryError("Email idempotency key must contain 1–255 characters.")

    config = auth_config(app_dir)
    message = {
        **dict(message_fields or {}),
        "to": email,
        "subject": normalized_subject,
        "text": normalized_text,
        "html": normalized_html,
        "idempotency_key": key,
    }
    custom_sender = config.get("email_sender")
    if custom_sender:
        try:
            result = import_callable(str(custom_sender))(message)
            if hasattr(result, "__await__"):
                result = await result
            return _custom_result(result)
        except Exception:
            # Application/provider exceptions can contain API keys, recipients,
            # or full email bodies. Never expose them to an API or a ledger.
            raise EmailDeliveryError("The configured email sender failed.") from None
    if runtime_environment() in {"development", "test"}:
        return await asyncio.to_thread(_write_development_message, message, app_dir)
    return await asyncio.to_thread(_send_with_resend, message, config)


async def send_verification_code(
    email: str,
    purpose: str,
    code: str,
    idempotency_key: str,
    *,
    app_dir: str | None = None,
    subject_id: str | None = None,
    action_subject_id: str | None = None,
) -> EmailSendResult:
    config = auth_config(app_dir)
    manage_url = None
    if purpose == "notification_subscription" and subject_id:
        token = sign_action_token(
            "notification_unsubscribe", action_subject_id or subject_id
        )
        manage_url = (
            f"{application_public_url(app_dir)}/api/notifications/unsubscribe?"
            + urlencode({"token": token})
        )
    try:
        context = VerificationEmailContext(
            purpose=purpose,
            code=code,
            app_name=str(config["app_name"]),
            expires_minutes=int(config.get("challenge_minutes", 10)),
            public_url=application_public_url(app_dir),
            manage_url=manage_url,
        )
        validate_verification_context(context)
        renderer_reference = config.get("verification_email_renderer")
        renderer = (
            import_callable(str(renderer_reference))
            if renderer_reference else default_verification_email
        )
        template = validate_email_template(renderer(context))
    except Exception:
        # Template exceptions can contain the code or other sensitive context.
        raise EmailDeliveryError("The configured verification email template failed.") from None
    return await send_transactional_email(
        email,
        template.subject,
        template.text,
        template.html,
        idempotency_key=idempotency_key,
        app_dir=app_dir,
        message_fields={
            "purpose": purpose,
            "code": code,
            "subject_id": subject_id,
            "manage_url": manage_url,
        },
    )
