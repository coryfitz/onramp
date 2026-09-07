"""Shared abuse counters must survive workers, restarts, and hostile headers."""

import asyncio
from datetime import datetime, timedelta, timezone
import multiprocessing
import os
from types import SimpleNamespace
import uuid

import pytest
from tortoise import Tortoise

from onramp.auth import service
from onramp.auth.models import ClientRequestRateLimit
from onramp.auth.security import client_request_digest


NOW = datetime(2026, 9, 7, tzinfo=timezone.utc)


async def database_scenario(database_url, scenario):
    await Tortoise.init(
        db_url=database_url,
        modules={"models": ["onramp.auth.models"]},
    )
    try:
        await Tortoise.generate_schemas()
        return await scenario()
    finally:
        await Tortoise.close_connections()


def configure(monkeypatch, limit=3):
    monkeypatch.setenv("ONRAMP_ENVIRONMENT", "test")
    monkeypatch.setenv("ONRAMP_AUTH_SECRET", "rate-limit-test-secret" * 3)
    monkeypatch.setattr(service, "auth_config", lambda _app=None: {"limit": limit})
    monkeypatch.setattr(service, "utcnow", lambda: NOW)


async def claim(host="192.0.2.40", scope="notification"):
    await service.enforce_client_request_limit(
        SimpleNamespace(client_host=host), scope=scope, config_key="limit"
    )


def _independent_worker(database_url, barrier, output, scope):
    """A separate interpreter and ORM connection, not a mocked shared worker."""
    service.auth_config = lambda _app=None: {"limit": 7}
    service.utcnow = lambda: NOW

    async def scenario():
        await Tortoise.init(
            db_url=database_url,
            modules={"models": ["onramp.auth.models"]},
        )
        try:
            await Tortoise.get_connection("default").execute_query("SELECT 1")
            barrier.wait(timeout=20)

            async def attempt():
                try:
                    await claim(scope=scope)
                    return "allowed"
                except service.AuthenticationError as error:
                    assert error.status == 429
                    return "limited"

            return await asyncio.gather(*(attempt() for _ in range(8)))
        finally:
            await Tortoise.close_connections()

    try:
        output.put(asyncio.run(scenario()))
    except Exception as error:
        output.put([f"unexpected:{type(error).__name__}:{error}"])


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_simultaneous_workers_and_restart_share_exact_limit(tmp_path, monkeypatch, backend):
    configure(monkeypatch, limit=7)
    if backend == "postgresql":
        database_url = os.environ.get("ONRAMP_TEST_POSTGRES_URL")
        if not database_url:
            pytest.skip("ONRAMP_TEST_POSTGRES_URL is not configured")
    else:
        database_url = f"sqlite://{tmp_path / 'shared.sqlite3'}"
    scope = f"notification-test-{uuid.uuid4()}"

    async def empty():
        pass

    asyncio.run(database_scenario(database_url, empty))
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(3)
    output = context.Queue()
    workers = [
        context.Process(target=_independent_worker, args=(database_url, barrier, output, scope))
        for _ in range(3)
    ]
    try:
        for worker in workers:
            worker.start()
        results = [item for _ in workers for item in output.get(timeout=30)]
        assert results.count("allowed") == 7, results
        assert results.count("limited") == 17, results
        assert len(results) == 24, results
    finally:
        for worker in workers:
            worker.join(timeout=5)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)
        output.close()

    async def after_restart():
        with pytest.raises(service.AuthenticationError, match="Too many requests"):
            await claim(scope=scope)
        matching = ClientRequestRateLimit.filter(
            scope_key=client_request_digest(scope, "192.0.2.40")
        )
        assert await matching.count() == 1
        row = await matching.first()
        assert row.count == 7
        assert row.expires_at == NOW + timedelta(hours=1)
        assert "192.0.2.40" not in row.scope_key
        assert len(row.scope_key) == 64

    asyncio.run(database_scenario(database_url, after_restart))


def test_environment_scope_address_and_unknown_peer_are_isolated(monkeypatch):
    configure(monkeypatch, limit=1)

    async def scenario():
        await claim()
        with pytest.raises(service.AuthenticationError):
            await claim("::ffff:192.0.2.40")
        await claim("192.0.2.41")
        await claim(scope="auth_request")
        monkeypatch.setenv("ONRAMP_ENVIRONMENT", "staging")
        await claim()
        await claim(host=None)
        with pytest.raises(service.AuthenticationError):
            await claim(host=None)
        assert await ClientRequestRateLimit.all().count() == 5

    asyncio.run(database_scenario("sqlite://:memory:", scenario))


def test_hmac_cannot_be_precomputed_or_linked_between_environments(monkeypatch):
    configure(monkeypatch)
    key = client_request_digest("notification", "192.0.2.40")
    monkeypatch.setenv("ONRAMP_AUTH_SECRET", "different-secret" * 3)
    assert client_request_digest("notification", "192.0.2.40") != key
    monkeypatch.setenv("ONRAMP_ENVIRONMENT", "staging")
    assert client_request_digest("notification", "192.0.2.40") != key


def test_expiry_resets_limit_without_extending_it_on_reuse(monkeypatch):
    configure(monkeypatch, limit=2)

    async def scenario():
        await claim()
        monkeypatch.setattr(service, "utcnow", lambda: NOW + timedelta(minutes=30))
        await claim()
        row = await ClientRequestRateLimit.all().first()
        assert row.expires_at == NOW + timedelta(hours=1)
        monkeypatch.setattr(service, "utcnow", lambda: NOW + timedelta(hours=1))
        await claim()
        row = await ClientRequestRateLimit.all().first()
        assert row.count == 1
        assert row.expires_at == NOW + timedelta(hours=2)

    asyncio.run(database_scenario("sqlite://:memory:", scenario))


def test_cleanup_is_bounded_and_preserves_live_buckets(monkeypatch):
    configure(monkeypatch)

    async def scenario():
        await ClientRequestRateLimit.bulk_create([
            ClientRequestRateLimit(
                scope_key=f"{number:064d}", count=1,
                expires_at=NOW - timedelta(seconds=1),
            )
            for number in range(150)
        ])
        await claim()
        assert await ClientRequestRateLimit.all().count() == 51
        assert await service.cleanup_client_request_limits(maximum=20) == 20
        assert await ClientRequestRateLimit.all().count() == 31
        assert await ClientRequestRateLimit.filter(expires_at__gt=NOW).count() == 1
        with pytest.raises(ValueError):
            await service.cleanup_client_request_limits(maximum=0)
        with pytest.raises(ValueError):
            await service.cleanup_client_request_limits(maximum=100_000)

    asyncio.run(database_scenario("sqlite://:memory:", scenario))
