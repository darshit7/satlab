# Change detection

## What it is

For two radar images of the same area from the same track, the detector flags **regions whose
backscatter changed** past a threshold. A region is either brighter after (an increase) or darker
after (a decrease), with its area and its mean change in dB. The compare dialog colours them,
fuchsia for an increase and cyan for a decrease, and lists them largest-scoring first.

## Why it works this way

- **Explainable first** (design doc §9.4). Every step is a textbook operation with one or two
  parameters, so a flagged region can be traced back to the pixels. A learned model may come later.
- **A ratio, in dB.** Radar backscatter is multiplicative: speckle scales the signal rather than
  adding to it. So a change is a ratio, and the log-ratio Δ = 10·log10(after / before) treats a
  doubling and a halving alike (±3 dB).
- **Speckle first.** A single radar pixel fluctuates strongly even over unchanged ground. A Lee
  filter averages it out where the scene is uniform and keeps edges where it isn't.
- **Otsu, with a floor.** Otsu's method splits the |Δ| histogram where the two groups are most
  distinct: unchanged pixels and changed ones. If nothing changed there is only one group, and Otsu
  would still split its noise. The **floor** (3 dB by default) means nothing under that is ever
  flagged.
- **Regions, not pixels.** Opening removes isolated pixels and closing fills pinholes. Then only
  connected regions above a minimum area (5000 m², 50 pixels) are reported.
- **The same stretch for both dates.** The before and after images are drawn with one fixed dB
  range per polarization, so a brighter pixel is really a stronger return, not a different contrast
  setting.

## How it flows

```{mermaid}
flowchart TB
  B[before, linear γ⁰] --> LB[Lee filter]
  A[after, linear γ⁰] --> LA[Lee filter]
  LB --> R["Δ = 10·log10(after / before)"]
  LA --> R
  R --> O["T = max(Otsu on |Δ|, floor)"]
  O --> M[masks: Δ ≥ T and Δ ≤ −T]
  M --> MO[opening, closing]
  MO --> L[label 8-connected,<br/>drop small regions]
  L --> RG[regions ranked by<br/>area × mean Δ]
  L --> PNG[change.png codes]
```

1. **Valid pixels**: finite and positive on both dates. If fewer than half are, the comparison
   fails as "no coverage" ({py:class}`~app.sar.detect.NoCoverage`).
2. **Lee filter** ({py:func}`~app.sar.detect.lee_filter`): a 7 × 7 window, 4.4 equivalent looks.
   Nodata doesn't bleed into its neighbours.
3. **Log-ratio** of the filtered images.
4. **Threshold** ({py:func}`~app.sar.detect.otsu_threshold`) on |Δ|, clipped to 20 dB, 256 bins,
   then raised to the floor. When the two groups are well separated, Otsu's criterion is flat across
   the empty gap between them, and the middle of that gap is taken.
5. **Masks** per sign, cleaned by one opening and one closing.
6. **Regions** ({py:func}`~app.sar.detect.detect_changes`): labelled, filtered by area, scored by
   area × |mean Δ|. At most 50 are listed, and the dialog says when there are more. More than a
   quarter of the area changed is flagged **widespread**: read it with caution.
7. **Images** ({py:mod}`app.sar.render`): `before.png` and `after.png` are grey + alpha.
   `change.png` is one band of codes: 0 no data, 1 valid and unflagged, 2–127 a flagged decrease and
   129–254 a flagged increase, from the threshold up to 10 dB. The browser colours it from the design
   tokens.

The parameters are seed data, `app/data/seed/sar_change_detection.json`, validated strictly and
versioned. Each comparison stores the version that produced it (see the
[seed data reference](../reference/seed-data.md)).

## Where in the code

- {py:mod}`app.sar.detect`: the detector (numpy and `scipy.ndimage`).
- {py:mod}`app.sar.params`: the parameters and their bounds.
- {py:mod}`app.sar.render`: the images.
- Tests: `backend/tests/test_sar_detect.py`. It covers Otsu on analytic bimodal data, a no-change
  speckle pair that must flag nothing, planted +8 dB and −7 dB blocks found with the right sign and
  area, the floor, truncation and coverage.

## Limits

- **It says where, not what.** Backscatter also changes with soil moisture, crop growth, harvest,
  snow, wind on water and ships. A flagged region is a place to look, not a finding.
- **Same track is required, not sufficient.** Steep slopes still differ slightly between passes,
  and RTC data can't recover ground in radar shadow or layover. Those pixels are nodata.
- **Cross-satellite pairs** (Sentinel-1A against -1C or -1D) are assumed comparable. Their
  calibration differences are well under the 3 dB floor, but this hasn't been measured here.
- **The parameters are generic.** They were checked on synthetic speckle and on the 2024 Valencia
  flood, not tuned per landscape.
