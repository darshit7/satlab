# Link budget

## What it is

For satellites with a **published downlink budget**, the simulator computes the received Eb/N0 at
the session's station, the **margin** over what the receiver needs, and the **Doppler shift**,
continuously as the geometry changes. The margin maps to RAG:

- **green** above `SATLAB_LINK_GREEN_MARGIN_DB` (6 dB);
- **amber** from `SATLAB_LINK_AMBER_MARGIN_DB` (0 dB) up to that;
- **red** below it, or whenever the satellite is below the horizon mask.

A satellite without a published budget has no link state at all. It shows as "no RF data" in grey,
never as a guessed margin.

## Why it works this way

- **Only sourced numbers.** Every value in a link profile is quoted from, or derived (with the
  formula written down) from, a public document. Each source's own worked margin is reproduced by
  the tests to 0.1 dB. That is why only five satellites have profiles (NOAA 20 and 21, FengYun-3D,
  GOES 18 and 19): amateur cubesats, Meteor and Metop don't publish the EIRP the budget needs.
- **One computation, many views.** The readout's margin, the sky-view dot colour, the pass-arc
  colours, the ground track's colours on the globe, the RF subsystem and the "Link OK" filter all
  come from {py:meth}`~app.sim.linkbudget.LinkTable.evaluate`. They can't disagree.

## How it flows

The margin, per sample (in dB):

```text
Eb/N0  = EIRP(θ) − FSPL(d, f) − L_atm(el) − L_sys + G/T − 10·log10(k) − 10·log10(R)
margin = Eb/N0 − required Eb/N0

FSPL(d, f) = 20·log10(4π · d · f / c)
L_atm(el)  = L_atm(ref) · sin(ref) / sin(el)       (cosecant law; el held at ≥ 2°)
Doppler    = −(range rate) · f / c                 (received − transmitted; + while approaching)
```

| Term | Meaning | From |
| ---- | ------- | ---- |
| `EIRP(θ)` | transmitted power towards the station; `θ` is the station's off-nadir angle | profile: EIRP plus an optional pattern, interpolated |
| `d` | slant range | [geometry](geometry.md) |
| `f` | downlink frequency | profile |
| `L_atm` | atmospheric and rain loss, quoted at one elevation and scaled along the slant path | profile |
| `L_sys` | every other loss the source lists (pointing, polarisation, demodulation, …) | profile |
| `G/T` | the source's reference receiving station's figure of merit | profile |
| `k` | Boltzmann's constant (−228.6 dBW/(Hz·K)) | physics |
| `R` | the data rate the source's Eb/N0 refers to | profile |

**Isoflux antennas.** Some satellites' antennas radiate more power off nadir, to make up for the
longer path to a station near the horizon. A profile can carry that pattern as (off-nadir angle,
EIRP offset) points. That is why the margin can be better at 20° elevation than overhead, and why
the "strong-link" region can be a ring rather than a disc.

**Strong-link bands.** For the selected satellite at its current altitude,
{py:func}`~app.sim.linkbudget.strong_link_bands` finds the elevation bands where the margin would be
green. It uses spherical geometry around the station, on a 0.25° grid with interpolated edges. The
sky views draw them as the faint green region, and the globe as the inner cone.

**Doppler.** The station hears the downlink shifted by the range rate: in LEO up to about ±170 kHz at
X-band (7.8 GHz), a few kHz at VHF. The Communications card shows the frequency and its live shift. A satellite with a link
profile uses the profile's frequency. Others use a cited frequency from the packet-template seed.

## Limits

- The budget assumes **the source's reference station**, whose G/T is part of the profile, and a
  **nadir-pointing** satellite.
- Atmospheric loss is the source's quoted value scaled geometrically; no weather is modelled.
- RAG is the link only; whether the satellite is transmitting at all isn't modelled.

## Where in the code

- {py:mod}`app.sim.linkbudget`: `LinkProfile`, `LinkTable.evaluate`, `rag_codes`,
  `strong_link_bands`.
- The profiles: `backend/app/data/seed/link_profiles.json`, shown in full in the
  [seed data](../reference/seed-data.md) with every source and check.
- Tests: `backend/tests/test_linkbudget.py` (each source's worked margin, RAG thresholds, pattern,
  cosecant scaling, Doppler sign).
- To add a satellite: [add a link profile](../how-to/add-link-profile.md).
