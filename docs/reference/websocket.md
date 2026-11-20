# WebSocket protocol

One socket per mission session, at `/ws/sessions/{id}` (`/satlab/ws/sessions/{id}` through the web
app). Messages are JSON objects tagged by `type`. The first client message must be `auth` with the
session's token, within `SATLAB_WS_AUTH_TIMEOUT_S`; the socket is closed with one of the codes below
otherwise. [Session lifecycle](../architecture/session-lifecycle.md) walks through a connection;
[change the WebSocket contract](../how-to/change-ws-contract.md) explains how a message changes.

The contract lives in {py:mod}`app.models.ws` and is mirrored by hand in
`frontend/src/types/ws.ts`. Every field added after the first release has a default, and the
TypeScript mirror marks it optional, so web and API versions can differ during a deploy.

```{include} _generated/websocket.md
```
