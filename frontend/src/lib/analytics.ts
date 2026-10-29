import type { PanelId, SkyView } from '../store/ui'
import type { SatelliteType } from '../types/ws'
import type { MapMode } from './mapMode'

/**
 * Product analytics: Umami Cloud custom events. This is the only module that talks to the tracker,
 * which production builds add when NEXT_UMAMI_ID is set (vite.config.ts); without it every call
 * is a no-op. The Hobby quota counts each event and each property, and the website is shared with
 * the portfolio, so events are few and coarse. Nothing personal goes out: no coordinates, search
 * text, command parameters, session ids or tokens. The closed `Events` map below is what may be sent.
 */
type Events = {
  /** A session started from the visitor's choice (or resumed on load), never on a reconnect. */
  'session-start': { how: 'resumed' | 'preset' | 'custom'; station: string }
  'session-failed': undefined
  /** At SATLAB_MAX_ACTIVE_SESSIONS. */
  'server-busy': undefined
  'satellite-select': { name: string; type: string }
  'satellite-track': { name: string }
  'catalog-remote-search': undefined
  'command-send': { satellite: string; command: string; when: 'now' | 'pass' }
  'command-result': { status: 'acked' | 'failed' | 'held' | 'aborted' }
  'panel-open': { panel: PanelId }
  'map-mode': { mode: MapMode }
  'sky-expand': { view: SkyView }
  'pass-schedule-open': undefined
  'clock-change': { to: string }
  'filter-on': { filter: 'sar' | 'link-ok' | 'comms' | SatelliteType }
  'sound-on': undefined
  'globe-unavailable': { reason: 'no-webgl' | 'failed' }
} & Record<TimeEvent, undefined>

/** Active time on the page passed this milestone (see EngagementClock). */
export type TimeEvent = 'time-30s' | 'time-2m' | 'time-5m' | 'time-15m' | 'time-30m'
export type EventName = keyof Events
type DataArg<E extends EventName> = Events[E] extends undefined ? [] : [data: Events[E]]

interface Umami {
  track(name: string, data?: object): void
  captureException?(error: unknown, options?: { tags?: Record<string, string> }): void
}

declare global {
  interface Window {
    umami?: Umami
  }
}

const hasDom = typeof window !== 'undefined' && typeof document !== 'undefined'
const tracker = (): Umami | undefined => (hasDom ? window.umami : undefined)
const debug = import.meta.env.DEV && import.meta.env.MODE !== 'test'

// The tracker is deferred: events from before it runs wait here and go out on `load`, or are
// dropped if it never arrives (dev, blocked, a build without an ID).
const MAX_QUEUED = 20
let queue: [string, object | undefined][] | null = []
if (hasDom) {
  if (document.readyState === 'complete') queue = null
  else window.addEventListener('load', flush, { once: true })
}

function flush() {
  const pending = queue ?? []
  queue = null
  const umami = tracker()
  if (umami) for (const [name, data] of pending) umami.track(name, data)
}

function send(name: string, data: object | undefined) {
  if (debug) console.debug('[analytics]', name, data ?? '')
  const umami = tracker()
  if (umami) umami.track(name, data)
  else if (queue && queue.length < MAX_QUEUED) queue.push([name, data])
}

export function track<E extends EventName>(name: E, ...data: DataArg<E>): void {
  send(name, data[0])
}

/** At most this many distinct `trackOnce` values per event and page load (e.g. satellites). */
export const MAX_ONCE_PER_EVENT = 20
const seen = new Map<string, Set<string>>()

/** Sends each (event, data) pair once per page load: discovery, not every click. */
export function trackOnce<E extends EventName>(name: E, ...data: DataArg<E>): void {
  const keys = seen.get(name) ?? new Set<string>()
  const key = JSON.stringify(data[0] ?? null)
  if (keys.has(key) || keys.size >= MAX_ONCE_PER_EVENT) return
  keys.add(key)
  seen.set(name, keys)
  send(name, data[0])
}

/** Reports a caught error to the tracker's error capture (data-errors), tagged by feature. */
export function captureError(error: unknown, feature: string): void {
  tracker()?.captureException?.(error, { tags: { feature } })
}

const MILESTONES: readonly (readonly [seconds: number, event: TimeEvent])[] = [
  [30, 'time-30s'],
  [120, 'time-2m'],
  [300, 'time-5m'],
  [900, 'time-15m'],
  [1800, 'time-30m'],
]
/** Without input for this long, time stops counting (a tab left open). Covers a watched pass. */
export const IDLE_AFTER_MS = 10 * 60_000
const INPUT_THROTTLE_MS = 1000

/**
 * Active time on the page, for the milestone events: Umami measures visit time from pageviews
 * only, so a one-page app reads 0 s. Time counts while the tab is visible and the visitor has
 * used the mouse or keyboard within IDLE_AFTER_MS. Pure: the caller passes the time in.
 */
export class EngagementClock {
  private activeMs = 0
  private reported = 0
  private lastMs: number
  private lastInputMs: number
  private visible: boolean

  constructor(nowMs: number, visible: boolean) {
    this.lastMs = nowMs
    this.lastInputMs = nowMs
    this.visible = visible
  }

  get done(): boolean {
    return this.reported === MILESTONES.length
  }

  /** Counts the time up to now; returns the milestones it crossed. */
  advance(nowMs: number): TimeEvent[] {
    if (this.visible) this.activeMs += Math.max(0, Math.min(nowMs, this.lastInputMs + IDLE_AFTER_MS) - this.lastMs)
    this.lastMs = Math.max(this.lastMs, nowMs)
    const crossed: TimeEvent[] = []
    for (let m = MILESTONES[this.reported]; m && this.activeMs >= m[0] * 1000; m = MILESTONES[this.reported]) {
      crossed.push(m[1])
      this.reported++
    }
    return crossed
  }

  input(nowMs: number): TimeEvent[] {
    if (nowMs - this.lastInputMs < INPUT_THROTTLE_MS) return []
    const crossed = this.advance(nowMs) // an idle gap before this input doesn't count
    this.lastInputMs = nowMs
    return crossed
  }

  /** Coming back to the tab counts as input. */
  setVisible(nowMs: number, visible: boolean): TimeEvent[] {
    const crossed = this.advance(nowMs)
    this.visible = visible
    if (visible) this.lastInputMs = nowMs
    return crossed
  }
}

const TICK_MS = 5000
const INPUT_EVENTS = ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart'] as const
let clockStarted = false

/** Starts the page's engagement clock (once; StrictMode may call it twice). */
export function startEngagementClock(): void {
  if (clockStarted || !hasDom) return
  clockStarted = true
  const isVisible = () => document.visibilityState === 'visible'
  const clock = new EngagementClock(performance.now(), isVisible())
  const report = (crossed: TimeEvent[]) => {
    for (const name of crossed) track(name)
    if (clock.done) stop()
  }
  const onInput = () => report(clock.input(performance.now()))
  const onVisibility = () => report(clock.setVisible(performance.now(), isVisible()))
  const timer = setInterval(() => report(clock.advance(performance.now())), TICK_MS)
  for (const type of INPUT_EVENTS) window.addEventListener(type, onInput, { passive: true })
  document.addEventListener('visibilitychange', onVisibility)
  function stop() {
    clearInterval(timer)
    for (const type of INPUT_EVENTS) window.removeEventListener(type, onInput)
    document.removeEventListener('visibilitychange', onVisibility)
  }
}
