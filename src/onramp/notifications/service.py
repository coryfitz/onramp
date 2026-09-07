"""Reusable verified subscriptions, delivery, suppression, and maintenance."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import html
import inspect
import json
import re
import secrets
from typing import Any
from urllib.parse import urlencode
import uuid
from tortoise.expressions import F, Q
from tortoise.transactions import in_transaction

from onramp.api import APIError
from onramp.auth.config import application_public_url, auth_config, import_callable
from onramp.auth.email import EmailDeliveryError, send_transactional_email
from onramp.auth.models import Account, EmailChallenge, EmailChallengeRateLimit
from onramp.auth.security import (
    email_digest,
    runtime_environment,
    sign_action_token,
    verify_action_token,
)
from onramp.auth.service import (
    AuthenticationError,
    api_email,
    classification_for_email,
    clear_challenge_resend_limit,
    cleanup_client_request_limits,
    consume_challenge,
    create_challenge,
    enforce_client_request_limit,
    utcnow,
)

from .models import (
    NotificationContactToken,
    NotificationDelivery,
    NotificationSubscription,
)


UNSUBSCRIBE_ACTION = "notification_unsubscribe"
DELIVERY_CLAIM_TIMEOUT = timedelta(minutes=5)
CONTACT_TOKEN_PREFIX = "onramp_notify_"
CONTACT_TOKEN_PATTERN = re.compile(r"onramp_notify_[A-Za-z0-9_-]{43}\Z")
SUBSCRIPTION_PAYLOAD_FIELDS = {
    "resource_type",
    "resource_id",
    "source",
    "resource_title",
    "canonical_resource_id",
    "metadata",
    "app_version",
    "email",
}


@dataclass(frozen=True)
class NotificationRequestContext:
    """Bounded request details available to an application validation hook.

    Routes use the ASGI server's trusted client for both host fields. The legacy
    ``direct_client_host`` name does not recover a socket peer after middleware.
    Never log or persist these addresses as analytics.
    """

    client_host: str | None = None
    direct_client_host: str | None = None
    user_agent: str | None = None
    origin: str | None = None


@dataclass(frozen=True)
class DeliveryOutcome:
    delivery: NotificationDelivery | None
    outcome: str
    error: str | None = None


@dataclass(frozen=True)
class _DeliveryClaim:
    """The consent snapshot associated with an atomic delivery claim."""

    subscription: NotificationSubscription | None
    outcome: str | None = None


@dataclass
class DispatchReport:
    event_key: str
    matched: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)

    def record(self, outcome: str) -> None:
        self.outcomes[outcome] = self.outcomes.get(outcome, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_key": self.event_key,
            "matched": self.matched,
            **dict(sorted(self.outcomes.items())),
        }


def _text(value: object, field_name: str, maximum: int) -> str:
    result = str(value or "").strip()
    if not result:
        raise APIError(f"{field_name} is required.")
    if len(result) > maximum:
        raise APIError(f"{field_name} is too long.")
    return result


def _canonical_resource_id(value: object) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as error:
        raise APIError(
            "Canonical resource ID must be a UUID.",
            code="invalid_canonical_resource_id",
        ) from error


def _normalized_subscription_payload(
    payload: Mapping[str, Any], account: Account | None, app_dir: str | None
) -> dict[str, Any]:
    unsupported = sorted(set(payload) - SUBSCRIPTION_PAYLOAD_FIELDS)
    if unsupported:
        raise APIError(
            "The notification request contains unsupported fields.",
            code="unsupported_fields",
            details={"fields": unsupported},
        )
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise APIError("Metadata must be a JSON object.")
    try:
        encoded_metadata = json.dumps(metadata, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise APIError("Metadata must be JSON serializable.") from error
    maximum_metadata_bytes = int(
        auth_config(app_dir).get("notification_metadata_bytes", 16_384)
    )
    if len(encoded_metadata) > maximum_metadata_bytes:
        raise APIError(
            f"Metadata cannot exceed {maximum_metadata_bytes} bytes.",
            code="metadata_too_large",
        )

    return {
        "resource_type": _text(payload.get("resource_type"), "Resource type", 64),
        "resource_id": _text(payload.get("resource_id"), "Resource ID", 255),
        "source": _text(payload.get("source") or "app", "Source", 64),
        "resource_title": _text(
            payload.get("resource_title"), "Resource title", 500
        ),
        "canonical_resource_id": _canonical_resource_id(
            payload.get("canonical_resource_id")
        ),
        "metadata": metadata,
        "app_version": str(payload.get("app_version") or "").strip()[:32] or None,
        "email": account.email if account else api_email(payload.get("email")),
    }


async def _validated_subscription_payload(
    payload: Mapping[str, Any],
    account: Account | None,
    app_dir: str | None,
    request_context: NotificationRequestContext | None,
) -> dict[str, Any]:
    normalized = _normalized_subscription_payload(payload, account, app_dir)
    validator_reference = auth_config(app_dir).get(
        "notification_subscription_validator"
    )
    if not validator_reference:
        return normalized
    validator = import_callable(str(validator_reference))
    result = validator(
        payload=dict(normalized),
        account=account,
        request_context=request_context,
        app_dir=app_dir,
    )
    if inspect.isawaitable(result):
        result = await result
    if result is None:
        result = normalized
    if not isinstance(result, Mapping):
        raise RuntimeError(
            "AUTH.notification_subscription_validator must return a mapping or None"
        )
    # Validate hook output again. In particular, application code may resolve
    # and persist a canonical UUID without bypassing framework limits.
    return _normalized_subscription_payload(result, account, app_dir)


async def _run_subscription_ready_hook(
    subscription: NotificationSubscription,
    *,
    app_dir: str | None,
    request_context: NotificationRequestContext | None,
) -> None:
    """Run the application's retriable post-verification subscription hook."""

    hook_reference = auth_config(app_dir).get("notification_subscription_ready_hook")
    if not hook_reference:
        return
    hook = import_callable(str(hook_reference))
    result = hook(
        subscription=subscription,
        app_dir=app_dir,
        request_context=request_context,
    )
    if inspect.isawaitable(result):
        await result


async def enforce_subscription_request_limit(
    request_context: NotificationRequestContext | None,
    *,
    app_dir: str | None = None,
) -> None:
    """Apply a shared, database-atomic client-address request limit."""
    await enforce_client_request_limit(
        request_context,
        scope="notification",
        config_key="notification_ip_hourly_limit",
        app_dir=app_dir,
    )


async def _audience(
    environment: str, hashed_email: str, account: Account | None
) -> tuple[str, bool]:
    if environment in {"development", "test"}:
        return "development", False
    if environment == "staging":
        return "beta", False
    audience = (
        account.audience_type if account else await classification_for_email(hashed_email)
    )
    return audience, audience == "regular"


def _subscription_challenge_subject(
    subscription: NotificationSubscription,
) -> str:
    return f"{subscription.id}:{subscription.consent_generation}"


def _subscription_challenges(
    subscription: NotificationSubscription, *, unconsumed_only: bool = True
):
    subscription_id = str(subscription.id)
    query = EmailChallenge.filter(
        Q(subject_id=subscription_id) | Q(subject_id__startswith=f"{subscription_id}:"),
        purpose="notification_subscription",
    )
    return query.filter(consumed_at=None) if unconsumed_only else query


async def request_subscription(
    payload: dict,
    account: Account | None = None,
    *,
    app_dir: str | None = None,
    request_context: NotificationRequestContext | None = None,
    notification_token: object = None,
) -> tuple[NotificationSubscription, bool]:
    await enforce_subscription_request_limit(request_context, app_dir=app_dir)
    environment = runtime_environment()
    if notification_token is not None and account is None:
        # Reject stale capabilities before an application validator can persist
        # canonical resource data. Check again atomically at the write boundary.
        preliminary = _normalized_subscription_payload(payload, None, app_dir)
        await _validated_contact_token(
            notification_token,
            email_digest(preliminary["email"]),
            preliminary["resource_type"],
            environment,
        )
    normalized = await _validated_subscription_payload(
        payload, account, app_dir, request_context
    )
    email = normalized["email"]
    hashed_email = account.email_hash if account else email_digest(email)
    audience, eligible_audience = await _audience(environment, hashed_email, account)
    verified_at = account.verified_at if account else None
    if notification_token is not None and account is None:
        async with in_transaction() as connection:
            contact = await _validated_contact_token(
                notification_token, hashed_email, normalized["resource_type"],
                environment, connection=connection,
            )
            subscription = await _persist_subscription_request(
                normalized, account, hashed_email, environment, audience,
                eligible_audience, contact.verified_at,
            )
    else:
        subscription = await _persist_subscription_request(
            normalized, account, hashed_email, environment, audience,
            eligible_audience, verified_at,
        )

    # Unproved anonymous callers never gain authority from prior membership.
    needs_verification = (
        account is None and notification_token is None
    ) or subscription.contact_verified_at is None
    if needs_verification:
        challenge_subject = _subscription_challenge_subject(subscription)
        try:
            await create_challenge(
                email,
                "notification_subscription",
                subject_id=challenge_subject,
                action_subject_id=str(subscription.id),
                app_dir=app_dir,
            )
        except AuthenticationError as error:
            active_challenge = await EmailChallenge.filter(
                email_hash=hashed_email,
                purpose="notification_subscription",
                subject_id=challenge_subject,
                consumed_at=None,
                expires_at__gt=utcnow(),
            ).exists()
            if error.code != "code_rate_limited" or not active_challenge:
                raise
    else:
        consent_generation = subscription.consent_generation
        pending_challenges = await _subscription_challenges(subscription)
        for challenge in pending_challenges:
            await clear_challenge_resend_limit(
                subscription.contact_email_hash,
                "notification_subscription",
                challenge.subject_id,
            )
        if pending_challenges:
            await EmailChallenge.filter(
                id__in=[challenge.id for challenge in pending_challenges]
            ).delete()
        await subscription.refresh_from_db()
        _require_current_consent(subscription, consent_generation)
        await _run_subscription_ready_hook(
            subscription,
            app_dir=app_dir,
            request_context=request_context,
        )
    return subscription, needs_verification


async def _persist_subscription_request(
    normalized: dict[str, Any],
    account: Account | None,
    hashed_email: str,
    environment: str,
    audience: str,
    eligible_audience: bool,
    verified_at: datetime | None,
) -> NotificationSubscription:

    identity = {
        "contact_email_hash": hashed_email,
        "resource_type": normalized["resource_type"],
        "source": normalized["source"],
        "resource_id": normalized["resource_id"],
        "environment": environment,
    }
    demand_eligible = (
        eligible_audience
        and verified_at is not None
    )
    values = {
        "resource_title": normalized["resource_title"],
        "canonical_resource_id": normalized["canonical_resource_id"],
        "metadata": normalized["metadata"],
        "account_id": account.id if account else None,
        "contact_email": normalized["email"],
        "audience_type": audience,
        "demand_eligible": demand_eligible,
        "source_app_version": normalized["app_version"],
    }
    subscription, created = await NotificationSubscription.get_or_create(
        **identity,
        defaults={
            **values,
            "contact_verified_at": verified_at,
        },
    )
    if not created:
        expected_generation = subscription.consent_generation
        # An unproved caller must not overwrite application-validated data or
        # trusted contact/audience state on an already-verified association.
        # It can only refresh activity and request a new proof.
        trusted_anonymous = verified_at is None and subscription.contact_verified_at
        if trusted_anonymous:
            await subscription.refresh_from_db()
            updated = subscription.consent_generation == expected_generation
        else:
            if subscription.suppressed_at and verified_at is None:
                values["contact_email"] = None
                values["account_id"] = None
                values["demand_eligible"] = False
            update_values = {
                key: value
                for key, value in values.items()
                if value is not None
                or key not in {"account_id", "source_app_version"}
            }
            if verified_at is not None:
                update_values.update(
                    contact_verified_at=verified_at,
                    suppressed_at=None,
                    suppression_reason=None,
                    demand_eligible=eligible_audience,
                )
            update_values["updated_at"] = utcnow()
            updated = await NotificationSubscription.filter(
                id=subscription.id,
                consent_generation=expected_generation,
            ).update(**update_values)
            await subscription.refresh_from_db()
        if not updated:
            raise AuthenticationError(
                "This notification request was cancelled. Request it again to continue.",
                status=409,
                code="subscription_cancelled",
            )

    return subscription


def _require_current_consent(
    subscription: NotificationSubscription, consent_generation: int
) -> None:
    if (
        subscription.consent_generation != consent_generation
        or subscription.suppressed_at is not None
        or subscription.anonymized_at is not None
    ):
        raise AuthenticationError(
            "This notification request was cancelled. Request it again to continue.",
            status=409, code="subscription_cancelled",
        )


def _contact_token_hash(token: object) -> str | None:
    if not isinstance(token, str) or not CONTACT_TOKEN_PATTERN.fullmatch(token):
        return None
    return hashlib.sha256(token.encode("ascii")).hexdigest()


async def _validated_contact_token(
    token: object,
    hashed_email: str,
    resource_type: str,
    environment: str,
    *,
    connection=None,
) -> NotificationContactToken:
    token_hash = _contact_token_hash(token)
    contact = None
    if token_hash:
        query = NotificationContactToken.filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=utcnow()),
            token_hash=token_hash, email_hash=hashed_email,
            resource_type=resource_type, environment=environment,
        )
        if connection is not None:
            # A no-op UPDATE locks the capability until subscription persistence
            # commits on SQLite and server databases. Concurrent revocation
            # either wins first or waits for this already-authorized request.
            query = query.using_db(connection)
            if await query.update(verified_at=F("verified_at")):
                contact = await query.first()
        else:
            contact = await query.first()
    if contact is None:
        raise AuthenticationError(
            "Verify your email again to request this notification.",
            status=401, code="notification_token_invalid",
        )
    return contact


async def issue_notification_contact_token(
    subscription: NotificationSubscription, *, app_dir: str | None = None
) -> tuple[str, NotificationContactToken]:
    """Issue revocable notification proof, optionally with a configured lifetime."""
    configured_days = auth_config(app_dir).get("notification_contact_token_days")
    days = int(configured_days) if configured_days is not None else None
    if days is not None and days < 1:
        raise ValueError("Notification contact token lifetime must be at least one day.")
    token = CONTACT_TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = utcnow()
    async with in_transaction() as connection:
        active = await NotificationSubscription.filter(
            id=subscription.id,
            contact_email_hash=subscription.contact_email_hash,
            contact_verified_at__isnull=False,
            consent_generation=subscription.consent_generation,
            suppressed_at=None, anonymized_at=None,
        ).using_db(connection).update(consent_generation=F("consent_generation"))
        if not active:
            raise AuthenticationError(
                "This notification request was cancelled. Request it again to continue.",
                status=409, code="subscription_cancelled",
            )
        contact = await NotificationContactToken.create(
            token_hash=_contact_token_hash(token),
            email_hash=subscription.contact_email_hash,
            resource_type=subscription.resource_type,
            environment=subscription.environment,
            verified_at=now,
            expires_at=now + timedelta(days=days) if days is not None else None,
            using_db=connection,
        )
    return token, contact


async def revoke_notification_contact_token(token: object) -> None:
    """Forget exactly one device capability, without cancelling subscriptions."""
    token_hash = _contact_token_hash(token)
    if token_hash:
        await NotificationContactToken.filter(
            token_hash=token_hash, environment=runtime_environment()
        ).delete()


async def verify_subscription(
    subscription_id: object,
    email_value: object,
    code_value: object,
    *,
    app_dir: str | None = None,
    request_context: NotificationRequestContext | None = None,
) -> NotificationSubscription:
    await enforce_subscription_request_limit(request_context, app_dir=app_dir)
    email = api_email(email_value)
    try:
        subscription = await NotificationSubscription.get_or_none(id=subscription_id)
    except (TypeError, ValueError):
        subscription = None
    if not subscription or subscription.contact_email_hash != email_digest(email):
        raise APIError(
            "This notification request could not be found.",
            status=404,
            code="subscription_not_found",
        )
    consent_generation = subscription.consent_generation
    challenge = await consume_challenge(
        email,
        "notification_subscription",
        code_value,
        subject_id=_subscription_challenge_subject(subscription),
        app_dir=app_dir,
        mark_consumed=False,
    )
    now = utcnow()
    restored = await NotificationSubscription.filter(
        id=subscription.id,
        contact_email_hash=email_digest(email),
        consent_generation=consent_generation,
    ).update(
        contact_verified_at=subscription.contact_verified_at or now,
        contact_email=email,
        suppressed_at=None,
        suppression_reason=None,
        demand_eligible=(
            subscription.environment == "production"
            and subscription.audience_type == "regular"
        ),
        updated_at=now,
    )
    if not restored:
        raise AuthenticationError(
            "This notification request was cancelled. Request it again to continue.",
            status=409,
            code="subscription_cancelled",
        )
    await subscription.refresh_from_db()
    if (
        subscription.consent_generation != consent_generation
        or subscription.suppressed_at is not None
    ):
        raise AuthenticationError(
            "This notification request was cancelled. Request it again to continue.",
            status=409,
            code="subscription_cancelled",
        )
    await _run_subscription_ready_hook(
        subscription,
        app_dir=app_dir,
        request_context=request_context,
    )
    consumed = await EmailChallenge.filter(
        id=challenge.id, consumed_at=None
    ).update(consumed_at=utcnow())
    if not consumed:
        await subscription.refresh_from_db()
        if subscription.suppressed_at is not None:
            raise AuthenticationError(
                "This notification request was cancelled. Request it again to continue.",
                status=409,
                code="subscription_cancelled",
            )
        raise AuthenticationError(
            "That verification code has expired. Request a new one.",
            code="code_expired",
        )
    return subscription


async def reclassify_subscriptions(hashed_email: str, audience_type: str) -> None:
    subscriptions = NotificationSubscription.filter(contact_email_hash=hashed_email)
    await subscriptions.update(audience_type=audience_type, demand_eligible=False)
    if audience_type == "regular":
        await subscriptions.filter(
            environment="production",
            contact_verified_at__isnull=False,
            anonymized_at__isnull=True,
            suppressed_at__isnull=True,
        ).update(demand_eligible=True)


def notification_unsubscribe_token(subscription: NotificationSubscription) -> str:
    return sign_action_token(UNSUBSCRIBE_ACTION, subscription.id)


def notification_unsubscribe_url(
    subscription: NotificationSubscription, *, app_dir: str | None = None
) -> str:
    return f"{application_public_url(app_dir)}{notification_unsubscribe_path(subscription)}"


def notification_unsubscribe_path(subscription: NotificationSubscription) -> str:
    """Return a signed relative path for platform-aware API clients."""

    token = notification_unsubscribe_token(subscription)
    return "/api/notifications/unsubscribe?" + urlencode({"token": token})


async def subscription_for_unsubscribe_token(
    token: object,
) -> NotificationSubscription:
    subscription_id = verify_action_token(UNSUBSCRIBE_ACTION, token)
    try:
        subscription = (
            await NotificationSubscription.get_or_none(id=subscription_id)
            if subscription_id
            else None
        )
    except (TypeError, ValueError):
        subscription = None
    if not subscription or subscription.anonymized_at:
        raise APIError(
            "This unsubscribe link is invalid.",
            status=400,
            code="invalid_unsubscribe_token",
        )
    return subscription


async def _delete_cancelled_subscription_challenges(
    subscription_id: object,
    contact_email_hash: str,
    consent_generation: int,
) -> None:
    """Invalidate only proofs issued before this cancellation linearized."""

    legacy_subject = str(subscription_id)
    cancelled_subjects = [
        legacy_subject,
        f"{legacy_subject}:{consent_generation}",
    ]
    challenges = await EmailChallenge.filter(
        purpose="notification_subscription",
        subject_id__in=cancelled_subjects,
    )
    for challenge in challenges:
        await clear_challenge_resend_limit(
            contact_email_hash,
            "notification_subscription",
            challenge.subject_id,
        )
    if challenges:
        await EmailChallenge.filter(id__in=[item.id for item in challenges]).delete()


async def suppress_subscription(
    token: object, *, reason: str = "recipient_unsubscribed"
) -> NotificationSubscription:
    subscription = await subscription_for_unsubscribe_token(token)
    now = utcnow()
    normalized_reason = _text(reason, "Suppression reason", 64)
    cancelled_generation = subscription.consent_generation
    cancelled = await NotificationSubscription.filter(
        id=subscription.id,
        consent_generation=cancelled_generation,
    ).update(
        suppressed_at=subscription.suppressed_at or now,
        suppression_reason=subscription.suppression_reason or normalized_reason,
        demand_eligible=False,
        contact_email=None,
        account_id=None,
        consent_generation=F("consent_generation") + 1,
        updated_at=now,
    )
    await subscription.refresh_from_db()
    if not cancelled:
        # A concurrent cancellation already owns cleanup for this generation.
        # Treat signed-link replays as an idempotent success without invalidating
        # a proof that may have been requested after that cancellation.
        return subscription
    # A signed manage link can cancel a request before its email proof is
    # entered. Never let a code issued before that cancellation restore consent;
    # a later explicit subscription request will issue a fresh challenge.
    await _delete_cancelled_subscription_challenges(
        subscription.id,
        subscription.contact_email_hash,
        cancelled_generation,
    )
    return subscription


def _delivery_idempotency_key(
    recipient_email_hash: str, event_key: str, environment: str
) -> str:
    digest = hashlib.sha256(
        f"{recipient_email_hash}:{event_key}:{environment}".encode("utf-8")
    ).hexdigest()
    return f"notification/{digest}"


def _delivery_exclusion(subscription: NotificationSubscription) -> str | None:
    if subscription.anonymized_at:
        return "anonymized"
    if subscription.suppressed_at:
        return "suppressed"
    if not subscription.contact_verified_at:
        return "unverified"
    if not subscription.contact_email:
        return "anonymized"
    return None


async def _claim_delivery_with_consent(
    subscription: NotificationSubscription,
    delivery: NotificationDelivery,
    *,
    expected_status: str,
    expected_attempts: int,
    attempted_at: datetime,
    metadata: Mapping[str, Any],
) -> _DeliveryClaim:
    """Linearize a provider-send claim against unsubscribe/anonymization.

    The no-op generation update is intentional. It takes the same database row
    write lock as suppression, including on SQLite, while the delivery state is
    claimed in the same short transaction. Provider I/O happens only after the
    transaction commits. If cancellation wins first, the guarded update cannot
    match and no email is sent; if this claim wins first, that consent snapshot
    owns the one provider attempt even if cancellation follows immediately.
    """

    expected_generation = subscription.consent_generation
    expected_hash = subscription.contact_email_hash
    async with in_transaction() as connection:
        consent_claimed = await NotificationSubscription.filter(
            id=subscription.id,
            consent_generation=expected_generation,
            contact_email_hash=expected_hash,
            contact_verified_at__isnull=False,
            contact_email__isnull=False,
            anonymized_at__isnull=True,
            suppressed_at__isnull=True,
        ).using_db(connection).update(
            consent_generation=F("consent_generation")
        )
        if not consent_claimed:
            current = await NotificationSubscription.get_or_none(
                id=subscription.id, using_db=connection
            )
            return _DeliveryClaim(
                current,
                (_delivery_exclusion(current) if current else "anonymized") or "busy",
            )

        claimed_subscription = await NotificationSubscription.get(
            id=subscription.id, using_db=connection
        )
        delivery_claimed = await NotificationDelivery.filter(
            id=delivery.id,
            status=expected_status,
            attempt_count=expected_attempts,
        ).using_db(connection).update(
            status="sending",
            attempt_count=expected_attempts + 1,
            last_attempt_at=attempted_at,
            last_error=None,
            metadata=dict(metadata),
        )
        if not delivery_claimed:
            current_delivery = await NotificationDelivery.get(
                id=delivery.id, using_db=connection
            )
            return _DeliveryClaim(
                None,
                "already_sent" if current_delivery.status == "sent" else "busy",
            )
    return _DeliveryClaim(claimed_subscription)


async def _resolve_content(
    value: str
    | Callable[[NotificationSubscription], str | Awaitable[str]],
    subscription: NotificationSubscription,
) -> str:
    result = value(subscription) if callable(value) else value
    if inspect.isawaitable(result):
        result = await result
    return str(result or "")


def _append_unsubscribe(
    text_body: str, html_body: str | None, unsubscribe_url: str
) -> tuple[str, str | None]:
    text = (
        text_body.rstrip()
        + "\n\nYou requested this notification. Stop notifications for this request: "
        + unsubscribe_url
    )
    if html_body is None:
        return text, None
    url = html.escape(unsubscribe_url, quote=True)
    footer = (
        '<p style="margin:20px 12px;text-align:center;color:#666;'
        'font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:18px">'
        'You requested this notification. '
        + f'<a href="{url}">Stop notifications for this request</a>.</p>'
    )
    # Complete document templates and older HTML fragments are both supported.
    # Never put the consent footer outside the document's closing body element.
    closing_body = re.search(r"</body\s*>", html_body, flags=re.IGNORECASE)
    if closing_body:
        return text, html_body[:closing_body.start()] + footer + html_body[closing_body.start():]
    return text, html_body.rstrip() + footer


async def dispatch_notification(
    subscription: NotificationSubscription,
    event_key: object,
    *,
    subject: str | Callable[[NotificationSubscription], str | Awaitable[str]],
    text_body: str | Callable[[NotificationSubscription], str | Awaitable[str]],
    html_body: (
        str | Callable[[NotificationSubscription], str | Awaitable[str]] | None
    ) = None,
    metadata: Mapping[str, Any] | None = None,
    retry_failed: bool = False,
    app_dir: str | None = None,
) -> DeliveryOutcome:
    """Send one retry-safe event notification to one eligible subscription."""

    normalized_event_key = _text(event_key, "Event key", 255)
    await subscription.refresh_from_db()
    exclusion = _delivery_exclusion(subscription)
    if exclusion:
        return DeliveryOutcome(None, exclusion)

    delivery_metadata = dict(metadata or {})
    try:
        json.dumps(delivery_metadata)
    except (TypeError, ValueError) as error:
        raise APIError("Delivery metadata must be JSON serializable.") from error
    delivery, _created = await NotificationDelivery.get_or_create(
        recipient_email_hash=subscription.contact_email_hash,
        event_key=normalized_event_key,
        environment=subscription.environment,
        defaults={
            "subscription_id": subscription.id,
            "idempotency_key": _delivery_idempotency_key(
                subscription.contact_email_hash,
                normalized_event_key,
                subscription.environment,
            ),
            "metadata": delivery_metadata,
        },
    )
    if delivery.status == "sent":
        if subscription.notified_at != delivery.sent_at:
            subscription.notified_at = delivery.sent_at or subscription.notified_at
            await subscription.save(update_fields=["notified_at", "updated_at"])
        return DeliveryOutcome(delivery, "already_sent")
    if delivery.status == "failed" and not retry_failed:
        return DeliveryOutcome(delivery, "failed", delivery.last_error)
    now = utcnow()
    if (
        delivery.status == "sending"
        and delivery.last_attempt_at
        and delivery.last_attempt_at > now - DELIVERY_CLAIM_TIMEOUT
    ):
        return DeliveryOutcome(delivery, "busy")

    expected_status = delivery.status
    expected_attempts = delivery.attempt_count
    claim = await _claim_delivery_with_consent(
        subscription,
        delivery,
        expected_status=expected_status,
        expected_attempts=expected_attempts,
        attempted_at=now,
        metadata=delivery_metadata,
    )
    if claim.outcome:
        await delivery.refresh_from_db()
        return DeliveryOutcome(delivery, claim.outcome)
    claimed_subscription = claim.subscription
    if claimed_subscription is None:  # pragma: no cover - defensive invariant
        await delivery.refresh_from_db()
        return DeliveryOutcome(delivery, "busy")

    try:
        resolved_subject = await _resolve_content(subject, claimed_subscription)
        resolved_text = await _resolve_content(text_body, claimed_subscription)
        resolved_html = (
            await _resolve_content(html_body, claimed_subscription)
            if html_body is not None
            else None
        )
        resolved_text, resolved_html = _append_unsubscribe(
            resolved_text,
            resolved_html,
            notification_unsubscribe_url(claimed_subscription, app_dir=app_dir),
        )
        send_result = await send_transactional_email(
            claimed_subscription.contact_email,
            resolved_subject,
            resolved_text,
            resolved_html,
            delivery.idempotency_key,
            app_dir=app_dir,
            message_fields={
                "purpose": "notification",
                "subscription_id": str(claimed_subscription.id),
                "event_key": normalized_event_key,
            },
        )
    except Exception as error:
        safe_error = (
            str(error) if isinstance(error, EmailDeliveryError)
            else "Notification delivery failed. Check the message configuration and sender."
        )
        await NotificationDelivery.filter(id=delivery.id).update(
            status="failed", last_error=safe_error
        )
        await delivery.refresh_from_db()
        return DeliveryOutcome(delivery, "failed", safe_error)

    sent_at = utcnow()
    await NotificationDelivery.filter(id=delivery.id).update(
        status="sent",
        provider=send_result.provider,
        provider_message_id=send_result.message_id,
        last_error=None,
        sent_at=sent_at,
    )
    await NotificationSubscription.filter(id=subscription.id).update(
        notified_at=sent_at, updated_at=sent_at
    )
    subscription.notified_at = sent_at
    await delivery.refresh_from_db()
    return DeliveryOutcome(delivery, "sent")


def _subscription_query(
    *,
    resource_type: str | None = None,
    source: str | None = None,
    resource_ids: Iterable[str] | None = None,
    canonical_resource_id: object | None = None,
    environment: str | None = None,
    unnotified_only: bool = False,
    deliverable_only: bool = False,
):
    query = NotificationSubscription.all()
    if resource_type:
        query = query.filter(resource_type=resource_type)
    if source:
        query = query.filter(source=source)
    if resource_ids:
        query = query.filter(resource_id__in=list(resource_ids))
    if canonical_resource_id:
        query = query.filter(
            canonical_resource_id=_canonical_resource_id(canonical_resource_id)
        )
    if environment:
        query = query.filter(environment=environment)
    if unnotified_only:
        query = query.filter(notified_at__isnull=True)
    if deliverable_only:
        query = query.filter(
            contact_verified_at__isnull=False,
            contact_email__isnull=False,
            anonymized_at__isnull=True,
            suppressed_at__isnull=True,
        )
    return query


async def dispatch_subscriptions(
    event_key: object,
    *,
    subject: str | Callable[[NotificationSubscription], str | Awaitable[str]],
    text_body: str | Callable[[NotificationSubscription], str | Awaitable[str]],
    html_body: (
        str | Callable[[NotificationSubscription], str | Awaitable[str]] | None
    ) = None,
    resource_type: str | None = None,
    source: str | None = None,
    resource_ids: Iterable[str] | None = None,
    canonical_resource_id: object | None = None,
    environment: str | None = None,
    unnotified_only: bool = False,
    metadata: Mapping[str, Any] | None = None,
    retry_failed: bool = False,
    dry_run: bool = False,
    app_dir: str | None = None,
) -> DispatchReport:
    """Dispatch one application event to every matching deliverable subscriber."""

    normalized_event_key = _text(event_key, "Event key", 255)
    subscriptions = await _subscription_query(
        resource_type=resource_type,
        source=source,
        resource_ids=resource_ids,
        canonical_resource_id=canonical_resource_id,
        environment=environment,
        unnotified_only=unnotified_only,
        deliverable_only=True,
    ).order_by("requested_at")
    report = DispatchReport(event_key=normalized_event_key, matched=len(subscriptions))
    seen_recipients: set[tuple[str, str, str]] = set()
    for subscription in subscriptions:
        if dry_run:
            recipient_key = (
                subscription.contact_email_hash,
                normalized_event_key,
                subscription.environment,
            )
            if recipient_key in seen_recipients:
                report.record("duplicate_recipient")
                continue
            seen_recipients.add(recipient_key)
            existing = await NotificationDelivery.get_or_none(
                recipient_email_hash=subscription.contact_email_hash,
                event_key=normalized_event_key,
                environment=subscription.environment,
            )
            if existing and existing.status == "sent":
                report.record("already_sent")
            elif existing and existing.status == "failed" and not retry_failed:
                report.record("failed")
            else:
                report.record("would_send")
            continue
        outcome = await dispatch_notification(
            subscription,
            normalized_event_key,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            metadata=metadata,
            retry_failed=retry_failed,
            app_dir=app_dir,
        )
        report.record(outcome.outcome)
    return report


async def notification_report(
    *,
    resource_type: str | None = None,
    source: str | None = None,
    resource_ids: Iterable[str] | None = None,
    canonical_resource_id: object | None = None,
    environment: str | None = None,
    unnotified_only: bool = False,
) -> dict[str, int]:
    """Return privacy-safe aggregate subscription and delivery counts."""

    query = _subscription_query(
        resource_type=resource_type,
        source=source,
        resource_ids=resource_ids,
        canonical_resource_id=canonical_resource_id,
        environment=environment,
        unnotified_only=unnotified_only,
    )
    subscriptions = await query
    ids = [item.id for item in subscriptions]
    deliveries = (
        await NotificationDelivery.filter(subscription_id__in=ids)
        if ids
        else []
    )
    return {
        "subscriptions": len(subscriptions),
        "verified": sum(bool(item.contact_verified_at) for item in subscriptions),
        "deliverable": sum(_delivery_exclusion(item) is None for item in subscriptions),
        "demand_eligible": sum(bool(item.demand_eligible) for item in subscriptions),
        "suppressed": sum(bool(item.suppressed_at) for item in subscriptions),
        "anonymized": sum(bool(item.anonymized_at) for item in subscriptions),
        "deliveries": len(deliveries),
        "sent": sum(item.status == "sent" for item in deliveries),
        "failed": sum(item.status == "failed" for item in deliveries),
    }


async def cleanup_notification_data(
    *,
    unverified_days: int | None = None,
    app_dir: str | None = None,
) -> dict[str, int]:
    """Remove expired proofs and abandoned unverified subscriptions."""

    days = int(
        unverified_days
        if unverified_days is not None
        else auth_config(app_dir).get("unverified_subscription_days", 30)
    )
    if days < 1:
        raise ValueError("Unverified subscription retention must be at least one day.")
    now = utcnow()
    expired_challenges = await EmailChallenge.filter(expires_at__lte=now).delete()
    expired_contact_tokens = await NotificationContactToken.filter(
        expires_at__isnull=False, expires_at__lte=now
    ).delete()
    abandoned = await NotificationSubscription.filter(
        contact_verified_at__isnull=True,
        anonymized_at__isnull=True,
        updated_at__lte=now - timedelta(days=days),
    )
    abandoned_ids = [str(item.id) for item in abandoned]
    related_challenges = 0
    if abandoned_ids:
        challenge_scope = Q(subject_id__in=abandoned_ids)
        for abandoned_id in abandoned_ids:
            challenge_scope |= Q(subject_id__startswith=f"{abandoned_id}:")
        related_challenges = await EmailChallenge.filter(
            challenge_scope,
            purpose="notification_subscription",
        ).delete()
        await NotificationSubscription.filter(
            id__in=[item.id for item in abandoned]
        ).delete()
    stale_rate_limits = await EmailChallengeRateLimit.filter(
        updated_at__lte=now - timedelta(hours=24)
    ).delete()
    expired_client_limits = await cleanup_client_request_limits(maximum=10_000)
    return {
        "expired_challenges": expired_challenges,
        "expired_contact_tokens": expired_contact_tokens,
        "related_challenges": related_challenges,
        "unverified_subscriptions": len(abandoned),
        "challenge_rate_limits": stale_rate_limits,
        "client_request_rate_limits": expired_client_limits,
    }


async def anonymize_notification_contact(email_value: object) -> dict[str, int]:
    """Remove one contact's notification PII while retaining aggregate history."""

    hashed_email = email_digest(api_email(email_value))
    now = utcnow()
    async with in_transaction() as connection:
        contact_tokens = await NotificationContactToken.filter(
            email_hash=hashed_email
        ).using_db(connection).delete()
        subscriptions = await NotificationSubscription.filter(
            contact_email_hash=hashed_email
        ).using_db(connection)
        subscription_ids = [item.id for item in subscriptions]
        matching_deliveries = await NotificationDelivery.filter(
            recipient_email_hash=hashed_email
        ).using_db(connection)
        for delivery in matching_deliveries:
            delivery.recipient_email_hash = None
            delivery.idempotency_key = f"anonymized/{uuid.uuid4()}"
            await delivery.save(
                using_db=connection,
                update_fields=["recipient_email_hash", "idempotency_key", "updated_at"],
            )
        deliveries = len(matching_deliveries)
        if subscription_ids:
            await NotificationSubscription.filter(
                id__in=subscription_ids
            ).using_db(connection).update(
                account_id=None,
                contact_email=None,
                contact_email_hash=None,
                demand_eligible=False,
                anonymized_at=now,
                consent_generation=F("consent_generation") + 1,
            )
        # Include a token issued by verification while this operation was
        # waiting to anonymize that subscription.
        contact_tokens += await NotificationContactToken.filter(
            email_hash=hashed_email
        ).using_db(connection).delete()
        challenges = await EmailChallenge.filter(
            email_hash=hashed_email,
            purpose="notification_subscription",
        ).using_db(connection).delete()
        rate_limits = await EmailChallengeRateLimit.filter(
            email_hash=hashed_email
        ).using_db(connection).delete()
    return {
        "anonymized_subscriptions": len(subscription_ids),
        "anonymized_deliveries": deliveries,
        "deleted_notification_challenges": challenges,
        "deleted_challenge_rate_limits": rate_limits,
        "deleted_notification_contact_tokens": contact_tokens,
    }
