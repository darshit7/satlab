# SatLab documentation

SatLab is a real-time **satellite mission control simulator**. Pick a ground station and every
satellite above its horizon appears on a 3D globe and two sky views. You can open a contact, send
commands up and receive telemetry, book commands for a future pass, and watch subsystem health and
link margin change through a pass.

It runs from real data and real physics:

- **Orbits** come from real TLEs (Celestrak), propagated with SGP4.
- **Geometry** is computed for your real location: look angles, passes, footprint, day and night.
- **Link health** comes from published link budgets, giving a margin in dB rather than an invented
  flag.
- **Replayed telemetry** is real frames received by SatNOGS stations.

These docs explain how the system works and why it is built the way it is. They are written from
the code and live next to it in the repo (`docs/`). The reference section is generated from the
code on every build, and CI refuses a change that breaks the docs.

[Open the simulator →](https://satlab.darshitp.dev/satlab/)

## Where to start

Getting started
: [Run it locally](getting-started/run-locally.md), then take the [tour of the UI](getting-started/ui-tour.md).

Architecture
: [How the pieces fit](architecture/overview.md): one simulation engine on the server streaming
  state to a client that only renders, [the tick](architecture/tick.md), [a session's
  life](architecture/session-lifecycle.md), [where data comes from](architecture/data-flow.md), the
  [frontend](architecture/frontend.md), [persistence](architecture/persistence.md),
  [security](architecture/security.md) and [deployment](architecture/deployment.md).

Simulation
: The physics, one page per model: [orbits](simulation/orbits.md),
  [geometry](simulation/geometry.md), [passes](simulation/passes.md), the
  [link budget](simulation/link-budget.md), [subsystems](simulation/subsystems.md),
  [communications](simulation/comms.md) and [pass automation](simulation/automation.md).

How-to guides
: Recipes for common changes: [a link profile](how-to/add-link-profile.md),
  [a packet template](how-to/add-packet-template.md),
  [a WebSocket message](how-to/change-ws-contract.md), [a setting](how-to/add-setting.md),
  [a migration](how-to/add-migration.md), and [these docs](how-to/write-docs.md).

Reference
: Generated from the code: the [Python API](reference/api/index.md), the [REST
  API](reference/rest.md), the [WebSocket protocol](reference/websocket.md),
  [settings](reference/settings.md), the [database schema](reference/database.md) and the
  [seed data](reference/seed-data.md).

Project
: The [decision log](project/decisions.md), [roadmap](project/roadmap.md),
  [design system](project/design-system.md) and [how to contribute](project/contributing.md).

## Honesty notes

The simulator draws a hard line between what is real and what is modelled, and the UI says which is
which.

```{include} ../README.md
:start-after: <!-- docs:honesty:start -->
:end-before: <!-- docs:honesty:end -->
```

```{toctree}
:hidden:
:caption: Getting started

getting-started/index
getting-started/run-locally
getting-started/ui-tour
```

```{toctree}
:hidden:
:caption: Architecture

architecture/index
architecture/overview
architecture/tick
architecture/session-lifecycle
architecture/data-flow
architecture/frontend
architecture/persistence
architecture/security
architecture/deployment
```

```{toctree}
:hidden:
:caption: Simulation

simulation/index
simulation/orbits
simulation/geometry
simulation/passes
simulation/link-budget
simulation/subsystems
simulation/comms
simulation/automation
```

```{toctree}
:hidden:
:caption: How-to guides

how-to/index
how-to/add-link-profile
how-to/add-packet-template
how-to/change-ws-contract
how-to/add-setting
how-to/add-migration
how-to/add-type-rule
how-to/deploy-and-rollback
how-to/write-docs
```

```{toctree}
:hidden:
:caption: Reference

reference/index
reference/api/index
reference/rest
reference/websocket
reference/settings
reference/database
reference/seed-data
reference/glossary
```

```{toctree}
:hidden:
:caption: Project

project/index
project/decisions
project/roadmap
project/design-system
project/contributing
```
