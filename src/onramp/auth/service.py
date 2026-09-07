"""Passwordless account lifecycle and audience classification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import hashlib
import hmac
import inspect
import uuid

from tortoise.transactions import in_transaction
from tortoise.expressions import F, Q

from onramp.api import APIError, bearer_token

from .config import auth_config, import_callable
from .email import send_verification_code
from .models import (
    Account,
    AccountSession,
    AudienceIdentity,
    ClientRequestRateLimit,
    EmailChallenge,
    EmailChallengeRateLimit,
)
from .security import (
    challenge_digest,
    client_request_digest,
    email_digest,
    new_code,
    new_session_token,
    normalize_email,
    token_digest,
)


VALID_AUDIENCES = {"regular", "internal", "tester"}


class AuthenticationError(APIError):
    pass


async def cleanup_client_request_limits(*, maximum: int = 100) -> int:
    """Delete a bounded batch; recheck expiry so a concurrent reset survives."""
    if not 1 <= maximum <= 10_000:
        raise ValueError("Client-limit cleanup batches must contain 1 to 10000 rows.")
    now = utcnow()
    stale_ids = await ClientRequestRateLimit.filter(expires_at__lte=now).order_by(
        "expires_at"
    ).limit(maximum).values_list("id", flat=True)
    if not stale_ids:
        return 0
    return await ClientRequestRateLimit.filter(
        id__in=stale_ids, expires_at__lte=now
    ).delete()


async def enforce_client_request_limit(
    request_context,
    *,
    scope: str,
    config_key: str,
    default: int = 120,
    app_dir: str | None = None,
) -> None:
    """Claim a database-atomic hourly slot shared across processes and hosts.

    This is application abuse protection, not a replacement for ingress-level
    request/body/concurrency limits. Trusted internal calls may omit context.
    HTTP contexts without an address share an unknown-client bucket.
    """

    limit = int(auth_config(app_dir).get(config_key, default))
    client_host = getattr(request_context, "client_host", None)
    if limit <= 0 or request_context is None:
        return
    await cleanup_client_request_limits()
    now = utcnow()
    expires_at = now + timedelta(hours=1)
    scope_key = client_request_digest(scope, client_host)
    _, created = await ClientRequestRateLimit.get_or_create(
        scope_key=scope_key,
        defaults={"count": 1, "expires_at": expires_at},
    )
    if created:
        return
    reset = await ClientRequestRateLimit.filter(
        scope_key=scope_key, expires_at__lte=now
    ).update(count=1, expires_at=expires_at)
    if reset:
        return
    claimed = await ClientRequestRateLimit.filter(
        scope_key=scope_key, expires_at__gt=now, count__lt=limit
    ).update(count=F("count") + 1)
    if not claimed:
        raise AuthenticationError(
            "Too many requests were submitted. Try again later.",
            status=429,
            code="request_rate_limited",
        )


@dataclass(frozen=True)
class _RateLimitClaim:
    scope_key: str
    window_started_at: datetime


def _rate_limit_scope(*parts: object) -> str:
    encoded = ":".join(str(part or "") for part in parts).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def _claim_challenge_rate_limit(
    *,
    scope_key: str,
    hashed_email: str,
    now: datetime,
    window: timedelta,
    maximum: int,
) -> _RateLimitClaim | None:
    """Atomically claim one slot in a fixed-start database window."""

    if maximum <= 0:
        return None
    row, created = await EmailChallengeRateLimit.get_or_create(
        scope_key=scope_key,
        defaults={
            "email_hash": hashed_email,
            "window_started_at": now,
            "count": 1,
        },
    )
    if created:
        return _RateLimitClaim(scope_key, now)

    cutoff = now - window
    reset = await EmailChallengeRateLimit.filter(
        scope_key=scope_key,
        window_started_at__lte=cutoff,
    ).update(window_started_at=now, count=1, updated_at=now)
    if reset:
        return _RateLimitClaim(scope_key, now)

    await row.refresh_from_db()
    claimed = await EmailChallengeRateLimit.filter(
        scope_key=scope_key,
        window_started_at=row.window_started_at,
        count__lt=maximum,
    ).update(count=F("count") + 1, updated_at=now)
    if not claimed:
        return None
    return _RateLimitClaim(scope_key, row.window_started_at)


async def _release_challenge_rate_limit(
    claim: _RateLimitClaim | None, *, now: datetime
) -> None:
    if not claim:
        return
    await EmailChallengeRateLimit.filter(
        scope_key=claim.scope_key,
        window_started_at=claim.window_started_at,
        count__gt=0,
    ).update(count=F("count") - 1, updated_at=now)


async def clear_challenge_resend_limit(
    hashed_email: str, purpose: str, subject_id: str | None
) -> None:
    """Allow a new proof after an explicit cancellation invalidates the old one."""

    await EmailChallengeRateLimit.filter(
        scope_key=_rate_limit_scope(
            "challenge-resend", hashed_email, purpose, subject_id
        )
    ).update(count=0, updated_at=utcnow())


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
    action_subject_id: str | None = None,
    app_dir: str | None = None,
) -> EmailChallenge:
    email = api_email(email_value)
    hashed_email = email_digest(email)
    config = auth_config(app_dir)
    now = utcnow()
    resend_delay = timedelta(seconds=int(config["resend_delay_seconds"]))
    resend_claim = await _claim_challenge_rate_limit(
        scope_key=_rate_limit_scope(
            "challenge-resend", hashed_email, purpose, subject_id
        ),
        hashed_email=hashed_email,
        now=now,
        window=resend_delay,
        maximum=1,
    )
    if not resend_claim:
        raise AuthenticationError(
            "A code was sent recently. Wait a minute before requesting another.",
            status=429,
            code="code_rate_limited",
        )
    hourly_claim = await _claim_challenge_rate_limit(
        scope_key=_rate_limit_scope("challenge-hourly", hashed_email),
        hashed_email=hashed_email,
        now=now,
        window=timedelta(hours=1),
        maximum=int(config["hourly_challenge_limit"]),
    )
    if not hourly_claim:
        await _release_challenge_rate_limit(resend_claim, now=utcnow())
        raise AuthenticationError(
            "Too many verification codes were requested. Try again later.",
            status=429,
            code="code_rate_limited",
        )
    code = new_code()
    challenge = None
    try:
        challenge = await EmailChallenge.create(
            email=email,
            email_hash=hashed_email,
            purpose=purpose,
            subject_id=subject_id,
            code_digest=challenge_digest(email, purpose, code),
            expires_at=now + timedelta(minutes=int(config["challenge_minutes"])),
        )
        await send_verification_code(
            email,
            purpose,
            code,
            f"verification/{challenge.id}",
            app_dir=app_dir,
            subject_id=subject_id,
            action_subject_id=action_subject_id,
        )
    except Exception:
        if challenge:
            await challenge.delete()
        failed_at = utcnow()
        await _release_challenge_rate_limit(resend_claim, now=failed_at)
        await _release_challenge_rate_limit(hourly_claim, now=failed_at)
        raise
    assert challenge is not None
    return challenge


async def consume_challenge(
    email_value: object,
    purpose: str,
    code_value: object,
    *,
    subject_id: str | None = None,
    app_dir: str | None = None,
    mark_consumed: bool = True,
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
        incremented = await EmailChallenge.filter(
            id=challenge.id,
            consumed_at=None,
            expires_at__gt=now,
            attempts__lt=maximum,
        ).update(attempts=F("attempts") + 1)
        if not incremented:
            await challenge.refresh_from_db()
            if challenge.attempts >= maximum:
                raise AuthenticationError(
                    "Too many incorrect attempts. Request a new code.",
                    status=429,
                    code="code_attempts_exceeded",
                )
            raise AuthenticationError(
                "That verification code has expired. Request a new one.",
                code="code_expired",
            )
        raise AuthenticationError("That verification code is incorrect.")
    if mark_consumed:
        consumed = await EmailChallenge.filter(
            id=challenge.id,
            consumed_at=None,
            expires_at__gt=now,
            attempts__lt=maximum,
        ).update(consumed_at=now)
        if not consumed:
            await challenge.refresh_from_db()
            if challenge.attempts >= maximum:
                raise AuthenticationError(
                    "Too many incorrect attempts. Request a new code.",
                    status=429,
                    code="code_attempts_exceeded",
                )
            raise AuthenticationError(
                "That verification code has expired. Request a new one.",
                code="code_expired",
            )
        challenge.consumed_at = now
    elif not await EmailChallenge.filter(
        id=challenge.id,
        consumed_at=None,
        expires_at__gt=now,
        attempts__lt=maximum,
    ).exists():
        raise AuthenticationError(
            "That verification code has expired. Request a new one.",
            code="code_expired",
        )
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
    from onramp.notifications.models import (
        NotificationContactToken,
        NotificationDelivery,
        NotificationSubscription,
    )

    now = utcnow()
    results: dict[str, object] = {}
    async with in_transaction() as connection:
        results["deleted_notification_contact_tokens"] = (
            await NotificationContactToken.filter(
                email_hash=account.email_hash
            ).using_db(connection).delete()
        )
        subscriptions = NotificationSubscription.filter(
            Q(account_id=account.id) | Q(contact_email_hash=account.email_hash)
        ).using_db(connection)
        subscription_ids = [item.id for item in await subscriptions]
        deliveries = await NotificationDelivery.filter(
            recipient_email_hash=account.email_hash
        ).using_db(connection)
        for delivery in deliveries:
            delivery.recipient_email_hash = None
            delivery.idempotency_key = f"anonymized/{uuid.uuid4()}"
            await delivery.save(
                using_db=connection,
                update_fields=["recipient_email_hash", "idempotency_key", "updated_at"],
            )
        results["anonymized_subscriptions"] = await subscriptions.update(
            account_id=None,
            contact_email=None,
            contact_email_hash=None,
            demand_eligible=False,
            anonymized_at=now,
            consent_generation=F("consent_generation") + 1,
        )
        results["deleted_notification_contact_tokens"] += (
            await NotificationContactToken.filter(
                email_hash=account.email_hash
            ).using_db(connection).delete()
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
        await EmailChallengeRateLimit.filter(email_hash=account.email_hash).using_db(
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
