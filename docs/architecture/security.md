# Security

## What it is

The simulator has been public, without login, since it was first deployed. Security therefore
comes from the code following a fixed set of rules, and from reviewing every change against them.
Those rules are reproduced below from the project rulebook (AGENTS.md §12).

## The threat model

A visitor can only drive **their own simulated session**. The realistic risks are:

- a malicious web page talking to the backend (including a local one, in development);
- hostile or malformed bytes from external sources: TLE text, SatNOGS frames, and later STAC
  JSON, KML and SAR rasters;
- secrets leaking into git, logs or the browser bundle;
- compromised dependencies;
- one client making the server do too much work, or abuse an upstream API.

## How the defences fit together

```{mermaid}
flowchart TB
  V[Visitor's browser] -->|TLS| N[nginx web container<br/>CSP + security headers<br/>per-IP limits]
  N -->|/satlab/api| A[API container<br/>internal ingress only]
  N -->|/satlab/ws| A
  A --> T{session token?<br/>Origin allowed?<br/>rate within budget?}
  T -->|no| X[same 404 / close code]
  T -->|yes| S[that session only]
  A -->|throttled, cached,<br/>size-capped| U[Celestrak · SatNOGS]
```

- **Per-session capability tokens.** Every session-scoped REST call sends `X-Session-Token`, and
  the WebSocket's first message carries the token. An unknown session and a wrong token get the
  same answer.
- **The WebSocket checks the browser's `Origin`** against `SATLAB_CORS_ORIGINS` before accepting.
  CORS doesn't cover WebSockets.
- **Everything a client sends crosses a pydantic model with bounds**: list lengths, string
  lengths, numeric ranges, command parameters. Unknown message types are rejected.
- **Rate and size limits.** Each connection has a message rate and burst; frames are capped at
  64 KiB; live sessions, stored rows per session, and commands per minute are all capped. A client
  that floods is disconnected, not queued.
- **Upstreams get one global budget.** Celestrak remote searches and single-satellite fetches,
  and SatNOGS refreshes, are throttled for all clients together, so nobody can make the server
  hammer them. A Celestrak download it refused is not repeated before its next update.
- **External data is parsed defensively.** TLE checksums are verified. Packet decoders check every
  declared length against the bytes present and the protocol maximum. HTTP bodies (Celestrak and SatNOGS)
  are streamed with a cap, and redirects aren't followed.
- **Generic errors outward, details in the log.** Clients never see stack traces, SQL or upstream
  error text.
- **Headers and CSP from nginx.** Scripts load only from our origin (plus the analytics host), and
  the one inline script is hashed at build time. These docs get their own CSP the same way
  ([deployment](deployment.md)).
- **This software never transmits RF.** The uplink is simulated, and there is no code path to a
  real transmitter or ground-station network.

## The rules

```{include} ../../AGENTS.md
:start-after: <!-- docs:security:start -->
:end-before: <!-- docs:security:end -->
```
