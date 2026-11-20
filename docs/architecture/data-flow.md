# Data flow

## What it is

Three kinds of data feed the simulation:

- **Orbital elements** from Celestrak.
- **Real telemetry frames** from the SatNOGS DB.
- **Versioned seed files** in the repo: stations, link profiles, packet templates, type rules and
  subsystem models.

None of it is read by the tick directly. Background services fetch, validate and cache it, then
build immutable in-memory objects that the tick reads with plain lookups.

## Why it works this way

- **Never block the loop.** Celestrak and SatNOGS are slow, rate-limited and sometimes down. Every
  fetch runs in a background task with a timeout, a size cap and a cache, and the loop keeps
  running on the last good copy.
- **Respect the upstreams.** Celestrak asks clients not to re-download a group more often than
  every ~2 hours, and SatNOGS answers 429 after about 20 quick requests. Group refreshes are
  throttled, remote searches share one global budget across all clients, and SatNOGS refreshes
  share another.
- **Everything external is hostile input.** TLEs are checksummed and parsed defensively (a bad
  record is skipped, not fatal). SatNOGS responses are streamed with a 2 MB cap, pagination only
  follows links to the same endpoint, and redirects are never followed. Frames are kept only if they
  decode cleanly with a template.
- **Cited, versioned reference data.** Seed files are part of the code: validated strictly when
  loaded (unknown keys are errors) and synced to the database on every catalog rebuild. A value that
  drives the physics cites its source. The [seed data reference](../reference/seed-data.md) is
  generated from them.

## How it flows

```{mermaid}
flowchart TB
  subgraph bg["Background tasks (asyncio + threads)"]
    C1[Celestrak GROUP=active<br/>every 30 min check,<br/>refetch if > 12 h old] --> C2[cache file<br/>backend/var/cache]
    C2 --> C3[upsert satellite rows<br/>classify by type rules]
    C3 --> C4[sync link_profile and<br/>packet_template from seeds]
    C4 --> C5[Fleet.build: SatrecArray,<br/>LinkTable, CommsTable]
    S1[SatNOGS DB per replayed satellite<br/>every 6 h, global budget] --> S2[decode with templates,<br/>keep clean frames]
    S2 --> S3[disk cache + in-memory frames]
  end
  C5 -- one assignment --> F[(catalog.fleet)]
  S3 --> A[(archive frames)]
  R[(registry: elements<br/>of tracked satellites)]
  F --> T[tick]
  A --> T
  R --> T
```

**Catalog ({py:class}`~app.services.catalog.CatalogService`)**

1. At startup the fleet is built from whatever the database holds (instant, and it works offline).
2. Every `SATLAB_CATALOG_REFRESH_CHECK_MINUTES` the service checks the cached group's age. When it
   is older than `SATLAB_CATALOG_MAX_AGE_HOURS`, it fetches `GROUP=active` in TLE format, writes the
   cache file, and upserts the `satellite` table in chunks. Each upsert is classified by the
   ordered type rules ({py:func}`app.data.classify.classify`).
3. {py:meth}`~app.services.catalog.CatalogService.rebuild_fleet` syncs the link profiles and
   packet templates from their seed files (only for satellites already catalogued, so foreign keys
   always hold), then builds a new {py:class}`~app.sim.fleet.Fleet` on a thread. The fleet holds
   parallel arrays of every satellite, one `SatrecArray`, and the link profiles and packet
   templates aligned with its rows. It is swapped in with one assignment.
4. Tracking or selecting a satellite resolves its elements into the
   {py:class}`~app.services.catalog.Registry`. That refreshes a stale TLE from Celestrak by catalog
   number first, which is why a newly tracked satellite can take a few seconds to appear.

**Remote search.** Only on explicit request (and for 3+ characters or a number), the service asks
Celestrak by name or catalog number. It skips a query it already made in the last 2 hours and
shares one global per-minute budget across every client. Results are ingested like the group.

**SatNOGS ({py:class}`~app.services.satnogs.SatnogsArchive`)**, for the satellites whose packet
templates replay real frames:

- It fetches each satellite's recent frames at most every `SATLAB_SATNOGS_REFRESH_HOURS`, within
  `SATLAB_SATNOGS_REQUESTS_PER_MIN` for all satellites together. A 429 makes it back off for the
  `Retry-After` the server sends, clamped between 1 minute and 6 hours.
- Each frame is decoded against the satellite's templates, and only clean ones are kept, so a
  replayed frame is always identified.
- The raw frames are cached on disk (validated again on load) and swapped into memory per
  satellite.
- Without `SATLAB_SATNOGS_API_TOKEN` the archive reads `not_configured` and the UI says so.

**Seeds ({py:mod}`app.data.seed`)** are read with lru-cached, validated loaders. Ground stations
are inserted by name and never updated. Link profiles are authoritative: rows the seed drops are
deleted. Packet templates are upserted, and rows the seed drops are marked inactive rather than
deleted, so `command.template_id` keeps pointing at something.

## The sources

```{include} ../../AGENTS.md
:start-after: <!-- docs:data-sources:start -->
:end-before: <!-- docs:data-sources:end -->
```

## Where in the code

- {py:mod}`app.data.celestrak`, {py:mod}`app.data.satnogs`: the HTTP clients and parsers.
- {py:mod}`app.services.catalog`, {py:mod}`app.services.satnogs`: the background services.
- {py:mod}`app.data.seed`, {py:mod}`app.data.classify`: the seed files and the type rules.
- {py:mod}`app.sim.fleet`: what the tick reads.
