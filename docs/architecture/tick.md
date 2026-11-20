# The tick

## What it is

Every live mission session has a {py:class}`~app.sim.loop.SessionRuntime`, which steps
`SATLAB_TICK_HZ` times a second (2 by default). One step, {py:meth}`~app.sim.loop.SessionRuntime.step`,
reads the session's sim clock and produces everything the client needs for that instant: a
`snapshot`, plus sky arcs, ground tracks, the rising-soon list, packets, command statuses and
alarms when those change. It reads only in-memory data and does no I/O.

## Why it works this way

- **The whole catalog, every tick.** The visible set is "every satellite above the mask", with no
  search needed. So the tick propagates the entire fleet (about 18,000 objects) in one vectorized
  SGP4 call, in about 20 ms, rather than keeping a candidate list that could go stale.
- **Deterministic in sim time.** The state at a sim instant doesn't depend on how the clock got
  there (running, jumping, a replay). The tick integrates nothing incrementally that a jump could
  break: subsystem health is recomputed over a fixed window, beacons sit on an absolute time grid,
  and random bit errors are seeded per packet.
- **No I/O.** The loop is an asyncio task in the API process; a slow database or network call
  would freeze every session. Records for the database go to a non-blocking queue
  ({py:class}`~app.services.comms.CommsWriter`).
- **The GIL is a budget.** The sgp4 C extension holds the GIL for a whole call, so any work bigger
  than "one instant across the catalog" is sliced across ticks. The rising-soon sweep runs one
  chunk per tick, and skips a tick that has already used 30% of its period.

## How it flows

```{mermaid}
flowchart TD
  A[Read the sim clock] --> B{Pass automation:<br/>booked pass near?}
  B -- clock too fast --> B1[Jump to AOS − lead,<br/>drop to 1×]
  B -- within lead --> B2[Take the antenna:<br/>select the booked satellite]
  B --> C
  B1 --> C
  B2 --> C
  C[1 · Propagate the whole fleet<br/>vectorized SGP4 → az/el/range] --> D[Above the mask ∩ session filter]
  D --> E[2 · Streamed set =<br/>shown + tracked + selected<br/>→ SatelliteState + link state]
  E --> F[3 · Sky arcs for newly visible,<br/>ground tracks for pinned<br/>only when they run out]
  F --> G[4 · Rising-soon sweep:<br/>one chunk, if time allows]
  G --> H[5 · Selection: health → comms step<br/>→ automation step]
  H --> I[Snapshot → every subscriber]
```

0. **Pass automation first.** If a booked pass is about to start and the clock runs faster than
   communications can follow (above `SATLAB_COMMS_MAX_TIME_SCALE`), the clock jumps forward to the
   moment the antenna should move and drops to real time. From `SATLAB_SCHEDULE_LEAD_S` before AOS
   until LOS, the booked satellite becomes the selection. See
   [pass automation](../simulation/automation.md).
1. **The whole fleet, from the station.** {py:func}`~app.sim.geometry.observe` runs SGP4 for every
   row of the {py:class}`~app.sim.fleet.Fleet` at once, rotates the positions into the Earth-fixed
   frame and then into the station's local frame. The result is azimuth, elevation, range and range
   rate for every satellite. "Above the mask" is one comparison over that array. See
   [orbits](../simulation/orbits.md) and [geometry](../simulation/geometry.md).
2. **What to stream.** The satellites shown are those above the mask that the session filter
   allows (type, SAR, Link OK, Communications, minimum elevation), plus every tracked satellite and
   the selection, wherever they are. Each becomes a {py:class}`~app.models.ws.SatelliteState`.
   Satellites with a published link budget get a {py:class}`~app.models.ws.LinkState` from the one
   {py:meth}`~app.sim.linkbudget.LinkTable.evaluate`.
3. **Geometry the views draw along.** A newly visible satellite gets a `pass_arc`: its current
   pass as evenly time-spaced (azimuth, elevation) samples, with the link state at each point. A
   tracked or selected satellite gets a `ground_track`: sub-point samples from half an orbit back
   to one orbit ahead. Both are recomputed only when the clock leaves the window they cover, the
   TLE changes, or the link profiles change, and at most `SATLAB_MAX_ARCS_PER_TICK` arcs per tick.
4. **Rising soon.** {py:class}`~app.sim.passes.UpcomingSweep` advances by one chunk of the
   catalog. When a sweep completes, the `upcoming` list is filtered and republished. Above 60× the
   sweep can't keep up, so it pauses and the UI says so. See [passes](../simulation/passes.md).
5. **The selection.** {py:class}`~app.sim.subsystems.HealthTracker` computes its subsystem health.
   {py:class}`~app.sim.comms.CommsRuntime` steps the contact: beacons down, queued commands up,
   answers back through the channel. {py:class}`~app.sim.scheduler.PassAutomation` runs the system
   check at AOS and releases or holds booked commands. See [communications](../simulation/comms.md).

The snapshot also carries the Sun's subsolar point and its rate of motion, so the client can shade
day and night smoothly between snapshots.

## Message order

Each subscriber has a {py:class}`~app.sim.loop.Subscriber` mailbox. Snapshots and the rising-soon
list coalesce (a slow client gets the latest, not a backlog). Tracks and arcs coalesce per
satellite. Packets, command statuses and alarms queue in order, up to 200. When the socket drains
the mailbox, messages go out in the order a client must apply them:

```text
errors → ground tracks → pass arcs → packets / statuses / alarms → upcoming → snapshot
```

The geometry therefore always arrives before the snapshot that uses it.

## Where in the code

- {py:class}`app.sim.loop.SessionRuntime` and its `step`; {py:class}`app.sim.loop.RuntimeConfig`
  (the settings it reads).
- {py:class}`app.services.sessions.SessionManager` starts and stops runtimes.
- {py:func}`app.main.session_stream` pumps a subscriber's messages to the socket.

## Limits

- The tick measures itself and logs when it runs slow. A session costs one catalog propagation per
  tick, which is why `SATLAB_MAX_ACTIVE_SESSIONS` caps live sessions on the public deploy.
- Sessions live in the API process's memory, so the API runs as exactly one replica (see
  [deployment](deployment.md)).
