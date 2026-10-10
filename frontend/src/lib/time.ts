import type { ClockState } from '../types/ws'

export interface ClockSample {
  simMs: number // sim time carried by the latest snapshot
  receivedAt: number // performance.now() when that snapshot arrived
  clock: ClockState
}

/** Smoothly advance the last server sim time between snapshots (the clock is linear). */
export function estimateSimMs(sample: ClockSample, nowPerf: number): number {
  if (sample.clock.paused) return sample.simMs
  return sample.simMs + Math.max(0, nowPerf - sample.receivedAt) * sample.clock.time_scale
}

export function formatUtc(ms: number): string {
  return new Date(ms).toISOString().replace('T', ' ').slice(0, 19) + ' UTC'
}

export function formatLocal(ms: number): string {
  return new Date(ms).toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    timeZoneName: 'short',
  })
}

/** Human-readable age, e.g. "3.2 d", "5 h", "12 min". */
export function formatAge(ms: number): string {
  const minutes = ms / 60_000
  if (minutes < 90) return `${Math.round(minutes)} min`
  const hours = minutes / 60
  if (hours < 48) return `${Math.round(hours)} h`
  return `${(hours / 24).toFixed(1)} d`
}

/** Countdown to an event, e.g. "0:42", "12:05", "1 h 12 min". Negative values read as 0. */
export function formatCountdown(ms: number): string {
  const totalS = Math.max(0, Math.round(ms / 1000))
  if (totalS < 3600) return `${Math.floor(totalS / 60)}:${String(totalS % 60).padStart(2, '0')}`
  const minutes = Math.round(totalS / 60)
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`
}

/** Clock time in UTC, e.g. "11:18:07". */
export function formatUtcTime(ms: number): string {
  return new Date(ms).toISOString().slice(11, 19)
}

/** Clock time in the viewer's zone, e.g. "16:48". */
export function formatLocalTime(ms: number): string {
  return new Date(ms).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false })
}
