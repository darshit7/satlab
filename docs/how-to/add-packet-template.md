# Add a packet template

Packet templates are what let the Communications section talk to a satellite. They say how its
frames are framed and laid out, how often it beacons, where the frames come from (simulated or
SatNOGS), and which commands it takes. Every satellite without templates of its own already uses
the generic set (illustrative), so add a satellite here when it has a format of its own worth
following: a published layout, real SatNOGS frames, or a published link budget.

## 1. Pick or write a template set

`backend/app/data/seed/packet_templates.json` has `template_sets` (reusable) and `satellites`
(which set each satellite uses). A new satellite with the same layout as an existing one only needs
a `satellites` entry. Otherwise add a set:

- **telemetry**: each with a `name`, `label`, `framing` (`ax25` with callsigns, or `ccsds` with
  an APID), a `payload` (`binary` fields at fixed offsets, `csv` values after a tag, or `aprs`), an
  `interval_s`, a `downlink` (`simulated` or `satnogs`), `layout_sourced` and `sources`;
- **commands** (optional): `name`, `label`, `framing`, `params` (bounded ints or text), the
  `response` (`ack`, `telemetry` or `digipeat`) and, for CCSDS, an `opcode`.

The schema is strict ({py:mod}`app.packets.templates`, `extra="forbid"`): an unknown key is an
error, so a typo can't silently drop a setting.

**Honesty rules.**

- A layout you can't cite is `layout_sourced: false`, and the UI labels it illustrative.
- `satnogs` frames must be real frames that decode cleanly with the template.
- A downlink frequency needs `downlink_sources` (for satellites without a link profile, it drives
  the Doppler shown).

**The generic set.** The top-level `generic` block names the set every other satellite uses
(`template_set`) and the TT&C antenna it assumes (`antenna_half_angle_deg`; 90 is omnidirectional,
which the attitude check uses for those satellites). Removing the block leaves only the listed
satellites with comms. Debris and rocket bodies never get it: that's the `no_radio` flag on their
rule in `satellite_types.json`.

## 2. Check it

```bash
cd backend
uv run pytest tests/test_seed_templates.py tests/test_packets.py tests/test_comms.py
```

For a SatNOGS-replayed satellite, decode a few real frames from the SatNOGS DB in a test, as the
existing ones do. That is how a template is shown to match reality.

## 3. What happens next

On the next catalog rebuild, {py:func}`app.data.seed.sync_packet_templates` upserts the templates
to `packet_template` (keyed by satellite, kind and name; the generic set's once, with no satellite;
dropped ones are marked inactive, not deleted). The fleet's `CommsTable` picks them up. The
satellite leaves the generic set and appears in the Communications list and under the Overhead
"Own formats" filter. If it replays SatNOGS frames, the archive starts fetching them on its next
check.

## Docs to update

- The [seed data reference](../reference/seed-data.md) regenerates.
- Update the table in [communications](../simulation/comms.md).
