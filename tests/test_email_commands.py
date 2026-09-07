import pytest
from tortoise import Tortoise

from onramp import cli, email_commands
from onramp.auth import email as delivery
from onramp.db import manager as db_manager


@pytest.fixture
def email_project(tmp_path, monkeypatch):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "settings.py").write_text(
        "AUTH = {'enabled': True, 'email_from': 'App <accounts@myapp.com>'}\n"
        "ENVIRONMENT = 'development'\n"
    )
    for name in (
        "ONRAMP_ENVIRONMENT", "ONRAMP_PUBLIC_URL", "ONRAMP_EMAIL_FROM",
        "RESEND_API_KEY", "ONRAMP_AUTH_SECRET", "ONRAMP_IDENTITY_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli, "APP_DIR", str(app_dir))
    monkeypatch.setattr(db_manager, "_db_manager", None)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Email diagnostics must not touch the network or database")

    monkeypatch.setattr(Tortoise, "init", forbidden)
    monkeypatch.setattr(delivery, "urlopen", forbidden)
    monkeypatch.setattr(cli, "_clean_empty_shadow_dirs", forbidden)
    return app_dir


def invoke(monkeypatch, *arguments):
    monkeypatch.setattr(cli.sys, "argv", ["onramp", "email", *arguments])
    return cli.main()


def hosted(monkeypatch):
    monkeypatch.setenv("ONRAMP_ENVIRONMENT", "staging")
    monkeypatch.setenv("ONRAMP_PUBLIC_URL", "https://api.myapp.com")
    monkeypatch.setenv("ONRAMP_AUTH_SECRET", "auth-private-" * 4)
    monkeypatch.setenv("ONRAMP_IDENTITY_SECRET", "identity-private-" * 4)
    monkeypatch.setenv("RESEND_API_KEY", "resend-private")


def test_local_check_and_preview_never_send_or_create_outbox(email_project, monkeypatch, capsys):
    assert invoke(monkeypatch, "--check") == 0
    assert invoke(monkeypatch, "test", "owner@myapp.com") == 0
    output = capsys.readouterr().out
    assert "delivery: development" in output
    assert "Test preview only" in output
    assert not (email_project.parent / ".onramp").exists()
    assert not (email_project / "db").exists()


def test_local_explicit_send_only_writes_outbox(email_project, monkeypatch, capsys):
    assert invoke(monkeypatch, "test", "owner@myapp.com", "--send") == 0
    outbox = email_project.parent / ".onramp" / "dev-mail-outbox.jsonl"
    content = outbox.read_text()
    assert "OnRamp email delivery test" in content
    assert "does not create an account" in content
    assert '"code"' not in content
    assert len(content.splitlines()) == 1
    assert not (email_project / "db").exists()
    assert "No external email was sent" in capsys.readouterr().out


def test_check_reports_all_missing_hosted_settings(email_project, monkeypatch, capsys):
    assert invoke(monkeypatch, "--check", "--environment", "staging") == 1
    output = capsys.readouterr().out
    for name in ("ONRAMP_PUBLIC_URL", "ONRAMP_AUTH_SECRET", "ONRAMP_IDENTITY_SECRET", "RESEND_API_KEY"):
        assert name in output
    assert not (email_project.parent / ".onramp").exists()


def test_configured_hosted_check_redacts_secrets_and_does_not_claim_delivery(email_project, monkeypatch, capsys):
    hosted(monkeypatch)
    assert invoke(monkeypatch, "--check") == 0
    output = capsys.readouterr().out
    assert "offline check cannot verify DNS" in output
    assert "private" not in output


def test_check_rejects_reused_secrets_insecure_url_and_placeholder(email_project, monkeypatch, capsys):
    hosted(monkeypatch)
    monkeypatch.setenv("ONRAMP_IDENTITY_SECRET", "auth-private-" * 4)
    monkeypatch.setenv("ONRAMP_PUBLIC_URL", "http://api.myapp.com")
    monkeypatch.setenv("ONRAMP_EMAIL_FROM", "App <accounts@example.com>")
    assert invoke(monkeypatch, "--check") == 1
    output = capsys.readouterr().out
    assert "must be different" in output
    assert "HTTPS" in output
    assert "placeholder" in output
    assert "private" not in output


def test_hosted_sends_require_explicit_flags(email_project, monkeypatch, capsys):
    hosted(monkeypatch)
    calls = []

    async def fake_send(*args, **kwargs):
        calls.append((args, kwargs))
        return delivery.EmailSendResult("resend", "fake-message-id")

    monkeypatch.setattr(email_commands, "send_transactional_email", fake_send)
    assert invoke(monkeypatch, "test", "owner@myapp.com") == 0
    assert calls == []
    assert invoke(monkeypatch, "test", "owner@myapp.com", "--send") == 0
    assert len(calls) == 1
    assert invoke(monkeypatch, "test", "owner@myapp.com", "--send", "--environment", "production") == 2
    assert len(calls) == 1
    assert invoke(monkeypatch, "test", "owner@myapp.com", "--send", "--confirm-production") == 0
    assert len(calls) == 2
    assert "not an inbox delivery confirmation" in capsys.readouterr().out
    assert not (email_project.parent / ".onramp").exists()


@pytest.mark.parametrize("arguments", [
    [], ["test"], ["test", "invalid"], ["--check", "--send"],
    ["test", "owner@myapp.com", "--check"],
    ["test", "owner@myapp.com", "--send", "--dry-run"],
    ["test", "owner@myapp.com", "--confirm-production"],
])
def test_invalid_commands_cannot_send(email_project, monkeypatch, arguments):
    assert invoke(monkeypatch, *arguments) == 2
    assert not (email_project.parent / ".onramp").exists()


def test_custom_sender_never_imported_by_preflight_or_preview(email_project, monkeypatch, capsys):
    (email_project / "settings.py").write_text(
        "AUTH = {'enabled': True, 'email_sender': 'not_installed.sender'}\n"
    )
    assert invoke(monkeypatch, "--check") == 0
    assert invoke(monkeypatch, "test", "owner@myapp.com") == 0
    assert "overrides the development outbox" in capsys.readouterr().out
    assert not (email_project.parent / ".onramp").exists()


def test_custom_sender_failure_does_not_print_secret(email_project, monkeypatch, capsys):
    async def broken_sender(*_args, **_kwargs):
        raise RuntimeError("provider-secret-in-exception")

    monkeypatch.setattr(email_commands, "send_transactional_email", broken_sender)
    assert invoke(monkeypatch, "test", "owner@myapp.com", "--send") == 1
    output = capsys.readouterr().out
    assert "Test email failed" in output
    assert "provider-secret" not in output


def test_setting_environment_is_respected_when_env_not_exported(email_project, monkeypatch, capsys):
    (email_project / "settings.py").write_text(
        "AUTH = {'enabled': True}\nENVIRONMENT = 'production'\n"
    )
    assert invoke(monkeypatch, "--check") == 1
    assert "Email environment: production" in capsys.readouterr().out


def test_no_project_is_not_reported_ready(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "APP_DIR", str(tmp_path / "missing"))
    assert invoke(monkeypatch, "--check") == 1
    assert "app/settings.py" in capsys.readouterr().out
