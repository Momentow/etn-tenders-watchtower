# Base image tag must match the playwright version pinned in pyproject.toml (browsers are preinstalled).
FROM mcr.microsoft.com/playwright/python:v1.65.2-noble AS base

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /bin/uv

ENV UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Docs site (T-019): Zensical builds docs/ into /src/site. A docs error fails the build, on purpose (ADR 0001).
FROM base AS docs
WORKDIR /src
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --only-group docs --no-install-project
COPY mkdocs.yml ./
COPY docs ./docs
RUN uv run --no-sync zensical build

FROM base

ENV DATA_DIR=/data

WORKDIR /app

# Dependencies first so source edits reuse the cached layer.
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY watchtower ./watchtower
COPY mockportals ./mockportals
RUN uv sync --frozen --no-dev
COPY --from=docs /src/site ./site

VOLUME ["/data"]
EXPOSE 8080
CMD ["/app/.venv/bin/python", "-m", "watchtower"]
