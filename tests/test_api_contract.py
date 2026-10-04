import re
from pathlib import Path

from app.main import app
from scripts.export_openapi import build_contract
from scripts.openapi_to_postman import build_collection

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_API_FILES = (
    "auth.js",
    "health-pages.js",
    "emergency-pages.js",
    "wellness-api-pages.js",
    "insights-chat-pages.js",
)
API_CALL = re.compile(
    r"\b(apiGet|apiPost|apiPatch|apiDelete|apiUpload|apiRequest|request)\s*"
    r"\(\s*([`'\"])(/[^`'\"]*?)\2",
)
AUTH_ROUTE_MAP = re.compile(r"const routes\s*=\s*\{([^}]+)\}", re.DOTALL)
AUTH_ROUTE = re.compile(r"""['"]((?:/api/v1)?/auth/[^'"]*)['"]""")
KNOWN_DYNAMIC_PATHS = {
    "/family-invites/{parameter}": (
        "/family-invites/accept",
        "/family-invites/decline",
    ),
}
DIRECT_METHODS = {
    "apiGet": "get",
    "apiPost": "post",
    "apiPatch": "patch",
    "apiDelete": "delete",
    "apiUpload": "post",
}


def _normalize_frontend_path(path: str) -> str:
    path = path.split("?", 1)[0]
    path = re.sub(r"\$\{[^}]+\}", "{parameter}", path)
    path = re.sub(r"^/api/v1(?=\/|$)", "", path)
    path = re.sub(r"^/api(?=\/|$)", "", path)
    return path or "/"


def _openapi_match(path: str, method: str | None) -> bool:
    for openapi_path, operations in app.openapi()["paths"].items():
        normalized = _normalize_frontend_path(openapi_path)
        regex = "/".join(
            "[^/]+" if segment.startswith("{") and segment.endswith("}") else re.escape(segment)
            for segment in normalized.split("/")
        )
        if re.fullmatch(regex, path) and (method is None or method in operations):
            return True
    return False


def test_auth_profiles_contract_contains_only_the_requested_api_surfaces() -> None:
    contract = build_contract()

    assert contract["paths"]
    assert all(path.startswith(("/api/v1/auth", "/api/v1/profiles")) for path in contract["paths"])
    assert "/api/v1/auth/csrf" in contract["paths"]
    assert "/api/v1/profiles" in contract["paths"]
    assert contract["components"] == app.openapi()["components"]
    assert "/api/v1/health" in app.openapi()["paths"]


def test_postman_collection_is_derived_from_contract_and_captures_csrf() -> None:
    collection = build_collection(build_contract())
    auth_folder = next(folder for folder in collection["item"] if folder["name"] == "auth")
    csrf_request = next(
        item for item in auth_folder["item"] if item["request"]["url"].endswith("/auth/csrf")
    )

    assert collection["info"]["schema"].endswith("collection/v2.1.0/collection.json")
    assert any(event["listen"] == "test" for event in csrf_request["event"])
    assert any(
        header["value"] == "{{csrfToken}}"
        for item in auth_folder["item"]
        if item["request"]["method"] == "POST"
        for header in item["request"]["header"]
    )
    assert auth_folder["item"][0]["name"] == "Issue Csrf Challenge"

    requests = [item for folder in collection["item"] for item in folder["item"]]
    upload = next(item for item in requests if "/attachments" in item["request"]["url"])
    assert upload["request"]["body"]["mode"] == "formdata"
    assert any("?" in item["request"]["url"] for item in requests)


def test_frontend_api_routes_match_generated_openapi() -> None:
    failures: list[str] = []
    for relative_path in FRONTEND_API_FILES:
        source = (ROOT / "frontend" / relative_path).read_text(encoding="utf-8")
        for match in API_CALL.finditer(source):
            function, _, raw_path = match.groups()
            path = _normalize_frontend_path(raw_path)
            method = DIRECT_METHODS.get(function)
            candidates = KNOWN_DYNAMIC_PATHS.get(path, (path,))
            if not all(_openapi_match(candidate, method) for candidate in candidates):
                failures.append(f"{relative_path}: {method or 'API'} {path}")

    auth_source = (ROOT / "frontend" / "auth.js").read_text(encoding="utf-8")
    route_map = AUTH_ROUTE_MAP.search(auth_source)
    assert route_map is not None
    for raw_route in AUTH_ROUTE.findall(route_map.group(1)):
        path = _normalize_frontend_path(raw_route)
        if not _openapi_match(path, None):
            failures.append(f"auth.js: API {path}")

    assert not failures, "Frontend API routes absent from OpenAPI:\n" + "\n".join(failures)
