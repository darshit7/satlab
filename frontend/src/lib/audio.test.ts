import { describe, expect, it } from 'vitest'
import type { PacketMessage } from '../types/ws'
import { cueFor } from './audio'

const packet = (over: Partial<PacketMessage>): PacketMessage => ({
  type: 'packet',
  seq: 1,
  norad_id: 43013,
  direction: 'down',
  kind: 'ack',
  template: 'ack',
  label: 'Command acknowledgement',
  framing: 'ccsds',
  source: 'simulated',
  sim_time: '2026-10-10T17:52:00Z',
  size_bytes: 14,
  raw_hex: '0a01',
  delivered: true,
  crc_ok: true,
  bit_errors: 0,
  ber: 1e-9,
  margin_db: 4.7,
  one_way_light_time_s: 0.005,
  decoded: [],
  layout_sourced: false,
  ...over,
})

describe('cueFor', () => {
  it('chirps for a command going up', () => {
    expect(cueFor(packet({ direction: 'up', kind: 'command' }))).toBe('sent')
  })

  it('chimes for an answer that came back intact, whatever its kind', () => {
    expect(cueFor(packet({ kind: 'ack' }))).toBe('reply')
    expect(cueFor(packet({ kind: 'response' }))).toBe('reply')
    expect(cueFor(packet({ kind: 'digipeat', framing: 'ax25' }))).toBe('reply')
  })

  it('bonks for an answer lost or corrupted on the way down', () => {
    expect(cueFor(packet({ delivered: false, crc_ok: null }))).toBe('no-reply')
    expect(cueFor(packet({ crc_ok: false }))).toBe('no-reply')
  })

  it('is silent for telemetry the satellite sends on its own', () => {
    expect(cueFor(packet({ kind: 'telemetry', template: 'hk' }))).toBeNull()
    expect(cueFor(packet({ kind: 'telemetry', crc_ok: false }))).toBeNull()
  })
})
