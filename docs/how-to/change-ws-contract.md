# Change the WebSocket contract

The contract is the pydantic models in {py:mod}`app.models.ws`, mirrored by hand in
`frontend/src/types/ws.ts`. Web and API deploy separately, so for a while a new web app can talk to
an old API, or the reverse. Every change must therefore be **additive**: old clients ignore what
they don't know, new clients cope with what's missing.

## Add a field to an existing message

1. **Backend model.** Add the field with a default (usually `None`) and a `#:` doc-comment. The
   comment becomes its description in the [WebSocket reference](../reference/websocket.md).

   ```python
   class Snapshot(BaseModel):
       ...
       #: The elevation floor the session filter applies to the satellites shown.
       shown_min_elevation_deg: float | None = None
   ```

2. **Produce it.** Fill it where the message is built, usually in
   {py:meth}`app.sim.loop.SessionRuntime.step`. If it needs physics, it is computed here and never in
   the browser (golden rule 1).
3. **TypeScript mirror.** Add it to `frontend/src/types/ws.ts` as **optional**
   (`shown_min_elevation_deg?: number | null`), and handle `undefined` wherever it's read.
4. **Tests.** A backend test that asserts the field (see `tests/test_ws*.py`); a vitest for any
   client logic that uses it.

## Add a server message

1. A model with `type: Literal["my_message"] = "my_message"`, added to the `ServerMessage` union.
2. Decide how {py:class}`~app.sim.loop.Subscriber` treats it: coalesce (latest wins), coalesce per
   key, or queue in order (and in what position relative to the snapshot). Add it to `offer` and
   `drain`.
3. Mirror it in `types/ws.ts` and handle it in the store (`store/mission.ts`). An old client
   receiving an unknown `type` must ignore it, which it does.

## Add a client message

1. A model with a `type` literal and **bounds on every field** (`Field(ge=…, le=…, max_length=…)`),
   added to the `ClientCommand` union. The discriminated union rejects unknown types.
2. Handle it in {py:meth}`app.services.sessions.SessionManager.handle`. Anything that writes
   or fans out must be budgeted like `send_command`.
3. Mirror it in `types/ws.ts` and send it from the store.
4. An old API rejects an unknown client message with an `error` message rather than closing the
   socket. Make sure the UI copes with that during a deploy.

## Docs to update

- The [WebSocket reference](../reference/websocket.md) regenerates from the models.
- Update the narrative page that describes the behaviour ([tick](../architecture/tick.md),
  [communications](../simulation/comms.md), …). The [sync map](write-docs.md#the-sync-map) lists
  them for `app/models/ws.py`.
- Add a decision-log entry in `AGENTS.md` §14 for any contract change, as every phase has.
