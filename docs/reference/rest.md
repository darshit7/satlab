# REST API

The API is small: reference data, the catalog, and mission sessions. Everything live (the
simulation's state, packets, alarms) goes over the [WebSocket](websocket.md). Session-scoped
routes take the session's capability token in the `X-Session-Token` header; an unknown session and
a wrong token get the same 404 ([security](../architecture/security.md)).

FastAPI's interactive `/docs` is switched off in production (`SATLAB_DOCS_ENABLED=false`); this
page is generated from the same OpenAPI document at build time instead.

```{include} _generated/rest.md
```
