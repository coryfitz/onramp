import asyncio

from starlette.requests import Request

from onramp.api import APIError, api_exception_handler, json_body, pagination
from onramp.app import OnRamp


def _route_project(tmp_path):
    app_dir = tmp_path / "app"
    api_dir = app_dir / "api"
    (api_dir / "account").mkdir(parents=True)
    (api_dir / "items").mkdir()
    (api_dir / "users" / "[user_id]").mkdir(parents=True)
    (app_dir / "__init__.py").write_text("")
    (api_dir / "__init__.py").write_text("")
    (api_dir / "account" / "__init__.py").write_text("")
    (api_dir / "items" / "__init__.py").write_text("")
    (api_dir / "users" / "__init__.py").write_text("")
    (api_dir / "users" / "[user_id]" / "__init__.py").write_text("")
    (api_dir / "index.py").write_text("def get():\n    return {}\n")
    (api_dir / "account" / "index.py").write_text(
        "async def get():\n    return {'account': None}\n"
    )
    (api_dir / "items" / "search.py").write_text(
        "def get():\n    return {'items': []}\n"
    )
    (api_dir / "items" / "[item_id].py").write_text(
        "def get(request, params):\n    return params\n"
    )
    (api_dir / "users" / "search.py").write_text(
        "def get():\n    return {'users': []}\n"
    )
    (api_dir / "users" / "[user_id]" / "index.py").write_text(
        "def get(request, params):\n    return params\n"
    )
    return app_dir


def test_nested_file_routes_match_directory_structure(tmp_path):
    onramp = OnRamp(str(_route_project(tmp_path)))
    onramp.discover_file_routes()

    paths = [route.path for route in onramp.routes]
    assert paths == [
        "/api",
        "/api/account",
        "/api/items/search",
        "/api/users/search",
        "/api/items/{item_id}",
        "/api/users/{user_id}",
    ]
    assert {
        operation["path"] for operation in onramp.api_operations
    } == set(paths)


def _request(body=b"", query=b""):
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/example",
            "headers": [(b"content-type", b"application/json")],
            "query_string": query,
        },
        receive,
    )


def test_json_and_pagination_helpers_return_consistent_errors():
    assert asyncio.run(json_body(_request(b'{"name":"OnRamp"}'))) == {
        "name": "OnRamp"
    }
    try:
        asyncio.run(json_body(_request(b"not-json")))
    except APIError as error:
        response = asyncio.run(api_exception_handler(_request(), error))
        assert response.status_code == 400
        assert b"valid JSON" in response.body
    else:
        raise AssertionError("invalid JSON should raise APIError")

    page = pagination(_request(query=b"limit=25&offset=50"))
    assert (page.limit, page.offset) == (25, 50)
