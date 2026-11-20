# Add a link profile

A link profile gives a satellite a real link margin, Eb/N0, Doppler and RAG colour. It must come
from a **published** downlink budget. Never estimate a missing value: a satellite without a
complete public budget stays "no RF data".

## 1. Find a source that has everything

You need, from one budget (or consistent documents):

- the downlink frequency and the EIRP (and the antenna pattern, if it isn't omni-directional);
- the reference receiving station's G/T;
- every loss the budget lists, plus the atmospheric loss at one stated elevation;
- the required Eb/N0 and the data rate it refers to;
- ideally, the source's own worked margin at a given range and elevation, so the tests can
  reproduce it.

## 2. Add the entry

Edit `backend/app/data/seed/link_profiles.json`. The file's `_comment` explains each field. Copy an
existing profile (the pattern, source and derivation below are placeholders):

```json
{
  "norad_id": 43013,
  "satellite_name": "NOAA 20 (JPSS-1)",
  "label": "X-band HRD 7812 MHz, 15 Mbps QPSK, to a 3 m direct-broadcast dish",
  "frequency_hz": 7812000000,
  "eirp_dbw": 12.4,
  "eirp_pattern": [[0, 0.0], [30, 1.5], [62, 4.0]],
  "coverage_half_angle_deg": 62.0,
  "station_gt_db_k": 22.7,
  "system_loss_db": 3.96,
  "atmospheric_loss_db": 3.65,
  "atmospheric_ref_elev_deg": 5.0,
  "required_ebn0_db": 4.4,
  "data_rate_bps": 13117647,
  "sources": [{"title": "Source document title", "url": "https://example.org/budget.pdf"}],
  "derivations": {"system_loss_db": "sum of the listed losses (Table 3-2)"},
  "checks": [{"range_km": 2835.0, "elevation_deg": 5.0, "off_nadir_deg": 61.9,
              "margin_db": 1.18, "where": "Fig. A-1, 5° elevation"}]
}
```

- Every value that isn't quoted directly gets a `derivations` entry with its formula.
- Sources are `https:` only (the schema rejects anything else).
- Bump `verified_on` (and `version` if the format changed).

## 3. Check it

```bash
cd backend
uv run pytest tests/test_linkbudget.py tests/test_seed.py
```

`test_linkbudget.py` reproduces every `checks` entry to 0.1 dB with the same code the tick runs. A
profile without a check fails.

## 4. What happens next

On the next catalog rebuild (startup, or any ingest), {py:func}`app.data.seed.sync_link_profiles`
syncs the seed to `link_profile` for satellites already in the catalog. The fleet's `LinkTable`
then aligns it with its row, and the satellite gets a `link` in every snapshot. Because the seed is
authoritative, removing an entry deletes the row.

## Docs to update

- The [seed data reference](../reference/seed-data.md) regenerates on its own.
- [Link budget](../simulation/link-budget.md) lists the satellites with profiles; update it.
- If the satellite also gets packet templates, see [communications](../simulation/comms.md).
