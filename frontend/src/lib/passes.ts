import type { PassWindow } from '../types/api'

/** The pass in progress at `nowMs` and the next one to start after it, from a schedule in AOS order. */
export function passesAround(passes: PassWindow[], nowMs: number): { live: PassWindow | null; next: PassWindow | null } {
  let live: PassWindow | null = null
  let next: PassWindow | null = null
  for (const p of passes) {
    const aos = Date.parse(p.aos_time)
    if (aos > nowMs) {
      next ??= p
    } else if (nowMs <= Date.parse(p.los_time)) {
      live ??= p
    }
  }
  return { live, next }
}
