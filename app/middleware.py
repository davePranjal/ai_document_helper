import os
import uuid

import structlog
from starlette.types import ASGIApp, Receive, Scope, Send

from slowapi import Limiter
from slowapi.util import get_remote_address

# Disable rate limiting in test mode
_enabled = os.environ.get("TESTING", "").lower() != "true"
limiter = Limiter(key_func=get_remote_address, enabled=_enabled)


class RequestIDMiddleware:
    """Pure ASGI middleware that adds a unique request ID and binds it to structlog context."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http":
            request_id = str(uuid.uuid4())[:8]
            # Store in scope state
            if "state" not in scope:
                scope["state"] = {}
            scope["state"]["request_id"] = request_id

            # Bind request ID to structlog context for all downstream log calls
            structlog.contextvars.clear_contextvars()
            structlog.contextvars.bind_contextvars(
                request_id=request_id,
                path=scope.get("path", ""),
                method=scope.get("method", ""),
            )

            async def send_with_request_id(message):
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append((b"x-request-id", request_id.encode()))
                    message["headers"] = headers
                await send(message)

            await self.app(scope, receive, send_with_request_id)
        else:
            await self.app(scope, receive, send)
