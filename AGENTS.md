# AGENTS.md

Guidance for AI-assisted development of the **Satellite Mission Control Simulator**. Read this first in every session. It defines the architecture, conventions, database design, and phase plan you must follow. When something here conflicts with a general default, **this file wins**.

---

## 1. What this project is

An end-to-end, real-time **satellite mission control simulator** (portfolio / learning project). A user picks a ground station, sees which satellites are overhead, opens a (simulated) contact, sends/receives payloads, schedules future passes, and — for SAR-capable satellites — tasks image captures and compares **real** open-source imagery over time with AI-assisted change detection.

**Realism is the core value.** Positions, geometry, link health, and SAR imagery are computed from **real physics and real open data**, never faked:
- Orbits: real **TLEs** → **SGP4** propagation.
- Geometry: correct frame transforms (ECI→ECEF→topocentric) for the user's real location.
- Health: a real **link budget** (signal margin in dB), not arbitrary flags.
- SAR: real open data (Sentinel-1, Umbra). *Tasking* is an honest simulation over the real acquisition cadence.

**Honesty rules (must surface in the UI, never quietly violate in code):**
1. You cannot command real satellites — uplink/commanding is **simulated**; downlink can replay **real** archived frames.
2. You cannot task Sentinel-1/Umbra on demand — simulate the workflow, then wait for the **real** next acquisition.
3. Geometric truth ≠ naked-eye visibility — sky views show true positions; most objects aren't eye-visible.

**Product/UX spec:** [docs/SATELLITE_MISSION_CONTROL_DESIGN.md](docs/SATELLITE_MISSION_CONTROL_DESIGN.md) holds the detailed feature behavior (user flows, views, the visibility cone, indicators, comms, SAR). This file wins on rules and conventions; the design doc wins on feature behavior and UX. **Before starting a phase, read the design-doc sections it cites** (§9 below).

---

## 2. Golden rules (do not break these)

1. **One simulation engine.** All physics lives in the backend sim loop. The frontend **renders streamed state**; it never re-derives orbital positions, link margins, or pass windows. A new feature that needs physics adds a field to the tick output — never a parallel calculation in JS.
2. **Events are immutable and additive.** The event log is the source of truth for playback. Add new event `type`s; never mutate the meaning of existing ones, or old recordings stop replaying.
3. **Server computes, client interpolates.** Propagate and filter server-side; stream only visible satellites; the client tweens between updates.
4. **One link-budget computation, many views.** The same link state drives the RF indicator, the sky-plot dot color, and the 3D cone boundary color. They must always agree.
5. **Schemas are the contract.** Pydantic models define the WebSocket snapshot and every event payload; the frontend mirrors them as TypeScript types. Change them deliberately and in lockstep.
6. **Never block the sim loop on a network call.** External data (Celestrak, SatNOGS, STAC) is cached and fetched off the loop; degrade gracefully when unavailable.
7. **Build strictly phase by phase** (see §9). Do not pull later-phase work forward unless asked. Every phase stays end-to-end runnable.
8. **No secrets in the repo.** API accounts (e.g. Space-Track) go in `.env` (gitignored); provide `.env.example`.
9. **Large binaries stay out of the DB.** SAR rasters live on disk/object storage; the database stores only paths and metadata.
10. **Everything from outside is untrusted.** Browser messages, REST input, and every byte from Celestrak, SatNOGS, STAC, KML or a downloaded raster is validated and bounded before use. See §12.

---

## 3. Tech stack

| Concern | Choice |
|---|---|
| Repo | Monorepo (`backend/`, `frontend/`) |
| Backend | Python 3.12+, **FastAPI**, async |
| Py package mgr | **uv** |
| Orbital math | **skyfield** (+ **sgp4**) |
| ORM / migrations | **SQLAlchemy 2.x** + **Alembic** |
| Validation / contract | **pydantic v2** |
| DB (local dev / tests) | **SQLite** (zero setup) |
| DB (persistent / prod, Phase 2b+) | **PostgreSQL on Neon** via **psycopg 3**; **PostGIS** at Phase 7 (optional **TimescaleDB**) |
| Frontend | **React + TypeScript + Vite** |
| JS package mgr | **pnpm** |
| 3D globe | **CesiumJS**: `@cesium/engine` (`CesiumWidget`, imperative like the 2D map; no Resium, no Viewer widgets) + `@cesium/core` |
| 2D map | **MapLibre GL** (or Leaflet) |
| Sky plot / all-sky / indicators | **Canvas 2D** (+ **D3** when a view earns it) |
| Client state | **Zustand** |
| Styling / design system | **Tailwind CSS v4** + **lucide-react** icons — rules in [design-system.md](design-system.md) |
| Transport | **WebSocket** (JSON; msgpack later if needed) |
| SAR access | **STAC** via **pystac-client** |
| SAR rasters | **rioxarray / rasterio / stackstac** |
| Change detection v1 | **NumPy / scikit-image** (log-ratio) |
| Change detection v2 (opt) | **PyTorch** (Siamese/U-Net) |
| Runtime | Local-first; optional **docker-compose** |
| Lint/format (py) | **ruff** |
| Test (py / js) | **pytest** / **vitest** |

---

## 4. Repository layout

```
satellite-mission-control/
├─ CLAUDE.md                 # this file
├─ README.md
├─ docker-compose.yml        # optional, for parity (Phase 9)
├─ .env.example
├─ .github/workflows/        # ci.yml (PR checks), deploy.yml (main → GHCR → Azure Container Apps)
├─ scripts/smoke.sh          # end-to-end smoke test of a running web+API pair (CI and post-deploy)
├─ deploy/                  # deploy runbooks, gitignored/local (neon.md, azure.md)
├─ backend/
│  ├─ Dockerfile             # API image: uvicorn, 1 worker, non-root
│  ├─ pyproject.toml         # uv-managed
│  ├─ alembic.ini
│  ├─ migrations/            # Alembic revisions
│  └─ app/
│     ├─ main.py             # FastAPI app + WebSocket endpoints
│     ├─ config.py           # settings from env
│     ├─ db/
│     │  ├─ base.py          # engine, session, Base
│     │  └─ models.py        # SQLAlchemy ORM models (see §8)
│     ├─ models/             # pydantic schemas = the API/WS contract
│     ├─ sim/
│     │  ├─ clock.py         # accelerable simulation clock
│     │  ├─ propagate.py     # SGP4 / skyfield wrappers (+ vectorized SatrecArray epochs)
│     │  ├─ geometry.py      # ECI→ECEF→topocentric: az/el/range/range-rate
│     │  ├─ fleet.py         # the whole catalog as arrays, for vectorized propagation
│     │  ├─ passes.py        # AOS/LOS/culmination, sky arcs, rising-soon sweep
│     │  ├─ linkbudget.py    # SNR/Eb·N0 margin, Doppler, RAG mapping
│     │  ├─ subsystems.py    # power/thermal/storage/attitude/RF models
│     │  ├─ sun.py           # Sun position + Earth shadow (eclipse), no ephemeris file
│     │  ├─ scheduler.py     # command queue, AOS trigger, alarms
│     │  └─ loop.py          # the tick; emits snapshots + events
│     ├─ packets/            # per-satellite AX.25 / CCSDS templates
│     ├─ data/               # TLE (Celestrak), SatNOGS, catalog ingestion; seed/ JSON (stations,
│     │                      #   type rules, link_profiles, subsystem_models)
│     ├─ sar/                # STAC search/download, change detection
│     ├─ events/             # event store: append + replay
│     └─ services/           # orchestration between sim, db, ws
│  └─ tests/
└─ frontend/
   ├─ Dockerfile             # web image: Vite build served by nginx-unprivileged
   ├─ nginx/                 # nginx config template + security headers (CSP)
   ├─ package.json           # pnpm-managed
   ├─ vite.config.ts
   └─ src/
      ├─ store/              # zustand store fed by WebSocket
      ├─ ws/                 # WS client + reconnect
      ├─ types/              # TS mirrors of pydantic contract
      ├─ views/
      │  ├─ Map/             # map panel: 3D globe (default) / 2D map toggle
      │  ├─ Globe/           # Cesium 3D globe + access cone (lazy chunk)
      │  ├─ AllSky/          # observer all-sky (fisheye) view
      │  ├─ SkyPlot/         # polar az/el plot
      │  ├─ Indicators/      # RAG panel + rollup bar
      │  ├─ Comms/           # packet send/receive + animation
      │  ├─ Scheduler/       # command scheduling + alarms
      │  ├─ SAR/             # image compare + change overlay
      │  └─ Playback/        # timeline scrubber
      └─ components/         # shared UI
```

---

## 5. Commands

> Confirmed against the Phase 1 scaffold — keep this section authoritative.

**Backend**
```bash
cd backend
uv sync                                   # install deps
uv run uvicorn app.main:app --reload      # dev server (http://localhost:8000); auto-migrates + seeds
uv run pytest                             # tests: Neon `test` branch if `.env.test` exists, else SQLite (Celestrak always mocked)
uv run ruff check . && uv run ruff format .
uv run alembic revision --autogenerate -m "msg"
uv run alembic upgrade head               # apply migrations (also runs on startup if SATLAB_AUTO_MIGRATE)
```
Runtime data (SQLite DB, Celestrak TLE cache) lives in `backend/var/` (gitignored, disposable).

**Postgres (Neon), one branch per stage** — full runbook in [deploy/neon.md](deploy/neon.md).
- `.env` (dev → `dev` branch): `SATLAB_DATABASE_URL` = least-privilege `satlab_app` on the **pooled** host,
  `SATLAB_MIGRATION_DATABASE_URL` = `neondb_owner` on the **direct** host; migrates at startup.
- `.env.test` (pytest → `test` branch): `SATLAB_TEST_POSTGRES_URL` = owner, **direct** host; one throwaway schema per test.
- Production (`production` branch): the app gets only the `satlab_app` pooled URL with `SATLAB_AUTO_MIGRATE=false`;
  migrations are a release step: `uv run --env-file ../.env.production alembic upgrade head` (owner, direct host).
- With no URL configured, the backend and tests fall back to SQLite.

**Frontend**
```bash
cd frontend
pnpm install
pnpm dev                                   # Vite dev server (http://localhost:5173/satlab/); proxies /satlab/api + /satlab/ws to :8000
pnpm build                                 # tsc -b && vite build
pnpm test                                  # vitest
pnpm lint                                  # oxlint + tsc typecheck
```

**Containers (Phase 2c)** — two images; build and smoke-test them locally before pushing:
```bash
docker build -t satlab-api:local backend
docker build -t satlab-web:local frontend                # --build-arg NEXT_UMAMI_ID=<uuid> for analytics
docker network create satlab
docker run -d --name api --network satlab -p 8000:8000 -e SATLAB_AUTO_MIGRATE=true \
  -e SATLAB_CATALOG_AUTOLOAD=false -e SATLAB_DOCS_ENABLED=false \
  -e 'SATLAB_CORS_ORIGINS=["http://localhost:8080"]' satlab-api:local
docker run -d --name web --network satlab -p 8080:8080 -e SATLAB_API_UPSTREAM=http://api:8000 satlab-web:local
scripts/smoke.sh http://localhost:8080 http://localhost:8000 http://localhost:8080   # app: http://localhost:8080/satlab/
```

**Branches, CI and deploy (Phase 2c)**
- All work happens on **`dev`**; `main` only receives PRs from `dev` (merge commits, never squash, so the
  branches don't diverge). `main`'s ruleset requires the CI checks `source-branch`, `backend`,
  `backend-postgres`, `frontend` and `images` (`.github/workflows/ci.yml`; `images` builds both images and
  runs `scripts/smoke.sh` against them).
- A merge to `main` runs `.github/workflows/deploy.yml`: CI again → push `ghcr.io/darshit7/satlab-api` and
  `satlab-web` (`:<sha>`, `:latest`) → `alembic upgrade head` on Neon `production` as the owner (the release
  step) → `az containerapp update` the API, then the web app, by SHA → `scripts/smoke.sh` on the public URL.
- Rollback: `az containerapp update -n <app> -g rg-portfolio --image ghcr.io/darshit7/<image>:<older sha>`
  (both apps). Migrations are not rolled back automatically, which is why they must be expand/contract.
- GitHub `production` environment (deploys from `main` only): secrets `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
  `AZURE_SUBSCRIPTION_ID` (OIDC, role scoped to the two satlab apps; GitHub presents the subject with
  immutable IDs, `repo:darshit7@9577827/satlab@1413263673:environment:production`, so the Entra app
  has a federated credential for that as well as for the plain `repo:darshit7/satlab:…` form),
  `SATLAB_MIGRATION_DATABASE_URL` (prod owner, direct host); variables `AZURE_RESOURCE_GROUP`,
  `AZURE_API_APP`, `AZURE_WEB_APP`, `PUBLIC_URL`. Repository level: secret `SATLAB_TEST_POSTGRES_URL`
  (Neon `test`, direct host) for `backend-postgres`, and variable `NEXT_UMAMI_ID`. The `publish` job runs
  outside the environment, so it only sees repository variables.
- Azure: environment `cae-portfolio` (rg `rg-portfolio`), apps `ca-satlab-api` (internal ingress :8000,
  exactly 1 replica) and `ca-satlab-web` (external :8080, `satlab.darshitp.dev`, managed cert). The API's
  settings live on the app (`SATLAB_DATABASE_URL` as a secret ref, `SATLAB_AUTO_MIGRATE=false`,
  `SATLAB_CORS_ORIGINS`, `SATLAB_DOCS_ENABLED=false`); `az containerapp update --image` keeps them.
  Images stay **private** in GHCR. Both apps pull them with the same registry credential as `ca-portfolio`
  (user `darshit7`, a classic PAT with `read:packages`, app secret `ghcrio-darshit7`). Rotate it on all
  three apps together. One-time setup steps: `deploy/azure.md` (local).

---

## 6. Conventions

- **Python:** 3.12+, full type hints, `ruff` for lint+format, `snake_case` modules/functions, `PascalCase` classes. Prefer pure functions in `sim/` (easy to unit-test against known passes).
- **TypeScript:** strict mode; `PascalCase` components, `camelCase` vars; colocate a view's components under its `views/<View>/` folder.
- **UI look & feel:** follow [design-system.md](design-system.md) ("Warm Notebook"). Compose `frontend/src/components/ui/` primitives; tokens live only in the `@theme` block of `src/index.css`; one emerald accent, `gray` only, rounded everywhere, both themes.
- **Time:** store and compute in **UTC**; every event/record carries both `sim_time` and `wall_time`; localize only in the UI (user is UTC+05:30).
- **IDs:** integer autoincrement PKs for local-session data; natural key `norad_id` (int) for satellites; STAC scene IDs stored as strings.
- **Angles/units:** degrees in the API/UI, radians internally in math code — convert at the boundary and name variables with units (`elevation_deg`, `range_m`).
- **JSON columns:** use SQLAlchemy `JSON` type (maps to SQLite JSON1 / Postgres JSONB). Keep flexible payloads (event payloads, decoded telemetry, packet specs) here.
- **Migrations:** every schema change is an Alembic revision; never hand-edit the DB. SQLite dev DB is disposable and gitignored.
- **Config:** ground stations, satellite link profiles, packet templates, and thresholds come from versioned seed data / config — not hard-coded in logic.

---

## 7. Domain glossary (so you reason correctly)

- **TLE** — two-line orbital element set; input to SGP4.
- **SGP4** — propagator: TLE + time → position/velocity (ECI).
- **NORAD ID** — integer catalog number identifying a satellite.
- **ECI / ECEF** — inertial (star-fixed) vs Earth-fixed rotating frames.
- **Topocentric** — observer-local frame → **azimuth**, **elevation**, **slant range**.
- **Horizon mask** — min elevation (e.g. 5°) to count as "in view".
- **AOS / LOS** — acquisition / loss of signal: the pass boundaries.
- **Pass / window** — AOS→LOS transit; LEO ~5–12 min, a few per day.
- **Max elevation** — peak elevation of a pass; higher = better link.
- **Doppler** — frequency shift from range-rate; large in LEO.
- **Link budget** — gains/losses → signal **margin (dB)**.
- **Eb/N0, SNR** — signal-quality metrics vs a required threshold.
- **RAG** — Red/Amber/Green status.
- **Rollup bar** — worst-of aggregate of subsystem RAG states.
- **SAR** — radar imaging; works day/night, through cloud.
- **SatNOGS** — open network of ground stations with archived decoded telemetry.
- **STAC** — SpatioTemporal Asset Catalog: query imagery by area+time.
- **RTC** — terrain-corrected, geocoded SAR; scenes align pixel-for-pixel.
- **AOI** — area of interest for imaging/comparison.

**Link-budget → RAG mapping (canonical):** margin > ~6 dB = **green**; 0–6 dB = **amber**; < 0 dB or below horizon mask = **red**. (Thresholds: `SATLAB_LINK_GREEN_MARGIN_DB` / `SATLAB_LINK_AMBER_MARGIN_DB`.) A satellite without a published link budget has **no** link state, shown as "no RF data" in grey, never a guessed margin.

---

## 8. Database design

### 8.1 Principles
- **Event sourcing at the core.** `event` is an append-only, ordered log and the source of truth for playback. Other operational tables are convenient projections/queryable views of what also appears as events; if they ever disagree, the event log is authoritative.
- **SQLite for local dev/tests, Postgres (Neon) for persistence/production — since Phase 2b.** All access via SQLAlchemy 2.x; migrations via Alembic; the URL picks the dialect. Every change must work on both (the opt-in Postgres test run checks this, §5).
- **Geometry:** store AOI/footprints as **bbox columns + GeoJSON (JSON)**; at Phase 7 enable **PostGIS** on the Neon DB and use real `geometry` columns for spatial queries (SQLite keeps the bbox/JSON fallback).
- **Rasters on disk, not in DB.** Store file paths / object-store keys only.

### 8.2 Entity groups
1. **Reference/config** — stations, satellites, packet templates, link profiles (small, relatively static).
2. **Operational/session** — sessions, events, commands, telemetry, passes, alarms (the live mission data).
3. **SAR** — tasks, scenes, change results.

### 8.3 Canonical DDL (SQLite dialect; the ORM is the source of truth)

```sql
-- ========== REFERENCE / CONFIG ==========
CREATE TABLE ground_station (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    lat_deg     REAL NOT NULL,
    lon_deg     REAL NOT NULL,
    alt_m       REAL NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL            -- ISO-8601 UTC
);

CREATE TABLE satellite (
    norad_id        INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    type            TEXT,                 -- station | weather | comms | navigation | sar | other
    is_sar_capable  INTEGER NOT NULL DEFAULT 0,  -- bool
    tle_line1       TEXT,
    tle_line2       TEXT,
    tle_epoch       TEXT,                 -- ISO-8601 UTC
    fetched_at      TEXT                  -- when TLE was pulled
);

CREATE TABLE packet_template (
    id          INTEGER PRIMARY KEY,
    norad_id    INTEGER NOT NULL REFERENCES satellite(norad_id),
    kind        TEXT NOT NULL,            -- telemetry | command
    framing     TEXT NOT NULL,            -- ax25 | ccsds
    name        TEXT NOT NULL,
    spec        JSON NOT NULL,            -- field definitions, IDs, encoding
    version     INTEGER NOT NULL DEFAULT 1
);

-- A published downlink budget; synced from app/data/seed/link_profiles.json (see §14).
CREATE TABLE link_profile (
    id                       INTEGER PRIMARY KEY,
    norad_id                 INTEGER NOT NULL UNIQUE REFERENCES satellite(norad_id),
    label                    TEXT NOT NULL,        -- mode + reference station
    frequency_hz             REAL NOT NULL,
    eirp_dbw                 REAL NOT NULL,        -- where the pattern offset is 0 dB
    eirp_pattern             JSON,                 -- [[off_nadir_deg, offset_db], ...]; NULL = isotropic
    coverage_half_angle_deg  REAL,                 -- antenna coverage (attitude check)
    station_gt_db_k          REAL NOT NULL,        -- reference station G/T (replaces station_gain_dbi)
    system_loss_db           REAL NOT NULL DEFAULT 0,
    atmospheric_loss_db      REAL NOT NULL DEFAULT 0,  -- as quoted at the ref. elevation
    atmospheric_ref_elev_deg REAL NOT NULL DEFAULT 90, -- scaled by the cosecant law
    required_ebn0_db         REAL NOT NULL,
    data_rate_bps            REAL NOT NULL,        -- the rate the source's Eb/N0 refers to
    sources                  JSON NOT NULL         -- [{title, url}], https only
);

-- ========== OPERATIONAL / SESSION ==========
CREATE TABLE mission_session (
    id             INTEGER PRIMARY KEY,
    station_id     INTEGER REFERENCES ground_station(id),  -- preset it came from; NULL = custom
    location_name  TEXT NOT NULL,         -- observer location, owned by the session
    lat_deg        REAL NOT NULL,
    lon_deg        REAL NOT NULL,
    alt_m          REAL NOT NULL DEFAULT 0,
    name           TEXT,
    started_at     TEXT NOT NULL,         -- wall clock, UTC
    ended_at       TEXT,
    sim_start_time TEXT NOT NULL,         -- simulated epoch at start
    time_scale     REAL NOT NULL DEFAULT 1.0
);

-- Append-only event log: SOURCE OF TRUTH for playback.
CREATE TABLE event (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES mission_session(id),
    seq         INTEGER NOT NULL,         -- monotonic per session
    sim_time    TEXT NOT NULL,            -- simulated timestamp
    wall_time   TEXT NOT NULL,            -- real timestamp
    type        TEXT NOT NULL,            -- e.g. aos, los, cmd_sent, tlm_rx,
                                          -- alarm, health_change, sar_status
    payload     JSON NOT NULL
);
CREATE UNIQUE INDEX ix_event_session_seq ON event(session_id, seq);
CREATE INDEX ix_event_session_simtime     ON event(session_id, sim_time);
CREATE INDEX ix_event_session_type        ON event(session_id, type);

CREATE TABLE command (
    id            INTEGER PRIMARY KEY,
    session_id    INTEGER NOT NULL REFERENCES mission_session(id),
    norad_id      INTEGER NOT NULL REFERENCES satellite(norad_id),
    template_id   INTEGER REFERENCES packet_template(id),
    params        JSON NOT NULL,
    scheduled_aos TEXT,                   -- target pass AOS (sim time)
    status        TEXT NOT NULL,          -- queued|sent|failed|aborted
    created_at    TEXT NOT NULL
);
CREATE INDEX ix_command_session_status ON command(session_id, status);

CREATE TABLE telemetry_frame (
    id          INTEGER PRIMARY KEY,
    session_id  INTEGER NOT NULL REFERENCES mission_session(id),
    norad_id    INTEGER NOT NULL REFERENCES satellite(norad_id),
    sim_time    TEXT NOT NULL,
    raw         BLOB,                     -- raw frame bytes (small)
    decoded     JSON,                     -- decoded named fields
    source      TEXT NOT NULL,            -- satnogs | simulated
    crc_ok      INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX ix_tlm_session_simtime ON telemetry_frame(session_id, sim_time);

CREATE TABLE pass_window (
    id              INTEGER PRIMARY KEY,
    session_id      INTEGER REFERENCES mission_session(id),
    norad_id        INTEGER NOT NULL REFERENCES satellite(norad_id),
    station_id      INTEGER REFERENCES ground_station(id),  -- NULL for a custom location
    aos_time        TEXT NOT NULL,
    los_time        TEXT NOT NULL,
    max_elev_deg    REAL NOT NULL,
    aos_az_deg      REAL,
    los_az_deg      REAL
);
CREATE INDEX ix_pass_sat_aos ON pass_window(norad_id, aos_time);

CREATE TABLE alarm (
    id           INTEGER PRIMARY KEY,
    session_id   INTEGER NOT NULL REFERENCES mission_session(id),
    sim_time     TEXT NOT NULL,
    severity     TEXT NOT NULL,           -- info | warning | critical
    subsystem    TEXT,                    -- power|thermal|storage|attitude|rf
    message      TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0
);

-- ========== SAR ==========
CREATE TABLE sar_task (
    id              INTEGER PRIMARY KEY,
    session_id      INTEGER REFERENCES mission_session(id),
    norad_id        INTEGER REFERENCES satellite(norad_id),
    aoi_bbox        JSON NOT NULL,        -- [minlon,minlat,maxlon,maxlat]
    aoi_geojson     JSON,                 -- full polygon (PostGIS geometry @P7)
    window_start    TEXT NOT NULL,
    window_end      TEXT NOT NULL,
    predicted_acq   TEXT,                 -- from real acquisition plan
    status          TEXT NOT NULL,        -- tasked|acquiring|downlink|
                                          -- processing|waiting_data|ready|failed
    created_at      TEXT NOT NULL
);
CREATE INDEX ix_sartask_status ON sar_task(status);

CREATE TABLE sar_scene (
    id             INTEGER PRIMARY KEY,
    task_id        INTEGER REFERENCES sar_task(id),
    source         TEXT NOT NULL,         -- sentinel1 | umbra
    collection     TEXT NOT NULL,         -- STAC collection id
    stac_scene_id  TEXT NOT NULL,
    acquired_at    TEXT NOT NULL,
    footprint      JSON,                  -- GeoJSON (PostGIS geometry @P7)
    assets         JSON,                  -- asset hrefs / local paths
    local_path     TEXT                   -- downloaded raster on disk
);
CREATE INDEX ix_scene_stac ON sar_scene(stac_scene_id);

CREATE TABLE change_result (
    id             INTEGER PRIMARY KEY,
    task_id        INTEGER REFERENCES sar_task(id),
    scene_before   INTEGER NOT NULL REFERENCES sar_scene(id),
    scene_after    INTEGER NOT NULL REFERENCES sar_scene(id),
    method         TEXT NOT NULL,         -- log_ratio | unet
    heatmap_path   TEXT,                  -- raster on disk
    regions        JSON,                  -- flagged bboxes/polygons + scores
    score          REAL,
    created_at     TEXT NOT NULL
);
```

### 8.4 Event payload conventions
- `type` is a stable string; add new types freely, never repurpose old ones.
- Payloads are JSON and self-describing. Examples:
  - `aos` / `los` → `{ norad_id, az_deg, elev_deg }`
  - `health_change` → `{ subsystem, from, to, margin_db? }`
  - `cmd_sent` → `{ command_id, norad_id, template_id }`
  - `tlm_rx` → `{ norad_id, frame_id, crc_ok, source }`
  - `alarm` → `{ alarm_id, severity, subsystem, message }`
  - `sar_status` → `{ task_id, from, to }`
- Replaying a session = reading `event` for a `session_id` ordered by `seq` and re-emitting through the same renderer. Nothing else is needed for playback.

### 8.5 SQLite ↔ Postgres notes
Already handled (Phase 2b):
- Timestamps: `UTCDateTime` is ISO-8601 `TEXT` on SQLite and `TIMESTAMPTZ` on Postgres.
- Booleans: `Boolean` maps to `INTEGER 0/1` / `BOOLEAN`; upserts pick `sqlite_insert` / `pg_insert` by dialect.
- Engine: Postgres connections use `pool_pre_ping` + recycling (Neon suspends idle compute); bare `postgresql://` URLs are routed to psycopg 3.

Still to do, when the feature arrives:
- `JSON` → `JSONB` on Postgres; add GIN indexes where you query inside payloads.
- Phase 7: `aoi_geojson` / `footprint` → **PostGIS `geometry(Polygon, 4326)`**; add GiST indexes; replace bbox filtering with `ST_Intersects`.
- Phase 9 (optional): `event` / `telemetry_frame` as **TimescaleDB hypertables** if volume warrants (Neon ships only the Apache-2 TimescaleDB feature set — verify then).
- Keep the ORM dialect-agnostic; gate Postgres-only types behind the configured dialect so SQLite dev still works.

---

## 9. Phase plan (build in this order)

Each phase is a **vertical slice** (backend + WebSocket + UI) that runs on its own. Mark the current phase here as you go.

> **Current phase: _Phase 4a — Globe._** Phases 1, 2, 2b, 2c and 3 are done and deployed. Phase 4 ships in two parts: 4a (globe, cone, 2D/3D toggle), then 4b (communications).
>
> Phase numbers are for this plan and the docs only: the UI never shows "Phase N" (`frontend/src/lib/copy.test.ts` guards it).

- **Phase 1 — Walking skeleton.** TLE fetch (Celestrak) + SGP4; topocentric geometry; accelerable sim clock; WebSocket snapshot. Frontend: location (geolocation + map click + lat/lon), **full catalog search**, 2D ground track moving live, az/el/range readout. DB: `ground_station`, `satellite`, `mission_session`.
- **Phase 2 — Visibility & passes.** Visible-set (horizon mask): once a location is set, the server streams **every satellite currently above the mask by default**, with no search needed (design §5.3); search/track still work alongside it. AOS/LOS prediction (+max elevation/azimuths); polar sky plot + all-sky view; filterable list (type, min elevation, time-to-AOS, SAR-capable); selection linked across the map and both sky views. DB: `pass_window`.
- **Phase 2b — Persistent Postgres (Neon).** Pulled forward from Phase 7 (2026-10-15) ahead of the Azure deploy: a container's filesystem is ephemeral, so SQLite can't persist there. psycopg 3 driver; Postgres engine pooling for Neon's auto-suspend; Alembic on Neon (owner role, direct host); least-privilege app role (pooled host); opt-in Postgres test run. No schema, contract or UI changes; SQLite stays the local/test default. Runbook: [deploy/neon.md](deploy/neon.md).
- **Phase 2c — Containers + CI/CD (Azure).** Pulled forward from Phase 9 (2026-10-16). API and web images; PR checks on `dev → main`; merge to `main` pushes to GHCR, migrates Neon `production` and rolls out to Azure Container Apps at `https://satlab.darshitp.dev/satlab/`. Public without login, so the §12.11 gaps were closed first: per-session capability tokens, WS `Origin` check + first-message auth + rate/size limits, active-session cap, Celestrak global rate limit, generic upstream errors, `/docs` off, CSP and security headers in nginx.
- **Phase 3 — Systems & link.** Link budget (margin dB + Doppler) + subsystem models; RAG indicators + rollup bar; sky-plot/all-sky dot colors and the inner "strong-link" region driven by the same link state (design §5.4, §6). DB: `link_profile`.
- **Phase 4 — Communications**, in two parts (2026-10-22):
  - **4a — Globe.** **3D globe (Cesium) + the visibility "V" cone** (design §5.3–5.4): outer cone = above the horizon mask, inner cone = green-margin region; satellites enter at one rim (AOS) and leave at the far rim (LOS), changing color at cone boundaries; the full orbital arc is drawn with the out-of-window part faded; the user can lock onto one active contact while the others dim. The globe joins the 2D map rather than replacing it: a **2D/3D toggle, 3D by default**.
  - **4b — Comms.** Per-satellite AX.25/CCSDS templates; command uplink (sim) + telemetry downlink; SatNOGS real-frame replay; packet animation; channel effects. DB: `packet_template`, `command`, `telemetry_frame`.
- **Phase 5 — Scheduling & alarms.** Queue command for a future window; AOS trigger; auto system check; transmit-if-green / alarm-if-red. DB: `alarm` (+ `command.scheduled_aos`).
- **Phase 6 — Record & playback.** Persist full `event` log; timeline scrubber (play/pause/speed/jump); playback reuses the live renderer.
- **Phase 7 — SAR archive explore (Flow 1).** STAC search (Sentinel-1 RTC / Umbra) for AOI + two dates; swipe compare; v1 log-ratio change detection; flagged regions. **Enable PostGIS on the existing Neon DB** (geometry columns + GiST). DB: `sar_scene`, `change_result`.
- **Phase 8 — SAR mission tasking (Flow 2).** Simulated tasking over the real acquisition plan; sim states tasked→acquiring→downlink→processing; STAC polling; "waiting for real data" gate; unlock compare when the real scene lands. DB: `sar_task`.
- **Phase 9 — Polish.** Cesium footprints, day/night terminator, naked-eye model, msgpack, TimescaleDB (Apache-2 feature set on Neon), cloud deploy, docker-compose parity.

---

## 10. Data sources (verify endpoints/collections at build time)

| Source | Use | Notes |
|---|---|---|
| Celestrak | TLEs by catalog number | Primary orbital data. |
| Space-Track | Authoritative TLEs | Needs account → `.env`. |
| SatNOGS (Network/DB API) | Real decoded telemetry; real stations | Archived observations, not a live station feed. |
| Copernicus / Sentinel-1 | Real C-band SAR | Final constellation Sentinel-1C+1D (2026), ~6-day revisit. |
| Microsoft Planetary Computer | STAC: `sentinel-1-rtc`, `sentinel-1-grd` | RTC = analysis-ready; **assets require `planetary_computer.sign`**. |
| Umbra Open Data (AWS) | Very high-res spotlight SAR time series | STAC catalog; great for change demos. |

Sentinel-1 publishes its acquisition plan as KML — use it to compute the **real** next overpass for the Phase 8 tasking gate.

---

## 11. Testing & quality

- Unit-test `sim/geometry.py` and `sim/linkbudget.py` against **known passes** (compare az/el to a reference like skyfield examples or published pass predictions).
- Snapshot-test RAG thresholds (margin → color).
- Integration test: write an `event` log, replay it, assert the reconstructed state matches.
- Keep `sim/` functions pure where possible so they're testable without a DB or network.
- Never commit a failing `ruff`/`pytest`/`vitest` state as "done."

---

## 12. Security (secure-by-default coding rules)

There is no separate vulnerability-scanning step in this project's workflow. Security comes from writing code that follows these rules and from reviewing every change against them. When a rule and a convenience conflict, the rule wins; if a rule genuinely blocks the task, stop and ask rather than quietly weakening it.

**Threat model, briefly.** Today this is a local-first, single-user app on `127.0.0.1`. Phase 9 adds a cloud deploy. The realistic risks are:
- a malicious web page talking to the local backend;
- hostile or malformed bytes from external data sources (TLE text, KML, STAC JSON, SAR rasters, SatNOGS frames);
- secrets leaking into git, logs or the frontend bundle;
- compromised dependencies.

Write code as if the Phase 9 deploy already exists.

### 12.1 Input validation (backend)
- **Every input crosses a pydantic model.** REST bodies and query params, WebSocket messages and event payloads are all parsed by pydantic before use. Never `json.loads` a client message and index into the dict. Put bounds in the schema (`Field(ge=…, le=…, max_length=…)`), not in ad-hoc `if` checks scattered through handlers.
- **Bound every collection and string** a client can send: list lengths (e.g. `norad_ids`), string lengths, numeric ranges (lat/lon, `time_scale`, limits). Unbounded input becomes a DoS: a huge list fans out into DB or network work.
- **Reject unknown message types**; don't silently ignore them. The discriminated unions in `app/models/ws.py` already do this. Keep it that way.
- **Fail closed with generic messages.** Errors sent to the client say *what* was wrong ("invalid message", "satellite not found"), never stack traces, SQL, file paths or raw upstream exception text. Log the details server-side instead.

### 12.2 Database
- **SQLAlchemy expressions only, always bound parameters.** No f-strings, `%` or `+` into SQL, and no `text()` with interpolated values. For `LIKE`/`contains`/`startswith` on user input, pass `autoescape=True`, as `search_local` does.
- **Migrations only via Alembic**, reviewed like code. Never run DDL from request handlers.
- **Postgres (since Phase 2b):** the app connects as a least-privilege role (`satlab_app`: DML on its tables only); migrations run as the owner role (`neondb_owner`) in a separate release step, and the app process never receives the owner URL. Credentials come from `.env` or the platform secret store, never `alembic.ini` or code.

### 12.3 WebSocket and HTTP surface
- **Check the `Origin` header on WebSocket upgrade** against the configured allow-list (`SATLAB_CORS_ORIGINS`). CORS does **not** protect WebSockets, and any web page can open a socket to `localhost`.
- **Cap message size and rate per connection.** Clients get a sensible max frame size and a per-connection command rate. A client that floods is disconnected, not queued.
- **CORS stays an explicit allow-list.** Never `allow_origins=["*"]` together with credentials, and never reflect the request origin.
- **Bind to `127.0.0.1` in development** (uvicorn's and Vite's defaults). `--host 0.0.0.0` only inside the API container, whose ingress is internal (reachable from the web container, not the internet).
- **Resource IDs aren't secrets.** Session and station IDs are sequential integers. Every session-scoped read or write checks the session's capability token (`X-Session-Token` on REST, the `auth` message on the WebSocket), and an unknown session and a wrong token get the same answer. Stations are read-only presets. Don't rely on IDs being hard to guess.
- **Throttle anything that fans out to a third party.** Remote Celestrak searches, SatNOGS and STAC queries go through a cache plus a global rate limit, so a client can't make our server abuse an upstream API (or get our IP banned).

### 12.4 External data is hostile input
- **Parse defensively, never execute.** TLE/OMM text, STAC JSON, SatNOGS frames and acquisition-plan KML are data. Validate structure (checksums, required fields, ranges) and skip bad records, as `parse_tle_text` does; don't crash the ingest on them.
- **XML (Sentinel-1 KML, Phase 8) is parsed with `defusedxml`**, never the stdlib `xml.etree`/`minidom` directly. That blocks entity-expansion and XXE attacks.
- **Network fetches have timeouts, size caps and an allow-list.** Use one shared `httpx.AsyncClient` with timeouts. Only fetch from configured hosts (Celestrak, SatNOGS, STAC/Planetary Computer, Umbra/AWS). STAC asset `href`s must match the allowed hosts before download; that's the SSRF guard. Stream large downloads to disk with a byte cap; never read an unbounded body into memory.
- **Binary decoders bounds-check every length field** (AX.25/CCSDS in Phase 4). A declared length is checked against the bytes actually present and a protocol maximum before slicing or looping.
- **Rasters (Phase 7+):** open with rasterio/GDAL only from our own storage directory. Check dimensions and band count against limits before reading pixels, and read windows rather than whole scenes.
- **Never unpickle untrusted data:** `numpy.load(..., allow_pickle=False)`; `torch.load(..., weights_only=True)`, and only for model files we produced. No `eval`/`exec`/`yaml.load` (use `yaml.safe_load`) on anything.

### 12.5 Files and paths
- **Never build a filesystem path from client input.** Raster/heatmap paths are generated server-side (IDs plus fixed directories). Before any read or write, resolve the path and assert it's inside the configured storage root (`path.resolve().is_relative_to(root)`).
- Downloaded and generated files go under `backend/var/` (or the configured storage root), never next to code.
- No `subprocess` with `shell=True`. If a subprocess is ever needed, pass an argument list built from validated values.

### 12.6 Secrets and logging
- **Secrets live only in `.env`** (gitignored) and are read through `app/config.py`. Each one gets a placeholder in `.env.example`, never a real value. If a secret is ever committed, rotate it; deleting the commit is not enough.
- **`VITE_*` variables are public.** Vite inlines them into the browser bundle. Never put a token, password or signed URL in a `VITE_` variable; anything secret stays server-side and the backend proxies or signs on demand.
- **Never log secrets or signed URLs** (Space-Track credentials, Planetary Computer SAS tokens, `Authorization` headers). Log identifiers and outcomes, not full payloads.
- Short-lived credentials (signed asset URLs) stay in memory, are never persisted to the DB, and are re-fetched when they expire.

### 12.7 Frontend
- **Let React escape.** Never use `dangerouslySetInnerHTML`, `innerHTML`, `eval` or `new Function`. Satellite names, station names and server error text are rendered as text nodes, so they can't run as HTML or script.
- **Treat server messages as untrusted input too.** Only render fields that the `src/types/` contract defines; don't spread unknown fields into props or DOM attributes.
- **External links** use `rel="noopener noreferrer"` with `target="_blank"`, and only `https:` URLs. Never build an `href` from data without checking the scheme (no `javascript:` URLs).
- **`localStorage` holds preferences only** (theme, last station, tracked list): nothing secret, nothing the server must trust. Every read is wrapped in `try/catch` and validated, as `src/lib/theme.ts` does.
- **CSP:** no new inline scripts or inline event-handler attributes. The single inline pre-paint theme script in `index.html` is the documented exception: the web image's build hashes it into the nginx `Content-Security-Policy` (`frontend/scripts/csp-script-hashes.mjs`, `frontend/nginx/security-headers.conf`), so the hash can't go stale. A new external host (tiles, analytics) must be added to that CSP too; `cloud.umami.is` (script) and `gateway.umami.is` (events) are there for analytics (§12.8). WebAssembly stays blocked (no `'wasm-unsafe-eval'`): the globe's Cesium workers and imagery come from our own origin, and the one Cesium dependency that compiles wasm on import (meshoptimizer) is stubbed out of the bundle (§14, Phase 4a).

### 12.8 Dependencies (no extra tooling, just habits)
- **Lockfiles are the source of truth and are committed** (`backend/uv.lock`, `frontend/pnpm-lock.yaml`). Install with `uv sync` / `pnpm install`; never `pip install` into the venv by hand.
- **Add a dependency only when it earns its place.** Prefer the standard library or something already in the stack. Choose packages that are actively maintained and widely used, and check the exact name before installing (typosquats look like real names).
- **pnpm 10 doesn't run dependency install scripts by default.** Allow one (`onlyBuiltDependencies`) only deliberately, for a known package that needs it.
- **Upgrade deliberately:** bump versions in a dedicated change and rerun the full test suite. Don't leave the code pinned to versions with known security fixes available.
- **No CDN `<script>` tags.** Everything the frontend runs is bundled from the lockfile. The one documented exception is the Umami Cloud tracker (`https://cloud.umami.is/script.js`, `defer`): a fixed host in `vite.config.ts`, injected only into production builds and only when `NEXT_UMAMI_ID` is set (validated as a UUID). Don't add others.

### 12.9 Deploy-time (in force since Phase 2c)
- **Public without login is accepted for now** (2026-10-16), because a visitor can only drive their own simulated session: per-session capability tokens, the WS `Origin` check, per-connection rate and size limits, a cap on concurrent sessions (`SATLAB_MAX_ACTIVE_SESSIONS`), a global Celestrak budget and nginx per-IP limits bound what one client can do. **Authentication is required before any feature that writes shared state, stores user data or needs per-user ownership** (accounts, saved missions, SAR tasking quotas).
- TLS everywhere (`wss://`, `https://`): ACA terminates TLS with a managed certificate; the API is internal-only.
- Security headers (CSP, `X-Content-Type-Options`, `Referrer-Policy`, `frame-ancestors`) come from nginx; FastAPI's `/docs`, `/redoc` and `/openapi.json` are off in production (`SATLAB_DOCS_ENABLED=false`).
- Containers run as a non-root user with no secrets baked into images; secrets come from the platform's secret store (ACA secrets, GitHub environment secrets).

### 12.10 Safety boundary (non-negotiable)
- **This software never transmits RF.** Uplink is simulated (§1). Never add code that keys a real transmitter or SDR, or sends commands to a real spacecraft or ground-station network: that is illegal without a licence and authorisation. Reading public data (SatNOGS archives, STAC) is fine; writing to those services is not part of this project.

### 12.11 Known gaps (current code vs these rules)
None open: the gaps listed here were closed before the Phase 2c public deploy (2026-10-16). Record new ones as they're found, and close them when touching that code.

When you close a gap, delete its line here.

---

## 13. When unsure
- Prefer the smallest change that keeps the current phase end-to-end runnable.
- If a decision affects the schema or the WS contract, update the pydantic models + TS types + an Alembic migration together, and note it here.
- Don't invent satellite link parameters silently — pull from public sources and record them in `link_profile` seed data.
- If a task seems to require breaking a §12 security rule, stop and ask instead of working around it.

---

## 14. Decision log (schema / contract)

- **Phase 1 — WebSocket contract** (`backend/app/models/ws.py` ↔ `frontend/src/types/ws.ts`). One socket per mission session at `/ws/sessions/{id}`. Server → client: `snapshot` (every tick: clock, observer, tracked `SatelliteState`s), `ground_track` (sub-point samples evenly spaced in time over −0.5…+1 orbit, re-sent when the clock leaves the window), `error`. Client → server: `set_tracked` (full replacement set), `set_clock` (`time_scale` / `paused` / `sim_time`). Close code `4404` = session ended/unknown → client starts a new session. Units in field names (`range_m`, `range_rate_m_s`, `alt_m`, `*_deg`).
- **Client tweening**: map markers interpolate along the server-sampled `ground_track` at the estimated sim time (last `sim_time` + elapsed × `time_scale`); readouts show raw snapshot values. No orbital math in the frontend.
- **Timestamps**: `UTCDateTime` column type (`app/db/base.py`) stores ISO-8601 TEXT on SQLite and switches to `TIMESTAMPTZ` on Postgres — no model changes for time columns across dialects.
- **Catalog**: Celestrak `GROUP=active` bulk-loaded into `satellite` (cached in `backend/var/cache/` and refreshed when > 12 h old); "full catalog" search hits Celestrak by `NAME`/`CATNR` only on explicit request. The sim loop reads only an in-memory elements registry — never the DB or network.
- **Session-scoped observer location** (2026-10-15). `ground_station` holds only seeded presets; there is no API to create one. A session observes from its own `location_name` / `lat_deg` / `lon_deg` / `alt_m`, copied from a preset (`station_id` set) or given as a custom `location` (geolocation, map click, typed lat/lon; `station_id` NULL, name "Custom location"). `POST /api/sessions` takes exactly one of `station_id` or `location`; `MissionSessionOut` drops the nested `station` for those flat fields; `ObserverState.station_id` and `PassPrediction.station_id` are nullable. The client remembers the last site (preset id or custom location) in `localStorage` and starts a new session there on reload.
- **Session clock** is in-memory; after a backend restart a session resumes at `sim_start_time + elapsed × initial time_scale` (approximate until the Phase 6 event log makes it exact). Sessions end after 60 s with no subscribers.
- **Phase 1 — UI design system**: the frontend follows `design-system.md` ("Warm Notebook", from the author's site). Tailwind v4 with tokens pinned to the system's v3 hexes and all other palettes removed; light/dark/system theme; the map reads its colors from the same tokens and swaps basemaps per theme.
- **Cesium globe + visibility "V" cone → Phase 4** (2026-10-10; resolves design doc open question #5). Until then the visible set is shown on the 2D map, polar sky plot and all-sky view (Phase 2). **2D/3D toggle, 3D by default** (2026-10-22): the map panel switches between the Cesium globe and the existing MapLibre ground-track map, which stays as the 2D option and takes over if the globe can't start (its chunk fails to load, Cesium throws, or the browser has no WebGL; MapLibre needs WebGL2 itself, so a browser with no WebGL at all gets neither). The choice is a `localStorage` preference (`satlab.mapMode`). Both render the same streamed state, selection and filters, so switching never changes what's shown.
- **Phase 4a — globe** (2026-10-22).
  - **Contract:** `GroundTrackMessage` gains `alt_m` and `above_mask` per point (both optional, default null). `ground_track()` moved to `sim/geometry.py` and now samples through the tick's vectorized chain (`observe` + `geodetic`) from the session's station, so the in-view flags are the same elevation test as everything else.
  - **Cesium:** `@cesium/engine` `CesiumWidget` only (no Viewer, so no Knockout and no `unsafe-eval`), loaded as a lazy chunk. Core classes (`Cartesian3`, `Matrix4`, geometries, `FixedFrameTransforms`) are imported from `@cesium/core`; the engine's re-exports of them are deprecated proxies that log and slow every access. Imagery is the bundled Natural Earth II on the ellipsoid: no Cesium ion, no token, no new CSP host. No sky box, so no Sun or Moon (they'd follow the wall clock, not the sim clock).
  - **Assets:** a Vite plugin serves `@cesium/engine/Build/{Workers,Assets}` at `/satlab/cesium/` in dev and copies them into `dist/cesium/` (ThirdParty's wasm decoders are left out). nginx serves that path with a 404 for missing files, never the SPA page; `scripts/smoke.sh` checks a worker's MIME type and the imagery.
  - **meshoptimizer is aliased to a stub** (`src/views/Globe/meshoptStub.ts`): its index compiles WebAssembly on import, which the CSP refuses, and Cesium uses it only for meshopt-compressed glTF and 3D-tiles terrain, neither of which the globe loads. Revisit together with the CSP if a globe feature ever needs those formats.
  - **Cones are presentation:** built on the client (`views/Globe/cone.ts`) from server angles (the mask, the selection's `strong_link_elev_bands_deg`) and the selection's streamed altitude, ending on its altitude shell (with nothing selected, a 1000 km reference shell). No state is decided from them; dot colours are the server's link RAG, as in the sky views. Wireframe only (rim + 8 lines from the station): a filled cone big enough to hold the camera, as a GEO one is, tints the whole view. A strong-link band that starts at the mask replaces the mask cone (same surface).
- **Phase 2 — one vectorized engine** (2026-10-11). The tick propagates the whole catalog at once (`sgp4.api.SatrecArray`; TEME → Earth-fixed by GMST 1982, as skyfield does) — ~20 ms for ~18k objects — and every streamed state (visible, tracked, selected) comes from that path. skyfield's per-satellite `look_angles`/`sub_point` stay as the test reference (agreement ~1e-6°, < 1 cm; `tests/test_vector_geometry.py`). `CatalogService.fleet` (`app/sim/fleet.py`) is rebuilt from the DB after each ingest and swapped in with one assignment; the loop reads only it and the elements registry. **sgp4's C extension holds the GIL**: anything bigger than one epoch across the catalog is sliced into per-tick chunks.
- **Phase 2 — WebSocket contract.** Snapshot streams (above the mask ∩ session filter) ∪ tracked ∪ selected; adds `visible_count`, `observer.horizon_mask_deg`, and per satellite `type`, `is_sar_capable`, `above_mask`. New server messages: `pass_arc` (the current/next pass as evenly time-spaced (az, el) samples clipped to [now − 30 min, now + 60 min]; sky views tween along it exactly like the map does along `ground_track`) and `upcoming` (rising-soon list, server-filtered, soonest `upcoming_limit`, with `total` and `status` computing/ready/paused). New client messages: `set_filter` (types, `sar_only`, `min_elevation_deg` — effective max(mask, value), also applied to rising passes' max elevation — and `rising_within_min` ≤ 60) and `set_selected` (the selection is always streamed with a ground track). **Filtering is server-side** (golden rule 3), so all views agree. Arc/track/upcoming messages precede the snapshot that uses them in each batch.
- **Pass prediction** (`app/sim/passes.py`): one algorithm (coarse elevation grid → runs above the mask + near-mask local maxima for short grazes → golden-section culmination + bisected AOS/LOS, batched as (satellite, instant) pairs) serves the schedule endpoint (`GET /api/sessions/{id}/passes`, persisted to `pass_window`, replaced per session/satellite/window), the sky arcs and the rising-soon sweep. Checked against skyfield `find_events` (AOS/LOS < 1 s, max elevation < 0.01°). The rising-soon sweep covers the next 90 min of the whole catalog in chunks (adaptive: full size while nothing is shown, otherwise just enough to finish before the current result expires) and **pauses above 60×**, where it can't keep up — the UI says so instead of showing a stale list.
- **Horizon mask**: one global setting `SATLAB_HORIZON_MASK_DEG` (default 5°) for every station; the UI's min-elevation filter raises it per view. A per-station mask is a later schema change if needed.
- **Satellite types**: `station | weather | comms | navigation | sar | other`, from ordered name-pattern rules in `app/data/seed/satellite_types.json` (debris/rocket bodies first; traps such as SENTINEL-2 ≠ SAR, SARAL ≠ SAR, GSAT0xxx = Galileo vs GSAT-xx = ISRO comms are tested). Applied on every upsert and re-applied to the whole catalog at startup, so editing the rules reclassifies existing rows.
- **Sky views are Canvas 2D without D3**: the projection is a few lines (`frontend/src/lib/sky.ts`); D3 joins when a view earns it.
- **Base path `/satlab/` + Umami analytics** (2026-10-14). The app is published at `https://<domain>/satlab/` next to the author's site and reports to the same Umami website, so pageviews land as `/satlab` and `/satlab/<page>`. Vite `base` is `/satlab/`; every same-origin URL (REST, WebSocket) goes through `appUrl()` (`frontend/src/lib/basePath.ts`); the dev proxy (and the production reverse proxy) strip the prefix, so the backend routes stay at `/api` and `/ws`. The Umami website ID comes from `NEXT_UMAMI_ID` (read only in `vite.config.ts`; `envPrefix` stays `VITE_`), is public by design, and the tracker is injected at build time only, so `pnpm dev` isn't counted. Future client-side routes are tracked automatically (Umami follows `history.pushState`).
- **Phase 2b — Postgres on Neon, pulled forward from Phase 7** (2026-10-15). Reason: the Azure deploy needs a database that survives container restarts, and the code was already dialect-ready. Neon in every stage, one branch each: `dev` (local `.env`), `test` (`.env.test`), `production` (2026-10-16); SQLite is only the fallback when no URL is configured. Two URLs: `SATLAB_DATABASE_URL` (app, `satlab_app` role, Neon pooled host) and `SATLAB_MIGRATION_DATABASE_URL` (Alembic only, owner role, direct host; falls back to the app URL). Bare `postgres(ql)://` URLs are normalized to `postgresql+psycopg://`; Alembic escapes `%` for configparser. `SATLAB_TEST_POSTGRES_URL` (shell or `.env.test`, never `.env`, so dev credentials can't drive the tests) runs the whole suite on Postgres with one throwaway schema per test via `search_path`. No schema or contract changes; existing SQLite data isn't migrated (the catalog re-ingests, stations re-seed).
- **Phase 2c — container deploy on Azure Container Apps** (2026-10-16). Two images: `satlab-web` (nginx-unprivileged serving the Vite build at `/satlab/`, `/` → 302 `/satlab/`, and proxying `/satlab/api` → `/api` and `/satlab/ws` → `/ws` with WebSocket upgrade; `SATLAB_API_UPSTREAM`, default `http://ca-satlab-api`) and `satlab-api` (uvicorn, **one worker, exactly one replica**: sessions, the sim loop and the fleet are in-memory, so a second replica would split WebSocket sessions; scaling out needs shared session state first). Both run in the existing `cae-portfolio` environment next to the portfolio site; only the web app has external ingress (`satlab.darshitp.dev`, the URL the portfolio already links to; the `/satlab/` base path is kept, so Umami paths are unchanged). Images live in GHCR, deployed by SHA tag. Migrations are a CI release step before rollout (owner URL only in the GitHub `production` environment), so every revision must be expand/contract-compatible with the code still running.
- **Session capability tokens + WS auth** (2026-10-16). `POST /api/sessions` returns `MissionSessionCreated` = `MissionSessionOut` + `token` (`secrets.token_urlsafe(32)`), once; the DB keeps only `mission_session.token_hash` (SHA-256 hex, nullable; older sessions without one can't be attached and clients start a new session). `GET /api/sessions/{id}`, `POST …/end` and `GET …/passes` require `X-Session-Token`; missing/wrong token = the same 404 as an unknown session. WebSocket: the first client message must be `auth {token}` within `SATLAB_WS_AUTH_TIMEOUT_S` (default 5 s), sent as a message so the token never appears in URLs or proxy logs. Close codes: `4401` no valid `auth` (client treats it like 4404), `4404` unknown/ended/not-yours, `4503` at `SATLAB_MAX_ACTIVE_SESSIONS` (client keeps its backoff and shows "at capacity" once), `1008` foreign `Origin` (refused before accept, an HTTP 403) or over the per-connection message rate (`SATLAB_WS_MESSAGES_PER_S` / `SATLAB_WS_MESSAGE_BURST`). Frames are capped at 64 KiB (`uvicorn --ws-max-size`). The frontend keeps the token in memory only (the store's `session`, which isn't persisted).
- **Phase 3 — link budget from published sources only** (2026-10-21). `link_profile` rows are synced from `backend/app/data/seed/link_profiles.json` inside `CatalogService.rebuild_fleet()` (startup and every ingest), only for satellites already in `satellite`, so the FK always holds without placeholder rows; the seed is authoritative (rows it drops are deleted) and an invalid seed is logged and skipped. Every value is quoted or derived (formula in `derivations`) from the cited source, and each source's own worked margin is a `checks` entry reproduced to 0.1 dB in `tests/test_linkbudget.py`. Deviations from the original DDL: `station_gt_db_k` (G/T, what budgets quote) replaces `station_gain_dbi`; `eirp_pattern` (isoflux antennas: EIRP by off-nadir angle, nadir pointing assumed), `atmospheric_loss_db` at `atmospheric_ref_elev_deg` scaled by the cosecant law, `coverage_half_angle_deg`, `label`, `sources`. Shipped: NOAA 20, NOAA 21 (X-band HRD ICDs), FengYun-3D (MPT ICD; its source holds losses constant, so they stay in `system_loss_db`), GOES 18/19 (GRB budget). Excluded: NOAA-15/18/19 (decommissioned 2025), analog FM (no Eb/N0), anything without a published EIRP (Metop AHRPT, Meteor LRPT, amateur cubesats). The fleet carries a `LinkTable` aligned with its rows; `LinkTable.evaluate` is the one computation behind snapshot links, arc colours, the link filter and RF health.
- **Phase 3 — contract** (2026-10-21). `SatelliteState.link: LinkState | null` (`frequency_hz`, `data_rate_bps`, `ebn0_db`/`margin_db` null below the horizon, `doppler_hz` = received − transmitted, `rag`, `strong_link_elev_bands_deg` = elevation bands where the margin is green at the current altitude). `PassArcMessage.rag` (link state per point). `Snapshot.health: HealthState | null` for the selected satellite only: `power` (`soc_pct`, `sunlit_fraction`, `illumination`), `thermal` (`temperature_c`, band), `storage` (`fill_pct`, `activity`; null without a profile), `attitude` (`off_nadir_deg` vs `half_beamwidth_deg`; null below the mask), `rf` (= the link), each with `rag` and `modelled`, plus `rollup` (red if any red, else null if any lacks data, else worse of amber/green) and `model_version`. `SetFilter.link_ok_only` (profiled satellites only; in view, margin ≥ amber). `GET /api/link-profiles` (thresholds + profiles with sources). Every new field has a default and the TS mirror marks them optional, so web/API version skew degrades gracefully.
- **Phase 3 — subsystem health is deterministic in sim time** (2026-10-21). `sim/subsystems.py` integrates from a fixed initial state (full battery, equilibrium temperature, full buffer) over the last `SATLAB_SUBSYSTEM_LOOKBACK_H` (24) hours on an absolute `SATLAB_SUBSYSTEM_STEP_S` (60 s) grid, so a clock jump or a replay gives the same values; grid inputs (sunlit fraction, downlink possible) are cached and extended incrementally (~4 ms/tick). Drivers are real: the Astronomical Almanac low-precision Sun (0.01°) with a conical umbra/penumbra (`sim/sun.py`, no ephemeris download), the pass geometry and the link margin. Parameters are generic and labelled modelled (`seed/subsystem_models.json`): the battery is sized for its own orbit's longest eclipse; storage knows only this station's contacts (a satellite unseen for a day reads full); attitude assumes nadir pointing. Not written to an event log yet (Phase 6); `health_change` events come then.
- **Phase 3 — RAG colours** (2026-10-21). Green reuses the emerald primary (700 light / 400 dark); amber is a new functional pair amber-700 / amber-400 (amber-600 stays reserved for the greeting); red reuses red-700 / red-400. In the sky views a dot's fill is its link state (grey = no RF data) and the selection is marked by shape (ink ring, larger dot, ink label); the selected pass is coloured by `arc.rag` over a faint green strong-link band. Colour is never the only cue: RAG icons and words (Go / Marginal / No-go / No data) go with it.
