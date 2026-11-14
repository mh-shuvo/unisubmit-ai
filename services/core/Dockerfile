# Build context: repository root (uv.lock covers the whole workspace).
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
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
COPY --from=build /src/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /app
EXPOSE 8000
CMD ["uvicorn", "unisubmit.main:app", "--host", "0.0.0.0", "--port", "8000"]
