# Session lifecycle

## What it is

A **mission session** is one observer location with its own sim clock, selection, filter, tracked
set, commands and alarms. A row in `mission_session` records it. While a client is connected, a
{py:class}`~app.sim.loop.SessionRuntime` runs it in memory.

## Why it works this way

- **The location belongs to the session.** Stations are read-only presets. A custom location
  (geolocation, a map click, typed coordinates) is copied onto the session and never stored
  anywhere else, so nothing a visitor types becomes shared state.
- **Capability tokens, not accounts.** The site is public without login. Creating a session returns
  a random token once, and the database keeps only its SHA-256. Every session-scoped request has to
  present the token, and an unknown session and a wrong token get the same answer, so sequential
  ids can't be probed. On the WebSocket the token is the first *message*, never part of the URL, so
  it can't end up in proxy logs.
- **Sessions end when nobody watches.** A session with no subscriber for
  `SATLAB_SESSION_IDLE_GRACE_S` (60 s) ends, which frees its share of the CPU. A tab that reloads
  quickly reattaches to the same session.

## How it flows

```{mermaid}
sequenceDiagram
  autonumber
  participant B as Browser (store)
  participant R as REST /api
  participant W as WebSocket
  participant M as SessionManager
  participant T as SessionRuntime
  B->>R: POST /api/sessions {station_id | location}
  R-->>B: 201 session + token (once)
  B->>W: connect (Origin checked)
  B->>W: auth {token} within 5 s
  W->>M: attach(session_id, token)
  M->>T: start it if not running
  M-->>W: a subscriber, primed for a reconnect
  loop every tick
    T-->>W: geometry, events, then the snapshot
    W-->>B: JSON messages
  end
  B->>W: client messages
  W->>M: validated, rate-limited: handle()
  Note over B,W: a drop: reconnect with backoff, auth again
  B--xW: last tab closes
  M->>M: idle 60 s: end()
```

1. **Create.** `POST /api/sessions` takes exactly one of `station_id` or `location` (validated
   ranges), an optional start time and rate. The response is the session plus its `token`, returned
   once ({py:class}`~app.models.api.MissionSessionCreated`). The client keeps the token in memory
   only. It remembers the *site* in `localStorage`, so a reload starts a new session at the same
   place.
2. **Connect.** The WebSocket upgrade is refused before `accept` if the browser's `Origin` isn't in
   `SATLAB_CORS_ORIGINS` (CORS does not protect WebSockets). The first message must be a valid
   `auth` within `SATLAB_WS_AUTH_TIMEOUT_S`.
3. **Attach.** {py:meth}`~app.services.sessions.SessionManager.attach` checks the token, refuses
   ended sessions, and starts the runtime if needed, up to `SATLAB_MAX_ACTIVE_SESSIONS`. The new
   subscriber is primed so a reconnecting client is whole again: ground tracks, sky arcs, the
   rising-soon list, the last 50 packets, the commands waiting for a pass and the last 50 alarms.
4. **Stream.** A pump task drains the subscriber into the socket. Client messages are parsed by
   the discriminated union in {py:mod}`app.models.ws` (an unknown type is an error), rate-limited
   per connection (a flooding client is disconnected), and handled by
   {py:meth}`~app.services.sessions.SessionManager.handle`.
5. **Reconnect.** The client (`frontend/src/ws/sessionSocket.ts`) reconnects with exponential
   backoff (0.5 to 10 s) and resends its state (tracked set, selection, filter) on every open.
6. **End.** After the idle grace, or on `POST /api/sessions/{id}/end`, the runtime stops, the row
   gets `ended_at`, and commands still waiting for a pass are aborted.

## Close codes

The socket closes with one of the codes in the [WebSocket reference](../reference/websocket.md),
and the client reacts to each:

| Code | Meaning | Client |
| ---- | ------- | ------ |
| 4401 | no valid `auth` in time | starts a new session |
| 4404 | unknown, ended, or not this token's session | starts a new session |
| 4503 | at `SATLAB_MAX_ACTIVE_SESSIONS` | keeps retrying with backoff, says "at capacity" once |
| 1008 | foreign `Origin`, or over the message rate | reconnects with backoff |

## After a restart

Runtimes live in memory, so an API restart drops them. A client that reconnects to its session
gets a fresh runtime. Its clock resumes at `sim_start_time + elapsed × initial time_scale`; later
rate changes weren't persisted, so this is approximate. Its booked commands are re-booked from the
database (or aborted if their pass can no longer be found), and its alarms and their numbering are
restored. Sessions created before tokens existed have no token hash and can't be attached; their
clients simply start a new session.

## Where in the code

- {py:func}`app.main.create_session`, {py:func}`app.main.session_stream`.
- {py:class}`app.services.sessions.SessionManager`: `attach`, `detach`, `end`, `handle`.
- {py:func}`app.services.sessions.new_session_token`, {py:func}`~app.services.sessions.session_token_matches`.
- Client: `frontend/src/store/mission.ts` (`startSession`) and `frontend/src/ws/sessionSocket.ts`.
