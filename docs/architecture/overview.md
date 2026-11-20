# Overview

## What it is

A browser app backed by one Python process. The backend runs a **simulation loop per mission
session** and streams its state over a WebSocket; the frontend renders that state on a globe, a 2D
map, two sky views and an inspector. A small REST API serves reference data, catalog search, the
pass schedule and session creation.

```{mermaid}
flowchart TB
  subgraph ext["Outside data · cached, fetched off the loop"]
    CT[Celestrak TLEs]
    SN[SatNOGS frames]
    SD[Seed files]
  end
  subgraph api["API process · FastAPI, one worker"]
    CS[CatalogService] --> FL[(Fleet<br/>in memory)]
    SA[SatnogsArchive] --> AR[(Frames<br/>in memory)]
    FL --> RT[SessionRuntime<br/>one per session, 2 Hz]
    AR --> RT
    RT --> CW[CommsWriter]
  end
  DB[(SQLite / Postgres)]
  B["Browser · store → globe, map,<br/>sky views, inspector"]
  CT --> CS
  SD --> CS
  SN --> SA
  CS <--> DB
  CW --> DB
  RT -- "WebSocket: snapshots, arcs, packets" --> B
  B -- "client messages, REST" --> api
```

## Why it is shaped this way

The project's value is **realism**: every position, pass, margin and frame is computed from real
data or labelled as modelled. Most of the architecture follows from protecting that:

- **One engine.** If the browser recomputed positions or margins, two implementations would drift
  and views would disagree. All physics lives in `backend/app/sim/`; a feature that needs physics
  adds a field to what the tick streams.
- **Server computes, client interpolates.** The server sends samples (ground tracks, sky arcs,
  the Sun's motion) along with each snapshot; the client moves markers along them between
  snapshots, so a 2 Hz stream looks smooth at 60 frames per second and 3600×.
- **The loop never waits on the network or the database.** It reads only in-memory objects (the
  fleet, the elements registry, the SatNOGS archive) and hands records for the database to a
  writer queue. Network fetches run in background tasks with caches, timeouts and rate limits.
- **Contracts change additively.** New fields get defaults and the TypeScript mirror marks them
  optional, so a deploy that updates the API before the web app (or the reverse) degrades
  gracefully.

These are the project's golden rules:

```{include} ../../AGENTS.md
:start-after: <!-- docs:golden-rules:start -->
:end-before: <!-- docs:golden-rules:end -->
```

## Where things live

Backend (`backend/app/`)
: - `sim/`: the physics and the per-session runtimes. Pure functions where possible, no I/O
    anywhere: {py:mod}`~app.sim.propagate`, {py:mod}`~app.sim.geometry`, {py:mod}`~app.sim.passes`,
    {py:mod}`~app.sim.linkbudget`, {py:mod}`~app.sim.subsystems`, {py:mod}`~app.sim.sun`,
    {py:mod}`~app.sim.channel`, and the runtimes {py:mod}`~app.sim.loop`,
    {py:mod}`~app.sim.comms` and {py:mod}`~app.sim.scheduler`.
  - `services/`: orchestration between the sim, the database and the socket:
    {py:mod}`~app.services.catalog`, {py:mod}`~app.services.sessions`,
    {py:mod}`~app.services.passes`, {py:mod}`~app.services.comms`, {py:mod}`~app.services.satnogs`.
  - `models/`: the pydantic contract ({py:mod}`~app.models.ws`, {py:mod}`~app.models.api`).
  - `packets/`: AX.25 and CCSDS codecs and the packet templates.
  - `data/`: Celestrak and SatNOGS clients, classification and the seed files.
  - `db/`: the engine and the ORM models; Alembic revisions are in `backend/migrations/`.
  - {py:mod}`app.main`: the FastAPI app, its REST routes and the WebSocket endpoint.

Frontend (`frontend/src/`)
: - `store/`: the zustand stores (mission state fed by the socket; view chrome).
  - `ws/`: the session socket with reconnects; `api/`: the REST client.
  - `types/`: the TypeScript mirror of the contract.
  - `views/`: one folder per part of the screen (Globe, GroundTrack, SkyPlot, AllSky, Inspector,
    Comms, Scheduler, …); `components/ui/`: the design-system primitives; `lib/`: drawing,
    tweening and formatting helpers.

The [Python API reference](../reference/api/index.md) has every backend module, and
[frontend](frontend.md) walks through the client.
