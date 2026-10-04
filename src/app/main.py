import re
from pathlib import Path, PurePosixPath

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.security_headers import SecurityHeadersMiddleware

FRONTEND_SOURCE_DIR = Path(__file__).resolve().parents[2] / "frontend"
FRONTEND_BUILD_DIR = FRONTEND_SOURCE_DIR / "dist"
FRONTEND_DIR = FRONTEND_BUILD_DIR if FRONTEND_BUILD_DIR.is_dir() else FRONTEND_SOURCE_DIR
HASHED_ASSET_NAME = re.compile(r"\.[a-f0-9]{8,}\.(?:css|js|mjs|woff2?|svg)$", re.IGNORECASE)


class FrontendStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope):
        request_path = scope.get("path", "")
        parts = (*PurePosixPath(path).parts, *PurePosixPath(request_path.lstrip("/")).parts)
        if any(part.startswith(".") or part == "node_modules" for part in parts) or any(
            part in {"server.mjs", "package.json", "package-lock.json"} for part in parts
        ):
            raise StarletteHTTPException(status_code=404, detail="Not Found")
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exception:
            if (
                exception.status_code != 404
                or PurePosixPath(path).suffix
                or request_path == "/api"
                or request_path.startswith("/api/")
            ):
                raise
            return await super().get_response("index.html", scope)
        if HASHED_ASSET_NAME.search(PurePosixPath(path).name):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif PurePosixPath(path).suffix.lower() in {".css", ".js", ".mjs", ".html"}:
            response.headers["Cache-Control"] = "no-cache"
        return response


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title=settings.app_name)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )
    application.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)
    application.add_middleware(SecurityHeadersMiddleware)
    register_exception_handlers(application)
    application.include_router(api_router, prefix="/api/v1")
    application.mount(
        "/",
        FrontendStaticFiles(directory=FRONTEND_DIR, html=True),
        name="frontend",
    )
    return application


app = create_app()
