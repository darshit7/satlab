import { describe, expect, it } from 'vitest'
import type { CommandParam, LinkProfiles, PacketTemplates, SatelliteComms } from '../types/api'
import type { CommandStatusMessage, PacketMessage } from '../types/ws'
import {
  appendPackets,
  commsCapabilities,
  commsSatellites,
  formatLightTime,
  latestDecoded,
  mergeStatuses,
  packetOutcome,
  parseParam,
  satnogsObservationUrl,
} from './comms'

const packet = (seq: number, over: Partial<PacketMessage> = {}): PacketMessage => ({
  type: 'packet',
  seq,
  norad_id: 43013,
  direction: 'down',
  kind: 'telemetry',
  template: 'hk',
  label: 'Housekeeping',
  framing: 'ccsds',
  source: 'simulated',
  sim_time: '2026-10-10T17:52:00Z',
  size_bytes: 24,
  raw_hex: '00',
  delivered: true,
  crc_ok: true,
  bit_errors: 0,
  ber: 1e-12,
  margin_db: 8,
  one_way_light_time_s: 0.004,
  decoded: [{ name: 'soc_pct', value: 88, unit: '%' }],
  layout_sourced: false,
  ...over,
})

describe('appendPackets', () => {
  it('keeps the newest, in order, without duplicates (a reconnect replays recent ones)', () => {
    const first = appendPackets([], [packet(1), packet(2)])
    const again = appendPackets(first, [packet(2), packet(3)], 2)
    expect(again.map((p) => p.seq)).toEqual([2, 3])
  })
})

describe('mergeStatuses', () => {
  const status = (id: number, s: CommandStatusMessage['status']): CommandStatusMessage => ({
    type: 'command_status',
    command_id: id,
    norad_id: 43013,
    command: 'PING',
    status: s,
    sim_time: '2026-10-10T17:52:00Z',
  })

  it('keeps the latest status per command, newest commands only', () => {
    const merged = mergeStatuses({}, [status(1, 'queued'), status(1, 'sent'), status(2, 'queued'), status(3, 'acked')], 2)
    expect(Object.keys(merged).map(Number).sort()).toEqual([2, 3])
    expect(mergeStatuses(merged, [status(3, 'failed')])[3]!.status).toBe('failed')
  })
})

describe('latestDecoded and outcomes', () => {
  it('shows the newest intact downlink of this satellite', () => {
    const packets = [packet(1), packet(2, { crc_ok: false, decoded: null }), packet(3, { direction: 'up' }), packet(4, { norad_id: 25544 })]
    expect(latestDecoded(packets, 43013)?.seq).toBe(1)
    expect(packets.map(packetOutcome)).toEqual(['ok', 'corrupted', 'sent', 'ok'])
    expect(packetOutcome(packet(5, { delivered: false, crc_ok: null }))).toBe('lost')
  })
})

describe('satnogsObservationUrl', () => {
  it('builds links from integer ids only', () => {
    expect(satnogsObservationUrl(1234)).toBe('https://network.satnogs.org/observations/1234/')
    expect(satnogsObservationUrl(null)).toBeNull()
    expect(satnogsObservationUrl(1.5)).toBeNull()
    expect(satnogsObservationUrl(-3)).toBeNull()
  })
})

describe('parseParam', () => {
  const interval: CommandParam = { name: 'interval_s', label: 'Interval', type: 'int', min: 10, max: 120, max_length: 32, charset: 'printable' }
  const to: CommandParam = { name: 'addressee', label: 'To', type: 'text', min: null, max: null, max_length: 9, charset: 'callsign' }
  const text: CommandParam = { name: 'text', label: 'Message', type: 'text', min: null, max: null, max_length: 67, charset: 'printable' }

  it('mirrors the server checks', () => {
    expect(parseParam(interval, ' 30 ')).toEqual({ value: 30 })
    expect(parseParam(interval, '5')).toEqual({ error: 'at least 10' })
    expect(parseParam(interval, '3.5')).toEqual({ error: 'a whole number' })
    expect(parseParam(to, 'w1aw-7')).toEqual({ value: 'W1AW-7' })
    expect(parseParam(to, 'not a call')).toHaveProperty('error')
    expect(parseParam(text, 'café')).toEqual({ error: 'plain ASCII characters only' })
    expect(parseParam(text, 'x'.repeat(68))).toHaveProperty('error')
  })
})

describe('formatLightTime', () => {
  it('reads in ms for LEO and GEO', () => {
    expect(formatLightTime(0.0042)).toBe('4.2 ms')
    expect(formatLightTime(0.1194)).toBe('119.4 ms')
    expect(formatLightTime(null)).toBe('—')
  })
})

describe('commsCapabilities', () => {
  const sat = (norad_id: number, commands: number): SatelliteComms => ({
    norad_id,
    satellite_name: `#${norad_id}`,
    telemetry: [],
    commands: Array.from({ length: commands }, (_, i) => ({
      name: `C${i}`,
      label: `C${i}`,
      framing: 'ccsds' as const,
      params: [],
      response: 'ack' as const,
      layout_sourced: false,
      sources: [],
    })),
  })

  it('tells commandable satellites from receive-only ones, and knows nothing without templates', () => {
    const templates: PacketTemplates = { satnogs_configured: false, satellites: [sat(43013, 3), sat(62394, 0)] }
    const caps = commsCapabilities(templates)
    expect(caps.get(43013)).toBe('command')
    expect(caps.get(62394)).toBe('receive')
    expect(caps.get(44713)).toBeUndefined() // a Starlink: no templates
    expect(commsCapabilities(null).size).toBe(0)
  })

  it('lists them commandable first, then by name, with or without a link budget', () => {
    const named = (norad: number, name: string, commands: number) => ({ ...sat(norad, commands), satellite_name: name })
    const templates: PacketTemplates = {
      satnogs_configured: true,
      satellites: [named(62394, 'CROCUBE', 0), named(43013, 'NOAA 20', 3), named(25544, 'ISS (ZARYA)', 1)],
    }
    const profiles = { profiles: [{ norad_id: 43013 }] } as unknown as LinkProfiles
    expect(commsSatellites(templates, profiles)).toEqual([
      { noradId: 25544, name: 'ISS (ZARYA)', capability: 'command', linkBudget: false },
      { noradId: 43013, name: 'NOAA 20', capability: 'command', linkBudget: true },
      { noradId: 62394, name: 'CROCUBE', capability: 'receive', linkBudget: false },
    ])
    // without the link profiles, don't claim "no RF data"
    expect(commsSatellites(templates, null).map((s) => s.linkBudget)).toEqual([null, null, null])
    expect(commsSatellites(null, profiles)).toEqual([])
  })
})
