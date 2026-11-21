# Subsystems

## What it is

The selected satellite's **health**, as five subsystems, each green, amber, red or no data:

| Subsystem | Shows | Source |
| --------- | ----- | ------ |
| Power | battery state of charge, how much of the Sun's disc it sees (sunlit / penumbra / eclipse) | modelled, driven by the real eclipse geometry |
| Thermal | temperature against an operating band | modelled, driven by the real eclipse geometry |
| Storage | on-board data buffer, filling or downlinking | modelled, driven by real contacts with *this* station (needs a link profile) |
| Attitude | the station's angle off the satellite's nadir against the antenna's coverage | modelled (nadir pointing assumed), real geometry |
| RF | the link margin | real: the [link budget](link-budget.md) |

A **rollup** sums them up: red if any subsystem is red; otherwise "no data" if any has none;
otherwise the worse of amber and green. The selection header shows it, and
[pass automation](automation.md) checks it at AOS.

## Why it works this way

- **Honest about what is modelled.** No satellite publishes its battery voltage for us, so power,
  thermal, storage and attitude use generic parameters (`subsystem_models.json`). Each carries
  `modelled: true` and the UI labels it. What drives them is real: when the satellite is in the
  Earth's shadow, when it is in contact, and where the station sits relative to its nadir.
- **Deterministic in sim time.** The state at time `t` is integrated from a fixed initial state
  over the window `[t − 24 h, t]`, on a grid of absolute instants (multiples of 60 s since the Unix
  epoch). The same `t` always gives the same values, whether the clock got there by running,
  jumping or replaying. The dynamics forget the initial state long before the window ends.
- **Cheap enough for every tick.** The grid's inputs (sunlit fraction, downlink possible) are
  cached and extended as the window slides, so a tick only computes the new instants: about 4 ms.

## How it flows

```{mermaid}
flowchart TB
  G[absolute grid<br/>t − 24 h … t, every 60 s] --> SG[SGP4 at each grid instant]
  SG --> SUN[sunlit fraction<br/>conical umbra/penumbra]
  SG --> DL[downlink possible?<br/>link RAG ≠ red]
  SUN --> I[integrate:<br/>battery, temperature, buffer]
  DL --> I
  I --> RAG[RAG per subsystem]
  NOW[instantaneous geometry:<br/>sunlit now, off-nadir, link] --> RAG
  RAG --> R[rollup]
```

**The Sun and the shadow** ({py:mod}`app.sim.sun`) use no ephemeris file:

- The Sun's position comes from the Astronomical Almanac's low-precision formula, good to 0.01°
  between 1950 and 2050.
- The Earth's shadow is a cone with an umbra and a penumbra (Montenbruck & Gill, *Satellite
  Orbits* §3.4.2), giving the fraction of the solar disc the satellite sees.
- The same Sun, rotated into the Earth-fixed frame, gives the subsolar point that lights the globe
  and shades night on the map.

**The models** ({py:func}`~app.sim.subsystems.integrate`):

- **Battery.** It is sized for the orbit's *longest* eclipse (β = 0, cylindrical shadow), which
  takes `asin(R⊕ / a) / π` of the orbit. The load drains `design_dod_pct` over that eclipse, and
  full sun recharges `recharge_factor` times faster than the rest of the orbit would need. The state
  of charge integrates `sunlit · charge − load`, clamped to 0–100%.
- **Thermal.** First-order lag (time constant `tau_s`) towards an equilibrium that interpolates
  between the eclipse and sunlit temperatures by the sunlit fraction.
- **Storage.** It fills at `100% / fill_hours` and drains at `100% / capacity_downlink_s` while a
  downlink to this station is possible. The station only knows its own contacts, so a satellite it
  hasn't seen for a day reads full. Without a link profile there is no downlink to model, so storage
  has no data.
- **Attitude.** Green while the station is inside the antenna's coverage, amber just outside it,
  red beyond. No data below the mask. The coverage is the link profile's; else, for a satellite on
  the generic packet template, the omnidirectional antenna that set assumes
  (`antenna_half_angle_deg`, 90°, so it can't go red in view); else the model's
  `half_beamwidth_deg`.

The thresholds, default parameters and per-type overrides are in the
{ref}`seed data <seed-subsystem-models>`.

## Limits

- The parameters are generic per satellite type, not the real spacecraft's. The ISS, for example,
  is "held" by pass automation at low elevations because the generic ±62° beam doesn't reach the
  station below about 11°. That is accepted: it exercises the alarm path. The generic template's
  omnidirectional antenna is an assumption too (typical of TT&C antennas), labelled as modelled.
- Attitude assumes the satellite points its antenna at nadir; real attitude modes aren't
  modelled.

## Where in the code

{py:mod}`app.sim.subsystems` (`HealthTracker`, `integrate`, the RAG functions, `rollup`),
{py:mod}`app.sim.sun`. Tests: `backend/tests/test_subsystems.py`, `test_sun.py` (against skyfield's
JPL kernels).
