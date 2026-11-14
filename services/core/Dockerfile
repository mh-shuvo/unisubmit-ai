# Build context: repository root (uv.lock covers the whole workspace).
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /bin/uv
# The venv is built where it runs: console scripts (uvicorn, unisubmit) carry
# its absolute path in their shebang.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY services/core/pyproject.toml services/core/
COPY services/ai/pyproject.toml services/ai/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package unisubmit --no-install-workspace
COPY services/core services/core
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package unisubmit --no-editable

FROM python:3.13-slim
# Non-root; /app/storage is created owned by `app`, so a new volume mounted
# there inherits that ownership.
RUN useradd --system --uid 10001 --create-home app \
    && install -d -o app -g app /app/storage
COPY --from=build --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
WORKDIR /app
EXPOSE 8000
CMD ["uvicorn", "unisubmit.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
