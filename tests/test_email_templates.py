import asyncio
from dataclasses import fields, replace
import json

import pytest

from onramp.auth import email as delivery
from onramp.auth.email_templates import (
    EmailTemplate,
    VerificationEmailContext,
    default_verification_email,
    safe_email_url,
    validate_email_template,
)
from onramp.email_commands import check_email_configuration
from onramp.notifications.service import _append_unsubscribe


CONTEXT = VerificationEmailContext(
    purpose="notification_subscription", code="123456", app_name="Example App",
    expires_minutes=10, public_url="https://api.example.com",
    manage_url="https://api.example.com/unsubscribe?token=public-action&confirm=1",
)


@pytest.mark.parametrize("purpose, expected", [
    ("notification_subscription", "verify your notification request"),
    ("signup", "create your Example App account"),
    ("signin", "sign in to Example App"),
    ("delete_account", "delete your Example App account"),
])
def test_default_templates_keep_purposes_and_expiry_distinct(purpose, expected):
    template = default_verification_email(replace(CONTEXT, purpose=purpose, expires_minutes=7, manage_url=None))
    assert expected in template.text
    assert expected in template.html
    assert "123456" in template.text and "123456" in template.html
    assert "123456" not in template.subject
    assert "Expires in 7 minutes." in template.text
    assert "Expires in 7 minutes." in template.html
    assert '<table role="presentation"' in template.html
    assert "<script" not in template.html
    if purpose == "notification_subscription":
        assert "No account is created" in template.text


def test_default_template_escapes_app_name_and_action_url():
    template = default_verification_email(replace(CONTEXT, app_name='<App "One" & Two>'))
    assert '&lt;App &quot;One&quot; &amp; Two&gt;' in template.html
    assert '<App "One"' not in template.html
    assert "public-action&amp;confirm=1" in template.html
    assert CONTEXT.manage_url in template.text


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://secret@api.example.com", "https://api.example.com/\n", "https://api.example.com:bad"])
def test_template_links_reject_unsafe_urls(url):
    with pytest.raises(ValueError):
        safe_email_url(url)


@pytest.mark.parametrize("code", ["12345", "<1234>", "１２３４５６", "123456<script>"])
def test_invalid_code_cannot_enter_template(code):
    with pytest.raises(ValueError):
        default_verification_email(replace(CONTEXT, code=code))


@pytest.mark.parametrize("template", [
    None, {"subject": "Example"}, EmailTemplate("A\nB", "text", "html"),
    EmailTemplate("Example", "", "html"), EmailTemplate("Example", "text", ""),
])
def test_invalid_override_result_fails_closed(template):
    with pytest.raises(ValueError):
        validate_email_template(template)


def test_override_context_is_presentation_only():
    assert {field.name for field in fields(VerificationEmailContext)} == {
        "purpose", "code", "app_name", "expires_minutes", "public_url", "manage_url",
    }


def test_override_retains_development_outbox_and_html(tmp_path, monkeypatch):
    config = {"app_name": "Example App", "verification_email_renderer": "app.mail.render", "challenge_minutes": 10}
    monkeypatch.setattr(delivery, "auth_config", lambda _: config)
    monkeypatch.setattr(delivery, "application_public_url", lambda _: CONTEXT.public_url)
    monkeypatch.setenv("ONRAMP_ENVIRONMENT", "development")
    monkeypatch.setattr(delivery, "import_callable", lambda _: lambda context: EmailTemplate(
        "Custom verification", f"Code: {context.code}", f"<p>Code: {context.code}</p>",
    ))
    monkeypatch.setattr(delivery, "urlopen", lambda *_args, **_kwargs: pytest.fail("No real mail may be sent"))
    result = asyncio.run(delivery.send_verification_code(
        "recipient@example.com", "signup", "123456", "template-test", app_dir=str(tmp_path / "app"),
    ))
    assert result.provider == "development"
    message = json.loads((tmp_path / ".onramp/dev-mail-outbox.jsonl").read_text())
    assert message["subject"] == "Custom verification"
    assert message["text"] == "Code: 123456"
    assert message["html"] == "<p>Code: 123456</p>"
    assert message["code"] == "123456"


def test_override_exception_does_not_leak_context_or_send(monkeypatch):
    monkeypatch.setattr(delivery, "auth_config", lambda _: {"app_name": "App", "verification_email_renderer": "app.mail.render"})
    monkeypatch.setattr(delivery, "application_public_url", lambda _: CONTEXT.public_url)
    def broken(_context):
        raise RuntimeError("123456 private-secret recipient@example.com")
    monkeypatch.setattr(delivery, "import_callable", lambda _: broken)
    monkeypatch.setattr(delivery, "send_transactional_email", lambda *_args, **_kwargs: pytest.fail("Bad template must not be sent"))
    with pytest.raises(delivery.EmailDeliveryError) as error:
        asyncio.run(delivery.send_verification_code("recipient@example.com", "signup", "123456", "test"))
    assert str(error.value) == "The configured verification email template failed."


@pytest.mark.parametrize("reference, valid", [("app.mail.render", True), ("not a callable", False)])
def test_email_preflight_checks_renderer_without_importing(monkeypatch, reference, valid):
    from onramp import email_commands
    monkeypatch.setenv("ONRAMP_ENVIRONMENT", "development")
    monkeypatch.setattr(email_commands, "auth_config", lambda _: {"enabled": True, "verification_email_renderer": reference})
    monkeypatch.setattr(email_commands, "application_public_url", lambda _: CONTEXT.public_url)
    monkeypatch.setattr(delivery, "import_callable", lambda _: pytest.fail("Preflight cannot import renderer"))
    report = check_email_configuration("/tmp/project/app")
    assert (not report.errors) == valid
    assert any("not been imported or called" in note for note in report.notes)


@pytest.mark.parametrize("body", ["<p>Ready</p>", "<html><body><p>Ready</p></body></html>", "<HTML><BODY>Ready</BODY></HTML>"])
def test_unsubscribe_is_preserved_inside_complete_email_document(body):
    text, html = _append_unsubscribe("Ready", body, "https://api.example.com/stop?a=1&b=2")
    assert text.endswith("https://api.example.com/stop?a=1&b=2")
    assert 'href="https://api.example.com/stop?a=1&amp;b=2"' in html
    if "</body>" in body.lower():
        assert html.index("Stop notifications") < html.lower().index("</body>")
