# syntax=docker/dockerfile:1

# Satlab web: the Vite build and the documentation site, served by unprivileged nginx, which also
# proxies /satlab/api and /satlab/ws to the API container. Built from the repository root, because
# the docs need docs/ and the backend's code (frontend/Dockerfile.dockerignore is the allow-list):
#   docker build -t satlab-web:local -f frontend/Dockerfile .
# Build arg NEXT_UMAMI_ID (public, a UUID) enables analytics; SATLAB_RELEASE (the commit SHA) tags
# the errors it captures.

FROM ghcr.io/astral-sh/uv:0.13.1 AS uv

FROM node:24-trixie-slim AS deps
RUN npm install -g pnpm@10.33.1
WORKDIR /src/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN --mount=type=cache,id=pnpm-store,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

FROM deps AS build
COPY frontend/ ./
ARG NEXT_UMAMI_ID=
ENV NEXT_UMAMI_ID=${NEXT_UMAMI_ID}
ARG SATLAB_RELEASE=
ENV SATLAB_RELEASE=${SATLAB_RELEASE}
RUN pnpm build \
    && hashes="$(node scripts/csp-script-hashes.mjs dist/index.html)" \
    && sed "s|__CSP_SCRIPT_HASHES__|${hashes}|" nginx/security-headers.conf > /tmp/security-headers.conf

# Mermaid and the fonts the docs use, from the same lockfile as the app (no CDN).
FROM deps AS vendor
COPY frontend/scripts/ scripts/
COPY frontend/public/ public/
RUN node scripts/docs-vendor.mjs /vendor

# The docs site (Sphinx; autodoc imports the backend, so its dependencies plus the docs group).
# Strict: any warning fails the image, as it fails CI's docs job.
FROM python:3.12-slim-trixie AS docs
COPY --from=uv /uv /usr/local/bin/uv
# rasterio's bundled GDAL links the system libexpat, which the slim image doesn't ship; autodoc
# imports app.sar.raster and app.sar.render, so the docs need it too.
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*
# The context is an allow-list, so the repo files the docs link to on GitHub (workflows, Dockerfiles,
# LICENSE) aren't here: skip that existence check (CI's docs job runs it on the full checkout).
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never DOCS_CHECK_REPO_LINKS=0
WORKDIR /build/satlab/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --group docs
WORKDIR /build/satlab
COPY backend/app/ backend/app/
COPY backend/migrations/versions/ backend/migrations/versions/
COPY docs/ docs/
COPY AGENTS.md README.md design-system.md .env.example ./
COPY --from=vendor /vendor docs/_static/vendor
WORKDIR /build/satlab/backend
RUN uv run --no-sync sphinx-build -M dirhtml ../docs ../docs/_build -W --keep-going

# The docs' own CSP: the hashes of every distinct inline script in the built site.
FROM node:24-trixie-slim AS docs-csp
WORKDIR /src
COPY frontend/scripts/csp-script-hashes.mjs frontend/nginx/docs-security-headers.conf ./
COPY --from=docs /build/satlab/docs/_build/dirhtml /docs
RUN hashes="$(node csp-script-hashes.mjs /docs)" \
    && sed "s|__DOCS_CSP_SCRIPT_HASHES__|${hashes}|" docs-security-headers.conf > /tmp/docs-security-headers.conf

FROM nginxinc/nginx-unprivileged:1.30.6-alpine AS runtime
USER root
RUN rm -rf /usr/share/nginx/html/* && mkdir -p /etc/nginx/snippets
COPY --from=build /src/frontend/dist /usr/share/nginx/html/satlab
COPY --from=docs /build/satlab/docs/_build/dirhtml /usr/share/nginx/html/satlab/docs
COPY --from=build /tmp/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=docs-csp /tmp/docs-security-headers.conf /etc/nginx/snippets/docs-security-headers.conf
COPY frontend/nginx/default.conf.template /etc/nginx/templates/default.conf.template
USER 101
ENV SATLAB_API_UPSTREAM=http://ca-satlab-api
EXPOSE 8080
