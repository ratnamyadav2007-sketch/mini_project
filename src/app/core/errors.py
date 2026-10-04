import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


def _error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = jsonable_encoder(details)
    return JSONResponse(
        status_code=status_code,
        content={"error": error},
        headers=headers,
    )


def register_exception_handlers(application: FastAPI) -> None:
    @application.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exception: StarletteHTTPException
    ) -> JSONResponse:
        del request
        return _error_response(
            exception.status_code,
            "http_error",
            str(exception.detail),
            headers=exception.headers,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exception: RequestValidationError
    ) -> JSONResponse:
        del request
        details = [
            {
                "location": error["loc"],
                "message": error["msg"],
                "type": error["type"],
            }
            for error in exception.errors()
        ]
        return _error_response(
            422,
            "validation_error",
            "Request validation failed",
            details,
        )

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exception: Exception) -> JSONResponse:
        logger.exception(
            "Unhandled application exception",
            exc_info=(type(exception), exception, exception.__traceback__),
        )
        return _error_response(500, "internal_server_error", "Internal server error")
