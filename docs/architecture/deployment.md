# Deployment

## What it is

Two container images, deployed to Azure Container Apps and served at
`https://satlab.darshitp.dev/satlab/`:

satlab-web
: nginx (unprivileged) serving the Vite build at `/satlab/`. It also serves **these docs** at
  `/satlab/docs/`, built from the same commit, and reverse-proxies `/satlab/api` and `/satlab/ws`
  (with WebSocket upgrade) to the API with the `/satlab` prefix stripped. It sets the CSP and the
  other security headers. It is the only app with external ingress.

satlab-api
: uvicorn with **one worker, exactly one replica**, as a non-root user. Sessions, their loops and
  the fleet live in this process's memory, so a second replica would split WebSocket sessions.
  Scaling out would need shared session state first. Its ingress is internal: only the web
  container reaches it. A SAR comparison runs in a short-lived child process of this container
  (spawned, niced, one at a time), and its crops and images sit in `/app/var/sar`, the container's
  ephemeral disk: a restart or redeploy empties it, and comparisons then read as expired until
  recomputed. `SATLAB_SAR_STORAGE_MAX_BYTES` (1 GiB by default) keeps it within that disk.
  rasterio (with its bundled GDAL) and scipy make the API image about 200 MB larger. GDAL links
  the system `libexpat`, which the slim Python base image doesn't ship, so both the API image and
  the web image's docs stage install `libexpat1`. The API image's build imports rasterio once, so
  a missing native library fails the build rather than a comparison in production.

The database is Postgres on Neon (see [persistence](persistence.md)).

## Why it works this way

- **Same origin for everything.** The app, its API, its WebSocket and its docs share one origin.
  So there is no CORS for the browser to negotiate, the docs link is a plain relative URL, and one
  TLS certificate covers it all.
- **Docs ship with the code they describe.** The web image builds the docs site from the same
  commit as the app, so the deployed docs always match the deployed code. A docs build failure
  fails the image.
- **Migrations are a release step.** They run against production, as the schema owner, before the
  new images roll out. That is why every migration must be expand/contract (see
  [persistence](persistence.md)).
- **Nothing secret in an image.** Settings and secrets come from the platform: the database URL
  and the SatNOGS token are Container App secrets. The web image's build context is an explicit
  allow-list (`frontend/Dockerfile.dockerignore`), so `.env`, `notes/` and `deploy/` can never
  enter it.

## How it flows

```{mermaid}
flowchart TB
  D[push to dev] --> CI[CI: source-branch · backend ·<br/>backend-postgres · frontend ·<br/>docs · images + smoke]
  PR[PR dev → main<br/>checks required] --> M[merge to main]
  M --> V[deploy.yml: CI again]
  V --> P[build + push both images<br/>to GHCR by commit SHA]
  P --> MG[alembic upgrade head<br/>on Neon production, as owner]
  MG --> RA[roll out API, then web,<br/>by SHA]
  RA --> SM[smoke test the public URL]
```

**CI** (`.github/workflows/ci.yml`) runs on every push to `dev` and every PR:

- `backend`: ruff, then pytest on SQLite. `backend-postgres` runs the same tests on a Neon test
  branch.
- `frontend`: lint, tests, build.
- `docs`: a strict docs build plus the docs tests (see [write the docs](../how-to/write-docs.md)).
- `images`: builds both images, runs them together, and runs `scripts/smoke.sh` against them. That
  checks redirects, the SPA fallback, the CSP hash, immutable assets, Cesium assets, the docs, the
  API's health and a WebSocket session.

`main` only accepts PRs from `dev`, merged with merge commits, and requires those checks.

**Deploy** (`.github/workflows/deploy.yml`) runs on a merge to `main`. It runs CI again, builds and
pushes `satlab-api` and `satlab-web` to GHCR tagged with the commit SHA, migrates Neon production,
updates the API and then the web app by SHA, and smoke-tests the public URL.

**Rollback** means pointing both apps back at an older SHA. Migrations are not rolled back, which
is again why they are expand/contract. See [deploy and roll back](../how-to/deploy-and-rollback.md).

## The web container

```text
/                      → 302 /satlab/
/satlab/               → the app (SPA: unknown paths fall back to index.html)
/satlab/assets/        → hashed, immutable bundles
/satlab/cesium/        → Cesium workers and imagery (a missing file is a 404, never the app)
/satlab/docs/          → this site (static; a missing page is the docs 404, never the app)
/satlab/api/…          → API, prefix stripped, per-IP request limit
/satlab/ws/…           → API WebSocket, prefix stripped, per-IP connection limit
```

The app and the docs each get a Content-Security-Policy with the SHA-256 of their inline scripts,
computed from the built files during the image build (`frontend/scripts/csp-script-hashes.mjs`).
A changed inline script can therefore never be silently blocked or silently allowed. Nothing loads
from a CDN: Cesium's assets, the fonts and the docs' Mermaid all come from the lockfile and are
served from our origin.

## Where in the code

- `frontend/Dockerfile`, `frontend/Dockerfile.dockerignore`, `frontend/nginx/`: the web image.
- `backend/Dockerfile`: the API image.
- `.github/workflows/ci.yml`, `deploy.yml`, `scripts/smoke.sh`.
