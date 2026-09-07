import asyncio
from datetime import timedelta
import json

import pytest
from tortoise import Tortoise

from onramp.auth.email import EmailDeliveryError, send_transactional_email
from onramp.auth.models import Account, AccountSession, EmailChallenge, EmailChallengeRateLimit
from onramp.auth.security import email_digest
from onramp.auth.service import utcnow
from onramp.db import manager as manager_module
from onramp.notifications.models import NotificationContactToken, NotificationDelivery, NotificationSubscription
from onramp.notifications.service import (
    NotificationRequestContext,
    cleanup_notification_data,
    dispatch_notification,
    dispatch_subscriptions,
    issue_notification_contact_token,
    notification_report,
    notification_unsubscribe_token,
    request_subscription,
    revoke_notification_contact_token,
    suppress_subscription,
    verify_subscription,
)
from onramp.api import APIError


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture
def notification_app(tmp_path, monkeypatch):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "settings.py").write_text(
        "AUTH = {\n"
        "  'enabled': True,\n"
        "  'app_name': 'Notification Test',\n"
        "  'resend_delay_seconds': 0,\n"
        "  'notification_ip_hourly_limit': 1000,\n"
        "}\n"
        "DATABASE = {'engine': 'sqlite', 'name': ':memory:'}\n"
    )
    monkeypatch.setenv("ONRAMP_ENVIRONMENT", "test")
    monkeypatch.setenv("ONRAMP_AUTH_SECRET", "a" * 32)
    monkeypatch.setenv("ONRAMP_IDENTITY_SECRET", "i" * 32)
    manager_module._db_manager = None
    yield app_dir
    manager_module._db_manager = None


async def with_database(scenario):
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["onramp.auth.models", "onramp.notifications.models"]},
    )
    await Tortoise.generate_schemas()
    try:
        return await scenario()
    finally:
        await Tortoise.close_connections()


async def verified_subscription(email="notify@example.com", **values):
    contact_email_hash = values.pop(
        "contact_email_hash", email_digest(email) if email else None
    )
    return await NotificationSubscription.create(
        resource_type=values.pop("resource_type", "model"),
        resource_id=values.pop("resource_id", "market-1"),
        source=values.pop("source", "provider"),
        resource_title=values.pop("resource_title", "Will it happen?"),
        contact_email=email,
        contact_email_hash=contact_email_hash,
        contact_verified_at=values.pop("contact_verified_at", utcnow()),
        environment=values.pop("environment", "test"),
        audience_type=values.pop("audience_type", "development"),
        demand_eligible=values.pop("demand_eligible", False),
        **values,
    )


def test_delivery_content_exception_does_not_persist_private_details(notification_app):
    def broken_content(_subscription):
        raise RuntimeError("recipient@example.com secret-api-key code-123456")

    async def scenario():
        subscription = await verified_subscription()
        result = await dispatch_notification(
            subscription,
            "release/private-error",
            subject=broken_content,
            text_body="Ready",
            app_dir=str(notification_app),
        )
        assert result.outcome == "failed"
        delivery = await NotificationDelivery.get()
        assert delivery.last_error == (
            "Notification delivery failed. Check the message configuration and sender."
        )

    run(with_database(scenario))


def test_transactional_email_uses_development_outbox(notification_app):
    result = run(
        send_transactional_email(
            "Person@Example.com",
            "A subject",
            "Plain text",
            "<p>Plain text</p>",
            "message/one",
            app_dir=str(notification_app),
        )
    )
    message = json.loads(
        (notification_app.parent / ".onramp" / "dev-mail-outbox.jsonl")
        .read_text()
        .splitlines()[-1]
    )
    assert result.provider == "development"
    assert message == {
        **message,
        "to": "person@example.com",
        "subject": "A subject",
        "text": "Plain text",
        "html": "<p>Plain text</p>",
        "idempotency_key": "message/one",
    }

    with pytest.raises(EmailDeliveryError):
        run(
            send_transactional_email(
                "person@example.com",
                "Safe subject\r\nBcc: attacker@example.com",
                "Plain text",
                app_dir=str(notification_app),
            )
        )


def test_transactional_email_uses_resend_and_protects_validated_envelope(
    notification_app, monkeypatch
):
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"id":"resend-message-1"}'

    def urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setenv("ONRAMP_ENVIRONMENT", "production")
    monkeypatch.setenv("RESEND_API_KEY", "resend-secret")
    monkeypatch.setattr("onramp.auth.email.urlopen", urlopen)
    result = run(
        send_transactional_email(
            "person@example.com",
            "Real subject",
            "Real body",
            idempotency_key="real-key",
            app_dir=str(notification_app),
            message_fields={
                "to": "attacker@example.com",
                "subject": "Wrong subject",
                "idempotency_key": "wrong-key",
                "purpose": "notification",
            },
        )
    )
    request = captured["request"]
    payload = json.loads(request.data)
    assert result.provider == "resend"
    assert result.message_id == "resend-message-1"
    assert payload["to"] == ["person@example.com"]
    assert payload["subject"] == "Real subject"
    assert request.headers["Idempotency-key"] == "real-key"


def test_delivery_is_idempotent_and_development_message_has_unsubscribe_link(
    notification_app,
):
    async def scenario():
        subscription = await verified_subscription()
        first = await dispatch_notification(
            subscription,
            "model/released/1",
            subject="Your model is ready",
            text_body="Open the model.",
            app_dir=str(notification_app),
        )
        second = await dispatch_notification(
            subscription,
            "model/released/1",
            subject="Your model is ready",
            text_body="Open the model.",
            app_dir=str(notification_app),
        )
        assert first.outcome == "sent"
        assert second.outcome == "already_sent"
        delivery = await NotificationDelivery.get(subscription_id=subscription.id)
        assert delivery.status == "sent"
        assert delivery.attempt_count == 1
        assert delivery.provider == "development"
        assert delivery.sent_at is not None

    run(with_database(scenario))
    messages = (
        notification_app.parent / ".onramp" / "dev-mail-outbox.jsonl"
    ).read_text().splitlines()
    assert len(messages) == 1
    message = json.loads(messages[0])
    assert "http://127.0.0.1:8000/api/notifications/unsubscribe?token=" in (
        message["text"]
    )


def test_concurrent_delivery_claims_call_provider_only_once(
    notification_app, monkeypatch
):
    from onramp.auth.email import EmailSendResult
    from onramp.notifications import service

    provider_calls = []
    provider_started = asyncio.Event()
    finish_provider = asyncio.Event()

    async def sender(*args, **kwargs):
        provider_calls.append((args, kwargs))
        provider_started.set()
        await finish_provider.wait()
        return EmailSendResult("test", "message-1")

    monkeypatch.setattr(service, "send_transactional_email", sender)

    async def scenario():
        subscription = await verified_subscription()
        first = asyncio.create_task(
            dispatch_notification(
                subscription,
                "release/concurrent",
                subject="Ready",
                text_body="Open",
                app_dir=str(notification_app),
            )
        )
        await provider_started.wait()
        second = await dispatch_notification(
            subscription,
            "release/concurrent",
            subject="Ready",
            text_body="Open",
            app_dir=str(notification_app),
        )
        finish_provider.set()
        first_result = await first
        assert first_result.outcome == "sent"
        assert second.outcome == "busy"
        assert len(provider_calls) == 1
        delivery = await NotificationDelivery.get()
        assert delivery.status == "sent"
        assert delivery.attempt_count == 1

    run(with_database(scenario))


def test_same_recipient_and_event_across_provider_resources_sends_only_once(
    notification_app,
):
    async def scenario():
        first = await verified_subscription(
            resource_id="kalshi-1", source="kalshi"
        )
        second = await verified_subscription(
            resource_id="polymarket-1", source="polymarket"
        )
        preview = await dispatch_subscriptions(
            "canonical-model/released/1",
            subject="Ready",
            text_body="Open it",
            resource_type="model",
            resource_ids=["kalshi-1", "polymarket-1"],
            dry_run=True,
            app_dir=str(notification_app),
        )
        assert preview.as_dict()["would_send"] == 1
        assert preview.as_dict()["duplicate_recipient"] == 1
        first_result = await dispatch_notification(
            first,
            "canonical-model/released/1",
            subject="Ready",
            text_body="Open it",
            app_dir=str(notification_app),
        )
        second_result = await dispatch_notification(
            second,
            "canonical-model/released/1",
            subject="Ready",
            text_body="Open it",
            app_dir=str(notification_app),
        )
        assert first_result.outcome == "sent"
        assert second_result.outcome == "already_sent"
        assert first_result.delivery.id == second_result.delivery.id
        assert await NotificationDelivery.all().count() == 1
        await second.refresh_from_db()
        assert second.notified_at == first_result.delivery.sent_at

        one_time = await dispatch_subscriptions(
            "canonical-model/released/2",
            subject="A later release",
            text_body="Open it",
            resource_type="model",
            resource_ids=["kalshi-1", "polymarket-1"],
            unnotified_only=True,
            dry_run=True,
            app_dir=str(notification_app),
        )
        assert one_time.matched == 0
        compatible_default = await dispatch_subscriptions(
            "canonical-model/released/2",
            subject="A later release",
            text_body="Open it",
            resource_type="model",
            resource_ids=["kalshi-1", "polymarket-1"],
            dry_run=True,
            app_dir=str(notification_app),
        )
        assert compatible_default.matched == 2

    run(with_database(scenario))
    messages = (
        notification_app.parent / ".onramp" / "dev-mail-outbox.jsonl"
    ).read_text().splitlines()
    assert len(messages) == 1


def test_delivery_idempotency_is_isolated_by_environment(notification_app):
    async def scenario():
        development = await verified_subscription(
            resource_id="development", environment="development"
        )
        staging = await verified_subscription(
            resource_id="staging", environment="staging"
        )
        first = await dispatch_notification(
            development,
            "release/same-key",
            subject="Ready",
            text_body="Open",
            app_dir=str(notification_app),
        )
        second = await dispatch_notification(
            staging,
            "release/same-key",
            subject="Ready",
            text_body="Open",
            app_dir=str(notification_app),
        )
        assert first.outcome == second.outcome == "sent"
        assert await NotificationDelivery.all().count() == 2

    run(with_database(scenario))


def test_subscription_identity_is_isolated_by_environment(
    notification_app, monkeypatch
):
    async def scenario():
        account = await Account.create(
            email="environment@example.com",
            email_hash=email_digest("environment@example.com"),
            verified_at=utcnow(),
        )
        monkeypatch.setenv("ONRAMP_ENVIRONMENT", "test")
        test_subscription, test_verification = await request_subscription(
            {
                "resource_type": "model",
                "resource_id": "same-resource",
                "source": "provider",
                "resource_title": "Same title",
            },
            account,
            app_dir=str(notification_app),
        )
        monkeypatch.setenv("ONRAMP_ENVIRONMENT", "staging")
        staging_subscription, staging_verification = await request_subscription(
            {
                "resource_type": "model",
                "resource_id": "same-resource",
                "source": "provider",
                "resource_title": "Same title",
            },
            account,
            app_dir=str(notification_app),
        )
        assert not test_verification
        assert not staging_verification
        assert test_subscription.id != staging_subscription.id
        assert test_subscription.environment == "test"
        assert staging_subscription.environment == "staging"

    run(with_database(scenario))


def test_concurrent_identical_requests_reuse_one_subscription(notification_app):
    async def scenario():
        payload = {
            "resource_type": "model",
            "resource_id": "same-concurrent-resource",
            "source": "provider",
            "resource_title": "Same resource",
            "email": "concurrent@example.com",
        }
        requests = await asyncio.gather(
            *(request_subscription(payload, app_dir=str(notification_app)) for _ in range(8))
        )
        assert len({subscription.id for subscription, _required in requests}) == 1
        assert all(required for _subscription, required in requests)
        assert await NotificationSubscription.all().count() == 1

    run(with_database(scenario))


def test_unproved_repeat_does_not_mutate_verified_subscription(notification_app):
    async def scenario():
        account = await Account.create(
            email="trusted@example.com",
            email_hash=email_digest("trusted@example.com"),
            verified_at=utcnow(),
        )
        canonical_id = "22ea7fbf-5522-43f0-9e80-7ee03bc3caca"
        subscription = await verified_subscription(
            "trusted@example.com",
            resource_id="trusted-resource",
            resource_title="Trusted title",
            canonical_resource_id=canonical_id,
            metadata={"trusted": True},
            source_app_version="1.2.3",
            account_id=account.id,
            audience_type="tester",
        )
        repeated, verification_required = await request_subscription(
            {
                "resource_type": "model",
                "resource_id": "trusted-resource",
                "source": "provider",
                "resource_title": "Unproved replacement",
                "canonical_resource_id": "00000000-0000-0000-0000-000000000001",
                "metadata": {"trusted": False},
                "app_version": "9.9.9",
                "email": "trusted@example.com",
            },
            app_dir=str(notification_app),
        )
        assert verification_required
        assert repeated.id == subscription.id
        await repeated.refresh_from_db()
        assert repeated.resource_title == "Trusted title"
        assert str(repeated.canonical_resource_id) == canonical_id
        assert repeated.metadata == {"trusted": True}
        assert repeated.source_app_version == "1.2.3"
        assert repeated.account_id == account.id
        assert repeated.audience_type == "tester"
        assert repeated.contact_email == "trusted@example.com"

    run(with_database(scenario))


def test_failed_delivery_requires_explicit_retry_and_reuses_idempotency_key(
    notification_app, monkeypatch
):
    calls = []

    async def sender(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            raise EmailDeliveryError("provider unavailable")
        from onramp.auth.email import EmailSendResult

        return EmailSendResult("test", "provider-1")

    monkeypatch.setattr(
        "onramp.notifications.service.send_transactional_email", sender
    )

    async def scenario():
        subscription = await verified_subscription()
        failed = await dispatch_notification(
            subscription,
            "release/1",
            subject="Ready",
            text_body="Open it",
            app_dir=str(notification_app),
        )
        held = await dispatch_notification(
            subscription,
            "release/1",
            subject="Ready",
            text_body="Open it",
            app_dir=str(notification_app),
        )
        retried = await dispatch_notification(
            subscription,
            "release/1",
            subject="Ready",
            text_body="Open it",
            retry_failed=True,
            app_dir=str(notification_app),
        )
        assert (failed.outcome, held.outcome, retried.outcome) == (
            "failed",
            "failed",
            "sent",
        )
        assert len(calls) == 2
        assert calls[0][0][4] == calls[1][0][4]
        assert retried.delivery.attempt_count == 2
        assert retried.delivery.provider_message_id == "provider-1"

    run(with_database(scenario))


def test_unverified_anonymized_and_suppressed_subscriptions_are_not_delivered(
    notification_app, monkeypatch
):
    calls = []

    async def sender(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(
        "onramp.notifications.service.send_transactional_email", sender
    )

    async def scenario():
        unverified = await verified_subscription(
            "unverified@example.com", contact_verified_at=None, resource_id="one"
        )
        anonymized = await verified_subscription(
            None, anonymized_at=utcnow(), resource_id="two"
        )
        suppressed = await verified_subscription(
            "suppressed@example.com", suppressed_at=utcnow(), resource_id="three"
        )
        assert (
            await dispatch_notification(
                unverified, "event", subject="Ready", text_body="Open"
            )
        ).outcome == "unverified"
        assert (
            await dispatch_notification(
                anonymized, "event", subject="Ready", text_body="Open"
            )
        ).outcome == "anonymized"
        assert (
            await dispatch_notification(
                suppressed, "event", subject="Ready", text_body="Open"
            )
        ).outcome == "suppressed"
        assert await NotificationDelivery.all().count() == 0

    run(with_database(scenario))
    assert calls == []


def test_unsubscribe_is_signed_idempotent_and_requires_reverification(
    notification_app,
):
    async def scenario():
        subscription = await verified_subscription(
            audience_type="regular",
            demand_eligible=True,
        )
        token = notification_unsubscribe_token(subscription)
        suppressed = await suppress_subscription(token)
        replayed = await suppress_subscription(token)
        assert replayed.id == suppressed.id
        assert replayed.suppressed_at == suppressed.suppressed_at
        assert not replayed.demand_eligible
        assert replayed.contact_email is None
        assert replayed.account_id is None
        assert (
            await dispatch_notification(
                replayed, "release/after-unsubscribe", subject="Ready", text_body="Open"
            )
        ).outcome == "suppressed"
        with pytest.raises(Exception) as error:
            await suppress_subscription(token + "tampered")
        assert getattr(error.value, "code", None) == "invalid_unsubscribe_token"

        requested, verification_required = await request_subscription(
            {
                "resource_type": "model",
                "resource_id": "market-1",
                "source": "provider",
                "resource_title": "Will it happen?",
                "email": "notify@example.com",
            },
            app_dir=str(notification_app),
        )
        assert verification_required
        assert requested.suppressed_at is not None
        assert not requested.demand_eligible
        assert requested.contact_email is None
        code = json.loads(
            (notification_app.parent / ".onramp" / "dev-mail-outbox.jsonl")
            .read_text()
            .splitlines()[-1]
        )["code"]
        requested.environment = "production"
        requested.audience_type = "regular"
        await requested.save()
        verified = await verify_subscription(
            requested.id,
            "notify@example.com",
            code,
            app_dir=str(notification_app),
        )
        assert verified.suppressed_at is None
        assert verified.contact_email == "notify@example.com"
        assert verified.demand_eligible

    run(with_database(scenario))


def test_unsubscribe_before_verification_invalidates_the_outstanding_code(
    notification_app,
):
    async def scenario():
        payload = {
            "resource_type": "model",
            "resource_id": "cancel-pending",
            "source": "provider",
            "resource_title": "Cancel pending",
            "email": "pending@example.com",
        }
        subscription, verification_required = await request_subscription(
            payload, app_dir=str(notification_app)
        )
        assert verification_required
        code = json.loads(
            (notification_app.parent / ".onramp" / "dev-mail-outbox.jsonl")
            .read_text()
            .splitlines()[-1]
        )["code"]
        await suppress_subscription(notification_unsubscribe_token(subscription))
        assert not await EmailChallenge.filter(
            purpose="notification_subscription",
            subject_id__startswith=f"{subscription.id}:",
        ).exists()
        with pytest.raises(Exception) as error:
            await verify_subscription(
                subscription.id,
                "pending@example.com",
                code,
                app_dir=str(notification_app),
            )
        assert getattr(error.value, "code", None) == "code_expired"

        requested_again, verification_required = await request_subscription(
            payload, app_dir=str(notification_app)
        )
        assert requested_again.id == subscription.id
        assert verification_required
        fresh_code = json.loads(
            (notification_app.parent / ".onramp" / "dev-mail-outbox.jsonl")
            .read_text()
            .splitlines()[-1]
        )["code"]
        restored = await verify_subscription(
            subscription.id,
            "pending@example.com",
            fresh_code,
            app_dir=str(notification_app),
        )
        assert restored.suppressed_at is None
        assert restored.contact_email == "pending@example.com"

    run(with_database(scenario))


def test_post_cancellation_resubscribe_proof_survives_stale_cleanup(
    notification_app, monkeypatch
):
    from onramp.notifications import service

    cancellation_committed = asyncio.Event()
    continue_cleanup = asyncio.Event()
    original_cleanup = service._delete_cancelled_subscription_challenges

    async def paused_cleanup(*args, **kwargs):
        cancellation_committed.set()
        await continue_cleanup.wait()
        return await original_cleanup(*args, **kwargs)

    monkeypatch.setattr(
        service, "_delete_cancelled_subscription_challenges", paused_cleanup
    )

    async def scenario():
        subscription = await verified_subscription(
            "fresh-consent@example.com", resource_id="fresh-consent"
        )
        cancellation = asyncio.create_task(
            suppress_subscription(notification_unsubscribe_token(subscription))
        )
        await cancellation_committed.wait()
        await subscription.refresh_from_db()
        assert subscription.suppressed_at is not None
        assert subscription.consent_generation == 1

        requested, verification_required = await request_subscription(
            {
                "resource_type": "model",
                "resource_id": "fresh-consent",
                "source": "provider",
                "resource_title": "Fresh consent",
                "email": "fresh-consent@example.com",
            },
            app_dir=str(notification_app),
        )
        assert verification_required
        current_subject = f"{subscription.id}:1"
        challenge = await EmailChallenge.get(subject_id=current_subject)
        code = json.loads(
            (notification_app.parent / ".onramp" / "dev-mail-outbox.jsonl")
            .read_text()
            .splitlines()[-1]
        )["code"]

        continue_cleanup.set()
        await cancellation
        assert await EmailChallenge.filter(id=challenge.id).exists()

        restored = await verify_subscription(
            requested.id,
            "fresh-consent@example.com",
            code,
            app_dir=str(notification_app),
        )
        assert restored.suppressed_at is None
        assert restored.contact_email == "fresh-consent@example.com"

    run(with_database(scenario))


def test_batch_dispatch_report_and_cleanup(notification_app):
    async def scenario():
        deliverable = await verified_subscription(resource_id="ready")
        await verified_subscription(
            "other@example.com", resource_id="other", source="elsewhere"
        )
        abandoned = await verified_subscription(
            "old@example.com", resource_id="old", contact_verified_at=None
        )
        old = utcnow() - timedelta(days=31)
        await NotificationSubscription.filter(id=abandoned.id).update(
            requested_at=old, updated_at=old
        )
        refreshed = await verified_subscription(
            "refreshed@example.com",
            resource_id="refreshed",
            contact_verified_at=None,
        )
        await NotificationSubscription.filter(id=refreshed.id).update(
            requested_at=old, updated_at=old
        )
        refreshed, refreshed_verification = await request_subscription(
            {
                "resource_type": "model",
                "resource_id": "refreshed",
                "source": "provider",
                "resource_title": "Refreshed title",
                "email": "refreshed@example.com",
            },
            app_dir=str(notification_app),
        )
        assert refreshed_verification
        cancelled = await verified_subscription(
            None,
            resource_id="cancelled-old",
            contact_verified_at=None,
            contact_email_hash=email_digest("cancelled-old@example.com"),
            suppressed_at=old,
            suppression_reason="recipient_unsubscribed",
        )
        await NotificationSubscription.filter(id=cancelled.id).update(
            requested_at=old, updated_at=old
        )
        await EmailChallenge.create(
            email="old@example.com",
            email_hash=email_digest("old@example.com"),
            purpose="notification_subscription",
            subject_id=str(abandoned.id),
            code_digest="digest",
            expires_at=utcnow() - timedelta(minutes=1),
        )
        await EmailChallenge.create(
            email="old@example.com",
            email_hash=email_digest("old@example.com"),
            purpose="notification_subscription",
            subject_id=str(abandoned.id),
            code_digest="still-current",
            expires_at=utcnow() + timedelta(minutes=5),
        )
        stale_limit = await EmailChallengeRateLimit.create(
            scope_key="stale-rate-limit",
            email_hash=email_digest("old@example.com"),
            window_started_at=old,
            count=1,
        )
        await EmailChallengeRateLimit.filter(id=stale_limit.id).update(
            updated_at=old
        )

        preview = await dispatch_subscriptions(
            "release/preview",
            subject="Ready",
            text_body="Open",
            resource_type="model",
            source="provider",
            resource_ids=[deliverable.resource_id],
            dry_run=True,
            app_dir=str(notification_app),
        )
        assert preview.as_dict()["would_send"] == 1
        assert await NotificationDelivery.all().count() == 0

        before = await notification_report(resource_type="model")
        assert before["subscriptions"] == 5
        assert before["deliverable"] == 2
        cleanup = await cleanup_notification_data(
            unverified_days=30, app_dir=str(notification_app)
        )
        assert cleanup["expired_challenges"] == 1
        assert cleanup["related_challenges"] == 1
        assert cleanup["unverified_subscriptions"] == 2
        assert cleanup["challenge_rate_limits"] == 1
        assert not await NotificationSubscription.filter(id=abandoned.id).exists()
        assert not await NotificationSubscription.filter(id=cancelled.id).exists()
        assert await NotificationSubscription.filter(id=refreshed.id).exists()

    run(with_database(scenario))


def test_operator_anonymization_clears_subscription_and_delivery_identifiers(
    notification_app,
):
    from onramp.notifications.service import anonymize_notification_contact

    async def scenario():
        subscription = await verified_subscription()
        await issue_notification_contact_token(subscription, app_dir=str(notification_app))
        await dispatch_notification(
            subscription,
            "release/anonymize",
            subject="Ready",
            text_body="Open",
            app_dir=str(notification_app),
        )
        delivery_before = await NotificationDelivery.get()
        original_key = delivery_before.idempotency_key
        await EmailChallengeRateLimit.create(
            scope_key="anonymize-rate-limit",
            email_hash=email_digest("notify@example.com"),
            window_started_at=utcnow(),
            count=1,
        )
        result = await anonymize_notification_contact("notify@example.com")
        assert result["anonymized_subscriptions"] == 1
        assert result["anonymized_deliveries"] == 1
        assert result["deleted_challenge_rate_limits"] == 1
        assert result["deleted_notification_contact_tokens"] == 1
        assert await NotificationContactToken.all().count() == 0
        await subscription.refresh_from_db()
        delivery = await NotificationDelivery.get()
        assert subscription.contact_email is None
        assert subscription.contact_email_hash is None
        assert subscription.account_id is None
        assert subscription.anonymized_at is not None
        assert not subscription.demand_eligible
        assert delivery.recipient_email_hash is None
        assert delivery.idempotency_key.startswith("anonymized/")
        assert delivery.idempotency_key != original_key

    run(with_database(scenario))


def test_subscription_validator_can_assign_canonical_resource_and_see_context(
    notification_app, monkeypatch
):
    canonical_id = "22ea7fbf-5522-43f0-9e80-7ee03bc3caca"
    seen = {}

    async def validator(**kwargs):
        seen.update(kwargs)
        return {**kwargs["payload"], "canonical_resource_id": canonical_id}

    monkeypatch.setattr(
        "onramp.notifications.service.auth_config",
        lambda _app_dir=None: {
            "notification_metadata_bytes": 16_384,
            "notification_ip_hourly_limit": 1000,
            "notification_subscription_validator": "app.validator.validate",
        },
    )
    monkeypatch.setattr(
        "onramp.notifications.service.import_callable", lambda _reference: validator
    )

    async def scenario():
        context = NotificationRequestContext(
            client_host="192.0.2.4", direct_client_host="127.0.0.1"
        )
        subscription, needs_verification = await request_subscription(
            {
                "resource_type": "model",
                "resource_id": "provider-id",
                "source": "provider",
                "resource_title": "Title",
                "email": "hook@example.com",
            },
            app_dir=str(notification_app),
            request_context=context,
        )
        assert needs_verification
        assert str(subscription.canonical_resource_id) == canonical_id
        assert seen["request_context"] == context

    run(with_database(scenario))


def test_subscription_request_has_database_client_limit(
    notification_app, monkeypatch
):
    from onramp.notifications import service
    from onramp.auth import service as auth_service

    original_auth_config = service.auth_config

    def limited_config(app_dir=None):
        return {
            **original_auth_config(app_dir),
            "notification_ip_hourly_limit": 1,
        }

    monkeypatch.setattr(service, "auth_config", limited_config)
    monkeypatch.setattr(auth_service, "auth_config", limited_config)

    async def scenario():
        context = NotificationRequestContext(client_host="192.0.2.99")
        await request_subscription(
            {
                "resource_type": "model",
                "resource_id": "one",
                "source": "provider",
                "resource_title": "One",
                "email": "one@example.com",
            },
            app_dir=str(notification_app),
            request_context=context,
        )
        with pytest.raises(APIError) as error:
            await request_subscription(
                {
                    "resource_type": "model",
                    "resource_id": "two",
                    "source": "provider",
                    "resource_title": "Two",
                    "email": "two@example.com",
                },
                app_dir=str(notification_app),
                request_context=context,
            )
        assert error.value.status == 429
        assert error.value.code == "request_rate_limited"

    run(with_database(scenario))


def test_ready_hook_runs_after_verification_and_retries_a_post_save_failure(
    notification_app, monkeypatch
):
    from onramp.notifications import service

    calls = []
    original_auth_config = service.auth_config

    def hook_config(app_dir=None):
        return {
            **original_auth_config(app_dir),
            "notification_subscription_ready_hook": "app.hooks.subscription_ready",
        }

    async def ready_hook(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("publication lookup temporarily failed")

    monkeypatch.setattr(service, "auth_config", hook_config)
    monkeypatch.setattr(service, "import_callable", lambda _reference: ready_hook)

    async def scenario():
        first_context = NotificationRequestContext(client_host="192.0.2.10")
        subscription, needs_verification = await request_subscription(
            {
                "resource_type": "model",
                "resource_id": "ready-race",
                "source": "provider",
                "resource_title": "Ready race",
                "email": "race@example.com",
            },
            app_dir=str(notification_app),
            request_context=first_context,
        )
        assert needs_verification
        assert calls == []
        code = json.loads(
            (notification_app.parent / ".onramp" / "dev-mail-outbox.jsonl")
            .read_text()
            .splitlines()[-1]
        )["code"]

        verify_context = NotificationRequestContext(client_host="192.0.2.11")
        with pytest.raises(
            RuntimeError, match="publication lookup temporarily failed"
        ):
            await verify_subscription(
                subscription.id,
                "race@example.com",
                code,
                app_dir=str(notification_app),
                request_context=verify_context,
            )
        await subscription.refresh_from_db()
        assert subscription.contact_verified_at is not None

        retried = await verify_subscription(
            subscription.id,
            "race@example.com",
            code,
            app_dir=str(notification_app),
            request_context=verify_context,
        )
        assert retried.id == subscription.id
        assert len(calls) == 2
        assert calls[0]["subscription"].id == subscription.id
        assert calls[0]["app_dir"] == str(notification_app)
        assert calls[0]["request_context"] == verify_context

        repeat_context = NotificationRequestContext(client_host="192.0.2.12")
        repeated, repeated_verification = await request_subscription(
            {
                "resource_type": "model",
                "resource_id": "ready-race",
                "source": "provider",
                "resource_title": "Ready race",
                "email": "race@example.com",
            },
            app_dir=str(notification_app),
            request_context=repeat_context,
        )
        assert repeated_verification
        assert repeated.id == subscription.id
        assert len(calls) == 2
        repeat_code = json.loads(
            (notification_app.parent / ".onramp" / "dev-mail-outbox.jsonl")
            .read_text()
            .splitlines()[-1]
        )["code"]
        await verify_subscription(
            repeated.id,
            "race@example.com",
            repeat_code,
            app_dir=str(notification_app),
            request_context=repeat_context,
        )
        assert len(calls) == 3
        assert calls[-1]["request_context"] == repeat_context

        await suppress_subscription(notification_unsubscribe_token(repeated))
        suppressed_request, reverify_required = await request_subscription(
            {
                "resource_type": "model",
                "resource_id": "ready-race",
                "source": "provider",
                "resource_title": "Ready race",
                "email": "race@example.com",
            },
            app_dir=str(notification_app),
            request_context=repeat_context,
        )
        assert reverify_required
        assert len(calls) == 3
        reverify_code = json.loads(
            (notification_app.parent / ".onramp" / "dev-mail-outbox.jsonl")
            .read_text()
            .splitlines()[-1]
        )["code"]
        await verify_subscription(
            suppressed_request.id,
            "race@example.com",
            reverify_code,
            app_dir=str(notification_app),
            request_context=verify_context,
        )
        assert len(calls) == 4

        account = await Account.create(
            email="signed-in@example.com",
            email_hash=email_digest("signed-in@example.com"),
            verified_at=utcnow(),
        )
        authenticated_context = NotificationRequestContext(
            client_host="192.0.2.13"
        )
        authenticated, authenticated_verification = await request_subscription(
            {
                "resource_type": "model",
                "resource_id": "authenticated-ready",
                "source": "provider",
                "resource_title": "Authenticated ready",
            },
            account,
            app_dir=str(notification_app),
            request_context=authenticated_context,
        )
        assert not authenticated_verification
        assert authenticated.contact_verified_at is not None
        assert len(calls) == 5
        assert calls[-1]["request_context"] == authenticated_context

    run(with_database(scenario))


def test_concurrent_unsubscribe_wins_before_verification_ready_hook(
    notification_app, monkeypatch
):
    from onramp.notifications import service

    validated = asyncio.Event()
    continue_verification = asyncio.Event()
    hook_calls = []
    original_consume = service.consume_challenge
    original_auth_config = service.auth_config

    async def paused_consume(*args, **kwargs):
        challenge = await original_consume(*args, **kwargs)
        validated.set()
        await continue_verification.wait()
        return challenge

    async def ready_hook(**kwargs):
        hook_calls.append(kwargs)

    def hook_config(app_dir=None):
        return {
            **original_auth_config(app_dir),
            "notification_subscription_ready_hook": "app.hooks.ready",
        }

    monkeypatch.setattr(service, "consume_challenge", paused_consume)
    monkeypatch.setattr(service, "auth_config", hook_config)
    monkeypatch.setattr(service, "import_callable", lambda _reference: ready_hook)

    async def scenario():
        subscription, _required = await request_subscription(
            {
                "resource_type": "model",
                "resource_id": "cancel-race",
                "source": "provider",
                "resource_title": "Cancel race",
                "email": "race-cancel@example.com",
            },
            app_dir=str(notification_app),
        )
        code = json.loads(
            (notification_app.parent / ".onramp" / "dev-mail-outbox.jsonl")
            .read_text()
            .splitlines()[-1]
        )["code"]
        verification = asyncio.create_task(
            verify_subscription(
                subscription.id,
                "race-cancel@example.com",
                code,
                app_dir=str(notification_app),
            )
        )
        await validated.wait()
        await suppress_subscription(notification_unsubscribe_token(subscription))
        continue_verification.set()
        with pytest.raises(AuthenticationError) as error:
            await verification
        assert error.value.code == "subscription_cancelled"
        await subscription.refresh_from_db()
        assert subscription.suppressed_at is not None
        assert subscription.contact_email is None
        assert hook_calls == []

    from onramp.auth.service import AuthenticationError

    run(with_database(scenario))


def test_concurrent_unsubscribe_wins_over_stale_repeat_request(
    notification_app, monkeypatch
):
    from onramp.auth.service import AuthenticationError
    from onramp.notifications import service

    subscription_loaded = asyncio.Event()
    continue_request = asyncio.Event()
    original_get_or_create = service.NotificationSubscription.get_or_create

    async def paused_get_or_create(*args, **kwargs):
        result = await original_get_or_create(*args, **kwargs)
        subscription_loaded.set()
        await continue_request.wait()
        return result

    monkeypatch.setattr(
        service.NotificationSubscription, "get_or_create", paused_get_or_create
    )

    async def scenario():
        existing = await verified_subscription(
            "request-race@example.com",
            resource_id="request-race",
            resource_title="Trusted title",
            metadata={"trusted": True},
        )
        repeat = asyncio.create_task(
            request_subscription(
                {
                    "resource_type": "model",
                    "resource_id": "request-race",
                    "source": "provider",
                    "resource_title": "Unproved replacement",
                    "metadata": {"trusted": False},
                    "email": "request-race@example.com",
                },
                app_dir=str(notification_app),
            )
        )
        await subscription_loaded.wait()
        await suppress_subscription(notification_unsubscribe_token(existing))
        continue_request.set()
        with pytest.raises(AuthenticationError) as error:
            await repeat
        assert error.value.code == "subscription_cancelled"
        await existing.refresh_from_db()
        assert existing.suppressed_at is not None
        assert existing.contact_email is None
        assert existing.resource_title == "Trusted title"
        assert existing.metadata == {"trusted": True}

    run(with_database(scenario))


def test_unsubscribe_committed_before_delivery_claim_prevents_send(
    notification_app, monkeypatch
):
    from onramp.notifications import service

    before_claim = asyncio.Event()
    continue_dispatch = asyncio.Event()
    provider_calls = []
    original_claim = service._claim_delivery_with_consent

    async def paused_claim(*args, **kwargs):
        before_claim.set()
        await continue_dispatch.wait()
        return await original_claim(*args, **kwargs)

    async def sender(*args, **kwargs):
        provider_calls.append((args, kwargs))
        raise AssertionError("suppressed delivery reached the provider")

    monkeypatch.setattr(service, "_claim_delivery_with_consent", paused_claim)
    monkeypatch.setattr(service, "send_transactional_email", sender)

    async def scenario():
        subscription = await verified_subscription(
            "dispatch-race@example.com", resource_id="dispatch-race"
        )
        dispatch = asyncio.create_task(
            dispatch_notification(
                subscription,
                "release/dispatch-race",
                subject="Ready",
                text_body="Open",
                app_dir=str(notification_app),
            )
        )
        await before_claim.wait()
        await suppress_subscription(notification_unsubscribe_token(subscription))
        continue_dispatch.set()
        outcome = await dispatch
        assert outcome.outcome == "suppressed"
        assert provider_calls == []
        delivery = await NotificationDelivery.get()
        assert delivery.status == "pending"
        assert delivery.attempt_count == 0

    run(with_database(scenario))


def test_remembered_contact_reuses_proof_across_resources_without_accounts(
    notification_app, monkeypatch
):
    from onramp.auth.service import AuthenticationError
    from onramp.notifications import service

    monkeypatch.setenv("ONRAMP_ENVIRONMENT", "production")

    async def scenario():
        original = await verified_subscription(
            environment="production", audience_type="regular", demand_eligible=True
        )
        token, contact = await issue_notification_contact_token(
            original, app_dir=str(notification_app)
        )
        assert token.startswith("onramp_notify_")
        assert len(token.removeprefix("onramp_notify_")) == 43
        assert contact.token_hash != token
        assert len(contact.token_hash) == 64
        assert contact.expires_at is None
        future = utcnow() + timedelta(days=3650)
        monkeypatch.setattr(service, "utcnow", lambda: future)
        cleanup = await cleanup_notification_data(app_dir=str(notification_app))
        assert cleanup["expired_contact_tokens"] == 0
        assert await NotificationContactToken.all().count() == 1
        for source, resource_id in (("kalshi", "one"), ("polymarket", "two")):
            subscription, required = await request_subscription(
                {
                    "resource_type": "model", "source": source,
                    "resource_id": resource_id, "resource_title": "Another resource",
                    "email": "  NOTIFY@example.com  ",
                },
                notification_token=token, app_dir=str(notification_app),
            )
            assert required is False
            assert subscription.contact_verified_at == contact.verified_at
            assert subscription.account_id is None
            assert subscription.demand_eligible is True
        await contact.refresh_from_db()
        assert contact.expires_at is None
        assert await EmailChallenge.all().count() == 0
        assert await Account.all().count() == 0
        assert await AccountSession.all().count() == 0
        assert not (notification_app.parent / ".onramp" / "dev-mail-outbox.jsonl").exists()
        await revoke_notification_contact_token(token)
        with pytest.raises(AuthenticationError) as error:
            await request_subscription(
                {"resource_type": "model", "source": "kalshi", "resource_id": "revoked",
                 "resource_title": "Revoked proof", "email": "notify@example.com"},
                notification_token=token, app_dir=str(notification_app),
            )
        assert error.value.code == "notification_token_invalid"
        assert await NotificationContactToken.all().count() == 0

    run(with_database(scenario))


def test_contact_token_explicit_lifetime_is_fixed_and_expires(notification_app, monkeypatch):
    from onramp.auth.service import AuthenticationError
    from onramp.notifications import service

    manager_module.get_db_manager(str(notification_app)).settings["AUTH"][
        "notification_contact_token_days"
    ] = 7

    async def scenario():
        original = await verified_subscription()
        token, contact = await issue_notification_contact_token(
            original, app_dir=str(notification_app)
        )
        assert contact.expires_at - contact.verified_at == timedelta(days=7)
        expiry = contact.expires_at
        payload = {
            "resource_type": "model", "source": "provider", "resource_id": "finite",
            "resource_title": "Finite proof", "email": "notify@example.com",
        }
        monkeypatch.setattr(service, "utcnow", lambda: expiry - timedelta(seconds=1))
        _subscription, required = await request_subscription(
            payload, notification_token=token, app_dir=str(notification_app)
        )
        assert required is False
        await contact.refresh_from_db()
        assert contact.expires_at == expiry
        monkeypatch.setattr(service, "utcnow", lambda: expiry)
        with pytest.raises(AuthenticationError) as error:
            await request_subscription(
                {**payload, "resource_id": "expired"},
                notification_token=token, app_dir=str(notification_app),
            )
        assert error.value.code == "notification_token_invalid"
        assert await NotificationSubscription.all().count() == 2
        cleanup = await cleanup_notification_data(app_dir=str(notification_app))
        assert cleanup["expired_contact_tokens"] == 1
        assert await NotificationContactToken.all().count() == 0

    run(with_database(scenario))


@pytest.mark.parametrize("invalid", ["email", "scope", "environment", "expired", "revoked", "unknown", "malformed"])
def test_contact_token_rejects_invalid_proof_before_persistence(
    notification_app, monkeypatch, invalid
):
    from onramp.auth.service import AuthenticationError
    from onramp.notifications import service

    async def scenario():
        original = await verified_subscription()
        token, contact = await issue_notification_contact_token(original)
        payload = {
            "resource_type": "model", "source": "another-provider",
            "resource_id": "never-persisted", "resource_title": "Another resource",
            "email": "notify@example.com",
        }
        if invalid == "email":
            payload["email"] = "different@example.com"
        elif invalid == "scope":
            payload["resource_type"] = "newsletter"
        elif invalid == "environment":
            monkeypatch.setenv("ONRAMP_ENVIRONMENT", "staging")
        elif invalid == "expired":
            contact.expires_at = utcnow() - timedelta(seconds=1)
            await contact.save()
        elif invalid == "revoked":
            await revoke_notification_contact_token(token)
        elif invalid == "unknown":
            token = "onramp_notify_" + "a" * 43
        else:
            token = "account-session-token"

        async def unexpected_validator(*_args, **_kwargs):
            raise AssertionError("Invalid token reached the application validator")

        monkeypatch.setattr(service, "_validated_subscription_payload", unexpected_validator)
        with pytest.raises(AuthenticationError) as error:
            await request_subscription(payload, notification_token=token)
        assert error.value.code == "notification_token_invalid"
        assert error.value.status == 401
        assert await NotificationSubscription.all().count() == 1
        assert await EmailChallenge.all().count() == 0

    run(with_database(scenario))


def test_contact_revocation_is_exact_and_cleanup_removes_expired_tokens(notification_app, monkeypatch):
    async def scenario():
        subscription = await verified_subscription()
        token, _first = await issue_notification_contact_token(subscription)
        _other_token, other = await issue_notification_contact_token(subscription)
        monkeypatch.setenv("ONRAMP_ENVIRONMENT", "staging")
        await revoke_notification_contact_token(token)
        assert await NotificationContactToken.all().count() == 2
        monkeypatch.setenv("ONRAMP_ENVIRONMENT", "test")
        await revoke_notification_contact_token(token)
        await revoke_notification_contact_token(token)
        await revoke_notification_contact_token("malformed")
        assert await NotificationContactToken.all().count() == 1
        await subscription.refresh_from_db()
        assert subscription.suppressed_at is None
        other.expires_at = utcnow() - timedelta(seconds=1)
        await other.save()
        _indefinite_token, indefinite = await issue_notification_contact_token(subscription)
        result = await cleanup_notification_data(app_dir=str(notification_app))
        assert result["expired_contact_tokens"] == 1
        assert await NotificationContactToken.all().count() == 1
        await indefinite.refresh_from_db()
        assert indefinite.expires_at is None

    run(with_database(scenario))


def test_revocation_during_validation_prevents_subscription_persistence(
    notification_app, monkeypatch
):
    from onramp.auth.service import AuthenticationError
    from onramp.notifications import service

    validated = asyncio.Event()
    resume = asyncio.Event()
    original_validator = service._validated_subscription_payload

    async def paused_validator(*args, **kwargs):
        result = await original_validator(*args, **kwargs)
        validated.set()
        await resume.wait()
        return result

    monkeypatch.setattr(service, "_validated_subscription_payload", paused_validator)

    async def scenario():
        original = await verified_subscription()
        token, _contact = await issue_notification_contact_token(original)
        pending = asyncio.create_task(request_subscription(
            {"resource_type": "model", "source": "provider", "resource_id": "race",
             "resource_title": "Race", "email": "notify@example.com"},
            notification_token=token, app_dir=str(notification_app),
        ))
        await validated.wait()
        await revoke_notification_contact_token(token)
        resume.set()
        with pytest.raises(AuthenticationError) as error:
            await pending
        assert error.value.code == "notification_token_invalid"
        assert await NotificationSubscription.all().count() == 1
        assert await EmailChallenge.all().count() == 0

    run(with_database(scenario))


def test_revocation_waits_for_atomically_authorized_subscription(
    notification_app, monkeypatch
):
    from onramp.auth.service import AuthenticationError
    from onramp.notifications import service

    claimed = asyncio.Event()
    resume = asyncio.Event()
    original_persist = service._persist_subscription_request

    async def paused_persist(*args, **kwargs):
        claimed.set()
        await resume.wait()
        return await original_persist(*args, **kwargs)

    monkeypatch.setattr(service, "_persist_subscription_request", paused_persist)

    async def scenario():
        original = await verified_subscription()
        token, _contact = await issue_notification_contact_token(original)
        payload = {"resource_type": "model", "source": "provider", "resource_id": "race",
                   "resource_title": "Race", "email": "notify@example.com"}
        pending = asyncio.create_task(request_subscription(
            payload, notification_token=token, app_dir=str(notification_app),
        ))
        await claimed.wait()
        revocation = asyncio.create_task(revoke_notification_contact_token(token))
        await asyncio.sleep(0)
        assert not revocation.done()
        resume.set()
        (subscription, required), _ = await asyncio.gather(pending, revocation)
        assert not required
        assert subscription.contact_verified_at is not None
        assert await NotificationSubscription.all().count() == 2
        with pytest.raises(AuthenticationError) as error:
            await request_subscription(payload, notification_token=token)
        assert error.value.code == "notification_token_invalid"

    run(with_database(scenario))
