// Sound cues for command traffic: which packets make a sound, and which. Presentation only: the
// packets and their outcomes are the server's (views/Comms/commsSound.ts plays them).

import type { PacketMessage } from '../types/ws'
import { packetOutcome } from './comms'

/** sent: a command goes up · reply: its answer came back intact · no-reply: the answer was lost
 *  or corrupted on the way down. */
export type Cue = 'sent' | 'reply' | 'no-reply'

const REPLIES = new Set<PacketMessage['kind']>(['ack', 'response', 'digipeat'])

/** The cue for a packet; null for what makes no sound (telemetry the satellite sends on its own). */
export function cueFor(packet: PacketMessage): Cue | null {
  if (packet.direction === 'up') return packet.kind === 'command' ? 'sent' : null
  if (!REPLIES.has(packet.kind)) return null
  return packetOutcome(packet) === 'ok' ? 'reply' : 'no-reply'
}
