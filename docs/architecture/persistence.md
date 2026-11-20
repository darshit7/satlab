# Persistence

## What it is

One relational database holds the reference data and the record of what happened in each session:
commands, telemetry frames, pass windows and alarms. It is SQLite in local development and tests,
and PostgreSQL on Neon in every deployed stage. The SQLAlchemy models in {py:mod}`app.db.models` are
the source of truth for the schema; every change to it is an Alembic migration. The
[database reference](../reference/database.md) is generated from those models, with an ER diagram
and the migration history.

## Why it works this way

- **One schema, two dialects.** SQLite needs no setup, so a fresh clone runs and the tests are
  fast. Postgres survives container restarts. The models stay dialect-neutral. Timestamps use
  {py:class}`~app.db.base.UTCDateTime`: ISO-8601 text on SQLite, `TIMESTAMPTZ` on Postgres, always
  aware UTC in Python. Upserts pick the dialect's `INSERT … ON CONFLICT`. An opt-in Postgres test
  run checks every change on both.
- **The operational tables are the record.** There is no separate event log. Each row is stamped
  with its sim time (and wall time where it matters), so the tables say what happened.
- **Least privilege.** In production the app connects as a role that can only read and write rows,
  on Neon's pooled host. Migrations run as the schema owner, on the direct host, as a separate
  release step. The app process never receives the owner's credentials.
- **Expand/contract migrations.** Migrations run before the new code rolls out and are never
  rolled back automatically. So every migration must work with the code still running: add a
  nullable column, ship code that uses it, and only later drop what the old code needed.
- **Never block the loop on the database.** The tick hands frames, command statuses and alarms to
  {py:class}`~app.services.comms.CommsWriter`. Its bounded queue is drained in batches on a worker
  thread.

## How it flows

```{mermaid}
flowchart TB
  subgraph Tick["Session tick (no I/O)"]
    CR[CommsRuntime] --> Q
    PA[PassAutomation] --> Q
  end
  Q[[bounded queue<br/>offer: never blocks]] --> WK[CommsWriter worker<br/>batches of 200 on a thread]
  WK --> DB[(command · telemetry_frame · alarm)]
  REST[REST: create session,<br/>pass schedule] --> DB2[(mission_session · pass_window)]
  CAT[CatalogService] --> DB3[(satellite · link_profile ·<br/>packet_template)]
  PURGE[hourly purge] -. sessions ended > retention .-> DB
```

- **Reference and config.** `ground_station` is seeded and read-only. `satellite` is upserted
  from Celestrak. `link_profile` and `packet_template` are synced from the seed files on every
  catalog rebuild.
- **Session.** `mission_session` is created by the REST API, carries the observer location and the
  token hash, and gets `ended_at` when it ends. `pass_window` is replaced per satellite and window
  each time the pass schedule is requested.
- **Comms and automation.**
  - `command` rows are written as `queued` (or `scheduled` for a booked pass) by the session
    manager, after its checks.
  - Later statuses, the system-check result, frames and alarms go through the writer.
  - Past `SATLAB_MAX_TELEMETRY_FRAMES_PER_SESSION`, frames still stream to the client but aren't
    stored.
  - Once an hour, the comms rows of sessions that ended more than `SATLAB_COMMS_RETENTION_DAYS` ago
    are purged.
- **After a restart** the database is how a session gets its booked commands and alarms back
  ([session lifecycle](session-lifecycle.md)).

`JSON` columns hold flexible payloads: decoded telemetry, packet template specs, check results,
SatNOGS provenance. Future SAR rasters will live on disk or in object storage, with only their paths
and metadata in the database.

## Configuring it

| Setting | Used by | Notes |
| ------- | ------- | ----- |
| `SATLAB_DATABASE_URL` | the app | default: SQLite in `backend/var/`; in production the app role, pooled host |
| `SATLAB_MIGRATION_DATABASE_URL` | Alembic only | the schema owner, direct host; defaults to the app URL |
| `SATLAB_AUTO_MIGRATE` | startup | apply migrations on start (dev); `false` in production |
| `SATLAB_TEST_POSTGRES_URL` | pytest only | runs the whole suite on Postgres, one throwaway schema per test |

Bare `postgres://` URLs (as Neon hands them out) are routed to the psycopg 3 driver, and Postgres
connections are pre-pinged and recycled because Neon suspends idle compute. See
{py:func}`app.config.normalize_database_url` and {py:func}`app.db.base.get_engine`.

## Where in the code

- {py:mod}`app.db.base` (engine, sessions, `UTCDateTime`), {py:mod}`app.db.models` (the schema).
- `backend/migrations/` (Alembic) and [add a migration](../how-to/add-migration.md).
- {py:mod}`app.services.comms` (the writer and the purge).
