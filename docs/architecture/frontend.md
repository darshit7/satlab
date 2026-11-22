# Frontend

## What it is

A single-page React and TypeScript app built with Vite and served at `/satlab/`. It keeps one
WebSocket per mission session, puts what arrives into a zustand store, and draws it on a Cesium
globe (or a MapLibre 2D map), two canvas sky views and the inspector.

## Why it works this way

- **It renders; it doesn't compute.** No orbital math, link budget or pass search exists in the
  browser. Positions between snapshots come from tweening along samples the server computed: ground
  tracks for the map and globe, sky arcs for the sky views, the Sun's rate of motion for day and
  night. Every view therefore agrees with the server, and with each other.
- **Server messages are untrusted input too.** The client renders only fields the contract in
  `src/types/` defines, as text nodes (no `innerHTML`). External links must be `https:` and open
  with `rel="noopener noreferrer"`.
- **`localStorage` holds preferences only**: the last site, tracked satellites and filters
  (`satlab.mission`), view chrome (`satlab.ui`), theme (`satlab.theme.v2`) and map mode
  (`satlab.mapMode`). Every read is wrapped in `try/catch` and validated. The session token never
  touches storage.
- **One look.** The UI follows the [design system](../project/design-system.md): Tailwind tokens,
  one emerald accent, one grey family, and `components/ui/` primitives composed by views.

## How it flows

```{mermaid}
flowchart TB
  WS[SessionSocket<br/>auth · reconnect · backoff] -- ServerMessage --> ST[useMission store]
  ST -- ClientMessage --> WS
  API[api/client.ts<br/>REST] --> ST
  ST --> SIM[estimateSimMs:<br/>last sim_time + elapsed × rate]
  SIM --> G[Globe / 2D map:<br/>positionOnTrack]
  SIM --> S[Sky plot / all-sky:<br/>positionOnArc]
  ST --> I[Inspector: readout,<br/>systems, comms]
  ST --> MB[Mission bar panels:<br/>station, catalog,<br/>overhead, schedule]
```

**The store** (`src/store/mission.ts`) holds the session, the connection state, the latest
`snapshot`, ground tracks and sky arcs by NORAD id, the rising-soon list, packets, command
statuses and alarms, and the user's tracked set, selection and filters.

- Arcs, packets and command statuses arrive in a burst just before a snapshot. The store buffers
  them and merges them with that snapshot in one update, so React renders once per tick.
- Filter changes are debounced before they go to the server.

**The SAR store** (`src/store/sar.ts`) is the one store fed by REST instead of the socket. SAR
searches and comparisons run outside the simulation loop
([archive explore](../imagery/archive-explore.md)). It holds the Imagery panel's form, the last
search, and this session's comparisons:

- It polls `GET …/sar/comparisons` every 2 s while one is queued or running, and stops when none is.
- It resets when the session changes, because comparisons belong to a session.
- The form (area, dates, polarization) is a remembered preference (`satlab.imagery`, sanitized on
  load); results are not remembered.
- Images are fetched with the session token (an `<img>` can't send it) and decoded to bitmaps
  without colour management. `change.png` holds codes, which `lib/sar.ts` colours from the design
  tokens.

**The socket** (`src/ws/sessionSocket.ts`):

- Opens `/satlab/ws/sessions/{id}`, sends `auth` first and reconnects with exponential backoff
  (0.5 to 10 s).
- On every (re)open, resends what the server should know: tracked, selected, filter.
- Close codes [4401 and 4404](session-lifecycle.md) start a new session; 4503 keeps retrying and
  tells the user once.

**Sim time between snapshots.** Each snapshot carries `sim_time` and the clock's rate. The client
records when it arrived and estimates the sim time since then
(`last sim_time + elapsed × time_scale`, or no change while paused). Views ask for that estimate
every frame:

- The globe and 2D map place each satellite on its ground track at that time (`lib/geo.ts`
  `positionOnTrack`).
- The sky views place it on its arc (`lib/sky.ts` `positionOnArc`).
- Readouts show the raw snapshot values, not tweened ones.

**Views** are one folder each under `src/views/`:

- `Map/` switches between `Globe/` and `GroundTrack/`.
- `Inspector/` composes the sky dock (`Sky/`, with `SkyPlot/` and `AllSky/`), `Readout/`,
  `Indicators/` (systems), `Comms/` and `Passes/`.
- `Mission/` holds the mission bar's panels: `Location/`, `Catalog/`, `Visible/` and
  `Scheduler/`.

**The globe** is a lazy chunk: Cesium's `CesiumWidget` with core classes from `@cesium/core`, its
workers and imagery served from our own origin, no Cesium ion and no token. It is lit by a
directional light set every frame from the tweened subsolar point, so it follows the *sim* clock,
not the wall clock. If the chunk fails to load, Cesium throws, or the browser has no WebGL, the 2D
map takes over. The [decision log](../project/decisions.md) records why each Cesium choice was
made: the meshoptimizer stub, no sky box, wireframe and then translucent cones.

## Contract mirror

`src/types/ws.ts` and `src/types/api.ts` mirror the pydantic models by hand. Fields added after a
release are optional in the mirror, so an older API never breaks a newer client. See
[change the WebSocket contract](../how-to/change-ws-contract.md).

## Analytics

`src/lib/analytics.ts` is the only module that talks to Umami. It has a closed catalogue of
events, sends no personal data (no coordinates, search text, command parameters or tokens), and
most UI events are sent once per value per page load. The tracker script is added to production
builds only, and only when a website ID is configured.

## Where in the code

- `frontend/src/store/mission.ts`, `store/ui.ts`, `store/sar.ts`: the stores.
- `frontend/src/ws/sessionSocket.ts`, `api/client.ts`: transport.
- `frontend/src/lib/`: `time.ts` (sim-time estimate), `geo.ts` and `sky.ts` (projection and
  tweening), `rag.ts`, `comms.ts`, `catalog.ts` and `sar.ts` (presentation of server state).
- `frontend/src/views/`: one folder per part of the screen.
