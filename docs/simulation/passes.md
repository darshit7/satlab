# Passes

## What it is

A **pass** is a satellite's transit above the horizon mask: AOS (acquisition of signal, rising
through the mask), culmination (maximum elevation) and LOS (loss of signal). One algorithm serves
three uses:

- the **pass schedule**: `GET /api/sessions/{id}/passes`, 24 hours of one satellite's passes,
  stored in `pass_window`;
- the **sky arcs**: each visible satellite's current pass, sampled for the sky views;
- the **rising-soon sweep**: every satellite in the catalog that will rise in the next hour or so.

## Why it works this way

- **Coarse grid, then refine.** Scanning elevation every 30 s is cheap and vectorized. Pinning AOS
  to 0.05 s by brute force would not be. Refinement only happens where the grid shows a pass.
- **Grazing passes aren't missed.** A short pass can peak between two grid samples while every
  sample is below the mask. Local maxima just below the mask are refined too.
- **Batched refinement.** Golden-section search (culmination) and bisection (AOS/LOS) run as
  (satellite, instant) pairs across all candidates at once, so each iteration is one cheap
  vectorized SGP4 evaluation.
- **Checked against skyfield.** The results match `EarthSatellite.find_events` to under a second
  for AOS and LOS, and 0.01° for maximum elevation.

## How it flows

```{mermaid}
flowchart TB
  G[1 · elevation on a coarse grid<br/>satellites × times] --> R[2 · runs above the mask<br/>+ near-mask local maxima]
  R --> C[3a · golden-section search<br/>→ culmination]
  R --> B[3b · bisection<br/>→ AOS and LOS]
  C --> S[Span: aos, los, max_time,<br/>max_elev, azimuths]
  B --> S
```

{py:func}`~app.sim.passes.find_spans` returns every pass of every satellite in a window, in time
order. A pass already in progress at the start of the window has no AOS, and one still in progress
at its end has no LOS. A geostationary satellite that stays above the mask throughout is "always
visible" and has neither. Precision differs by use: the schedule pins crossings to 0.05 s
(`SCHEDULE`), while the live arcs and the sweep use 0.25 s (`LIVE`).

**The pass schedule** ({py:class}`~app.services.passes.PassService`) searches past both ends of
the requested window, so a pass in progress at either end keeps its true AOS and LOS. It returns the
complete passes that overlap the window and replaces the stored `pass_window` rows for that
satellite and window.

**Sky arcs** ({py:func}`~app.sim.passes.pass_arcs`) sample the pass in progress, or the next one
starting within the hour, clipped to the 30 minutes before and 60 after now, as evenly
time-spaced (azimuth, elevation) points. Each point carries the
range, range rate and off-nadir angle, so the link state can colour the arc point by point. The
client tweens the satellite's dot along this arc.

**The rising-soon sweep** ({py:class}`~app.sim.passes.UpcomingSweep`) finds every rise in the
next `SATLAB_UPCOMING_WINDOW_MIN` (90 min) across the whole fleet:

- It runs a chunk of the fleet per tick: the sgp4 extension holds the GIL, so the whole catalog
  over 90 minutes would stall the loop.
- The last completed result keeps answering "rising within N minutes" while the next sweep runs.
- The chunk size adapts. It is the full chunk while there is nothing to show yet; otherwise just
  enough to finish, with a 2× margin, before the current result expires at this clock rate.
- Above `SATLAB_UPCOMING_MAX_TIME_SCALE` (60×) the sweep can't keep up, so it pauses, and the UI
  says so rather than showing a stale list.

## Limits

- The sweep's maximum elevations come from its coarse grid, so they are approximate (the field's
  doc says so).
- Passes are geometric: whether the satellite is sunlit, or bright enough to see, is not
  considered.

## Where in the code

{py:mod}`app.sim.passes`, {py:mod}`app.services.passes`; booking uses the same search
({py:func}`app.sim.scheduler.search_passes`). Tests: `backend/tests/test_passes.py`.
