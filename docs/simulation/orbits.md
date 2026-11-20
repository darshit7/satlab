# Orbits

## What it is

Every satellite's position comes from its **TLE** (two-line element set, from Celestrak),
propagated to the sim time with **SGP4**, the propagator TLEs are made for. Each tick propagates the
**whole catalog at once**: about 18,000 objects in roughly 20 ms.

## Why it works this way

- **Real elements, the right propagator.** A TLE is a mean-element fit that only SGP4 reproduces
  correctly. The UI shows each TLE's age, because accuracy degrades with it.
- **Vectorized, because the visible set is the whole sky.** The tick has to know which of all
  catalogued satellites are above the mask *now*. Doing that one satellite at a time, in Python,
  would take seconds. The sgp4 library's `SatrecArray` runs the C propagator over many satellites
  and instants in one call.
- **One chain, tested against a reference.** The fast path (numpy) is checked against skyfield,
  one satellite at a time, to about 1e-6° and well under a centimetre. skyfield stays in the code as
  that reference.

## How it flows

```{mermaid}
flowchart TB
  TLE[TLE lines] --> SR[Satrec<br/>parsed once per fleet]
  SR --> SA[SatrecArray<br/>whole fleet]
  T[sim time] --> EP[Epochs:<br/>UTC Julian date jd + fr,<br/>GMST 1982 on UT1]
  SA --> SGP4[sgp4_teme<br/>TEME r, v<br/>n_sats × n_times]
  EP --> SGP4
  SGP4 --> ROT[rotate by GMST<br/>TEME → Earth-fixed]
  ROT --> GEO[geometry: look angles,<br/>sub-point]
```

1. **The fleet.** {py:class}`~app.sim.fleet.Fleet` holds every catalogued satellite as parallel
   arrays (NORAD ids, names, types, TLE epochs) plus one `SatrecArray`. TLEs that SGP4 rejects
   (decayed, or a non-positive mean motion) are dropped when the fleet is built. The catalog service
   rebuilds the fleet after each ingest and swaps it in with one assignment.
2. **Time.** {py:func}`~app.sim.propagate.epochs` turns the sim time (and any offsets) into what
   SGP4 needs: a UTC Julian date, split into whole days and a fraction for precision, as skyfield
   does. It also computes the Greenwich mean sidereal time (GMST 1982) on UT1, for the rotation
   into the Earth-fixed frame.
3. **Propagate.** {py:func}`~app.sim.propagate.sgp4_teme` returns TEME positions (km), velocities
   (km/s) and a validity mask, shaped `[n_sats, n_times, 3]`. An entry SGP4 can't compute (e.g.
   past decay) is masked out and never "in view".
4. **Into the Earth-fixed frame.** {py:func}`~app.sim.geometry.teme_to_itrf` rotates TEME about
   the z axis by GMST (Vallado's TEME → PEF, without polar motion, as in skyfield's default). The
   velocity also gets the frame-rotation term `−ω × r`. Everything after this happens on the
   Earth-fixed vectors ([geometry](geometry.md)).

A satellite that isn't in the fleet yet (just found by a remote search) is propagated on its own
through the same chain, from {py:class}`~app.sim.propagate.Elements` in the registry.

## Limits

- **The GIL.** The sgp4 C extension holds Python's GIL for the whole call, so one call across the
  catalog must stay small: one instant per tick. Anything bigger (the rising-soon sweep, subsystem
  windows) is sliced into chunks or cached ([passes](passes.md), [subsystems](subsystems.md)).
- **TLE accuracy.** Errors grow from a few kilometres at epoch to tens of kilometres after a week
  or two. The catalog refreshes every 12 hours; tracking a satellite with a stale TLE refetches it.

## Where in the code

{py:mod}`app.sim.propagate`, {py:mod}`app.sim.fleet`, {py:func}`app.sim.geometry.teme_to_itrf`.
Tests: `backend/tests/test_vector_geometry.py` (the vectorized chain against skyfield).
