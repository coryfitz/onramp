from starlette.testclient import TestClient

from onramp.app import OnRamp


def test_api_root(tmp_path):
    # Construct against the generated application directory explicitly so the
    # test remains reliable from IDEs and repository tools with another cwd.
    app = OnRamp("app").create_app()
    with TestClient(app) as client:
        response = client.get("/api", headers={"Accept": "application/json"})
    assert response.status_code == 200
