import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from onramp.secrets import (
    SecretStore,
    SecretStoreError,
    copy_secret_to_clipboard,
    environment_with_local_secrets,
    local_secret_environment,
    push_render_secret,
)


class MemoryKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[(service, account)] = value

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


class ProviderResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status


def test_secret_store_scopes_values_by_project_and_environment(tmp_path):
    backend = MemoryKeyring()
    first = SecretStore(tmp_path / "one", backend=backend)
    second = SecretStore(tmp_path / "two", backend=backend)

    first.set(None, "RESEND_API_KEY", "shared-secret")
    first.set("production", "RESEND_API_KEY", "production-secret")

    assert first.names() == ("RESEND_API_KEY",)
    assert first.resolve("development", "RESEND_API_KEY") == "shared-secret"
    assert first.get("production", "RESEND_API_KEY") == "production-secret"
    assert first.resolve("production", "RESEND_API_KEY") == "production-secret"
    assert second.get("development", "RESEND_API_KEY") is None
    assert second.get(None, "RESEND_API_KEY") is None


def test_secret_store_deletes_values_without_returning_them(tmp_path):
    store = SecretStore(tmp_path, backend=MemoryKeyring())
    store.set("staging", "RESEND_API_KEY", "hidden")

    assert store.delete("staging", "RESEND_API_KEY") is True
    assert store.delete("staging", "RESEND_API_KEY") is False
    assert store.names("staging") == ()


@pytest.mark.parametrize(
    "name",
    [
        "resend_api_key",
        "RESEND-API-KEY",
        "PATH",
        "PYTHONPATH",
        "LD_PRELOAD",
        "RENDER_API_KEY",
        "ONRAMP_RENDER_BACKEND_SERVICE",
    ],
)
def test_secret_store_rejects_unsafe_names(tmp_path, name):
    store = SecretStore(tmp_path, backend=MemoryKeyring())

    with pytest.raises(SecretStoreError):
        store.set("development", name, "hidden")


def test_local_secret_environment_does_not_override_explicit_values(tmp_path):
    store = SecretStore(tmp_path, backend=MemoryKeyring())
    store.set(None, "RESEND_API_KEY", "stored")
    store.set(None, "DATABASE_URL", "stored-database")

    resolved = environment_with_local_secrets(
        tmp_path,
        "development",
        {"RESEND_API_KEY": "explicit"},
        store=store,
    )

    assert resolved["RESEND_API_KEY"] == "explicit"
    assert resolved["DATABASE_URL"] == "stored-database"


def test_local_secret_context_removes_only_injected_values(tmp_path, monkeypatch):
    store = SecretStore(tmp_path, backend=MemoryKeyring())
    store.set(None, "RESEND_API_KEY", "stored")
    monkeypatch.setenv("DATABASE_URL", "explicit")

    with local_secret_environment(tmp_path, "development", store=store):
        assert os.environ["RESEND_API_KEY"] == "stored"
        assert os.environ["DATABASE_URL"] == "explicit"

    assert "RESEND_API_KEY" not in os.environ
    assert os.environ["DATABASE_URL"] == "explicit"


def test_environment_override_wins_over_shared_secret(tmp_path):
    store = SecretStore(tmp_path, backend=MemoryKeyring())
    store.set(None, "RESEND_API_KEY", "shared")
    store.set("staging", "RESEND_API_KEY", "staging")

    development = environment_with_local_secrets(
        tmp_path, "development", {}, store=store
    )
    staging = environment_with_local_secrets(tmp_path, "staging", {}, store=store)

    assert development["RESEND_API_KEY"] == "shared"
    assert staging["RESEND_API_KEY"] == "staging"


def test_copy_secret_to_clipboard_uses_stdin_without_leaking_output(monkeypatch):
    monkeypatch.setattr("onramp.secrets.sys.platform", "darwin")
    captured = {}

    def runner(command, **kwargs):
        captured.update(command=command, **kwargs)
        return SimpleNamespace(returncode=0)

    copy_secret_to_clipboard("resend-secret", runner=runner)

    assert captured["command"] == ["/usr/bin/pbcopy"]
    assert captured["input"] == b"resend-secret"
    assert captured["stdout"] == subprocess.DEVNULL
    assert captured["stderr"] == subprocess.DEVNULL
    assert captured["timeout"] == 5
    assert captured["check"] is False


def test_copy_secret_to_clipboard_rejects_unsupported_platform(monkeypatch):
    monkeypatch.setattr("onramp.secrets.sys.platform", "linux")

    with pytest.raises(SecretStoreError, match="macOS only"):
        copy_secret_to_clipboard("resend-secret", runner=lambda *_args, **_kwargs: None)


def test_copy_secret_to_clipboard_hides_provider_error(monkeypatch):
    monkeypatch.setattr("onramp.secrets.sys.platform", "darwin")

    def runner(_command, **_kwargs):
        raise OSError("resend-secret")

    with pytest.raises(SecretStoreError, match="Could not copy") as error:
        copy_secret_to_clipboard("resend-secret", runner=runner)

    assert "resend-secret" not in str(error.value)


def test_copy_secret_to_clipboard_hides_failed_command_output(monkeypatch):
    monkeypatch.setattr("onramp.secrets.sys.platform", "darwin")

    with pytest.raises(SecretStoreError, match="Could not copy") as error:
        copy_secret_to_clipboard(
            "resend-secret",
            runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
        )

    assert "resend-secret" not in str(error.value)


def _write_render_config(root: Path):
    (root / "onramp.toml").write_text(
        """[deploy]
provider = "render"
environment = "production"

[deploy.targets.backend]
kind = "container"
components = ["backend"]
render_service = "srv-backend123"
""",
        encoding="utf-8",
    )


def test_push_render_secret_updates_only_the_named_backend_value(tmp_path):
    _write_render_config(tmp_path)
    captured = {}

    def opener(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return ProviderResponse()

    service = push_render_secret(
        tmp_path,
        "production",
        "RESEND_API_KEY",
        "resend-secret",
        "render-token",
        opener=opener,
    )

    request = captured["request"]
    assert service == "srv-backend123"
    assert request.full_url.endswith(
        "/services/srv-backend123/env-vars/RESEND_API_KEY"
    )
    assert request.method == "PUT"
    assert json.loads(request.data) == {"value": "resend-secret"}
    assert request.get_header("Authorization") == "Bearer render-token"
    assert captured["timeout"] == 20


def test_push_render_secret_does_not_expose_provider_response(tmp_path):
    _write_render_config(tmp_path)

    def opener(_request, timeout):
        assert timeout == 20
        raise HTTPError(
            "https://api.render.com/redacted",
            403,
            "forbidden: resend-secret render-token",
            {},
            None,
        )

    with pytest.raises(
        SecretStoreError, match="rejected the API key or its permissions"
    ) as error:
        push_render_secret(
            tmp_path,
            "production",
            "RESEND_API_KEY",
            "resend-secret",
            "render-token",
            opener=opener,
        )

    assert "resend-secret" not in str(error.value)
    assert "render-token" not in str(error.value)
