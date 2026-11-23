# Satlab — Satellite Mission Control Simulator

A real-time satellite mission control simulator. Positions and geometry come from **real TLEs** propagated
with **SGP4** for **your real location**.

## Quick start

<!-- docs:quick-start:start -->
Prerequisites: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 20+ with [pnpm](https://pnpm.io/).

```bash
# terminal 1 — backend (http://localhost:8000)
cd backend
uv sync
uv run uvicorn app.main:app --reload

# terminal 2 — frontend (http://localhost:5173/satlab/)
cd frontend
pnpm install
pnpm dev
```

On first start the backend creates `backend/var/satlab.db` (SQLite; see below for Postgres), seeds a few ground stations, and downloads
the Celestrak "active" catalog (~16k objects) in the background. Open http://localhost:5173/satlab/.

Optional settings: copy [.env.example](.env.example) to `.env`.
<!-- docs:quick-start:end -->

### Persistent database (Postgres on Neon)

Each stage uses its own Neon branch: `dev` for local development (`.env`), `test` for `pytest`
(`.env.test`) and `production`. The app connects as a role that can only read and write rows;
migrations run as the schema owner. Without any database URL the backend falls back to SQLite, so a
fresh clone still runs. Setup, roles, migrations and verification are in [deploy/neon.md](deploy/neon.md).

### Containers and deployment

The app runs as two containers and is live at https://satlab.darshitp.dev/satlab/ (Azure Container Apps):

- **`satlab-web`** ([frontend/Dockerfile](frontend/Dockerfile), built from the repo root): nginx serves the Vite
  build at `/satlab/` (`/` redirects there) and the docs at `/satlab/docs/`, and reverse-proxies `/satlab/api` and `/satlab/ws` (WebSocket upgrade) to the API
  **with the `/satlab` prefix stripped**. It also sets the CSP and other security headers.
  `SATLAB_API_UPSTREAM` points it at the API. Build with `--build-arg NEXT_UMAMI_ID=<uuid>` to include
  the Umami tracker (custom events, Web Vitals, errors; it runs only on `satlab.darshitp.dev`), and
  `--build-arg SATLAB_RELEASE=<commit sha>` to tag captured errors; without an ID, no tracker is added.
- **`satlab-api`** ([backend/Dockerfile](backend/Dockerfile)): uvicorn with one worker, run as a single
  replica, because sessions live in memory. Only the web container can reach it.

Run both locally and smoke-test them with the commands in [AGENTS.md §5](AGENTS.md#5-commands).

Work happens on `dev`. A PR from `dev` to `main` must pass CI ([ci.yml](.github/workflows/ci.yml)):
backend lint and tests on SQLite and Postgres, frontend lint, tests and build, and both images built
and smoke-tested together. Merging runs [deploy.yml](.github/workflows/deploy.yml), which pushes
both images to GHCR, migrates the production database, rolls out to Azure Container Apps and
smoke-tests the public URL.

## Documentation

The full documentation is at https://satlab.darshitp.dev/satlab/docs/ (the **Docs** link in the app's
header). It covers how the system works and why, with diagrams, and has the Python API, REST,
WebSocket, settings, database and seed-data references generated from the code. Its source is
[docs/](docs/), written as docs-as-code: it changes with the code and CI builds it strictly. To build it:

```bash
pnpm -C frontend install && pnpm -C frontend docs:vendor
cd backend && uv run --group docs sphinx-build -M dirhtml ../docs ../docs/_build -W --keep-going
```

## Checks

```bash
cd backend  && uv run pytest && uv run ruff check . && uv run ruff format --check .   # pytest uses the Neon test branch if .env.test exists
cd frontend && pnpm test && pnpm lint && pnpm build
scripts/smoke.sh http://localhost:8080 http://localhost:8000 http://localhost:8080   # against the local containers
```

## How it fits together

```
Celestrak ──(background fetch, cached)──▶ satellite table ──▶ in-memory fleet (whole catalog)
                                          (+ type rules)        + elements registry
                                                                     │
browser ──REST──▶ FastAPI ── stations / sessions / pass schedule     │
   ▲                                                                 ▼
   └──── WebSocket /ws/sessions/{id} ◀── sim loop (per session): clock → vectorized SGP4 →
         snapshot @ 2 Hz (in view ∩ filter                   az/el/range for every satellite →
         + tracked + selected), sky arcs,                    above-mask set, pass arcs,
         ground tracks, rising-soon list                     rising-soon sweep (chunked)
```

- **Backend** (`backend/app`): `sim/` holds the pure physics (clock, propagation, geometry, fleet,
  pass prediction, tick),
  `services/` orchestrates the catalog and sessions, `models/` is the pydantic WS/REST contract, and
  `db/` + `migrations/` hold the SQLAlchemy models and Alembic revisions.
- **Frontend** (`frontend/src`): a zustand store fed by the WebSocket; the Cesium globe, the MapLibre 2D
  map and the two canvas sky views only render streamed state, tweening markers along server-computed ground tracks
  and pass arcs. Styling is Tailwind v4; shared primitives live in `components/ui/`.

## Honesty notes

<!-- docs:honesty:start -->
- Elevation is **geometric**: "in view" (above the horizon mask, 5° by default) does not mean visible
  to the naked eye; most satellites shown are too small or unlit to see.
- TLE accuracy degrades with age; each satellite's TLE age is shown.
- Commanding real satellites is impossible. Uplink is **simulated**: nothing is ever transmitted.
- Link margins use each satellite's **published** RF budget and that source's reference receiving
  station; a satellite without one shows "no RF data" rather than a guessed margin
  ([`link_profiles.json`](backend/app/data/seed/link_profiles.json) cites every value).
- Power, thermal, storage and attitude are **modelled** from generic parameters, driven by real eclipse
  and pass geometry; the UI labels them so.
- SAR imagery is **real archived** Sentinel-1 data; nothing is captured on request. Change flags come from
  a simple statistical test on the radar return: they mark where it changed, not what changed (wet soil,
  crops or wind on water change it too).
<!-- docs:honesty:end -->

## Data sources

<!-- docs:data-sources:start -->
- Orbital elements: [Celestrak GP](https://celestrak.org/NORAD/documentation/gp-data-formats.php)
  (please respect their fetch etiquette; the backend caches and refreshes at most every 12 h).
- Link budgets: NOAA/NESDIS JPSS-1 and JPSS-2 HRD RF ICDs, the CMA FY-3D space-to-ground ICD and
  NOAA OSPO's GOES-R GRB link budget (links in the seed file and in the app).
- SAR imagery: [Sentinel-1 RTC on Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/dataset/sentinel-1-rtc)
  (CC BY 4.0). Contains modified Copernicus Sentinel data; radiometric terrain correction by Catalyst.
- Basemap: [OpenFreeMap](https://openfreemap.org/) © OpenMapTiles, © OpenStreetMap contributors.
- Emoji: [Twemoji](https://github.com/jdecked/twemoji) graphics, CC-BY 4.0. Fonts: Nunito, JetBrains Mono, Playpen Sans (SIL OFL), self-hosted via Fontsource.
<!-- docs:data-sources:end -->

## Design

The UI follows [design-system.md](design-system.md) ("Warm Notebook"): one emerald accent, one grey family,
rounded everywhere, light/dark/system themes.

## License

[MIT](LICENSE)
