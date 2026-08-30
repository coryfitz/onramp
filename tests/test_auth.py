import asyncio
import json

import pytest
from tortoise import Tortoise

from onramp.auth.models import Account, AccountSession, AudienceIdentity, EmailChallenge
from onramp.auth.security import email_digest, token_digest
from onramp.auth.service import (
    AuthenticationError,
    classify_email,
    delete_account,
    request_account_code,
    verify_account_code,
    update_account_role,
)
from onramp.db import manager as manager_module
from onramp.notifications.models import NotificationSubscription
from onramp.notifications.service import request_subscription, verify_subscription


def run(coroutine):
    return asyncio.run(coroutine)


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

        from onramp.auth.service import request_account_deletion

        await request_account_deletion(account, app_dir=str(auth_app))
        result = await delete_account(
            account, latest_code(auth_app), app_dir=str(auth_app)
        )
        retained = await NotificationSubscription.get(id=subscription.id)
        assert result["anonymized_subscriptions"] == 1
        assert retained.account_id is None
        assert retained.contact_email is None
        assert retained.contact_email_hash is None
        assert retained.anonymized_at is not None
        assert await AccountSession.all().count() == 0
        assert await Account.all().count() == 0
        assert await AudienceIdentity.filter(
            email_hash=email_digest("tester@example.com"),
            audience_type="tester",
        ).exists()

    run(with_database(scenario))


def test_production_demand_counts_only_verified_regular_subscriptions(
    auth_app, monkeypatch
):
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
        assert not verification_required
        assert repeated.id == verified.id
        assert repeated.demand_eligible

    run(with_database(scenario))
