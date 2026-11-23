# SAR imagery

Real radar images of the Earth from the public Sentinel-1 archive, compared on the server. Unlike
the rest of the simulator, nothing here is simulated: the scenes are what the satellites recorded,
and the change detector runs on their pixels.

- [Archive explore](archive-explore.md): pick an area and two dates, find the passes, compare them.
  How the archive is searched, how scenes are read safely, where the images live and for how long.
- [Change detection](change-detection.md): the v1 detector (Lee filter, log-ratio, Otsu threshold
  with a floor, regions), its parameters, and what a flagged region does and doesn't mean.

Tasking a capture over a future pass (Flow 2 in the design) comes later; so do Umbra's
high-resolution spotlight scenes.
