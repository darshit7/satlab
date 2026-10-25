// Presentation of the comms link. Every packet, status and decoded value comes from the server;
// nothing here frames, corrupts or decodes anything.

import type { CommandParam, LinkProfiles, PacketTemplates } from '../types/api'
import type { CommandStatusMessage, PacketMessage } from '../types/ws'

export const MAX_PACKETS = 100
export const MAX_COMMANDS = 50
/** How long a packet takes to cross between station and satellite in the views: slowed down to be
 *  seen (a LEO one-way light time is ~5 ms; the real value is printed beside it). */
export const PACKET_FLIGHT_MS = 1400

/** What comms can do with a satellite: command it (and hear it), or only hear it. */
export type CommsCapability = 'command' | 'receive'

export const COMMS_CAPABILITY_WORDS: Record<CommsCapability, string> = {
  command: 'Communications: commands and telemetry',
  receive: 'Communications: receive-only',
}

/** Satellites with packet templates, from the reference data (absent: comms can't talk to it). */
export function commsCapabilities(templates: PacketTemplates | null): Map<number, CommsCapability> {
  return new Map((templates?.satellites ?? []).map((s) => [s.norad_id, s.commands.length > 0 ? 'command' : 'receive']))
}

export const COMMS_CAPABILITY_DETAIL: Record<CommsCapability, string> = {
  command: 'Commands and telemetry',
  receive: 'Receive-only',
}

export interface CommsSatellite {
  noradId: number
  name: string
  capability: CommsCapability
  /** A published link budget (the Link OK filter's satellites); null when that's not known. */
  linkBudget: boolean | null
}

/** Every satellite comms works with, commandable ones first, then by name (reference data). */
export function commsSatellites(templates: PacketTemplates | null, linkProfiles: LinkProfiles | null): CommsSatellite[] {
  const capabilities = commsCapabilities(templates)
  const budgets = linkProfiles ? new Set(linkProfiles.profiles.map((p) => p.norad_id)) : null
  return (templates?.satellites ?? [])
    .map((s) => ({
      noradId: s.norad_id,
      name: s.satellite_name,
      capability: capabilities.get(s.norad_id)!,
      linkBudget: budgets ? budgets.has(s.norad_id) : null,
    }))
    .sort((a, b) => Number(a.capability !== 'command') - Number(b.capability !== 'command') || a.name.localeCompare(b.name))
}

/** Keep the newest `max` packets (the server numbers them per session). */
export function appendPackets(previous: PacketMessage[], incoming: PacketMessage[], max = MAX_PACKETS): PacketMessage[] {
  if (incoming.length === 0) return previous
  const seen = new Set(previous.map((p) => p.seq))
  const merged = [...previous, ...incoming.filter((p) => !seen.has(p.seq))]
  merged.sort((a, b) => a.seq - b.seq)
  return merged.slice(-max)
}

/** The latest status of each command, the newest `max` commands only. */
export function mergeStatuses(
  previous: Record<number, CommandStatusMessage>,
  incoming: CommandStatusMessage[],
  max = MAX_COMMANDS,
): Record<number, CommandStatusMessage> {
  if (incoming.length === 0) return previous
  const merged = { ...previous }
  for (const message of incoming) merged[message.command_id] = message
  const keep = Object.keys(merged)
    .map(Number)
    .sort((a, b) => b - a)
    .slice(0, max)
  return Object.fromEntries(keep.map((id) => [id, merged[id]!]))
}

/** The newest downlink frame of this satellite that arrived intact (what the telemetry panel shows). */
export function latestDecoded(packets: PacketMessage[], noradId: number): PacketMessage | null {
  for (let i = packets.length - 1; i >= 0; i--) {
    const p = packets[i]!
    if (p.norad_id === noradId && p.direction === 'down' && p.crc_ok && p.decoded) return p
  }
  return null
}

export type PacketOutcome = 'sent' | 'ok' | 'corrupted' | 'lost'

export function packetOutcome(p: PacketMessage): PacketOutcome {
  if (p.direction === 'up') return 'sent'
  if (!p.delivered) return 'lost'
  return p.crc_ok ? 'ok' : 'corrupted'
}

export const OUTCOME_WORDS: Record<PacketOutcome, string> = {
  sent: 'sent (simulated)',
  ok: 'CRC ok',
  corrupted: 'corrupted',
  lost: 'lost',
}

/** The public page of a SatNOGS observation; only ever built from a validated integer id. */
export function satnogsObservationUrl(id: number | null | undefined): string | null {
  return typeof id === 'number' && Number.isInteger(id) && id > 0 ? `https://network.satnogs.org/observations/${id}/` : null
}

/** A decoded value as text: numbers keep their precision, units follow with a thin space. */
export function formatValue(value: number | string | null, unit?: string | null): string {
  if (value === null) return '—'
  const text = typeof value === 'number' ? String(value) : value
  return unit ? `${text} ${unit}` : text
}

/** "4.2 ms" / "119.4 ms" / "1.28 s". */
export function formatLightTime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  return seconds < 1 ? `${(seconds * 1000).toFixed(1)} ms` : `${seconds.toFixed(2)} s`
}

/** "1.0e-5" style for bit error rates; "—" without a channel model. */
export function formatBer(ber: number | null | undefined): string {
  if (ber === null || ber === undefined) return '—'
  return ber === 0 ? '0' : ber.toExponential(1)
}

const CALLSIGN = /^[A-Z0-9]{1,6}(-[0-9]{1,2})?$/
const PRINTABLE = /^[\x20-\x7e]+$/

/** A parameter as typed, checked like the server will (it checks again): the value, or why not. */
export function parseParam(param: CommandParam, raw: string): { value: number | string } | { error: string } {
  const text = raw.trim()
  if (param.type === 'int') {
    if (!/^-?\d+$/.test(text)) return { error: 'a whole number' }
    const value = Number(text)
    if (param.min !== null && value < param.min) return { error: `at least ${param.min}` }
    if (param.max !== null && value > param.max) return { error: `at most ${param.max}` }
    return { value }
  }
  if (text.length === 0 || text.length > param.max_length) return { error: `1 to ${param.max_length} characters` }
  if (param.charset === 'callsign') {
    return CALLSIGN.test(text.toUpperCase()) ? { value: text.toUpperCase() } : { error: 'a callsign, e.g. W1AW-7' }
  }
  return PRINTABLE.test(text) ? { value: text } : { error: 'plain ASCII characters only' }
}
