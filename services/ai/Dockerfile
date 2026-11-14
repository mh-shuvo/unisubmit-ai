# Build context: repository root (uv.lock covers the whole workspace).
# On Linux, uv takes torch from the CPU-only index (services/ai/pyproject.toml).
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY services/core/pyproject.toml services/core/
COPY services/ai/pyproject.toml services/ai/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package unisubmit-ai --no-install-workspace
COPY services/ai services/ai
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package unisubmit-ai --no-editable

FROM python:3.13-slim
COPY --from=build /src/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /app
# BAAI/bge-m3 (~2 GB) is downloaded here on first start; the compose file
# mounts a named volume at this path so it survives container rebuilds.
ENV HF_HOME=/root/.cache/huggingface
EXPOSE 8001
CMD ["uvicorn", "unisubmit_ai.main:app", "--host", "0.0.0.0", "--port", "8001"]
