# Data flow

## What it is

Three kinds of data feed the simulation:

- **Orbital elements** from Celestrak.
- **Real telemetry frames** from the SatNOGS DB.
- **Real radar scenes** from the Sentinel-1 RTC archive on Microsoft Planetary Computer, for the
  Imagery panel ([archive explore](../imagery/archive-explore.md)). Unlike the two above, the
  simulation loop never uses them: they are searched and read only when a user asks.
- **Versioned seed files** in the repo: stations, link profiles, packet templates, type rules and
  subsystem models.

None of it is read by the tick directly. Background services fetch, validate and cache it, then
build immutable in-memory objects that the tick reads with plain lookups.

## Why it works this way

- **Never block the loop.** Celestrak and SatNOGS are slow, rate-limited and sometimes down. Every
  fetch runs in a background task with a timeout, a size cap and a cache, and the loop keeps
  running on the last good copy.
- **Respect the upstreams.** Celestrak's data updates every 2 hours, and since March 2026 it
  refuses a repeat download before then (HTTP 403, "GP data has not updated since your last
  successful download"); 50 such errors in 2 hours get the address firewalled. SatNOGS answers 429
  after about 20 quick requests. So a refused download is never repeated before Celestrak's next
  update, remote searches and single-satellite fetches share one global budget across all clients,
  and SatNOGS refreshes share another. That's also why there's no refresh button: a user can't get
  anything newer than the server already has.
- **Everything external is hostile input.** TLEs are checksummed and parsed defensively (a bad
  record is skipped, not fatal), off the event loop. Celestrak responses are streamed with a byte
  cap (`SATLAB_CELESTRAK_MAX_BYTES`), SatNOGS responses with a 2 MB cap, and redirects are never
  followed; SatNOGS pagination only follows links to the same endpoint. Frames are kept only if they
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
2. Every `SATLAB_CATALOG_REFRESH_CHECK_MINUTES` the service checks the group's age. When it
   is older than `SATLAB_CATALOG_MAX_AGE_HOURS`, it fetches `GROUP=active` in TLE format, writes the
   cache file, and upserts the `satellite` table in chunks. Each upsert is classified by the
   ordered type rules ({py:func}`app.data.classify.classify`).
   - **A refused repeat download** (the 403 above) means this address got the current data less
     than 2 hours ago. If that was our own download, the copy is current: either the cache file
     dates from then, or a container restart lost the cache while the database kept the catalog.
     Either way nothing is asked again before Celestrak's next update, so a restart costs at most
     one request. The age is kept in memory as well as in the cache file's mtime.
   - The Catalog panel shows the age and the refresh interval from `GET /api/catalog/status`
     ("fetched 3 h ago · refreshed every 12 h"), and the last error beside a catalog that still
     works.
3. {py:meth}`~app.services.catalog.CatalogService.rebuild_fleet` syncs the link profiles and
   packet templates from their seed files (only for satellites already catalogued, so foreign keys
   always hold; the generic packet-template set once, with no satellite), then builds a new
   {py:class}`~app.sim.fleet.Fleet` on a thread, noting which objects are debris or rocket bodies
   (no radio, so no generic templates). The fleet holds
   parallel arrays of every satellite, one `SatrecArray`, and the link profiles and packet
   templates aligned with its rows. It is swapped in with one assignment.
4. Tracking or selecting a satellite resolves its elements into the
   {py:class}`~app.services.catalog.Registry`. That refreshes a stale TLE from Celestrak by catalog
   number first, which is why a newly tracked satellite can take a few seconds to appear. A
   satellite is fetched this way at most once per Celestrak update and within the remote-search
   budget; otherwise whatever the database holds is used.

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
deleted. Packet templates are upserted (the generic set's rows by kind and name, with no
satellite), and rows the seed drops are marked inactive rather than deleted, so
`command.template_id` keeps pointing at something.

**Planetary Computer ({py:class}`~app.services.sar.SarService`)**, on request only:

- STAC searches and SAS tokens go through {py:mod}`app.data.http`: https on the one allowed host,
  no redirects, a 4 MB cap, generic errors. They share one budget for all clients
  (`SATLAB_SAR_UPSTREAM_REQUESTS_PER_MIN`), and a 429 backs everyone off. A search window is cached
  for 15 minutes.
- Items are validated by pydantic models of just the fields used. An item whose asset isn't on the
  allowed storage account is dropped, so the worker can only read files the server checked.
- Scene windows are read by a worker process, never by the API process
  ([reading scenes safely](../imagery/archive-explore.md#reading-scenes-safely)).

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
- {py:mod}`app.data.http`, {py:mod}`app.sar.stac`, {py:mod}`app.sar.token`: the SAR archive clients.
