"""Structured logs with a request ID on every line (P4.7).

`configure_logging` routes the standard library's loggers through structlog,
so `logging.getLogger(...)` and `structlog.get_logger(...)` lines both carry
the context bound with `structlog.contextvars`: `request_id` in the web app,
`job_id` and the originating `request_id` in the worker.

Never log passwords, cookies, document text or prompts; log IDs and sizes.
"""

import logging
import re
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "x-request-id"
# Only IDs that are safe to copy into logs and headers are accepted from a client.
_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_HANDLER_NAME = "unisubmit"


def configure_logging(*, json: bool) -> None:
    """Idempotent; leaves handlers it did not add (pytest's, uvicorn's) alone."""
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    renderer = (
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer(colors=False)
    )
    handler = logging.StreamHandler()
    handler.name = _HANDLER_NAME
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                renderer,
            ],
        )
    )
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if h.name != _HANDLER_NAME] + [handler]
    root.setLevel(logging.INFO)


def current_request_id() -> str | None:
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    return request_id if isinstance(request_id, str) else None


class RequestIdMiddleware:
    """Gives every request an ID: the caller's `X-Request-ID` if it is safe,
    otherwise a new one. It is bound for logging, kept on `request.state`
    for the error page, and returned in the response with `nosniff`."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        supplied = dict(scope["headers"]).get(REQUEST_ID_HEADER.encode(), b"").decode("latin-1")
        request_id = supplied if _VALID_ID.match(supplied) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        # Not unbound on exit: the 500 handler runs after this returns, and
        # each request runs in its own context anyway.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers += [
                    (REQUEST_ID_HEADER.encode(), request_id.encode()),
                    (b"x-content-type-options", b"nosniff"),
                ]
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
