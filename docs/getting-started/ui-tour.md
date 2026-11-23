# A tour of the UI

The screen has three levels: the **header** (the sim clock and app-wide controls), the **mission
bar** with the **map** under it, and the **inspector** on the right, which is about the selected
satellite. Every number on screen comes from the server. The browser
[never computes physics](../architecture/overview.md): it renders what the WebSocket streams and
moves markers smoothly between updates.

## Header

The **sim clock** shows simulated UTC. **Play/pause** and the **rates** (1×, 10×, 60×, 600× and
3600×) control the session's clock; **Now** jumps back to the wall clock. On the right are the
connection status, **Docs** (this site, in a new tab), **About the data** (sources and honesty
notes) and the theme switcher.

The clock is the session's, kept on the server. A rate change re-anchors it, so sim time never
jumps; see {py:class}`~app.sim.clock.SimClock`.

## Mission bar and map

Each mission-bar button opens a floating panel over the map:

Ground station
: Where you observe from: a preset station, your browser's geolocation, a click on the map, or a
  typed latitude and longitude. Changing it starts a new session there; a custom location belongs to
  that session only.

Catalog
: Search the whole catalog (`/` focuses it) and **track** satellites. A tracked satellite is always
  streamed with its ground track, whether or not it is in view. Search runs on the local catalog
  (Celestrak's active satellites) as you type; when nothing matches, **Search all of Celestrak**
  asks Celestrak itself (debris, rocket bodies, inactive satellites), rate-limited for all users
  together. The line above the search says how old the catalog is and how often the server
  refreshes it ("fetched 3 h ago · refreshed every 12 h"). There's no refresh button: Celestrak
  updates every 2 hours and refuses a repeat download, so there's nothing newer to get on demand.

Overhead
: The satellites above the horizon mask right now, plus the ones **rising soon**. Filters (type,
  SAR-capable, Link OK, Own formats, minimum elevation, rising within N minutes) are applied on
  the server, so the map, both sky views and the lists always agree. The summary on the button says
  what is being filtered.

Schedule
: Commands waiting for a future pass, their outcomes, and alarms (acknowledge them here). The
  button shows how many commands are waiting and how many alarms are unacknowledged.

Imagery
: Real Sentinel-1 radar images of an area ([archive explore](../imagery/archive-explore.md)). Set
  the area (it starts at the station; click the map or the globe to move it; 5, 10 or 20 km) and two
  dates, and **Find scenes** lists the passes by orbit track, with the pair closest to your dates
  already chosen. **Compare** runs it on the server, which takes a few seconds to a minute. **Open**
  shows the compare dialog: swipe between the dates, turn the change heatmap on or off, and pick a
  flagged region to frame it, then **Show on map**. Comparisons last for the visit.

The map is a **3D globe** (Cesium) by default, or a **2D map** (MapLibre). It shows the satellites
in view as dots coloured by link state, tracked orbits, and the station's visibility cone. The outer
cone is the horizon mask; the inner one is where the link margin would be green. For the selection
it also shows the orbit, coloured by link margin, with marks where the satellite enters and leaves
the cone, and its footprint. The globe is lit by the Sun at sim time and the 2D map shades the night
side. If the globe can't start (no WebGL, or the chunk fails to load), the 2D map takes over.

## Inspector

Sky views
: A **polar sky plot** (azimuth around, elevation inward) and an **all-sky** view (looking up, as a
  fisheye camera would). Dots are satellites in view, coloured by link state (grey means "no RF
  data"). The selected pass is drawn as an arc coloured along its length, over a faint band where
  the link would be strong. Either view opens larger in a dialog.

The selection
: Pick a satellite in any view and the others follow. A sticky header gives the go/no-go rollup.
  Below it: azimuth, elevation, range, range rate, sub-point, altitude and the TLE's age; the link
  margin and Doppler shift when the satellite has a published link budget; then the next pass (the full 24-hour schedule opens in a dialog) and two collapsible sections:

  - **Systems**: power, thermal, storage, attitude and RF, each green, amber, red or no data,
    with the value behind it. RF is the [link budget](../simulation/link-budget.md); the others are
    [modelled](../simulation/subsystems.md) from generic parameters driven by real eclipse and pass
    geometry, and labelled so.
  - **Communications**: the contact state, a packet-flow strip (commands going up, frames coming
    down, intact, corrupted or lost), a command composer that sends now or books a command for a
    pass, recent command statuses, the packet log and the latest decoded frame. The downlink
    frequency and its live Doppler shift are shown, and optional sound cues follow your commands.
    Any satellite can be contacted: the eight with formats of their own (**Own formats** in
    Overhead, listed under the section's (i)) use theirs, every other one a generic, illustrative
    template, and debris has no radio. See [communications](../simulation/comms.md).

When a booked pass approaches, **pass automation** takes the antenna: the selection jumps to that
satellite and the header shows the countdown to the system check at AOS. See
[pass automation](../simulation/automation.md).

## Colour is never the only cue

Every link or subsystem state has a word and an icon as well as a colour: Go (green), Marginal
(amber), No-go (red), No data (grey). The thresholds are on the [settings](../reference/settings.md)
page (`SATLAB_LINK_GREEN_MARGIN_DB`, `SATLAB_LINK_AMBER_MARGIN_DB`), and the full design rules are in
the [design system](../project/design-system.md).
