# Pass automation

## What it is

A command can be **booked for a future pass** instead of sent now. The server then runs the pass
for you:

1. A minute before AOS it turns the antenna to the satellite, which becomes the selection.
2. At AOS it runs a **system check**.
3. If no subsystem is red, it sends the booked commands. Otherwise it **holds** them and raises a
   critical **alarm** naming each red subsystem and its value.

The Schedule panel shows what is waiting, what happened, and the alarms to acknowledge.

## Why it works this way

- **It mirrors real operations.** Ground stations book contacts in advance and run automated
  go/no-go checks at AOS, because a human can't watch every pass. The check uses the same
  {py:class}`~app.models.ws.HealthState` the inspector shows, so what the operator sees is what was
  checked.
- **One antenna.** The station has one antenna and it follows the selection. So automation must
  take it for the pass, and passes of different satellites can't overlap (including the lead
  time). Booking one that would is refused, naming the booking in the way.
- **A fast clock must not skip the pass.** At 3600× a tick advances half an hour of sim time. If the
  next tick would cross "AOS − lead", the clock instead jumps exactly to that instant and drops to
  real time. Inside the window it only slows down.
- **The server decides.** The client only follows `Snapshot.automation`, selecting the satellite
  once per pass. A reconnecting client can't miss a pass, and two tabs can't disagree.

## How it flows

```{mermaid}
sequenceDiagram
  participant O as Operator
  participant M as SessionManager
  participant A as PassAutomation
  participant T as Tick
  participant C as CommsRuntime
  O->>M: schedule_command<br/>(a command, its pass's AOS)
  M->>M: match a predicted pass (±120 s),<br/>check the limits and the antenna
  M->>A: book (row stored as scheduled)
  Note over T,A: … time passes …
  T->>A: clock_catch: will the next tick<br/>cross AOS − lead?
  A-->>T: jump to AOS − lead, run at 1×
  T->>A: prepare: from AOS − lead to LOS<br/>the antenna is the booked satellite
  T->>A: step: the first tick<br/>in contact after AOS
  A->>A: system check: any subsystem red?
  alt go: nothing red
    A->>C: release: queued,<br/>sent next tick
  else no-go
    A->>A: held, and a critical alarm<br/>naming the red subsystems
  end
```

**Booking** ({py:meth}`~app.sim.loop.SessionRuntime.schedule_command`):

- The client names the pass by its AOS. The server matches that to a pass it predicts itself
  ({py:func}`~app.sim.scheduler.nearest_pass`, within 120 s), so the client can't invent one.
- Geostationary and always-visible satellites have no AOS, so they can't be booked.
- Limits: `SATLAB_SCHEDULE_HORIZON_H` (24 h) ahead and `SATLAB_MAX_SCHEDULED_COMMANDS` (20)
  waiting per session, plus the normal command budget and cap.

**The check, once.** At the first tick in contact after AOS:

- **Go**: no subsystem is red. Amber passes, a subsystem with no data isn't checked, and the
  commands go to {py:class}`~app.sim.comms.CommsRuntime`.
- **No-go**: anything red. The commands are `held` and one critical alarm is raised.
- **No health at all**: held.

The check is stored with each command (`command.check_result`) and travels with its later statuses.

**What can go wrong, and what it says:**

| Situation | Result |
| --------- | ------ |
| The clock jumped past the whole pass | `aborted` + warning alarm (missed pass) |
| No tick in contact during the pass (clock sped up again) | `aborted` + warning alarm |
| The operator selected another satellite before the check | `aborted` ("took the antenna"), no alarm |
| The clock moved back before the booking was made | `aborted` |
| The operator cancelled it | `aborted` |
| The session ended | `aborted` (by the session manager) |

**After a restart**, waiting commands are re-booked from the database around their stored AOS (or
aborted if their pass can't be found any more), and alarms and their numbering are restored.

With the current models, NOAA 20 and 21 go (RF amber passes), FengYun-3D goes (green), and the ISS
is held at low elevations: its modelled ±62° beam doesn't reach the station below about 11°. That
is accepted, because it exercises the alarm path.

## Where in the code

- {py:mod}`app.sim.scheduler`: `PassAutomation`, `search_passes`, `nearest_pass`.
- {py:meth}`app.sim.loop.SessionRuntime.step`: step 0 and step 5.
- {py:class}`app.services.sessions.SessionManager`: booking, restore.
- Client: `frontend/src/views/Scheduler/`, `lib/schedule.ts`.
- Tests: `test_scheduler.py`, `test_ws_schedule.py`.
