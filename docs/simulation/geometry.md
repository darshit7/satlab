# Geometry

## What it is

Given Earth-fixed satellite positions (see [orbits](orbits.md)), geometry answers three
questions for the session's station:

- **Where is it in my sky?** Azimuth, elevation, slant range and range rate.
- **Where is it over the Earth?** The sub-satellite point (WGS84 latitude, longitude) and height.
- **From where on Earth can it be seen?** Its footprint: the ground from which it stands above the
  horizon mask.

"In view" means **elevation ≥ the horizon mask** (`SATLAB_HORIZON_MASK_DEG`, 5° by default). That
is geometric truth, not naked-eye visibility: most satellites in view are far too faint to see.

## Why it works this way

- **WGS84, not a sphere.** The station sits on the ellipsoid. Its local "up" is the geodetic
  vertical, and sub-points are geodetic latitudes. A spherical Earth would misplace both by up to
  ~0.2°.
- **Degrees and metres at the boundary, radians inside.** Every API field carries its unit in its
  name (`elevation_deg`, `range_m`, `range_rate_m_s`).
- **One mask test everywhere.** The same `elevation ≥ mask` comparison decides the visible set,
  AOS and LOS, the in-view parts of ground tracks, and the footprint. So the cone on the globe, the
  dots in the sky views and the pass times agree.

## How it flows

```text
r, v (Earth-fixed)          observer (WGS84 lat, lon, alt)
        │                              │
        ├── d = r − r_station ◀────────┘  (station's Earth-fixed position)
        │
        ├── ENU = R_enu · d               east / north / up axes at the station
        │     elevation = asin(up / |d|)
        │     azimuth   = atan2(east, north)  mod 360   (clockwise from true north)
        │     range     = |d|
        │     range_rate = (d · v) / |d|     positive while receding
        │
        └── geodetic(r) → lat, lon, height  (iterative WGS84 inverse, < 1 mm in 5 iterations)
```

- {py:func}`~app.sim.geometry.observe` runs the whole chain over many satellites and times;
  {py:func}`~app.sim.geometry.elevations` is the cheap elevation-only version that pass searches
  scan.
- {py:func}`~app.sim.geometry.off_nadir_deg` is the angle at the satellite between its nadir and
  the station. It tells where the station sits in a nadir-pointing antenna's pattern, which feeds
  the [link budget](link-budget.md) and the attitude subsystem.
- {py:func}`~app.sim.geometry.mask_footprint_deg` gives the footprint radius as an Earth central
  angle: `λ = arccos(R/r · cos ε) − ε` for mask elevation `ε`, with `R` the ellipsoid's radius under
  the satellite. The client draws it as a small circle around the sub-point.
- {py:func}`~app.sim.geometry.ground_track` samples an orbit from half a period back to one period
  ahead (`SATLAB_GROUND_TRACK_POINTS` samples). Each sample has its sub-point, height and whether the
  station sees it above the mask. The exact AOS/LOS crossings between samples are found by
  bisection ({py:func}`~app.sim.passes.mask_crossings`), so the globe can split the orbit precisely
  where it enters and leaves the cone.

## The visibility cone

On the globe the station's **cone** is drawn from these numbers. Its outer surface is the horizon
mask; its inner surface is the elevation band where the selected satellite's link margin would be
green at its current altitude (`strong_link_elev_bands_deg`, from the link budget). The cone ends on
the satellite's altitude shell. At a 5° mask a LEO cone is a wide, shallow dish (about 2,950 km
across and 260 km high), so it reads as a "V" only from a low camera angle. The client builds the
cone as presentation only: no state is decided from it.

## Where in the code

{py:mod}`app.sim.geometry`. Tests: `backend/tests/test_geometry.py` (against skyfield's
`look_angles` / `sub_point`) and `test_vector_geometry.py`.
