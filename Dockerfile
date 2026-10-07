# The backend as one container image. docker-compose.yml runs it four ways: avtal-mcp (mcp),
# the API (api), the ingestion (ingest) and the measurement of the answers (eval), each with
# its own command. Two stages:
#   build    installs the locked dependencies with uv, then the project itself
#   runtime  the environment and the code, run as an unprivileged user
# The Python image is multi-architecture, so the same file builds for a Mac with Apple silicon
# (linux/arm64) and in CI (linux/amd64). PyTorch, which Docling's models need for the
# ingestion, is its CPU build on both (pyproject.toml, ADR 0008).

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${PYTHON_IMAGE} AS build
# The uv version that locked uv.lock and that pyproject.toml's build backend matches.
COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
# The dependencies in a layer of their own, so a change to the code does not reinstall them.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

FROM ${PYTHON_IMAGE} AS runtime
# OpenCV, which Docling's table model uses, loads GLib, which the slim image lacks.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
# An unprivileged user, with the folder Docling's models are downloaded to (a volume in
# docker-compose.yml takes its owner from it).
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /home/app/.cache/huggingface \
    && chown -R app:app /home/app/.cache
WORKDIR /app
COPY --from=build /app /app
# Alembic's settings and migrations (in src/), and the accepted deviations the ingestion reads.
COPY alembic.ini accepted_findings.toml ./
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data
USER app
CMD ["python", "-m", "avtalsagent.api"]
