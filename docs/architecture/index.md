# Architecture

How the system is put together, and why. Start with the [overview](overview.md); the other pages
follow one flow each.

- [Overview](overview.md): the components, the rules that shape them, and the repo layout.
- [The tick](tick.md): what one session's loop does, two times a second.
- [Session lifecycle](session-lifecycle.md): from choosing a station to a live socket, reconnects
  and the end of a session.
- [Data flow](data-flow.md): Celestrak, SatNOGS and the seed files, into the in-memory fleet.
- [Frontend](frontend.md): the store, the socket, tweening and the views.
- [Persistence](persistence.md): what is stored, where, and how the schema changes.
- [Security](security.md): the threat model and the rules every change follows.
- [Deployment](deployment.md): the two images, CI/CD and production.
