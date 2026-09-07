"""Read-only email preflight and explicitly requested delivery smoke tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from email.utils import parseaddr
import os
from pathlib import Path

from onramp.auth.config import application_public_url, auth_config
from onramp.auth.email import (
    EmailDeliveryError,
    development_outbox,
    email_sender_address,
    send_transactional_email,
)
from onramp.auth.security import normalize_email, runtime_environment


@dataclass
class EmailReadiness:
    environment: str
    provider: str
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def check_email_configuration(app_dir: str) -> EmailReadiness:
    """Inspect local configuration without opening a DB or contacting a provider.

    A successful check is not evidence of DNS verification, API-key validity,
    provider acceptance, or inbox delivery. Never include secret values in it.
    """
    config = auth_config(app_dir)
    environment = runtime_environment()
    hosted = environment in {"staging", "production"}
    custom_sender = config.get("email_sender")
    provider = "custom" if custom_sender else "resend" if hosted else "development"
    result = EmailReadiness(environment=environment, provider=provider)
    renderer = config.get("verification_email_renderer")
    if renderer:
        reference = str(renderer).strip()
        if "." not in reference or not all(part.isidentifier() for part in reference.split(".")):
            result.errors.append("AUTH.verification_email_renderer must be a module.callable reference.")
        result.notes.append(
            "The verification email renderer has not been imported or called. "
            "Preview actual code-request emails in the development outbox to check its design."
        )
    if not config.get("enabled"):
        result.errors.append("Enable AUTH.enabled in app/settings.py for email verification.")
    try:
        application_public_url(app_dir)
    except RuntimeError as error:
        result.errors.append(str(error))

    if hosted:
        secrets = [os.getenv(name, "").strip() for name in (
            "ONRAMP_AUTH_SECRET", "ONRAMP_IDENTITY_SECRET"
        )]
        for name, value in zip(("ONRAMP_AUTH_SECRET", "ONRAMP_IDENTITY_SECRET"), secrets):
            if len(value) < 32:
                result.errors.append(f"{name} must contain at least 32 characters.")
        if secrets[0] and secrets[0] == secrets[1]:
            result.errors.append("ONRAMP_AUTH_SECRET and ONRAMP_IDENTITY_SECRET must be different.")

    if provider == "resend":
        api_key = os.getenv("RESEND_API_KEY", "").strip()
        if not api_key:
            result.errors.append("RESEND_API_KEY is not configured.")
        elif any(ord(char) < 33 or ord(char) > 126 for char in api_key):
            result.errors.append("RESEND_API_KEY must be a valid single-line API key.")
        try:
            sender = email_sender_address(config)
            domain = parseaddr(sender)[1].rsplit("@", 1)[-1].lower()
            if (
                any(domain == value or domain.endswith(f".{value}") for value in (
                    "example.com", "example.net", "example.org", "localhost"
                ))
                or domain.endswith((".invalid", ".test", ".localhost"))
            ):
                result.errors.append("Replace the placeholder ONRAMP_EMAIL_FROM with your verified sending domain.")
            if domain == "resend.dev":
                result.notes.append(
                    "resend.dev is Resend's restricted testing sender. Verify your own "
                    "domain before sending to app users."
                )
        except EmailDeliveryError as error:
            result.errors.append(str(error))
        result.notes.append(
            "Verify the sender domain in Resend and use a sending-access API key. "
            "This offline check cannot verify DNS, API-key validity, or inbox delivery."
        )
    elif provider == "custom":
        reference = str(custom_sender).strip()
        if "." not in reference or not all(part.isidentifier() for part in reference.split(".")):
            result.errors.append("AUTH.email_sender must be a module.callable reference.")
        result.notes.append(
            "AUTH.email_sender overrides the development outbox, even in development. "
            "It has not been imported or called; validate its provider setup separately."
        )
    else:
        result.notes.append(
            f"Mail stays local in {development_outbox(app_dir)}; no Resend account is needed."
        )
        if os.getenv("RESEND_API_KEY", "").strip():
            result.notes.append(
                "RESEND_API_KEY is ignored in this environment. Use a separate staging "
                "backend/configuration to test real delivery."
            )
    return result


def run_email_command(args, app_dir: str) -> int:
    """Require an explicit send, plus an extra acknowledgement in production."""
    check = bool(args.check)
    send = bool(args.send)
    dry_run = bool(args.dry_run)
    confirm = bool(args.confirm_production)
    valid_check = check and args.name is None and not args.extra and not (send or dry_run or confirm)
    valid_test = not check and args.name == "test" and len(args.extra) == 1
    if not (valid_check or valid_test) or (send and dry_run) or (confirm and not send):
        print(
            "Usage: 'onramp email --check' or 'onramp email test <email> "
            "[--send [--confirm-production]]'. A test is a preview unless --send is explicit."
        )
        return 2
    if not (Path(app_dir) / "settings.py").is_file():
        print("Run this command from an OnRamp project with app/settings.py.")
        return 1
    try:
        recipient = normalize_email(args.extra[0]) if valid_test else None
        report = check_email_configuration(app_dir)
    except ValueError:
        print("Enter one valid test recipient email address.")
        return 2
    except Exception:
        # Settings/custom code can embed credentials in its exception text.
        print("Could not load email configuration. Check app/settings.py and ONRAMP_ENVIRONMENT.")
        return 1

    print(f"Email environment: {report.environment}; delivery: {report.provider}.")
    for note in report.notes:
        print(note)
    for error in report.errors:
        print(f"Not ready: {error}")
    if report.errors:
        print("No email was sent and no database records were changed.")
        return 1
    print("Email configuration checks passed (no network or database check performed).")
    if valid_check:
        return 0
    if not send:
        print("Test preview only. Add --send to send one test message; no account or subscription is created.")
        return 0
    if report.environment == "production" and not confirm:
        print("Production test not sent. Add --confirm-production along with --send.")
        return 2

    try:
        result = asyncio.run(send_transactional_email(
            recipient,
            "OnRamp email delivery test",
            f"This is an email delivery test from your {report.environment} backend.\n\n"
            "It does not create an account, verify email ownership, or subscribe you to notifications.\n\n"
            "To test verification, request a code in the app and enter that code there.",
            app_dir=app_dir,
        ))
    except EmailDeliveryError as error:
        print(f"Test email failed: {error}")
        return 1
    except Exception:
        print("Test email failed. Check the configured sender; no account or subscription was created.")
        return 1
    if result.provider == "development":
        print("Test message written to the local outbox. No external email was sent.")
    else:
        print("Sender accepted the test message. This is not an inbox delivery confirmation; check your inbox and provider dashboard.")
    return 0
