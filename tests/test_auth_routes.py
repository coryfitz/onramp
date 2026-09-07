import json
from datetime import datetime, timedelta

import pytest

from starlette.testclient import TestClient

from onramp.app import OnRamp
from onramp.auth.security import sign_action_token
from onramp.db import manager as manager_module


def latest_code(root):
    outbox = root / ".onramp" / "dev-mail-outbox.jsonl"
    return json.loads(outbox.read_text().splitlines()[-1])["code"]


def write_auth_app(tmp_path, database_name="auth.sqlite3"):
    app_dir = tmp_path / "app"
    (app_dir / "api").mkdir(parents=True)
    (app_dir / "models").mkdir()
    for package in (app_dir, app_dir / "api", app_dir / "models"):
        (package / "__init__.py").write_text("")
    (app_dir / "settings.py").write_text(
        "AUTH = {\n"
        "  'enabled': True,\n"
        "  'app_name': 'Route Test',\n"
        "  'resend_delay_seconds': 0,\n"
        "}\n"
        "ENVIRONMENT = 'development'\n"
        "AUTO_GENERATE_SCHEMAS = True\n"
        f"DATABASE = {{'engine': 'sqlite', 'name': {str(tmp_path / database_name)!r}}}\n"
    )
    return app_dir


def configure_auth_environment(monkeypatch):
    monkeypatch.setenv("ONRAMP_ENVIRONMENT", "development")
    monkeypatch.setenv("ONRAMP_AUTH_SECRET", "a" * 32)
    monkeypatch.setenv("ONRAMP_IDENTITY_SECRET", "i" * 32)
    manager_module._db_manager = None


def test_builtin_auth_cookie_and_notification_routes(tmp_path, monkeypatch):
    app_dir = write_auth_app(tmp_path)
    configure_auth_environment(monkeypatch)

    with TestClient(OnRamp(str(app_dir)).create_app()) as client:
        invalid = client.post(
            "/api/auth/request",
            json={"email": "not-an-email", "intent": "signup"},
        )
        assert invalid.status_code == 400
        assert invalid.json()["code"] == "invalid_email"

        request = client.post(
            "/api/auth/request",
            json={"email": "person@example.com", "intent": "signup"},
        )
        assert request.status_code == 202
        verify = client.post(
            "/api/auth/verify",
            json={
                "email": "person@example.com",
                "intent": "signup",
                "code": latest_code(tmp_path),
                "session_mode": "cookie",
            },
        )
        assert verify.status_code == 200
        assert "session_token" not in verify.json()
        assert verify.cookies.get("onramp_session")
        assert client.get("/api/account").json()["account"]["email"] == (
            "person@example.com"
        )

        subscription = client.post(
            "/api/notifications/subscriptions",
            json={
                "resource_type": "model",
                "resource_id": "market-1",
                "resource_title": "A public market",
                "source": "provider",
            },
        )
        assert subscription.status_code == 200
        assert subscription.json()["verification_required"] is False
        assert subscription.json()["demand_eligible"] is False
        assert subscription.json()["unsubscribe_url"].startswith(
            "http://127.0.0.1:8000/api/notifications/unsubscribe?token="
        )
        assert subscription.json()["unsubscribe_path"].startswith(
            "/api/notifications/unsubscribe?token="
        )

        unknown = client.post(
            "/api/notifications/subscriptions",
            json={
                "resource_type": "model",
                "resource_id": "market-1",
                "resource_title": "A public market",
                "source": "provider",
                "unexpected": "not stored",
            },
        )
        assert unknown.status_code == 400
        assert unknown.json()["code"] == "unsupported_fields"

        too_large = client.post(
            "/api/notifications/subscriptions",
            content=json.dumps({"metadata": {"padding": "x" * 66_000}}),
            headers={"content-type": "application/json"},
        )
        assert too_large.status_code == 413
        assert too_large.json()["code"] == "request_too_large"

        unsubscribe_token = sign_action_token(
            "notification_unsubscribe", subscription.json()["subscription_id"]
        )
        confirmation = client.get(
            "/api/notifications/unsubscribe", params={"token": unsubscribe_token}
        )
        assert confirmation.status_code == 200
        assert "Stop this notification?" in confirmation.text
        assert confirmation.headers["referrer-policy"] == "no-referrer"
        assert "default-src 'none'" in confirmation.headers["content-security-policy"]
        invalid_confirmation = client.get(
            "/api/notifications/unsubscribe", params={"token": "invalid-private-token"},
            headers={"accept": "text/html"},
        )
        assert invalid_confirmation.status_code == 400
        assert invalid_confirmation.headers["referrer-policy"] == "no-referrer"
        assert invalid_confirmation.headers["cache-control"] == "no-store"
        assert "invalid-private-token" not in invalid_confirmation.text
        oversized_form = client.post(
            "/api/notifications/unsubscribe",
            content="token=" + ("x" * 9_000),
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert oversized_form.status_code == 413
        assert oversized_form.json()["code"] == "request_too_large"
        unsubscribe = client.post(
            "/api/notifications/unsubscribe", json={"token": unsubscribe_token}
        )
        assert unsubscribe.status_code == 200
        assert unsubscribe.json()["unsubscribed"] is True
        assert client.post(
            "/api/notifications/unsubscribe", json={"token": unsubscribe_token}
        ).status_code == 200

        deletion_request = client.post("/api/account/delete/request")
        assert deletion_request.status_code == 202
        deleted = client.request(
            "DELETE", "/api/account", json={"code": latest_code(tmp_path)}
        )
        assert deleted.status_code == 200
        assert deleted.json()["anonymized_subscriptions"] == 1
        assert client.get("/api/account").status_code == 401

    manager_module._db_manager = None


def test_notification_verification_is_client_rate_limited(tmp_path, monkeypatch):
    app_dir = write_auth_app(tmp_path, "verification-limit.sqlite3")
    settings = (app_dir / "settings.py").read_text()
    (app_dir / "settings.py").write_text(
        settings.replace(
            "  'resend_delay_seconds': 0,",
            "  'resend_delay_seconds': 0,\n"
            "  'notification_ip_hourly_limit': 2,",
        )
    )
    configure_auth_environment(monkeypatch)

    with TestClient(OnRamp(str(app_dir)).create_app()) as client:
        requested = client.post(
            "/api/notifications/subscriptions",
            json={
                "resource_type": "model",
                "resource_id": "rate-limited-verify",
                "resource_title": "Rate limited",
                "source": "provider",
                "email": "limited@example.com",
            },
        )
        assert requested.status_code == 202
        correct_code = latest_code(tmp_path)
        incorrect_code = "000000" if correct_code != "000000" else "999999"
        first_verify = client.post(
            "/api/notifications/subscriptions/verify",
            json={
                "subscription_id": requested.json()["subscription_id"],
                "email": "limited@example.com",
                "code": incorrect_code,
            },
        )
        assert first_verify.status_code == 400
        blocked = client.post(
            "/api/notifications/subscriptions/verify",
            json={
                "subscription_id": requested.json()["subscription_id"],
                "email": "limited@example.com",
                "code": correct_code,
            },
        )
        assert blocked.status_code == 429
        assert blocked.json()["code"] == "request_rate_limited"

    manager_module._db_manager = None


def test_account_request_and_verification_are_client_rate_limited(
    tmp_path, monkeypatch
):
    app_dir = write_auth_app(tmp_path, "account-limit.sqlite3")
    settings = (app_dir / "settings.py").read_text()
    (app_dir / "settings.py").write_text(
        settings.replace(
            "  'resend_delay_seconds': 0,",
            "  'resend_delay_seconds': 0,\n"
            "  'auth_ip_hourly_limit': 2,",
        )
    )
    configure_auth_environment(monkeypatch)

    with TestClient(OnRamp(str(app_dir)).create_app()) as client:
        first = client.post(
            "/api/auth/request",
            json={"email": "first@example.com", "intent": "signup"},
        )
        first_code = latest_code(tmp_path)
        second = client.post(
            "/api/auth/request",
            json={"email": "second@example.com", "intent": "signup"},
        )
        second_code = latest_code(tmp_path)
        assert first.status_code == second.status_code == 202
        rotated = client.post(
            "/api/auth/request",
            json={"email": "third@example.com", "intent": "signup"},
        )
        assert rotated.status_code == 429
        assert rotated.json()["code"] == "request_rate_limited"

        for email, correct_code in (
            ("first@example.com", first_code),
            ("second@example.com", second_code),
        ):
            incorrect_code = "000000" if correct_code != "000000" else "999999"
            incorrect = client.post(
                "/api/auth/verify",
                json={"email": email, "intent": "signup", "code": incorrect_code},
            )
            assert incorrect.status_code == 400
        blocked = client.post(
            "/api/auth/verify",
            json={
                "email": "first@example.com",
                "intent": "signup",
                "code": first_code,
            },
        )
        assert blocked.status_code == 429
        assert blocked.json() == {
            "error": "Too many requests were submitted. Try again later.",
            "code": "request_rate_limited",
        }

    manager_module._db_manager = None


@pytest.mark.parametrize("trusted_proxy", [False, True])
def test_limiter_uses_only_asgi_server_trusted_proxy_resolution(
    tmp_path, monkeypatch, trusted_proxy
):
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    app_dir = write_auth_app(tmp_path)
    settings = app_dir / "settings.py"
    settings.write_text(settings.read_text().replace(
        "  'resend_delay_seconds': 0,",
        "  'resend_delay_seconds': 0,\n"
        "  'auth_ip_hourly_limit': 1,\n"
        # The legacy setting must not authorize parsing attacker headers.
        "  'trust_notification_proxy_headers': True,",
    ))
    configure_auth_environment(monkeypatch)
    app = ProxyHeadersMiddleware(
        OnRamp(str(app_dir)).create_app(),
        trusted_hosts="testclient" if trusted_proxy else "127.0.0.1",
    )
    with TestClient(app) as client:
        first = client.post(
            "/api/auth/request", json={"email": "invalid", "intent": "signup"},
            headers={"x-forwarded-for": "192.0.2.1"},
        )
        assert first.status_code == 400
        rotated = client.post(
            "/api/auth/request", json={"email": "invalid", "intent": "signup"},
            headers={"x-forwarded-for": "192.0.2.2"},
        )
        assert rotated.status_code == (400 if trusted_proxy else 429)
        repeated = client.post(
            "/api/auth/request", json={"email": "invalid", "intent": "signup"},
            headers={"x-forwarded-for": "192.0.2.1"},
        )
        assert repeated.status_code == 429
    manager_module._db_manager = None


def test_anonymous_subscription_response_does_not_reveal_existing_membership(
    tmp_path, monkeypatch
):
    app_dir = write_auth_app(tmp_path, "oracle.sqlite3")
    configure_auth_environment(monkeypatch)
    payload = {
        "resource_type": "model",
        "resource_id": "market-oracle",
        "resource_title": "A market",
        "source": "provider",
        "email": "private@example.com",
    }

    with TestClient(OnRamp(str(app_dir)).create_app()) as client:
        new_response = client.post("/api/notifications/subscriptions", json=payload)
        assert new_response.status_code == 202
        assert "unsubscribe_url" not in new_response.json()
        assert "unsubscribe_path" not in new_response.json()
        verification_message = json.loads(
            (tmp_path / ".onramp" / "dev-mail-outbox.jsonl")
            .read_text()
            .splitlines()[-1]
        )
        assert verification_message["manage_url"].startswith(
            "http://127.0.0.1:8000/api/notifications/unsubscribe?token="
        )
        assert verification_message["manage_url"] in verification_message["text"]
        verified = client.post(
            "/api/notifications/subscriptions/verify",
            json={
                "subscription_id": new_response.json()["subscription_id"],
                "email": payload["email"],
                "code": latest_code(tmp_path),
            },
        )
        assert verified.status_code == 200
        assert verified.json()["unsubscribe_url"].startswith(
            "http://127.0.0.1:8000/api/notifications/unsubscribe?token="
        )
        assert verified.json()["unsubscribe_path"].startswith(
            "/api/notifications/unsubscribe?token="
        )
        existing_response = client.post(
            "/api/notifications/subscriptions", json=payload
        )
        assert existing_response.status_code == 202
        assert existing_response.json() == new_response.json()
        assert existing_response.json() == {
            "subscription_id": new_response.json()["subscription_id"],
            "status": "unverified",
            "verification_required": True,
            "demand_eligible": False,
            "suppressed": False,
        }

    manager_module._db_manager = None


@pytest.mark.parametrize("lifetime_days", [None, 7])
def test_notification_remembered_proof_is_optional_scoped_and_not_account_auth(
    tmp_path, monkeypatch, lifetime_days
):
    app_dir = write_auth_app(tmp_path, "remembered.sqlite3")
    configure_auth_environment(monkeypatch)
    payload = {
        "resource_type": "model", "resource_id": "first", "source": "kalshi",
        "resource_title": "First resource", "email": "remember@example.com",
    }
    with TestClient(OnRamp(str(app_dir)).create_app()) as client:
        manager_module.get_db_manager(str(app_dir)).settings["AUTH"][
            "notification_contact_token_days"
        ] = lifetime_days
        requested = client.post("/api/notifications/subscriptions", json=payload)
        assert requested.status_code == 202
        verification = {
            "subscription_id": requested.json()["subscription_id"],
            "email": payload["email"], "code": latest_code(tmp_path),
        }
        wrong_code = "000000" if verification["code"] != "000000" else "999999"
        invalid = client.post(
            "/api/notifications/subscriptions/verify",
            json={**verification, "code": wrong_code, "remember_email": True},
        )
        assert invalid.status_code == 400
        assert "notification_token" not in invalid.json()
        invalid_type = client.post(
            "/api/notifications/subscriptions/verify",
            json={**verification, "remember_email": "true"},
        )
        assert invalid_type.status_code == 400
        verified = client.post(
            "/api/notifications/subscriptions/verify",
            json={**verification, "remember_email": True},
        )
        assert verified.status_code == 200
        assert verified.headers["cache-control"] == "no-store"
        token = verified.json()["notification_token"]
        if lifetime_days is None:
            assert verified.json()["notification_token_expires_at"] is None
        else:
            from onramp.notifications.models import NotificationContactToken

            expires_at = datetime.fromisoformat(verified.json()["notification_token_expires_at"])
            contact = client.portal.call(NotificationContactToken.get)
            assert expires_at == contact.expires_at
            assert expires_at - contact.verified_at == timedelta(days=lifetime_days)
        assert "session_token" not in verified.json()
        assert not verified.cookies
        headers = {"X-OnRamp-Notification-Token": token}
        assert client.get("/api/account", headers=headers).status_code == 401
        assert client.get(
            "/api/account", headers={"Authorization": f"Bearer {token}"}
        ).status_code == 401
        assert client.post("/api/account/delete/request", headers=headers).status_code == 401

        outbox = tmp_path / ".onramp" / "dev-mail-outbox.jsonl"
        proof_mail = outbox.read_text()
        next_payload = {**payload, "source": "polymarket", "resource_id": "second"}
        reused = client.post(
            "/api/notifications/subscriptions", json=next_payload, headers=headers
        )
        assert reused.status_code == 200
        assert reused.json()["verification_required"] is False
        assert reused.json()["status"] == "verified"
        assert reused.json()["unsubscribe_path"]
        assert outbox.read_text() == proof_mail
        assert token not in proof_mail
        assert not reused.cookies

        # Merely providing the same address still requires fresh proof.
        unproved = client.post("/api/notifications/subscriptions", json=next_payload)
        assert unproved.status_code == 202
        assert unproved.json()["verification_required"] is True
        assert "unsubscribe_path" not in unproved.json()
        assert "notification_token" not in unproved.json()

        for wrong_payload in (
            {**next_payload, "email": "other@example.com"},
            {**next_payload, "resource_type": "newsletter"},
        ):
            invalid = client.post(
                "/api/notifications/subscriptions", json=wrong_payload, headers=headers
            )
            assert invalid.status_code == 401
            assert invalid.json()["code"] == "notification_token_invalid"
        for _ in range(2):
            revoked = client.post("/api/notifications/contact/revoke", headers=headers)
            assert revoked.status_code == 200
            assert revoked.json() == {"revoked": True}
        after_revoke = client.post(
            "/api/notifications/subscriptions", json=next_payload, headers=headers
        )
        assert after_revoke.status_code == 401
        assert after_revoke.json()["code"] == "notification_token_invalid"

        # Verification without the opt-in does not issue a capability.
        verified_without_remember = client.post(
            "/api/notifications/subscriptions/verify",
            json={"subscription_id": unproved.json()["subscription_id"],
                  "email": payload["email"], "code": latest_code(tmp_path)},
        )
        assert verified_without_remember.status_code == 200
        assert "notification_token" not in verified_without_remember.json()

        # A signed-in account keeps precedence, even with a stale contact header.
        client.post("/api/auth/request", json={"email": payload["email"], "intent": "signup"})
        signed_in = client.post("/api/auth/verify", json={
            "email": payload["email"], "intent": "signup", "code": latest_code(tmp_path),
            "session_mode": "cookie",
        })
        assert signed_in.status_code == 200
        authenticated = client.post(
            "/api/notifications/subscriptions",
            json={key: value for key, value in payload.items() if key != "email"},
            headers=headers,
        )
        assert authenticated.status_code == 200
        assert authenticated.json()["verification_required"] is False
    manager_module._db_manager = None


def test_notification_token_issued_only_after_successful_retriable_hook(
    tmp_path, monkeypatch
):
    from onramp.notifications import service
    from onramp.notifications.models import NotificationContactToken

    app_dir = write_auth_app(tmp_path, "remembered-hook.sqlite3")
    configure_auth_environment(monkeypatch)
    calls = []

    async def ready_hook(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("Retry the application hook")

    monkeypatch.setattr(service, "_run_subscription_ready_hook", ready_hook)
    with TestClient(OnRamp(str(app_dir)).create_app(), raise_server_exceptions=False) as client:
        requested = client.post("/api/notifications/subscriptions", json={
            "resource_type": "model", "resource_id": "hook", "source": "provider",
            "resource_title": "Retriable hook", "email": "hook@example.com",
        })
        verification = {
            "subscription_id": requested.json()["subscription_id"],
            "email": "hook@example.com", "code": latest_code(tmp_path),
            "remember_email": True,
        }
        failed = client.post("/api/notifications/subscriptions/verify", json=verification)
        assert failed.status_code == 500
        assert "notification_token" not in failed.text
        assert client.portal.call(NotificationContactToken.all().count) == 0
        retried = client.post("/api/notifications/subscriptions/verify", json=verification)
        assert retried.status_code == 200
        assert retried.json()["notification_token"]
        assert client.portal.call(NotificationContactToken.all().count) == 1
        repeated = client.post("/api/notifications/subscriptions/verify", json=verification)
        assert repeated.status_code == 400
        assert client.portal.call(NotificationContactToken.all().count) == 1
    manager_module._db_manager = None
