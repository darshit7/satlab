// Presentation of pass automation. The server books passes, takes the antenna, runs the system
// check and raises alarms; nothing here decides any of it.

import type { PassPrediction, PassWindow } from '../types/api'
import type { AlarmMessage, AutomationState, CommandStatusMessage, Rag, Subsystem, SystemCheck } from '../types/ws'

export const MAX_ALARMS = 50
const SUBSYSTEMS: Subsystem[] = ['power', 'thermal', 'storage', 'attitude', 'rf']
export const SUBSYSTEM_LABELS: Record<Subsystem, string> = {
  power: 'power',
  thermal: 'thermal',
  storage: 'storage',
  attitude: 'attitude',
  rf: 'RF',
}

/** Commands waiting for their pass, soonest AOS first. */
export function scheduledCommands(commands: Record<number, CommandStatusMessage>): CommandStatusMessage[] {
  return Object.values(commands)
    .filter((c) => c.status === 'scheduled' && c.scheduled_pass)
    .sort((a, b) => Date.parse(a.scheduled_pass!.aos_time) - Date.parse(b.scheduled_pass!.aos_time) || a.command_id - b.command_id)
}

/** Booked commands that have moved on (released, held, missed, cancelled), newest first. */
export function bookedOutcomes(commands: Record<number, CommandStatusMessage>, max = 10): CommandStatusMessage[] {
  return Object.values(commands)
    .filter((c) => c.status !== 'scheduled' && c.scheduled_pass)
    .sort((a, b) => b.command_id - a.command_id)
    .slice(0, max)
}

/** The latest state of each alarm, the newest `max` only. */
export function mergeAlarms(
  previous: Record<number, AlarmMessage>,
  incoming: AlarmMessage[],
  max = MAX_ALARMS,
): Record<number, AlarmMessage> {
  if (incoming.length === 0) return previous
  const merged = { ...previous }
  for (const alarm of incoming) merged[alarm.alarm_id] = alarm
  const keep = Object.keys(merged)
    .map(Number)
    .sort((a, b) => b - a)
    .slice(0, max)
  return Object.fromEntries(keep.map((id) => [id, merged[id]!]))
}

/** Unacknowledged first, then newest first. */
export function sortAlarms(alarms: Record<number, AlarmMessage>): AlarmMessage[] {
  return Object.values(alarms).sort((a, b) => Number(a.acknowledged) - Number(b.acknowledged) || b.alarm_id - a.alarm_id)
}

export const unacknowledgedCount = (alarms: Record<number, AlarmMessage>): number =>
  Object.values(alarms).filter((a) => !a.acknowledged).length

/** An alarm worth a toast: new, not yet acknowledged, and more than information. */
export const shouldAnnounce = (alarm: AlarmMessage, known: Record<number, AlarmMessage>): boolean =>
  !alarm.acknowledged && alarm.severity !== 'info' && !(alarm.alarm_id in known)

export const SEVERITY_RAG: Record<AlarmMessage['severity'], Rag | null> = { critical: 'red', warning: 'amber', info: null }
export const SEVERITY_WORDS: Record<AlarmMessage['severity'], string> = { critical: 'Critical', warning: 'Warning', info: 'Info' }

const listOf = (subsystems: Subsystem[]) => subsystems.map((s) => SUBSYSTEM_LABELS[s]).join(', ')

/** The check in a few words: "Go · amber: RF · not checked: storage" / "Held · attitude red". */
export function checkSummary(check: SystemCheck): string {
  const having = (rag: Rag | null) => SUBSYSTEMS.filter((s) => check.subsystems[s] === rag)
  if (!check.go) {
    const red = having('red')
    return red.length > 0 ? `Held · ${listOf(red)} red` : 'Held · no system data'
  }
  const parts = ['Go']
  const amber = having('amber')
  const unchecked = having(null)
  if (amber.length > 0) parts.push(`amber: ${listOf(amber)}`)
  if (unchecked.length > 0) parts.push(`not checked: ${listOf(unchecked)}`)
  return parts.join(' · ')
}

/** Passes a command can be booked for: the server's schedule for this satellite, not yet started. */
export function schedulablePasses(prediction: PassPrediction | null | undefined, noradId: number, simMs: number): PassWindow[] {
  if (!prediction || prediction.norad_id !== noradId || prediction.always_visible) return []
  return prediction.passes.filter((p) => Date.parse(p.aos_time) > simMs)
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "19:31 UTC", with the date when it isn't the sim day's ("11 Oct 06:09 UTC"). */
export function utcWhen(ms: number, simMs: number): string {
  const at = new Date(ms)
  const time = at.toISOString().slice(11, 16)
  const sameDay = at.toISOString().slice(0, 10) === new Date(simMs).toISOString().slice(0, 10)
  return sameDay ? `${time} UTC` : `${at.getUTCDate()} ${MONTHS[at.getUTCMonth()]} ${time} UTC`
}

/** A pass as the composer offers it: "Next pass · 19:31 UTC · max 62°". */
export function passLabel(p: Pick<PassWindow, 'aos_time' | 'max_elev_deg'>, simMs: number, next: boolean): string {
  const when = utcWhen(Date.parse(p.aos_time), simMs)
  return `${next ? 'Next pass · ' : ''}${when} · max ${p.max_elev_deg.toFixed(0)}°`
}

export const automationKey = (a: AutomationState): string => `${a.norad_id}@${a.aos_time}`

/**
 * Following pass automation: on a pass it hasn't seen yet, the client selects the satellite the
 * server turned the antenna to (without telling the server, which already did) and says so once.
 */
export function followAutomation(
  automation: AutomationState | null | undefined,
  lastKey: string | null,
  selectedId: number | null,
): { key: string | null; select: number | null; announce: boolean } {
  if (!automation) return { key: lastKey, select: null, announce: false }
  const key = automationKey(automation)
  if (key === lastKey) return { key, select: null, announce: false }
  return { key, select: automation.norad_id !== selectedId ? automation.norad_id : null, announce: true }
}
