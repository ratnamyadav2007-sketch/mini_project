from starlette.types import ASGIApp, Message, Receive, Scope, Send


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {key.lower() for key, _ in headers}
                path = scope.get("path", "")
                is_frontend = not (
                    path.startswith("/api/") or path in {"/openapi.json", "/docs", "/redoc"}
                )
                if path in {"/docs", "/redoc"}:
                    policy = (
                        "default-src 'none'; script-src 'self' 'unsafe-inline' "
                        "https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' "
                        "https://cdn.jsdelivr.net; img-src 'self' data:; connect-src 'self'; "
                        "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
                    )
                elif is_frontend:
                    policy = (
                        "default-src 'self'; script-src 'self'; "
                        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                        "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; "
                        "connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; "
                        "base-uri 'self'; form-action 'self'"
                    )
                else:
                    policy = (
                        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
                        "form-action 'none'"
                    )
                additions = {
                    b"x-content-type-options": b"nosniff",
                    b"x-frame-options": b"SAMEORIGIN" if is_frontend else b"DENY",
                    b"referrer-policy": b"no-referrer",
                    b"permissions-policy": b"camera=(), microphone=(), geolocation=(self)",
                    b"content-security-policy": policy.encode("ascii"),
                }
                if scope.get("scheme") == "https":
                    additions[b"strict-transport-security"] = b"max-age=31536000"
                for key, value in additions.items():
                    if key not in existing:
                        headers.append((key, value))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
