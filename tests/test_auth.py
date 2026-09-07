import asyncio
import json

import pytest
from tortoise import Tortoise

from onramp.auth.models import (
    Account,
    AccountSession,
    AudienceIdentity,
    EmailChallenge,
    EmailChallengeRateLimit,
)
from onramp.auth.config import application_public_url
from onramp.auth.security import email_digest, token_digest
from onramp.auth.service import (
    AuthenticationError,
    classify_email,
    consume_challenge,
    create_challenge,
    delete_account,
    request_account_code,
    verify_account_code,
    update_account_role,
)
from onramp.db import manager as manager_module
from onramp.notifications.models import NotificationDelivery, NotificationSubscription
from onramp.notifications.service import request_subscription, verify_subscription


def run(coroutine):
    return asyncio.run(coroutine)


def test_public_action_url_requires_a_safe_transport(monkeypatch):
    monkeypatch.setenv("ONRAMP_ENVIRONMENT", "production")
    monkeypatch.setenv("ONRAMP_PUBLIC_URL", "https://api.example.test/base/")
    assert application_public_url() == "https://api.example.test/base"

    for unsafe_url in (
        "http://api.example.test",
        "https://user:password@api.example.test",
        "https://api.example.test/?campaign=one",
        "https://api.example.test/#fragment",
        "https://:443",
    ):
        monkeypatch.setenv("ONRAMP_PUBLIC_URL", unsafe_url)
        with pytest.raises(RuntimeError):
            application_public_url()

    monkeypatch.setenv("ONRAMP_ENVIRONMENT", "test")
    monkeypatch.setenv("ONRAMP_PUBLIC_URL", "http://localhost:8000")
    assert application_public_url() == "http://localhost:8000"
    monkeypatch.setenv("ONRAMP_PUBLIC_URL", "http://192.0.2.4:8000")
    with pytest.raises(RuntimeError):
        application_public_url()


@pytest.fixture
def auth_app(tmp_path, monkeypatch):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "settings.py").write_text(
        "AUTH = {\n"
        "  'enabled': True,\n"
        "  'app_name': 'Test App',\n"
        "  'resend_delay_seconds': 0,\n"
        "}\n"
        "DATABASE = {'engine': 'sqlite', 'name': ':memory:'}\n"
    )
    monkeypatch.setenv("ONRAMP_ENVIRONMENT", "test")
    monkeypatch.setenv("ONRAMP_AUTH_SECRET", "a" * 32)
    monkeypatch.setenv("ONRAMP_IDENTITY_SECRET", "i" * 32)
    manager_module._db_manager = None

    yield app_dir
    manager_module._db_manager = None


def latest_code(app_dir):
    messages = (app_dir.parent / ".onramp" / "dev-mail-outbox.jsonl").read_text()
    return json.loads(messages.splitlines()[-1])["code"]


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


def test_signup_is_explicit_and_stores_only_code_and_session_digests(auth_app):
    async def scenario():
        await request_account_code("Person@Example.com", "signup", app_dir=str(auth_app))
        code = latest_code(auth_app)
        challenge = await EmailChallenge.all().first()
        assert challenge.code_digest != code

        account, token, session = await verify_account_code(
            "person@example.com", "signup", code, app_dir=str(auth_app)
        )
        assert account.email == "person@example.com"
        assert token.startswith("or_")
        assert session.token_hash == token_digest(token)
        assert session.token_hash != token

        _email, roles = await update_account_role(
            "person@example.com", "beta_tester", enabled=True
        )
        assert roles == ["beta_tester"]
        _email, roles = await update_account_role(
            "person@example.com", "beta_tester", enabled=False
        )
        assert roles == []

        with pytest.raises(AuthenticationError) as error:
            await request_account_code(
                "missing@example.com", "signin", app_dir=str(auth_app)
            )
        assert error.value.code == "account_not_found"

    run(with_database(scenario))


def test_notification_verification_never_creates_an_account(auth_app):
    async def scenario():
        subscription, verification_required = await request_subscription(
            {
                "resource_type": "model",
                "resource_id": "market-1",
                "source": "provider",
                "resource_title": "Will it happen?",
                "email": "notify@example.com",
            },
            app_dir=str(auth_app),
        )
        assert verification_required
        assert await Account.all().count() == 0
        code = latest_code(auth_app)

        verified = await verify_subscription(
            subscription.id,
            "notify@example.com",
            code,
            app_dir=str(auth_app),
        )
        assert verified.contact_verified_at is not None
        assert await Account.all().count() == 0
        assert not verified.demand_eligible

    run(with_database(scenario))


def test_notification_resend_delay_is_scoped_to_one_subscription(
    auth_app, monkeypatch
):
    from onramp.auth import service

    original_auth_config = service.auth_config

    def delayed_config(app_dir=None):
        return {**original_auth_config(app_dir), "resend_delay_seconds": 60}

    monkeypatch.setattr(service, "auth_config", delayed_config)

    async def scenario():
        first = await create_challenge(
            "person@example.com",
            "notification_subscription",
            subject_id="subscription-one",
            app_dir=str(auth_app),
        )
        second = await create_challenge(
            "person@example.com",
            "notification_subscription",
            subject_id="subscription-two",
            app_dir=str(auth_app),
        )
        assert first.id != second.id
        with pytest.raises(AuthenticationError) as error:
            await create_challenge(
                "person@example.com",
                "notification_subscription",
                subject_id="subscription-one",
                app_dir=str(auth_app),
            )
        assert error.value.code == "code_rate_limited"

    run(with_database(scenario))


def test_concurrent_challenge_limits_and_wrong_attempts_are_atomic(
    auth_app, monkeypatch
):
    from onramp.auth import service

    original_auth_config = service.auth_config

    def limited_config(app_dir=None):
        return {
            **original_auth_config(app_dir),
            "resend_delay_seconds": 60,
            "hourly_challenge_limit": 3,
            "challenge_attempts": 6,
        }

    async def discard_email(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "auth_config", limited_config)
    monkeypatch.setattr(service, "send_verification_code", discard_email)
    monkeypatch.setattr(service, "new_code", lambda: "123456")

    async def capture(coroutine):
        try:
            return await coroutine
        except AuthenticationError as error:
            return error

    async def scenario():
        same_subject = await asyncio.gather(
            *(
                capture(
                    create_challenge(
                        "parallel@example.com",
                        "notification_subscription",
                        subject_id="same-subscription",
                        app_dir=str(auth_app),
                    )
                )
                for _ in range(12)
            )
        )
        assert sum(isinstance(result, EmailChallenge) for result in same_subject) == 1
        assert sum(isinstance(result, AuthenticationError) for result in same_subject) == 11

        hourly = await asyncio.gather(
            *(
                capture(
                    create_challenge(
                        "hourly@example.com",
                        "notification_subscription",
                        subject_id=f"subscription-{index}",
                        app_dir=str(auth_app),
                    )
                )
                for index in range(12)
            )
        )
        assert sum(isinstance(result, EmailChallenge) for result in hourly) == 3

        challenge = await create_challenge(
            "attempts@example.com",
            "notification_subscription",
            subject_id="attempt-limited",
            app_dir=str(auth_app),
        )
        attempts = await asyncio.gather(
            *(
                capture(
                    consume_challenge(
                        "attempts@example.com",
                        "notification_subscription",
                        "000000",
                        subject_id="attempt-limited",
                        app_dir=str(auth_app),
                    )
                )
                for _ in range(20)
            )
        )
        await challenge.refresh_from_db()
        assert challenge.attempts == 6
        assert all(isinstance(result, AuthenticationError) for result in attempts)
        with pytest.raises(AuthenticationError) as error:
            await consume_challenge(
                "attempts@example.com",
                "notification_subscription",
                "123456",
                subject_id="attempt-limited",
                app_dir=str(auth_app),
            )
        assert error.value.code == "code_attempts_exceeded"

    run(with_database(scenario))


def test_classification_and_deletion_retain_anonymized_history(auth_app):
    async def scenario():
        await classify_email("tester@example.com", "tester")
        identity = await AudienceIdentity.get(
            email_hash=email_digest("tester@example.com")
        )
        assert identity.audience_type == "tester"

        await request_account_code("tester@example.com", "signup", app_dir=str(auth_app))
        account, _token, _session = await verify_account_code(
            "tester@example.com", "signup", latest_code(auth_app), app_dir=str(auth_app)
        )
        subscription, needs_verification = await request_subscription(
            {
                "resource_type": "model",
                "resource_id": "market-2",
                "source": "provider",
                "resource_title": "A second market",
            },
            account,
            app_dir=str(auth_app),
        )
        assert not needs_verification
        assert subscription.account_id == account.id

        earlier_anonymous = await NotificationSubscription.create(
            resource_type="model",
            resource_id="market-before-account",
            source="provider",
            resource_title="An earlier request",
            contact_email=account.email,
            contact_email_hash=account.email_hash,
            contact_verified_at=account.verified_at,
            environment="test",
            audience_type="development",
            demand_eligible=False,
        )
        from onramp.notifications.models import NotificationContactToken
        from onramp.notifications.service import issue_notification_contact_token

        await issue_notification_contact_token(earlier_anonymous, app_dir=str(auth_app))

        delivery = await NotificationDelivery.create(
            subscription_id=subscription.id,
            event_key="ready/1",
            environment="test",
            idempotency_key="notification/test",
            recipient_email_hash=subscription.contact_email_hash,
        )
        original_delivery_key = delivery.idempotency_key

        from onramp.auth.service import request_account_deletion

        await request_account_deletion(account, app_dir=str(auth_app))
        result = await delete_account(
            account, latest_code(auth_app), app_dir=str(auth_app)
        )
        retained = await NotificationSubscription.get(id=subscription.id)
        assert result["anonymized_subscriptions"] == 2
        assert result["deleted_notification_contact_tokens"] == 1
        assert await NotificationContactToken.all().count() == 0
        assert retained.account_id is None
        assert retained.contact_email is None
        assert retained.contact_email_hash is None
        assert retained.anonymized_at is not None
        await earlier_anonymous.refresh_from_db()
        assert earlier_anonymous.contact_email is None
        assert earlier_anonymous.contact_email_hash is None
        assert earlier_anonymous.anonymized_at is not None
        await delivery.refresh_from_db()
        assert delivery.recipient_email_hash is None
        assert delivery.idempotency_key.startswith("anonymized/")
        assert delivery.idempotency_key != original_delivery_key
        assert await AccountSession.all().count() == 0
        assert await EmailChallengeRateLimit.all().count() == 0
        assert await Account.all().count() == 0
        assert await AudienceIdentity.filter(
            email_hash=email_digest("tester@example.com"),
            audience_type="tester",
        ).exists()

    run(with_database(scenario))


def test_production_demand_counts_only_verified_regular_subscriptions(
    auth_app, monkeypatch
):
    from onramp.auth import email as email_module

    def development_delivery(message, _config):
        return email_module._write_development_message(message, str(auth_app))

    monkeypatch.setattr(email_module, "_send_with_resend", development_delivery)
    monkeypatch.setenv("ONRAMP_PUBLIC_URL", "https://api.example.test")

    async def scenario():
        subscription, verification_required = await request_subscription(
            {
                "resource_type": "model",
                "resource_id": "market-3",
                "source": "provider",
                "resource_title": "A production market",
                "email": "demand@example.com",
            },
            app_dir=str(auth_app),
        )
        assert verification_required
        assert not subscription.demand_eligible

        subscription.environment = "production"
        subscription.audience_type = "regular"
        await subscription.save(
            update_fields=["environment", "audience_type", "updated_at"]
        )
        code = latest_code(auth_app)
        monkeypatch.setenv("ONRAMP_ENVIRONMENT", "production")
        verified = await verify_subscription(
            subscription.id,
            "demand@example.com",
            code,
            app_dir=str(auth_app),
        )
        assert verified.demand_eligible

        repeated, verification_required = await request_subscription(
            {
                "resource_type": "model",
                "resource_id": "market-3",
                "source": "provider",
                "resource_title": "An updated production title",
                "email": "demand@example.com",
            },
            app_dir=str(auth_app),
        )
        assert verification_required
        assert repeated.id == verified.id
        assert repeated.demand_eligible
        repeated_verified = await verify_subscription(
            repeated.id,
            "demand@example.com",
            latest_code(auth_app),
            app_dir=str(auth_app),
        )
        assert repeated_verified.id == verified.id

    run(with_database(scenario))
