"""Passwordless account lifecycle and audience classification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hmac
import inspect

from tortoise.transactions import in_transaction

from onramp.api import APIError, bearer_token

from .config import auth_config, import_callable
from .email import send_verification_code
from .models import Account, AccountSession, AudienceIdentity, EmailChallenge
from .security import (
    challenge_digest,
    email_digest,
    new_code,
    new_session_token,
    normalize_email,
    token_digest,
)


VALID_AUDIENCES = {"regular", "internal", "tester"}


class AuthenticationError(APIError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def api_email(value: object) -> str:
    """Normalize an email while keeping validation failures client-safe."""
    try:
        return normalize_email(value)
    except ValueError as error:
        raise AuthenticationError(str(error), code="invalid_email") from error


async def classification_for_email(hashed_email: str) -> str:
    override = await AudienceIdentity.get_or_none(email_hash=hashed_email)
    return override.audience_type if override else "regular"


async def create_challenge(
    email_value: object,
    purpose: str,
    *,
    subject_id: str | None = None,
    app_dir: str | None = None,
) -> EmailChallenge:
    email = api_email(email_value)
    hashed_email = email_digest(email)
    config = auth_config(app_dir)
    now = utcnow()
    resend_delay = timedelta(seconds=int(config["resend_delay_seconds"]))
    latest = await EmailChallenge.filter(
        email_hash=hashed_email,
        purpose=purpose,
        created_at__gte=now - resend_delay,
    ).first()
    if latest:
        raise AuthenticationError(
            "A code was sent recently. Wait a minute before requesting another.",
            status=429,
            code="code_rate_limited",
        )
    hourly_count = await EmailChallenge.filter(
        email_hash=hashed_email,
        created_at__gte=now - timedelta(hours=1),
    ).count()
    if hourly_count >= int(config["hourly_challenge_limit"]):
        raise AuthenticationError(
            "Too many verification codes were requested. Try again later.",
            status=429,
            code="code_rate_limited",
        )
    code = new_code()
    challenge = await EmailChallenge.create(
        email=email,
        email_hash=hashed_email,
        purpose=purpose,
        subject_id=subject_id,
        code_digest=challenge_digest(email, purpose, code),
        expires_at=now + timedelta(minutes=int(config["challenge_minutes"])),
    )
    try:
        await send_verification_code(
            email,
            purpose,
            code,
            f"verification/{challenge.id}",
            app_dir=app_dir,
        )
    except Exception:
        await challenge.delete()
        raise
    return challenge


async def consume_challenge(
    email_value: object,
    purpose: str,
    code_value: object,
    *,
    subject_id: str | None = None,
    app_dir: str | None = None,
) -> EmailChallenge:
    email = api_email(email_value)
    code = str(code_value or "").strip()
    if len(code) != 6 or not code.isdigit():
        raise AuthenticationError("Enter the six-digit verification code.")
    query = EmailChallenge.filter(
        email_hash=email_digest(email), purpose=purpose, consumed_at=None
    )
    if subject_id is not None:
        query = query.filter(subject_id=subject_id)
    challenge = await query.order_by("-created_at").first()
    now = utcnow()
    if not challenge or challenge.expires_at <= now:
        raise AuthenticationError(
            "That verification code has expired. Request a new one.",
            code="code_expired",
        )
    maximum = int(auth_config(app_dir)["challenge_attempts"])
    if challenge.attempts >= maximum:
        raise AuthenticationError(
            "Too many incorrect attempts. Request a new code.",
            status=429,
            code="code_attempts_exceeded",
        )
    expected = challenge_digest(email, purpose, code)
    if not hmac.compare_digest(challenge.code_digest, expected):
        challenge.attempts += 1
        await challenge.save(update_fields=["attempts"])
        raise AuthenticationError("That verification code is incorrect.")
    challenge.consumed_at = now
    await challenge.save(update_fields=["consumed_at"])
    return challenge


async def request_account_code(
    email_value: object, intent: str, *, app_dir: str | None = None
) -> str:
    if intent not in {"signup", "signin"}:
        raise AuthenticationError("Choose sign up or sign in.")
    email = api_email(email_value)
    account = await Account.get_or_none(email_hash=email_digest(email))
    if intent == "signup" and account:
        raise AuthenticationError(
            "An account already exists for this email. Sign in instead.",
            status=409,
            code="account_exists",
        )
    if intent == "signin" and not account:
        raise AuthenticationError(
            "No account exists for this email yet.",
            status=404,
            code="account_not_found",
        )
    await create_challenge(email, intent, app_dir=app_dir)
    return email


async def create_session(
    account: Account, *, app_dir: str | None = None
) -> tuple[str, AccountSession]:
    token = new_session_token()
    now = utcnow()
    session = await AccountSession.create(
        account_id=account.id,
        token_hash=token_digest(token),
        expires_at=now + timedelta(days=int(auth_config(app_dir)["session_days"])),
        last_used_at=now,
    )
    return token, session


async def verify_account_code(
    email_value: object,
    intent: str,
    code_value: object,
    *,
    app_dir: str | None = None,
) -> tuple[Account, str, AccountSession]:
    email = api_email(email_value)
    if intent not in {"signup", "signin"}:
        raise AuthenticationError("Choose sign up or sign in.")
    await consume_challenge(email, intent, code_value, app_dir=app_dir)
    hashed_email = email_digest(email)
    account = await Account.get_or_none(email_hash=hashed_email)
    if intent == "signup":
        if account:
            raise AuthenticationError(
                "An account already exists for this email. Sign in instead.",
                status=409,
                code="account_exists",
            )
        account = await Account.create(
            email=email,
            email_hash=hashed_email,
            audience_type=await classification_for_email(hashed_email),
            verified_at=utcnow(),
        )
    elif not account:
        raise AuthenticationError(
            "This account no longer exists.", status=404, code="account_not_found"
        )
    token, session = await create_session(account, app_dir=app_dir)
    return account, token, session


async def account_for_token(token: str) -> Account | None:
    if not token:
        return None
    session = await AccountSession.get_or_none(token_hash=token_digest(token))
    now = utcnow()
    if not session or session.expires_at <= now:
        if session:
            await session.delete()
        return None
    account = await Account.get_or_none(id=session.account_id)
    if not account:
        await session.delete()
        return None
    if session.last_used_at < now - timedelta(hours=1):
        session.last_used_at = now
        await session.save(update_fields=["last_used_at"])
    return account


def request_token(request) -> str:
    return bearer_token(request) or request.cookies.get("onramp_session", "")


async def account_for_request(request, *, required: bool = True) -> Account | None:
    token = request_token(request)
    account = await account_for_token(token)
    if required and not account:
        raise AuthenticationError(
            "Sign in to continue.", status=401, code="authentication_required"
        )
    if token and not account:
        raise AuthenticationError(
            "Your session has expired. Sign in again.",
            status=401,
            code="session_expired",
        )
    return account


async def revoke_session(token: str) -> None:
    if token:
        await AccountSession.filter(token_hash=token_digest(token)).delete()


async def request_account_deletion(
    account: Account, *, app_dir: str | None = None
) -> None:
    await create_challenge(
        account.email,
        "delete_account",
        subject_id=str(account.id),
        app_dir=app_dir,
    )


async def delete_account(
    account: Account, code_value: object, *, app_dir: str | None = None
) -> dict:
    await consume_challenge(
        account.email,
        "delete_account",
        code_value,
        subject_id=str(account.id),
        app_dir=app_dir,
    )
    from onramp.notifications.models import NotificationSubscription

    now = utcnow()
    results: dict[str, object] = {}
    async with in_transaction() as connection:
        subscriptions = NotificationSubscription.filter(
            account_id=account.id
        ).using_db(connection)
        results["anonymized_subscriptions"] = await subscriptions.update(
            account_id=None,
            contact_email=None,
            contact_email_hash=None,
            anonymized_at=now,
        )
        for reference in auth_config(app_dir).get("deletion_hooks", []):
            hook = import_callable(str(reference))
            value = hook(account=account, connection=connection, now=now)
            if inspect.isawaitable(value):
                value = await value
            results[str(reference)] = value
        await AccountSession.filter(account_id=account.id).using_db(connection).delete()
        await EmailChallenge.filter(email_hash=account.email_hash).using_db(
            connection
        ).delete()
        # AudienceIdentity is a server-controlled classification, not account
        # data. Retaining it prevents a deleted/recreated tester or internal
        # account from accidentally becoming production demand.
        await Account.filter(id=account.id).using_db(connection).delete()
    return results


async def classify_email(email_value: object, audience_type: str) -> str:
    email = normalize_email(email_value)
    if audience_type not in VALID_AUDIENCES:
        raise ValueError(
            "Audience type must be one of: " + ", ".join(sorted(VALID_AUDIENCES))
        )
    hashed_email = email_digest(email)
    if audience_type == "regular":
        await AudienceIdentity.filter(email_hash=hashed_email).delete()
    else:
        identity, _ = await AudienceIdentity.get_or_create(
            email_hash=hashed_email,
            defaults={"audience_type": audience_type},
        )
        if identity.audience_type != audience_type:
            identity.audience_type = audience_type
            await identity.save(update_fields=["audience_type", "updated_at"])
    await Account.filter(email_hash=hashed_email).update(audience_type=audience_type)
    from onramp.notifications.service import reclassify_subscriptions

    await reclassify_subscriptions(hashed_email, audience_type)
    return email


async def update_account_role(
    email_value: object, role_value: object, *, enabled: bool
) -> tuple[str, list[str]]:
    """Add or remove a normalized application role from an existing account."""
    email = normalize_email(email_value)
    role = str(role_value or "").strip().lower()
    if not role or len(role) > 64 or not all(
        character.isalnum() or character in {"-", "_"} for character in role
    ):
        raise ValueError("Role names use 1–64 letters, numbers, hyphens, or underscores.")
    account = await Account.get_or_none(email_hash=email_digest(email))
    if not account:
        raise AuthenticationError(
            "No account exists for this email yet.",
            status=404,
            code="account_not_found",
        )
    roles = set(account.roles or [])
    if enabled:
        roles.add(role)
    else:
        roles.discard(role)
    account.roles = sorted(roles)
    await account.save(update_fields=["roles", "updated_at"])
    return email, account.roles


def account_json(account: Account) -> dict:
    return {
        "id": str(account.id),
        "email": account.email,
        "verified": True,
        "audience_type": account.audience_type,
        "roles": list(account.roles or []),
        "created_at": account.created_at.isoformat(),
    }
