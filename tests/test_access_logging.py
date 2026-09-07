"""Real HTTP access logs must never retain a query-string capability."""

import asyncio
import io
import json
import logging
import socket
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from uvicorn import Config, Server
from uvicorn.logging import AccessFormatter

from onramp.logging_filters import (
    AccessQueryRedactionFilter,
    MAX_LOGGED_REQUEST_PATH,
    install_access_log_redaction,
)


def access_record(target, status=200):
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:12345", "GET", target, "1.1", status), None,
    )


def access_formatter():
    return AccessFormatter(
        '%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False,
    )


@pytest.mark.parametrize("status", [200, 400, 404, 500])
def test_redaction_preserves_path_status_and_uvicorn_format(status):
    record = access_record(
        "/api/notifications/unsubscribe?token=private-proof&email=private%40example.com",
        status,
    )
    assert AccessQueryRedactionFilter().filter(record)
    for rendered in (record.getMessage(), access_formatter().format(record)):
        assert "private" not in rendered
        assert "token=" not in rendered
        assert "email=" not in rendered
        assert "/api/notifications/unsubscribe?[redacted] HTTP/1.1" in rendered
        assert str(status) in rendered


def test_redaction_bounds_oversized_targets_and_leaves_normal_paths_intact():
    redactor = AccessQueryRedactionFilter()
    normal = access_record("/health/ready")
    redactor.filter(normal)
    assert normal.args[2] == "/health/ready"
    for target in (
        "/api?token=" + "s" * 1_000_000,
        "/" + "p" * 10_000 + "?token=private-proof",
    ):
        oversized = access_record(target)
        redactor.filter(oversized)
        assert len(oversized.args[2]) <= MAX_LOGGED_REQUEST_PATH + 12
        assert "private-proof" not in oversized.getMessage()
        assert "token=" not in oversized.getMessage()


def test_repeated_factory_setup_installs_only_one_filter(tmp_path, monkeypatch):
    from onramp.app import OnRamp
    from onramp.db import manager as manager_module

    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "settings.py").write_text("AUTH = {'enabled': False}\n")
    monkeypatch.setattr(manager_module, "_db_manager", None)
    logger = logging.getLogger("uvicorn.access")
    saved_filters = logger.filters[:]
    try:
        logger.filters = []
        for _ in range(3):
            OnRamp(str(app_dir)).create_app()
            install_access_log_redaction()
        assert len(logger.filters) == 1
        assert isinstance(logger.filters[0], AccessQueryRedactionFilter)
    finally:
        logger.filters = saved_filters


def test_actual_uvicorn_response_keeps_query_but_access_log_redacts_it():
    async def echo(request):
        # Prove that redaction does not change the URL delivered to the app.
        return JSONResponse({"token": request.query_params["token"]})

    async def scenario():
        output = io.StringIO()
        logger = logging.getLogger("uvicorn.access")
        saved = (logger.handlers[:], logger.filters[:], logger.level, logger.propagate)
        handler = logging.StreamHandler(output)
        handler.setFormatter(access_formatter())
        logger.handlers = [handler]
        logger.filters = []
        logger.setLevel(logging.INFO)
        logger.propagate = False
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        config = Config(
            Starlette(routes=[Route("/inspect", echo)]),
            log_config=None, lifespan="off", access_log=True, http="h11",
        )
        install_access_log_redaction()
        server = Server(config)
        running = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            for _ in range(500):
                if server.started:
                    break
                await asyncio.sleep(0.01)
            assert server.started

            def requests():
                with urlopen(
                    f"http://127.0.0.1:{port}/inspect?token=private-signed-proof", timeout=3,
                ) as response:
                    assert response.status == 200
                    assert json.load(response) == {"token": "private-signed-proof"}
                with pytest.raises(HTTPError) as error:
                    urlopen(
                        f"http://127.0.0.1:{port}/missing?token=private-missing-proof", timeout=3,
                    )
                assert error.value.code == 404
                error.value.close()

            await asyncio.to_thread(requests)
            logs = output.getvalue()
            assert "private-signed-proof" not in logs
            assert "private-missing-proof" not in logs
            assert "/inspect?[redacted] HTTP/1.1\" 200 OK" in logs
            assert "/missing?[redacted] HTTP/1.1\" 404 Not Found" in logs
        finally:
            server.should_exit = True
            await asyncio.wait_for(running, timeout=5)
            sock.close()
            logger.handlers, logger.filters, logger.level, logger.propagate = saved

    asyncio.run(scenario())
