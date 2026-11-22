"""SAR imagery: real Sentinel-1 RTC scenes from the public archive, compared on the server.

The API process uses only the light modules here (grid, STAC, token, assets, pairs, params,
protocol, storage, jobs). Raster reading, change detection and rendering need rasterio and scipy
and run only in the worker process (:mod:`app.sar.worker`), so the process that runs the
simulation loop never loads GDAL.
"""
