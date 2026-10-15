# syntax=docker/dockerfile:1

# Satlab API: FastAPI + the simulation loop. One process, one worker: sessions, the sim loop and
# the fleet live in memory, so this image must run as a single replica (AGENTS.md §14).

ARG PYTHON_IMAGE=python:3.12-slim-trixie

FROM ghcr.io/astral-sh/uv:0.13.1 AS uv

FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first (cached until the lockfile changes), exactly as locked, without dev tools.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM ${PYTHON_IMAGE} AS runtime
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /app/.venv ./.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
# Runtime data (Celestrak cache) goes to backend/var, i.e. /app/var: the only writable path.
RUN mkdir -p var/cache && chown -R app:app var
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SATLAB_AUTO_MIGRATE=false
USER app
EXPOSE 8000
# 0.0.0.0 inside the container only; in Azure the API has internal ingress (reachable from the
# web container, not the internet). Proxy headers come from the web container / ACA ingress.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips=*", "--ws-max-size", "65536", "--no-server-header"]
