import json

from starlette.testclient import TestClient

from onramp.app import OnRamp
from onramp.db import manager as manager_module


def latest_code(root):
    outbox = root / ".onramp" / "dev-mail-outbox.jsonl"
    return json.loads(outbox.read_text().splitlines()[-1])["code"]


def test_builtin_auth_cookie_and_notification_routes(tmp_path, monkeypatch):
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
        f"DATABASE = {{'engine': 'sqlite', 'name': {str(tmp_path / 'auth.sqlite3')!r}}}\n"
    )
    monkeypatch.setenv("ONRAMP_ENVIRONMENT", "development")
    monkeypatch.setenv("ONRAMP_AUTH_SECRET", "a" * 32)
    monkeypatch.setenv("ONRAMP_IDENTITY_SECRET", "i" * 32)
    manager_module._db_manager = None

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

        deletion_request = client.post("/api/account/delete/request")
        assert deletion_request.status_code == 202
        deleted = client.request(
            "DELETE", "/api/account", json={"code": latest_code(tmp_path)}
        )
        assert deleted.status_code == 200
        assert deleted.json()["anonymized_subscriptions"] == 1
        assert client.get("/api/account").status_code == 401

    manager_module._db_manager = None
