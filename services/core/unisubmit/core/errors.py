"""Expected failures and the pages that report them (P4.6).

A `DomainError`'s message is written for the user and shown on the error page.
Anything else is logged with its traceback and the user sees only a reference
to quote: the request ID.
"""

from http import HTTPStatus
from typing import ClassVar

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException

logger = structlog.get_logger("unisubmit.errors")

STATUS_TITLES = {
    400: "Request not accepted",
    401: "Login required",
    403: "Not allowed",
    404: "Not found",
    405: "Not allowed",
    409: "Please wait",
}


class DomainError(Exception):
    """An expected failure whose message is safe to show to the user."""

    status_code: ClassVar[int] = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidInput(DomainError):
    pass


class PermissionDenied(DomainError):
    status_code = 403


class NotFound(DomainError):
    status_code = 404


class Conflict(DomainError):
    status_code = 409


def _login_page(path: str) -> str | None:
    if path.startswith("/admin") or path.startswith("/platform"):
        return "/admin/login"
    if path.startswith(("/student", "/submit", "/submission", "/files")):
        return "/student/login"
    return None


def register_error_handlers(app: FastAPI, templates: Jinja2Templates) -> None:
    def page(request: Request, status: int, message: str) -> Response:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": message}, status_code=status)
        title = STATUS_TITLES.get(status, "Something went wrong")
        return templates.TemplateResponse(
            request,
            "error.html",
            {"request": request, "title": title, "message": message, "status": status},
            status_code=status,
        )

    async def domain_error(request: Request, exc: Exception) -> Response:
        if not isinstance(exc, DomainError):
            raise exc
        if exc.status_code == HTTPStatus.FORBIDDEN:
            logger.warning("security.permission_denied", path=request.url.path)
        return page(request, exc.status_code, exc.message)

    async def http_error(request: Request, exc: Exception) -> Response:
        if not isinstance(exc, HTTPException):
            raise exc
        if exc.status_code == HTTPStatus.UNAUTHORIZED and (login := _login_page(request.url.path)):
            return RedirectResponse(login, 303)
        if exc.status_code == HTTPStatus.FORBIDDEN:
            logger.warning("security.permission_denied", path=request.url.path)
        detail = exc.detail if isinstance(exc.detail, str) else ""
        return page(request, exc.status_code, detail)

    async def unexpected(request: Request, exc: Exception) -> Response:
        logger.error("request.unhandled_error", exc_info=exc, path=request.url.path)
        reference = getattr(request.state, "request_id", "-")
        return page(request, 500, f"Quote reference {reference} when reporting this.")

    app.add_exception_handler(DomainError, domain_error)
    app.add_exception_handler(HTTPException, http_error)
    app.add_exception_handler(Exception, unexpected)
