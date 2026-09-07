"""Provider acceptance and errors are tested without sending external mail."""

import asyncio
from http.client import IncompleteRead, RemoteDisconnected
import io
import json
from urllib.error import HTTPError, URLError

import pytest

from onramp.auth import email as email_module
from onramp.auth.email import EmailDeliveryError, EmailSendResult, email_sender_address


@pytest.fixture
def delivery_config(monkeypatch):
    config = {"email_from": "Test App <accounts@mailer.test>", "app_name": "Test App"}
    monkeypatch.setattr(email_module, "auth_config", lambda _app_dir=None: config)
    monkeypatch.setenv("ONRAMP_ENVIRONMENT", "production")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_secret_not_for_display")
    monkeypatch.delenv("ONRAMP_EMAIL_FROM", raising=False)

    def unexpected_network(*_args, **_kwargs):
        pytest.fail("Test did not supply a mocked email provider")

    monkeypatch.setattr(email_module, "urlopen", unexpected_network)
    return config


def send():
    return asyncio.run(
        email_module.send_transactional_email(
            "recipient@example.com",
            "Verification test",
            "The body must never be included in error details.",
            idempotency_key="verification/stable-test-id",
        )
    )


@pytest.mark.parametrize("error_type", [RuntimeError, EmailDeliveryError])
def test_custom_sender_failures_do_not_expose_secrets(delivery_config, monkeypatch, error_type):
    delivery_config["email_sender"] = "custom.sender"

    async def broken(_message):
        raise error_type("recipient@example.com provider-secret email-code-123456")

    monkeypatch.setattr(email_module, "import_callable", lambda _reference: broken)
    with pytest.raises(EmailDeliveryError) as error:
        send()
    assert str(error.value) == "The configured email sender failed."


class ProviderResponse:
    def __init__(self, body=b'{"id":"accepted-message-id"}', status=200):
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


@pytest.mark.parametrize(
    "sender",
    [
        "accounts@mailer.test",
        "Test App <accounts@mailer.test>",
        '"Test, Inc." <accounts@mailer.test>',
        "Prévisions <accounts@mailer.test>",
    ],
)
def test_sender_accepts_one_mailbox(delivery_config, monkeypatch, sender):
    monkeypatch.setenv("ONRAMP_EMAIL_FROM", f"  {sender}  ")
    assert email_sender_address(delivery_config) == sender


@pytest.mark.parametrize(
    "sender",
    [
        "not-an-address",
        "accounts@localhost",
        "one@mailer.test,two@mailer.test",
        "Team: one@mailer.test;",
        "Sender <one@mailer.test> trailing garbage",
        "Sender <one@mailer.test>\r\nBcc: private@example.com",
        "Sender\x00 <one@mailer.test>",
        "x" * 999,
    ],
)
def test_sender_rejects_malformed_values_without_echoing_them(
    delivery_config, monkeypatch, sender
):
    delivery_config["email_from"] = sender
    with pytest.raises(EmailDeliveryError) as error:
        send()
    assert "one@mailer.test" not in str(error.value)
    assert "private@example.com" not in str(error.value)
    assert error.value.code == "email_unavailable"


def test_blank_sender_environment_uses_application_setting(delivery_config, monkeypatch):
    monkeypatch.setenv("ONRAMP_EMAIL_FROM", "   ")
    assert email_sender_address(delivery_config) == delivery_config["email_from"]


def test_resend_acceptance_requires_provider_id_and_retains_request_key(
    delivery_config, monkeypatch
):
    requests = []

    def provider(request, timeout):
        requests.append(request)
        assert timeout == 15
        return ProviderResponse()

    monkeypatch.setattr(email_module, "urlopen", provider)
    assert send() == EmailSendResult(provider="resend", message_id="accepted-message-id")
    request = requests[0]
    assert request.headers["Idempotency-key"] == "verification/stable-test-id"
    assert json.loads(request.data)["from"] == delivery_config["email_from"]


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not-json recipient@example.com",
        b"[]",
        b"null",
        b"true",
        b"{}",
        b'{"id":null}',
        b'{"id":42}',
        b'{"id":""}',
        b'{"id":"   "}',
        b'{"id":"message\\nforged"}',
        json.dumps({"id": "x" * 256}).encode(),
        b"\xff",
    ],
)
def test_invalid_success_response_is_not_reported_as_sent(
    delivery_config, monkeypatch, body
):
    monkeypatch.setattr(email_module, "urlopen", lambda *_args, **_kwargs: ProviderResponse(body))
    with pytest.raises(EmailDeliveryError, match="delivery could not be confirmed") as error:
        send()
    assert "recipient@example.com" not in str(error.value)
    assert error.value.status == 503


@pytest.mark.parametrize(
    "status,expected",
    [
        (400, "sender and message configuration"),
        (401, "RESEND_API_KEY"),
        (403, "verified domain"),
        (409, "idempotency key"),
        (422, "sender and message configuration"),
        (429, "quota and rate limit"),
        (503, "temporarily unavailable"),
    ],
)
def test_http_error_uses_actionable_static_diagnostic_not_provider_body(
    delivery_config, monkeypatch, status, expected
):
    private_text = "recipient@example.com re_test_secret_not_for_display body contents"

    def provider(*_args, **_kwargs):
        raise HTTPError(
            email_module.RESEND_ENDPOINT,
            status,
            private_text,
            {},
            io.BytesIO(private_text.encode()),
        )

    monkeypatch.setattr(email_module, "urlopen", provider)
    with pytest.raises(EmailDeliveryError, match=expected) as error:
        send()
    assert f"HTTP {status}" in str(error.value)
    assert "recipient@example.com" not in str(error.value)
    assert "re_test_secret" not in str(error.value)
    assert "body contents" not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize("failure_phase", ["connect", "read"])
@pytest.mark.parametrize(
    "exception",
    [
        TimeoutError("private recipient@example.com"),
        ConnectionResetError("private recipient@example.com"),
        URLError("private recipient@example.com"),
        RemoteDisconnected("private recipient@example.com"),
        IncompleteRead(b"private recipient@example.com"),
    ],
)
def test_transport_failure_is_safe_retryable_api_error(
    delivery_config, monkeypatch, exception, failure_phase
):
    def provider(*_args, **_kwargs):
        if failure_phase == "connect":
            raise exception
        return ProviderResponse(exception)

    monkeypatch.setattr(email_module, "urlopen", provider)
    with pytest.raises(EmailDeliveryError) as error:
        send()
    assert error.value.status == 503
    assert error.value.code == "email_unavailable"
    assert "private" not in str(error.value)
    assert "recipient@example.com" not in str(error.value)


@pytest.mark.parametrize("api_key", ["", "bad\nkey", "bad key", "clé"])
def test_bad_api_key_fails_before_network_without_echoing_value(
    delivery_config, monkeypatch, api_key
):
    monkeypatch.setenv("RESEND_API_KEY", api_key)
    with pytest.raises(EmailDeliveryError, match="RESEND_API_KEY"):
        send()


def test_custom_sender_is_not_changed_by_resend_requirements(delivery_config, monkeypatch):
    delivered = []
    delivery_config["email_sender"] = "example.custom_sender"
    delivery_config["email_from"] = "custom configuration"
    monkeypatch.delenv("RESEND_API_KEY")

    def custom_sender(message):
        delivered.append(message)
        return {"provider": "custom-test", "id": "accepted"}

    monkeypatch.setattr(email_module, "import_callable", lambda _reference: custom_sender)
    assert send() == EmailSendResult(provider="custom-test", message_id="accepted")
    assert delivered[0]["idempotency_key"] == "verification/stable-test-id"


def test_development_remains_local_without_resend_configuration(
    delivery_config, monkeypatch
):
    monkeypatch.setenv("ONRAMP_ENVIRONMENT", "development")
    monkeypatch.delenv("RESEND_API_KEY")
    delivery_config["email_from"] = "not-a-real-sender"
    messages = []

    def outbox(message, _app_dir):
        messages.append(message)
        return EmailSendResult(provider="development", message_id="local")

    monkeypatch.setattr(email_module, "_write_development_message", outbox)
    assert send().provider == "development"
    assert len(messages) == 1
