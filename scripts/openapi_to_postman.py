import json
import re
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "openapi" / "auth-profiles.openapi.json"
DEFAULT_OUTPUT = ROOT / "postman" / "auth-profiles.postman_collection.json"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _sample_value(schema: dict[str, Any], name: str, components: dict[str, Any]) -> Any:
    reference = schema.get("$ref")
    if reference:
        name_in_schema = reference.rsplit("/", 1)[-1]
        return _sample_value(components.get(name_in_schema, {}), name, components)
    if "default" in schema:
        return schema["default"]
    if "example" in schema:
        return schema["example"]
    if schema.get("enum"):
        return schema["enum"][0]

    value_type = schema.get("type")
    if value_type == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", list(properties))
        return {
            key: _sample_value(properties[key], key, components)
            for key in required
            if key in properties
        }
    if value_type == "array":
        return []
    if value_type in {"integer", "number"}:
        return 1
    if value_type == "boolean":
        return False
    if value_type == "string":
        if name == "email":
            return "{{email}}"
        if "password" in name:
            return "{{password}}"
        if name == "display_name":
            return "{{displayName}}"
        if name == "recovery_code":
            return "{{recoveryCode}}"
        if name in {"family_id", "profile_id"} or schema.get("format") == "uuid":
            return "{{familyId}}" if name == "family_id" else "{{profileId}}"
        if schema.get("format") == "date":
            return "2000-01-01"
        if schema.get("format") == "date-time":
            return "2026-01-01T12:00:00Z"
        return "string"
    return None


def _resolve_schema(schema: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    reference = schema.get("$ref")
    if reference:
        return _resolve_schema(components.get(reference.rsplit("/", 1)[-1], {}), components)
    return schema


def _request_body(operation: dict[str, Any], components: dict[str, Any]) -> dict[str, Any] | None:
    request_body = operation.get("requestBody", {})
    content = request_body.get("content", {})
    json_content = content.get("application/json")
    if json_content:
        value = _sample_value(json_content.get("schema", {}), "", components)
        return {
            "mode": "raw",
            "raw": json.dumps(value, indent=2),
            "options": {"raw": {"language": "json"}},
        }
    multipart_content = content.get("multipart/form-data")
    if multipart_content:
        schema = _resolve_schema(multipart_content.get("schema", {}), components)
        properties = schema.get("properties", {})
        form_data = []
        for name, prop in properties.items():
            if prop.get("format") == "binary":
                form_data.append({"key": name, "type": "file", "src": ""})
            else:
                sample = _sample_value(prop, name, components)
                form_data.append(
                    {"key": name, "type": "text", "value": "" if sample is None else str(sample)}
                )
        return {
            "mode": "formdata",
            "formdata": form_data,
        }
    return None


def _variable_name(name: str) -> str:
    first, *rest = name.split("_")
    return first + "".join(part.capitalize() for part in rest)


def _path_priority(path: str) -> tuple[int, str]:
    priorities = {
        "/api/v1/auth/csrf": 0,
        "/api/v1/auth/login": 1,
        "/api/v1/auth/me": 2,
        "/api/v1/profiles": 3,
        "/api/v1/auth/register": 4,
        "/api/v1/auth/account": 100,
    }
    return priorities.get(path, 10), path


def build_collection(schema: dict[str, Any]) -> dict[str, Any]:
    components = schema.get("components", {}).get("schemas", {})
    folders: dict[str, list[dict[str, Any]]] = {}

    for path, path_item in sorted(
        schema.get("paths", {}).items(),
        key=lambda entry: _path_priority(entry[0]),
    ):
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS:
                continue
            folder = operation.get("tags", ["Other"])[0]
            headers = []
            body_schema = operation.get("requestBody", {}).get("content", {})
            if method.lower() in {"post", "put", "patch"} and "application/json" in body_schema:
                headers.append({"key": "Content-Type", "value": "application/json"})
            if method.lower() in {"post", "put", "patch", "delete"}:
                headers.append({"key": "X-CSRF-Token", "value": "{{csrfToken}}"})

            url_path = re.sub(
                r"\{([^}]+)\}",
                lambda match: "{{" + _variable_name(match.group(1)) + "}}",
                path,
            )
            query_parameters = [
                parameter
                for parameter in operation.get("parameters", [])
                if parameter.get("in") == "query"
            ]
            if query_parameters:
                query = "&".join(
                    f"{parameter['name']}="
                    f"{_sample_value(parameter.get('schema', {}), parameter['name'], components)}"
                    for parameter in query_parameters
                )
                url_path += "?" + query
            item: dict[str, Any] = {
                "name": operation.get("summary", f"{method.upper()} {path}"),
                "request": {
                    "method": method.upper(),
                    "header": headers,
                    "url": "{{baseUrl}}" + url_path,
                },
            }
            body = _request_body(operation, components)
            if body is not None:
                item["request"]["body"] = body
            if path.endswith(("/auth/csrf", "/auth/login", "/auth/register")):
                item["event"] = [
                    {
                        "listen": "test",
                        "script": {
                            "type": "text/javascript",
                            "exec": [
                                "if (pm.response.code < 400) {",
                                "  const body = pm.response.json();",
                                "  if (body.csrf_token) "
                                "pm.collectionVariables.set('csrfToken', body.csrf_token);",
                                "}",
                            ],
                        },
                    }
                ]
            elif path == "/api/v1/profiles" and method.lower() == "get":
                item["event"] = [
                    {
                        "listen": "test",
                        "script": {
                            "type": "text/javascript",
                            "exec": [
                                "if (pm.response.code < 400) {",
                                "  const profile = pm.response.json().find(item => item.user_id);",
                                "  if (profile) "
                                "pm.collectionVariables.set('profileId', profile.id);",
                                "}",
                            ],
                        },
                    }
                ]
            folders.setdefault(folder, []).append(item)

    if not folders:
        raise RuntimeError("The OpenAPI document contains no Auth or Profiles operations")
    collection_id = str(uuid5(NAMESPACE_URL, "fieldnote-care-auth-profiles-postman"))
    return {
        "info": {
            "_postman_id": collection_id,
            "name": "Fieldnote Care Auth and Profiles",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
            "description": (
                "Generated from openapi/auth-profiles.openapi.json. "
                "Postman stores session cookies automatically."
            ),
        },
        "variable": [
            {"key": "baseUrl", "value": "http://127.0.0.1:8000"},
            {"key": "email", "value": "demo.owner@example.com"},
            {"key": "password", "value": "replace-with-a-local-demo-password-of-12-chars"},
            {"key": "displayName", "value": "Demo User"},
            {"key": "csrfToken", "value": ""},
            {"key": "familyId", "value": ""},
            {"key": "profileId", "value": ""},
            {"key": "recoveryCode", "value": ""},
        ],
        "item": [{"name": name, "item": items} for name, items in folders.items()],
    }


def main() -> None:
    if not DEFAULT_INPUT.exists():
        raise FileNotFoundError(
            f"OpenAPI input does not exist: {DEFAULT_INPUT}; run make openapi first"
        )
    schema = json.loads(DEFAULT_INPUT.read_text(encoding="utf-8"))
    collection = build_collection(schema)
    DEFAULT_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUTPUT.write_text(
        json.dumps(collection, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote Postman collection to {DEFAULT_OUTPUT}")


if __name__ == "__main__":
    main()
