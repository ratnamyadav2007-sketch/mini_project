import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.main import app

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "openapi" / "auth-profiles.openapi.json"
API_PREFIXES = ("/api/v1/auth", "/api/v1/profiles")


def build_contract() -> dict[str, Any]:
    schema = deepcopy(app.openapi())
    paths = {
        path: operations
        for path, operations in schema["paths"].items()
        if path.startswith(API_PREFIXES)
    }
    if not paths:
        raise RuntimeError("No Auth or Profiles paths were found in the application OpenAPI schema")

    schema["info"]["title"] = "Fieldnote Care Auth and Profiles API"
    schema["info"]["description"] = (
        "Generated from the FastAPI application. See API_CONVENTIONS.md for shared "
        "request, response, cookie, and CSRF conventions."
    )
    schema["paths"] = paths
    used_tags = {
        tag
        for operations in paths.values()
        for operation in operations.values()
        if isinstance(operation, dict)
        for tag in operation.get("tags", [])
    }
    if "tags" in schema:
        schema["tags"] = [tag for tag in schema["tags"] if tag["name"] in used_tags]
    return schema


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the Auth and Profiles OpenAPI contract")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(build_contract(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote OpenAPI contract to {arguments.output}")


if __name__ == "__main__":
    main()
