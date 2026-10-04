from fastapi.testclient import TestClient

from app.main import create_app


def test_health_endpoint_returns_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_frontend_root_and_static_assets_are_served() -> None:
    client = TestClient(create_app())

    index = client.get("/")
    landing = client.get("/landing.html")
    public_card = client.get("/public-sos.html")

    assert index.status_code == 200
    assert "url=/landing.html" in index.text
    assert landing.status_code == 200
    assert "<title>Fieldnote Care | Care, kept close</title>" in landing.text
    assert landing.headers["x-frame-options"] == "SAMEORIGIN"
    assert "https://fonts.googleapis.com" in landing.headers["content-security-policy"]
    assert public_card.status_code == 200
    assert "public-sos.js" in public_card.text
    assert landing.headers["permissions-policy"].endswith("geolocation=(self)")


def test_frontend_client_routes_fall_back_to_index_but_assets_do_not() -> None:
    client = TestClient(create_app())

    client_route = client.get("/family/care-circle")
    missing_asset = client.get("/missing.js")
    missing_api_route = client.get("/api/v1/missing")
    private_source = client.get("/server.mjs")
    dependency_file = client.get("/node_modules/qrcode/package.json")

    assert client_route.status_code == 200
    assert "url=/landing.html" in client_route.text
    assert missing_asset.status_code == 404
    assert missing_api_route.status_code == 404
    assert missing_api_route.json()["error"]["code"] == "http_error"
    assert private_source.status_code == 404
    assert dependency_file.status_code == 404


def test_cors_allows_only_localhost_origin() -> None:
    client = TestClient(create_app())

    allowed = client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://localhost",
            "Access-Control-Request-Method": "GET",
        },
    )
    denied = client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://127.0.0.1",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert allowed.headers["access-control-allow-origin"] == "http://localhost"
    assert "access-control-allow-origin" not in denied.headers


def test_cors_allows_credentialed_auth_requests_from_localhost() -> None:
    client = TestClient(create_app())

    response = client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "http://localhost",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-csrf-token",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "x-csrf-token" in response.headers["access-control-allow-headers"].lower()


def test_cors_preflight_allows_account_deletion() -> None:
    client = TestClient(create_app())

    response = client.options(
        "/api/v1/auth/account",
        headers={
            "Origin": "http://localhost",
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "x-csrf-token",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost"
    assert "DELETE" in response.headers["access-control-allow-methods"]
