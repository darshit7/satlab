# Communications

## What it is

When the selected satellite is in contact, the station exchanges framed packets with it:

- **Telemetry comes down.** For some satellites it is simulated from the modelled subsystems and
  sent through a channel whose bit errors follow the real link margin. For others it is real frames
  received by SatNOGS stations, replayed.
- **Commands go up.** They are simulated: nothing is ever transmitted. Their answers (an
  acknowledgement, a telemetry packet, or the frame repeated by a digipeater) come back down through
  the same channel.

Every frame is real bytes in a real format: CCSDS space packets or AX.25 UI frames, with real
CRCs, encoded and decoded by bounds-checked codecs.

## Why it works this way

- **The antenna follows the selection.** The station has one antenna, so the contact is the
  selected satellite while it is above the mask, has packet templates, and the clock runs no faster
  than `SATLAB_COMMS_MAX_TIME_SCALE` (60×). Faster than that, the contact pauses ("too fast"):
  frames every few seconds can't be followed at 600×.
- **Real where it can be, labelled where it can't.** The one-way light time is real: range divided
  by c. Bit errors are modelled, and only for satellites with a published link budget; for others
  the channel isn't modelled and frames arrive as sent. Uplink budgets aren't published, so the
  uplink isn't modelled: a command is sent only in contact. Some housekeeping layouts are
  illustrative (no public ICD), and the packet log says so.
- **Deterministic.** Beacons sit on an absolute sim-time grid (multiples of their interval since
  the Unix epoch). Bit flips are drawn from a generator seeded by (session, packet). The same packet
  is therefore corrupted the same way on replay, and a clock jump causes no backlog.
- **Bounded.** Each tick sends at most `SATLAB_COMMS_MAX_PACKETS_PER_TICK` frames. A session has a
  command budget per minute and a stored-command cap. Every decoder checks declared lengths
  against the bytes present and the protocol maximum.

## How it flows

**Down: a beacon.**

```{mermaid}
sequenceDiagram
  participant S as Satellite (sim)
  participant C as Channel
  participant G as Station
  Note over S: interval due on the absolute grid
  alt simulated telemetry
    S->>S: encode housekeeping from HealthState<br/>(template: fields, scaling, CRC)
    S->>C: frame
    C->>C: BER from link margin → bit flips<br/>(seeded per packet)
    C->>G: frame as received, after range / c
    G->>G: decode with the same template,<br/>the CRC decides crc_ok
  else replayed (SatNOGS)
    S->>G: next real archived frame for this template<br/>(already decoded by a real station)
  end
  G-->>G: packet message → client, frame → telemetry_frame
```

**Up: a command and its lifecycle.**

```{mermaid}
stateDiagram-v2
  [*] --> queued: send_command (in contact)
  [*] --> scheduled: schedule_command (a future pass)
  scheduled --> queued: released at AOS (no subsystem red)
  scheduled --> held: a subsystem red at AOS
  scheduled --> aborted: missed pass · cancelled · antenna taken
  queued --> sent: next tick in contact
  queued --> aborted: contact lost before sending · another satellite in contact · clock moved back
  sent --> acked: answer arrives intact
  sent --> failed: answer corrupted or lost · contact lost first
  acked --> [*]
  failed --> [*]
  held --> [*]
  aborted --> [*]
```

1. **Submit.** The session manager checks the command against its budget, the satellite's
   template, the parameters' bounds and the contact. Then it stores the `command` row as `queued`
   and hands it to {py:class}`~app.sim.comms.CommsRuntime`.
2. **Send.** On the next tick in contact the command is framed (CCSDS telecommand with an opcode,
   or an APRS message in an AX.25 frame) and marked `sent`. Its answer is due after twice the light
   time plus the template's on-board delay.
3. **Answer.** The answer is built from the template (an acknowledgement, a requested telemetry
   packet, or for the ISS the digipeated copy of the frame with its H bit set) and sent down through
   the channel. If it arrives intact the command is `acked`; if it is corrupted or lost (a weak
   link) the command `failed`.
4. **Effects.** `SET_BEACON_INTERVAL` changes this session's beacon cadence for that satellite.
   Nothing changes for anyone else.

**The channel** ({py:mod}`app.sim.channel`) maps the link margin to a bit error rate on the
uncoded-BPSK curve, anchored so that a margin of 0 dB (the published requirement) gives a BER of
1e-5:

```text
BER(margin) = Q(√(2 · Eb/N0)),   Eb/N0 = E_ref + margin   (dB),   E_ref ≈ 9.59 dB
```

Real coded links fall off a much steeper cliff below their threshold. This gentler curve makes the
degradation near the horizon visible instead of abrupt, and the code says it is modelled.

**Doppler and sound.** The Communications card shows the frequency the station listens on and its
Doppler shift. That comes from the link profile when there is one, or from the cited frequency in
the packet-template seed. In the browser, optional sound cues follow *your* commands: a rising chirp
as one goes up, a two-note chime when its answer comes back intact, a low bonk when it doesn't.
Telemetry the satellite sends on its own is silent. A continuous Doppler tone was tried and
dropped as tiring.

## The satellites

| Satellite | Framing | Downlink | Commands |
| --------- | ------- | -------- | -------- |
| NOAA 20, NOAA 21, FengYun-3D, GOES 18, GOES 19 | CCSDS | simulated housekeeping (illustrative layout), channel modelled from the link budget | PING, REQUEST_HK, SET_BEACON_INTERVAL |
| ISS | AX.25 / APRS | real frames from the 145.825 MHz digipeater, replayed | APRS_MESSAGE, answered by the digipeated copy |
| CroCube | AX.25, CSV beacons | real frames, replayed | none (receive-only) |
| UWE-4 | AX.25, binary | real frames, replayed | none (receive-only) |

The templates, their sources and the cited frequencies are in the
[seed data](../reference/seed-data.md); to add one, see
[add a packet template](../how-to/add-packet-template.md).

## Where in the code

- {py:mod}`app.sim.comms` (the runtime), {py:mod}`app.sim.channel` (the channel).
- {py:mod}`app.packets` (codecs and templates), {py:mod}`app.services.satnogs` (the archive).
- {py:mod}`app.services.comms` (persistence).
- Client: `frontend/src/views/Comms/`.
- Tests: `test_packets.py`, `test_channel.py`, `test_comms.py`, `test_ws_comms.py`.
