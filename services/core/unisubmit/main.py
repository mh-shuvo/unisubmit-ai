"""The web app: `uvicorn unisubmit.main:create_app --factory`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .auth.router import router as auth_router
from .core.config import get_settings
from .core.db import create_db_engine, make_session_factory
from .core.errors import register_error_handlers
from .core.logging import RequestIdMiddleware, configure_logging
from .core.security import csrf_field
from .core.templating import PACKAGE, templates
from .guidelines.router import router as guidelines_router
from .organisation.router import router as organisation_router
from .portal.router import router as portal_router
from .report_parser import inline_markdown_html
from .submissions.router import router as submissions_router
from .system.router import router as system_router
from .tenancy.router import router as tenancy_router

ROUTERS = (
    portal_router,
    auth_router,
    tenancy_router,
    organisation_router,
    guidelines_router,
    submissions_router,
    system_router,
)
SESSION_HOURS = 8


def create_app() -> FastAPI:
    """Run with `uvicorn unisubmit.main:create_app --factory`.

    The app creates no tables and no accounts at startup: the schema comes from
    `unisubmit migrate` (DEF-039), accounts from `unisubmit create-super-admin`
    (DEF-028). Validation jobs run in `python -m unisubmit.worker` (DEF-024).
    """
    settings = get_settings()
    configure_logging(json=settings.log_json)
    engine = create_db_engine(str(settings.database_url))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = FastAPI(title="UniSubmit AI", version="10.0", lifespan=lifespan)
    app.state.session_factory = make_session_factory(engine)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key.get_secret_value(),
        max_age=SESSION_HOURS * 60 * 60,
        same_site="lax",
        https_only=settings.session_https_only,
    )
    app.add_middleware(RequestIdMiddleware)  # added last, so it runs first
    register_error_handlers(app, templates)
    for router in ROUTERS:
        app.include_router(router)
    app.mount("/static", StaticFiles(directory=str(PACKAGE / "static")), name="static")
    templates.env.globals["render_md"] = inline_markdown_html
    templates.env.globals["csrf_field"] = csrf_field
    templates.env.globals["default_llm_model"] = settings.ollama_model
    return app
