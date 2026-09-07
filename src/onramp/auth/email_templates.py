"""Presentation-only email templates; delivery and verification stay separate.

Applications may set ``AUTH.verification_email_renderer`` to a synchronous
callable accepting ``VerificationEmailContext`` and returning ``EmailTemplate``.
The context intentionally excludes recipients, session tokens, and secrets.
Renderers must escape dynamic HTML and must not send mail or perform I/O.
"""

from dataclasses import dataclass
from html import escape
import re
from urllib.parse import urlsplit


@dataclass(frozen=True)
class EmailTemplate:
    subject: str
    text: str
    html: str


@dataclass(frozen=True)
class VerificationEmailContext:
    purpose: str
    code: str
    app_name: str
    expires_minutes: int
    public_url: str
    manage_url: str | None = None


def validate_email_template(value: object) -> EmailTemplate:
    """Fail closed on a broken override before a sender can be called."""
    if not isinstance(value, EmailTemplate):
        raise ValueError("Email renderer must return EmailTemplate.")
    if (
        not isinstance(value.subject, str)
        or not value.subject.strip()
        or len(value.subject) > 998
        or any(ord(char) < 32 or ord(char) == 127 for char in value.subject)
    ):
        raise ValueError("Email template subject must be a single nonempty line.")
    if not isinstance(value.text, str) or not value.text.strip():
        raise ValueError("Email template must include a plain-text fallback.")
    if not isinstance(value.html, str) or not value.html.strip():
        raise ValueError("Email template must include HTML.")
    return value


def safe_email_url(value: str) -> str:
    """Validate then attribute-escape app-owned links, never fetch them."""
    try:
        parts = urlsplit(value)
        if (
            parts.scheme not in {"https", "http"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError("Invalid email URL.")
        parts.port
    except (TypeError, ValueError):
        raise ValueError("Invalid email URL.") from None
    return escape(value, quote=True)


def validate_verification_context(context: VerificationEmailContext) -> None:
    if not re.fullmatch(r"[0-9]{6}", context.code):
        raise ValueError("Verification email requires a six-digit code.")
    if (
        not isinstance(context.expires_minutes, int)
        or isinstance(context.expires_minutes, bool)
        or context.expires_minutes < 1
    ):
        raise ValueError("Verification email expiry must be positive minutes.")


def default_verification_email(context: VerificationEmailContext) -> EmailTemplate:
    """A dependency-free, table-based default suitable for common mail clients."""
    validate_verification_context(context)
    actions = {
        "signup": f"create your {context.app_name} account",
        "signin": f"sign in to {context.app_name}",
        "delete_account": f"delete your {context.app_name} account",
        "notification_subscription": "verify your notification request",
    }
    if context.purpose not in actions:
        raise ValueError("Unknown verification email purpose.")
    action = actions[context.purpose]
    expiry = f"Expires in {context.expires_minutes} minute{'s' if context.expires_minutes != 1 else ''}."
    reassurance = (
        "No account is created by verifying a notification request."
        if context.purpose == "notification_subscription"
        else "Enter this code only in the app where you requested it."
    )
    footer = "If you did not request this code, you can ignore this email. Do not share this code with anyone."
    text = f"Use {context.code} to {action}. {expiry}\n\n{reassurance}\n\n{footer}"
    management = ""
    if context.manage_url:
        text += f"\n\nManage or cancel this notification request: {context.manage_url}"
        management = (
            '<p style="font-size:13px;line-height:20px">'
            f'<a href="{safe_email_url(context.manage_url)}">Manage or cancel this notification request</a></p>'
        )
    # Keep the code out of the subject and preview text, which notification
    # banners may display even when message content is hidden on a lock screen.
    subject = f"Your {context.app_name} verification code"
    html = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{escape(subject)}</title></head>
<body style="margin:0;padding:0;background-color:#f3f5f7;color:#172033;font-family:Arial,Helvetica,sans-serif">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all">Confirm your email to {escape(action)}.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f3f5f7"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;background-color:#ffffff;border:1px solid #dbe2ea;border-radius:16px">
<tr><td style="padding:28px 28px 20px;font-size:18px;font-weight:bold">{escape(context.app_name)}</td></tr>
<tr><td style="padding:0 28px 28px"><h1 style="margin:0 0 18px;font-size:28px;line-height:34px">Confirm your email</h1>
<p style="font-size:16px;line-height:25px">Use this code to {escape(action)}.</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td align="center" style="padding:24px 12px;background-color:#eef3fc;border-radius:10px">
<div style="font-family:Consolas,monospace;font-size:36px;font-weight:bold;letter-spacing:5px;color:#173f82">{context.code}</div>
<p style="margin:12px 0 0;font-size:13px;line-height:20px">{expiry}</p></td></tr></table>
<p style="font-size:14px;line-height:22px">{escape(reassurance)}</p>
<p style="font-size:12px;line-height:19px;color:#58657a">{footer}</p>{management}
</td></tr></table></td></tr></table></body></html>'''
    return validate_email_template(EmailTemplate(subject, text, html))
