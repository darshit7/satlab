# Run it locally

The simulator is two processes: the API (FastAPI, Python, managed with uv) and the web app
(React and Vite, managed with pnpm). In development, Vite serves the app at
`http://localhost:5173/satlab/` and proxies `/satlab/api` and `/satlab/ws` to the API on port 8000,
so the browser sees a single origin, as it does in production.

```{include} ../../README.md
:start-after: <!-- docs:quick-start:start -->
:end-before: <!-- docs:quick-start:end -->
```

## What happens on first start

1. The API applies the database migrations (`SATLAB_AUTO_MIGRATE`, on by default), seeds the
   preset ground stations and re-applies the satellite type rules.
2. The catalog service builds the in-memory fleet from whatever the database already holds. That
   works offline, so a restart is instant.
3. In the background it downloads Celestrak's `active` group (cached in `backend/var/cache/`,
   refreshed when older than 12 hours), upserts the `satellite` table, syncs the link profiles and
   packet templates from the seed files, and swaps in a new fleet.
4. The web app loads the stations, link profiles and packet templates, then asks you where to
   observe from. Choosing a station starts a [mission session](../architecture/session-lifecycle.md).

Everything the backend writes at runtime (the SQLite database, the Celestrak and SatNOGS caches)
lives in `backend/var/`, which is gitignored and safe to delete.

## Optional: real SatNOGS frames

Replayed telemetry (the ISS's APRS digipeater, CroCube, UWE-4) comes from the SatNOGS DB, which
needs an API token: set `SATLAB_SATNOGS_API_TOKEN` in `.env`. Without one the archive reads "not
configured" and the UI says so; everything else works.

## Optional: Postgres

With no database URL the backend uses SQLite. Production uses Postgres on Neon, with separate
roles for the app and for migrations. See [persistence](../architecture/persistence.md) and the
`SATLAB_DATABASE_URL` / `SATLAB_MIGRATION_DATABASE_URL` [settings](../reference/settings.md).

## Checks

```bash
cd backend  && uv run ruff check . && uv run ruff format --check . && uv run pytest
cd frontend && pnpm lint && pnpm test && pnpm build
```

`pytest` uses SQLite unless a `.env.test` points it at a Postgres test branch.

## These docs

The docs build needs the backend's dependencies (autodoc imports the code) plus the `docs`
group, and two assets from the frontend lockfile (Mermaid and the fonts):

```bash
pnpm -C frontend install && pnpm -C frontend docs:vendor
cd backend
uv sync --group docs
uv run --group docs sphinx-build -M dirhtml ../docs ../docs/_build -W --keep-going
```

To preview while you write, run `sphinx-autobuild` on port 8001; `pnpm dev` proxies
`/satlab/docs/` to it, so the app's Docs link works in development too. See
[write the docs](../how-to/write-docs.md) for details.

:::{note}
A plain `uv sync` removes the `docs` group again (it syncs only the default groups). Use
`uv sync --group docs`, or `uv run --group docs …`, which adds it for that command.
:::
