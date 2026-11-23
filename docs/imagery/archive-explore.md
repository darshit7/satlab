# Archive explore

## What it is

The **SAR Imagery** panel compares two real Sentinel-1 radar images of one area:

1. Pick the area: a 5, 10 or 20 km square around the ground station, a map click or typed
   coordinates.
2. Pick a "before" and an "after" date.
3. The server lists the satellite passes over the area within 12 days of each date, grouped by
   **track**, and proposes the pair on one track closest to both dates. You can pick another.
4. **Compare** queues the job. The server reads just the area from both scenes, aligns them on one
   grid, flags what changed and draws three images. The compare dialog shows them with a swipe and a
   change heatmap, and lists the flagged regions, which are also drawn on the map and the globe.

Each region also names the satellite that took the scenes. A Sentinel-1 satellite's inspector has
an **Archive imagery** button that opens the panel with only that satellite's passes.

## Why it works this way

- **Real data, read honestly.** The scenes are the Sentinel-1 RTC product on Microsoft Planetary
  Computer: terrain-corrected backscatter (γ⁰) at 10 m. Each band of each scene is about 2 GB, so
  the server reads only the area's window over HTTP range requests and never downloads a scene.
- **Only same-track pairs.** A track is a relative orbit plus a direction. Two passes on the same
  track see the ground from the same angle, so a difference between them is a difference on the
  ground. Pairs across tracks would mostly flag viewing geometry, so they are never offered.
  Sentinel-1C and -1D share the tracks, 6 days apart, so a pair may mix them.
- **A pass is several frames.** The archive cuts each pass into consecutive scenes a few dozen
  seconds apart. An area across a cut is covered by two of them, so a pass is read as every frame
  touching the area, mosaicked.
- **The server computes, the client draws** (golden rule 1). Coverage, the grid, the threshold and
  the regions all come from the server. The client only colours the change image with the design
  tokens and lays out the swipe.
- **Comparisons belong to a session.** They need its token, like the pass schedule, and last for the
  page visit: a reload starts a new session, and the old one's comparisons are purged with the
  retention (`SATLAB_COMMS_RETENTION_DAYS`). Scene metadata and window crops are a shared cache of
  public data, like the satellite catalog.
- **The simulation loop is never slowed.** Raster reads and the numpy/scipy work run in a separate,
  lower-priority process, spawned for each comparison, one at a time.

## How it flows

```{mermaid}
flowchart TB
  P[Imagery panel] -->|GET .../sar/scenes| S[SarService.search]
  S -->|global budget, cache| STAC[(Planetary Computer STAC)]
  S --> G[passes by track,<br/>proposed pair]
  G --> P
  P -->|POST .../sar/comparisons<br/>STAC ids only| C[SarService.create]
  C -->|checks: same track, order,<br/>polarization, coverage| Q[JobRunner queue]
  Q --> W[worker process]
  W -->|/vsiaz/ window reads,<br/>SAS in GDAL config| BLOB[(Azure blob storage)]
  W --> D[detect_changes]
  D --> I[before / after / change PNG]
  P -->|poll every 2 s| C
  I -->|GET .../images/*.png| V[Compare dialog]
```

1. **Search** ({py:meth}`~app.services.sar.SarService.search`): the area becomes a square grid in its
   UTM zone ({py:func}`~app.sar.grid.aoi_grid`). The STAC API is queried for each date's window
   ({py:class}`~app.sar.stac.StacClient`), cached per area and window. Items are validated, frames
   are grouped into passes ({py:func}`~app.sar.pairs.group_passes`) with a coverage estimate from
   their footprints, and passes are paired by track ({py:func}`~app.sar.pairs.propose`). Nothing is
   written to the database.
2. **Compare** ({py:meth}`~app.services.sar.SarService.create`): the client sends STAC ids, never
   URLs. The server resolves them from its own search results (or the item endpoint), checks the pair
   again, stores the scenes and a `queued` comparison, and queues it.
3. **Read** ({py:mod}`app.sar.worker`): for each frame, a cached crop for this grid, or a window
   read through GDAL ({py:func}`~app.sar.raster.read_into_grid`), reprojected onto the grid and
   saved as a crop. Frames of a pass fill each other's gaps.
4. **Detect and draw**: see [change detection](change-detection.md). The three PNGs go into a fresh
   directory per run (`comparisons/<id>/<generation>/`).
5. **Show**: the client polls the list every 2 s while anything runs. When a comparison is ready it
   fetches the images with the session token and opens the dialog.

### Reading scenes safely

The amended raster rule (AGENTS.md §12.4) in code:

- **Hrefs are checked at the parse boundary** ({py:func}`~app.sar.assets.blob_ref`). An item's asset
  must be https on the one allowed storage account and container, with a plain blob name ending in
  `.tif`. Items that fail are dropped from the search.
- **GDAL opens only paths the server built**: `/vsiaz/<container>/<blob>`. The account and the SAS
  token are GDAL config options ({py:func}`~app.sar.protocol.gdal_options`), never part of the path,
  so neither appears in a path, a log line or an error. Only the GTiff driver is allowed.
- **The file is checked before any pixel is read**: one float32 band, the CRS the item announced,
  10 m pixels, sane dimensions and tiles. The source window for the area is computed and bounded
  ({py:class}`~app.sar.protocol.SourceLimits`).
- **The worker's environment is scrubbed** and its log lines are redacted
  ({py:func}`~app.sar.worker.redact`). Failures come back as a fixed set of error codes
  ({py:data}`~app.sar.protocol.ERROR_MESSAGES`), never as exception text.
- **SAS tokens** come anonymously from the token endpoint ({py:class}`~app.sar.token.SasTokenProvider`).
  They last about 45 minutes and are refreshed before a job could outlive one. They are kept in
  memory only. An optional subscription key (`SATLAB_SAR_SUBSCRIPTION_KEY`) only relaxes rate
  limits.

### Budgets

| Budget | Default | Setting |
| ------ | ------- | ------- |
| Upstream requests (STAC + token), all clients | 30 per min | `SATLAB_SAR_UPSTREAM_REQUESTS_PER_MIN` |
| Searches per session | 6 per min | `SATLAB_SAR_SEARCHES_PER_MIN` |
| Comparisons per session (recomputes included) | 6 per hour | `SATLAB_SAR_COMPARISONS_PER_HOUR` |
| Comparisons, all sessions | 30 per hour | `SATLAB_SAR_COMPARISONS_PER_HOUR_TOTAL` |
| Kept per session | 12 | `SATLAB_SAR_MAX_COMPARISONS_PER_SESSION` |
| Waiting for the worker | 4 | `SATLAB_SAR_QUEUE_MAX` |
| One comparison | 240 s, then the worker is killed | `SATLAB_SAR_JOB_TIMEOUT_S` |

Search and compare need a **live** session (one with a client attached). Anyone can open a session,
so per-session budgets alone could be dodged; the cap on live sessions and the global budgets bound
the rest. A 429 from Planetary Computer backs every client off for its `Retry-After`.

### Where the images live

Under `SATLAB_SAR_STORAGE_DIR` (default `backend/var/sar`), as an **ephemeral cache**:

- `crops/<digest>.tif`: a scene's window on one grid (float32, compressed), shared by every
  comparison of that area.
- `comparisons/<id>/<generation>/{before,after,change}.png`: one run's images.

Paths are built from ids and digests only, and every one is checked to resolve inside the root
({py:class}`~app.sar.storage.SarStorage`). Past `SATLAB_SAR_STORAGE_MAX_BYTES` the least recently
used entries go. A container restart wipes the cache, and a redeploy too. A comparison whose images
are gone reads as **expired**, and **Recompute** runs it again; it is quick when the crops are still
there.

## Where in the code

- {py:class}`app.services.sar.SarService`: search, comparisons, images, retention.
- {py:mod}`app.sar.grid`, {py:mod}`app.sar.pairs`: the grid (pure UTM math), passes, tracks and
  the proposal.
- {py:mod}`app.sar.stac`, {py:mod}`app.sar.token`, {py:mod}`app.sar.assets`, {py:mod}`app.data.http`:
  the upstream clients and their guards.
- {py:mod}`app.sar.jobs`, {py:mod}`app.sar.worker`, {py:mod}`app.sar.raster`: the queue, the
  worker process and the bounded reads.
- REST: `/api/sar/source` and `/api/sessions/{id}/sar/...` ([REST reference](../reference/rest.md)).
  Tables: `sar_scene`, `sar_comparison` ([database](../reference/database.md)).
- Tests: `backend/tests/test_sar_clients.py`, `test_sar_geometry.py`, `test_sar_detect.py`,
  `test_sar_api.py` (one of them spawns the real worker process).

## Limits

- **Archive only.** Nothing is captured on request: these are the passes the satellites already
  flew. The newest scene is the archive's newest, usually a few days old.
- **Sentinel-1 RTC only.** Umbra's spotlight scenes are a later step.
- **Coverage is estimated in a search** from the footprints and **measured after reading**. A pass
  estimated under 30 % of the area isn't offered. A comparison with under 50 % valid on both dates
  fails as "no coverage", from swath edges, terrain shadow or layover.
- **Areas near the poles or across the antimeridian** are refused: the grid is UTM, between 80° S
  and 84° N, and standard zones only.
- **Images last for the visit** and are a cache: expect "expired" after a redeploy.
