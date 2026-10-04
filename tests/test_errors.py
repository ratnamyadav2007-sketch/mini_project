import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import register_exception_handlers


def _create_error_test_app() -> FastAPI:
    application = FastAPI()
    register_exception_handlers(application)

    @application.get("/items/{item_id}")
    def get_item(item_id: int) -> int:
        return item_id

    @application.get("/failure")
    def fail() -> None:
        raise RuntimeError("sensitive internal detail")

    return application


def test_http_errors_use_consistent_json_format() -> None:
    client = TestClient(_create_error_test_app())

    response = client.get("/not-found")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "http_error",
            "message": "Not Found",
        }
    }


def test_validation_errors_use_consistent_json_format() -> None:
    client = TestClient(_create_error_test_app())

    response = client.get("/items/not-an-integer")

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "validation_error",
        "message": "Request validation failed",
        "details": [
            {
                "location": ["path", "item_id"],
                "message": "Input should be a valid integer, unable to parse string as an integer",
                "type": "int_parsing",
            }
        ],
    }


def test_unhandled_errors_are_logged_without_leaking_details(
    caplog: logging.LogCaptureFixture,
) -> None:
    client = TestClient(_create_error_test_app(), raise_server_exceptions=False)

    with caplog.at_level(logging.ERROR, logger="app.core.errors"):
        response = client.get("/failure")

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_server_error",
            "message": "Internal server error",
        }
    }
    assert "Unhandled application exception" in caplog.text
    assert "sensitive internal detail" not in response.text
