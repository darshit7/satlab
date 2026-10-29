# syntax=docker/dockerfile:1

# Satlab web: the Vite build served by unprivileged nginx, which also proxies /satlab/api and
# /satlab/ws to the API container. Build arg NEXT_UMAMI_ID (public, a UUID) enables analytics;
# SATLAB_RELEASE (the commit SHA) tags the errors it captures.

FROM node:24-trixie-slim AS build
RUN npm install -g pnpm@10.33.1
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN --mount=type=cache,id=pnpm-store,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile
COPY . .
ARG NEXT_UMAMI_ID=
ENV NEXT_UMAMI_ID=${NEXT_UMAMI_ID}
ARG SATLAB_RELEASE=
ENV SATLAB_RELEASE=${SATLAB_RELEASE}
RUN pnpm build \
    && hashes="$(node scripts/csp-script-hashes.mjs dist/index.html)" \
    && sed "s|__CSP_SCRIPT_HASHES__|${hashes}|" nginx/security-headers.conf > /tmp/security-headers.conf

FROM nginxinc/nginx-unprivileged:1.30.6-alpine AS runtime
USER root
RUN rm -rf /usr/share/nginx/html/* && mkdir -p /etc/nginx/snippets
COPY --from=build /app/dist /usr/share/nginx/html/satlab
COPY --from=build /tmp/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
USER 101
ENV SATLAB_API_UPSTREAM=http://ca-satlab-api
EXPOSE 8080
