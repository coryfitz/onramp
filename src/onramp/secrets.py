"""Local secret storage and explicit hosting-provider handoff."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import keyring
from keyring import errors as keyring_errors

from .deployment import (
    _render_service_for_target,
    deployment_targets,
    load_deployment_config,
)
from .project import package_version


SECRET_ENVIRONMENTS = {"development", "test", "staging", "production"}
SHARED_SECRET_SCOPE = "shared"
_SECRET_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_RESERVED_NAMES = {
    "BASH_ENV",
    "ENV",
    "HOME",
    "IFS",
    "NODE_OPTIONS",
    "NODE_PATH",
    "OLDPWD",
    "ONRAMP_ENVIRONMENT",
    "ONRAMP_HOST",
    "ONRAMP_PORT",
    "PATH",
    "PORT",
    "PWD",
    "PYTHONHOME",
    "PYTHONPATH",
    "RENDER_API_KEY",
    "SHELL",
    "SSLKEYLOGFILE",
    "TMPDIR",
    "VIRTUAL_ENV",
}
_RESERVED_PREFIXES = ("DYLD_", "LD_", "ONRAMP_RENDER_", "PYTHON")
_INDEX_NAME = "__onramp_secret_names_v1__"
_MAX_SECRET_BYTES = 65_536


class SecretStoreError(RuntimeError):
    """A safe, non-secret-bearing error from secret storage or handoff."""


class SecretStoreUnavailable(SecretStoreError):
    """The operating-system credential store is not available."""


def validate_secret_name(name: str) -> str:
    """Validate a server-side environment variable name."""
    normalized = str(name or "").strip()
    if not _SECRET_NAME.fullmatch(normalized):
        raise SecretStoreError(
            "Secret names must use uppercase letters, numbers, and underscores."
        )
    if normalized in _RESERVED_NAMES or normalized.startswith(_RESERVED_PREFIXES):
        raise SecretStoreError(
            f"{normalized} controls the runtime and cannot be managed as an app secret."
        )
    return normalized


def validate_secret_environment(environment: str) -> str:
    normalized = str(environment or "").strip().lower()
    if normalized not in SECRET_ENVIRONMENTS:
        raise SecretStoreError(
            "Environment must be development, test, staging, or production."
        )
    return normalized


def _secret_scope(environment: str | None) -> str:
    if environment is None:
        return SHARED_SECRET_SCOPE
    return validate_secret_environment(environment)


def validate_secret_value(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SecretStoreError("Secret values cannot be empty.")
    if "\x00" in value:
        raise SecretStoreError("Secret values cannot contain a null byte.")
    if len(value.encode("utf-8")) > _MAX_SECRET_BYTES:
        raise SecretStoreError("Secret values cannot exceed 64 KiB.")
    return value


def _credential_service(project_root: str | Path) -> str:
    root = Path(project_root).resolve()
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:20]
    label = re.sub(r"[^a-z0-9]+", "-", root.name.lower()).strip("-")
    return f"dev.onramp.secrets.v1.{label or 'project'}.{digest}"


class SecretStore:
    """Project- and environment-scoped values in the OS credential store."""

    def __init__(self, project_root: str | Path, *, backend=None):
        self.project_root = Path(project_root).resolve()
        self.service = _credential_service(self.project_root)
        self.backend = backend if backend is not None else keyring

    @staticmethod
    def _account(scope: str, name: str) -> str:
        return f"{scope}:{name}"

    def _call(self, method: str, *args):
        try:
            return getattr(self.backend, method)(*args)
        except keyring_errors.NoKeyringError as error:
            raise SecretStoreUnavailable(
                "No supported operating-system credential store is available."
            ) from error
        except keyring_errors.KeyringLocked as error:
            raise SecretStoreError(
                "The operating-system credential store is locked."
            ) from error
        except keyring_errors.KeyringError as error:
            raise SecretStoreError(
                "The operating-system credential store could not be accessed."
            ) from error
        except (OSError, UnicodeError, ValueError) as error:
            raise SecretStoreError(
                "The operating-system credential store could not be accessed."
            ) from error

    def _read_names(self, environment: str | None) -> list[str]:
        scope = _secret_scope(environment)
        raw = self._call(
            "get_password", self.service, self._account(scope, _INDEX_NAME)
        )
        if raw is None:
            return []
        try:
            names = json.loads(raw)
        except (TypeError, ValueError) as error:
            raise SecretStoreError("The local secret index is damaged.") from error
        if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
            raise SecretStoreError("The local secret index is damaged.")
        try:
            return sorted({validate_secret_name(name) for name in names})
        except SecretStoreError as error:
            raise SecretStoreError("The local secret index is damaged.") from error

    def _write_names(self, environment: str | None, names: list[str]) -> None:
        scope = _secret_scope(environment)
        payload = json.dumps(sorted(set(names)), separators=(",", ":"))
        self._call(
            "set_password",
            self.service,
            self._account(scope, _INDEX_NAME),
            payload,
        )

    def names(self, environment: str | None = None) -> tuple[str, ...]:
        return tuple(self._read_names(environment))

    def effective_names(self, environment: str) -> tuple[str, ...]:
        selected = validate_secret_environment(environment)
        return tuple(sorted({*self._read_names(None), *self._read_names(selected)}))

    def get(self, environment: str | None, name: str) -> str | None:
        scope = _secret_scope(environment)
        name = validate_secret_name(name)
        return self._call(
            "get_password", self.service, self._account(scope, name)
        )

    def resolve(self, environment: str, name: str) -> str | None:
        selected = validate_secret_environment(environment)
        value = self.get(selected, name)
        return value if value is not None else self.get(None, name)

    def set(self, environment: str | None, name: str, value: str) -> None:
        scope = _secret_scope(environment)
        name = validate_secret_name(name)
        value = validate_secret_value(value)
        names = self._read_names(environment)
        previous = self.get(environment, name)
        self._call(
            "set_password", self.service, self._account(scope, name), value
        )
        if name in names:
            return
        try:
            self._write_names(environment, [*names, name])
        except SecretStoreError:
            try:
                if previous is None:
                    self._call(
                        "delete_password",
                        self.service,
                        self._account(scope, name),
                    )
                else:
                    self._call(
                        "set_password",
                        self.service,
                        self._account(scope, name),
                        previous,
                    )
            except SecretStoreError:
                pass
            raise

    def delete(self, environment: str | None, name: str) -> bool:
        scope = _secret_scope(environment)
        name = validate_secret_name(name)
        names = self._read_names(environment)
        previous = self.get(environment, name)
        if previous is None:
            return False
        try:
            self._call(
                "delete_password", self.service, self._account(scope, name)
            )
            self._write_names(environment, [item for item in names if item != name])
        except SecretStoreError:
            try:
                self._call(
                    "set_password",
                    self.service,
                    self._account(scope, name),
                    previous,
                )
            except SecretStoreError:
                pass
            raise
        return True


def environment_with_local_secrets(
    project_root: str | Path,
    environment: str,
    base_environment: Mapping[str, str] | None = None,
    *,
    store: SecretStore | None = None,
) -> dict[str, str]:
    """Add local secrets without overriding explicitly supplied values."""
    selected = validate_secret_environment(environment)
    result = dict(base_environment if base_environment is not None else os.environ)
    secret_store = store or SecretStore(project_root)
    try:
        names = secret_store.effective_names(selected)
    except SecretStoreUnavailable:
        return result
    for name in names:
        if name in result:
            continue
        value = secret_store.resolve(selected, name)
        if value is not None:
            result[name] = value
    return result


@contextmanager
def local_secret_environment(
    project_root: str | Path,
    environment: str,
    *,
    store: SecretStore | None = None,
):
    """Temporarily expose local secrets to backend-only in-process commands."""
    resolved = environment_with_local_secrets(
        project_root, environment, os.environ, store=store
    )
    injected = {
        name: value for name, value in resolved.items() if name not in os.environ
    }
    os.environ.update(injected)
    try:
        yield
    finally:
        for name in injected:
            os.environ.pop(name, None)


def push_render_secret(
    project_root: str | Path,
    environment: str,
    name: str,
    value: str,
    render_api_key: str,
    *,
    opener=urlopen,
) -> str:
    """Set one secret on the project's configured Render backend service."""
    root = Path(project_root).resolve()
    selected = validate_secret_environment(environment)
    name = validate_secret_name(name)
    value = validate_secret_value(value)
    render_api_key = validate_secret_value(render_api_key)
    config = load_deployment_config(root)
    if config is None:
        raise SecretStoreError(
            "No onramp.toml deployment configuration was found."
        )
    if str(config.get("provider", "container")).lower() != "render":
        raise SecretStoreError(
            "Secret push currently supports Render deployments only."
        )
    targets = deployment_targets(root, config)
    backend_targets = [
        (target_name, target)
        for target_name, target in targets.items()
        if "backend" in set(target.get("components", []))
    ]
    if not backend_targets:
        raise SecretStoreError("No backend deployment target is configured.")
    if len(backend_targets) > 1:
        raise SecretStoreError(
            "More than one backend target is configured; choose one in onramp.toml."
        )
    target_name, target = backend_targets[0]
    service = _render_service_for_target(
        config,
        target_name,
        target,
        selected_count=1,
        deployment_environment=selected,
    )
    if not service:
        raise SecretStoreError(
            "Set the Render backend service ID in onramp.toml or the documented "
            "ONRAMP_RENDER_*_SERVICE environment variable."
        )

    endpoint = (
        "https://api.render.com/v1/services/"
        f"{quote(service, safe='')}/env-vars/{quote(name, safe='')}"
    )
    request = Request(
        endpoint,
        data=json.dumps({"value": value}).encode("utf-8"),
        method="PUT",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {render_api_key}",
            "Content-Type": "application/json",
            "User-Agent": f"OnRamp/{package_version()}",
        },
    )
    try:
        with opener(request, timeout=20) as response:
            status = getattr(response, "status", None) or response.getcode()
    except HTTPError as error:
        if error.code in {401, 403}:
            message = "Render rejected the API key or its permissions."
        elif error.code == 404:
            message = "Render could not find the configured backend service."
        elif error.code == 429:
            message = "Render rate-limited the secret update; try again later."
        else:
            message = f"Render rejected the secret update (HTTP {error.code})."
        raise SecretStoreError(message) from error
    except (URLError, TimeoutError, OSError) as error:
        raise SecretStoreError("Could not reach Render to update the secret.") from error
    if not 200 <= int(status) < 300:
        raise SecretStoreError(
            f"Render rejected the secret update (HTTP {status})."
        )
    return service


def copy_secret_to_clipboard(value: str, *, runner=subprocess.run) -> None:
    """Copy a secret on macOS without passing it as an argument or printing it."""
    value = validate_secret_value(value)
    if sys.platform != "darwin":
        raise SecretStoreError(
            "Secret clipboard copy is currently supported on macOS only."
        )
    try:
        result = runner(
            ["/usr/bin/pbcopy"],
            input=value.encode("utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SecretStoreError("Could not copy the secret to the clipboard.") from error
    if result.returncode != 0:
        raise SecretStoreError("Could not copy the secret to the clipboard.")
