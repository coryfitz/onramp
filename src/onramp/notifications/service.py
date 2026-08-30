"""Reusable verified waitlist and notification-subscription operations."""

from __future__ import annotations

from onramp.api import APIError
from onramp.auth.models import Account
from onramp.auth.security import email_digest, runtime_environment
from onramp.auth.service import (
    api_email,
    classification_for_email,
    consume_challenge,
    create_challenge,
    utcnow,
)

from .models import NotificationSubscription


def _text(value: object, field: str, maximum: int) -> str:
    result = str(value or "").strip()
    if not result:
        raise APIError(f"{field} is required.")
    if len(result) > maximum:
        raise APIError(f"{field} is too long.")
    return result


async def _audience(
    environment: str, hashed_email: str, account: Account | None
) -> tuple[str, bool]:
    if environment in {"development", "test"}:
        return "development", False
    if environment == "staging":
        return "beta", False
    audience = (
        account.audience_type
        if account
        else await classification_for_email(hashed_email)
    )
    return audience, audience == "regular"


async def request_subscription(
    payload: dict,
    account: Account | None = None,
    *,
    app_dir: str | None = None,
) -> tuple[NotificationSubscription, bool]:
    resource_type = _text(payload.get("resource_type"), "Resource type", 64)
    resource_id = _text(payload.get("resource_id"), "Resource ID", 255)
    source = _text(payload.get("source") or "app", "Source", 64)
    title = _text(payload.get("resource_title"), "Resource title", 500)
    app_version = str(payload.get("app_version") or "").strip()[:32] or None
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise APIError("Metadata must be a JSON object.")

    email = account.email if account else api_email(payload.get("email"))
    hashed_email = account.email_hash if account else email_digest(email)
    environment = runtime_environment()
    audience, eligible_audience = await _audience(environment, hashed_email, account)

    subscription = await NotificationSubscription.get_or_none(
        contact_email_hash=hashed_email,
        resource_type=resource_type,
        source=source,
        resource_id=resource_id,
    )
    verified_at = (
        account.verified_at
        if account
        else subscription.contact_verified_at
        if subscription
        else None
    )
    demand_eligible = eligible_audience and verified_at is not None
    values = {
        "resource_title": title,
        "metadata": metadata,
        "account_id": account.id if account else None,
        "contact_email": email,
        "environment": environment,
        "audience_type": audience,
        "demand_eligible": demand_eligible,
        "source_app_version": app_version,
    }
    if subscription:
        for key, value in values.items():
            if value is not None or key not in {"account_id", "source_app_version"}:
                setattr(subscription, key, value)
        if verified_at and not subscription.contact_verified_at:
            subscription.contact_verified_at = verified_at
        await subscription.save()
    else:
        subscription = await NotificationSubscription.create(
            resource_type=resource_type,
            resource_id=resource_id,
            source=source,
            contact_email_hash=hashed_email,
            contact_verified_at=verified_at,
            **values,
        )

    needs_verification = subscription.contact_verified_at is None
    if needs_verification:
        await create_challenge(
            email,
            "notification_subscription",
            subject_id=str(subscription.id),
            app_dir=app_dir,
        )
    return subscription, needs_verification


async def verify_subscription(
    subscription_id: object,
    email_value: object,
    code_value: object,
    *,
    app_dir: str | None = None,
) -> NotificationSubscription:
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
    if subscription.contact_verified_at:
        return subscription
    await consume_challenge(
        email,
        "notification_subscription",
        code_value,
        subject_id=str(subscription.id),
        app_dir=app_dir,
    )
    subscription.contact_verified_at = utcnow()
    subscription.demand_eligible = (
        subscription.environment == "production"
        and subscription.audience_type == "regular"
    )
    await subscription.save(
        update_fields=["contact_verified_at", "demand_eligible", "updated_at"]
    )
    return subscription


async def reclassify_subscriptions(hashed_email: str, audience_type: str) -> None:
    subscriptions = NotificationSubscription.filter(contact_email_hash=hashed_email)
    await subscriptions.update(audience_type=audience_type, demand_eligible=False)
    if audience_type == "regular":
        await subscriptions.filter(
            environment="production", contact_verified_at__isnull=False
        ).update(demand_eligible=True)
