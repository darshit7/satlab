import { afterEach, describe, expect, it, vi } from 'vitest'
import { EngagementClock, IDLE_AFTER_MS } from './analytics'

const s = (seconds: number) => seconds * 1000
const min = (minutes: number) => minutes * 60_000

describe('EngagementClock', () => {
  it('reports each milestone once, as active time passes it', () => {
    const clock = new EngagementClock(0, true)
    expect(clock.advance(s(29))).toEqual([])
    expect(clock.advance(s(30))).toEqual(['time-30s'])
    expect(clock.advance(s(60))).toEqual([])
    expect(clock.input(min(9))).toEqual(['time-2m', 'time-5m'])
    expect(clock.input(min(18))).toEqual(['time-15m'])
    expect(clock.input(min(20))).toEqual([])
    expect(clock.done).toBe(false)
    expect(clock.advance(min(30))).toEqual(['time-30m'])
    expect(clock.done).toBe(true)
    expect(clock.advance(min(90))).toEqual([])
  })

  it('pauses while the tab is hidden', () => {
    const clock = new EngagementClock(0, true)
    expect(clock.setVisible(s(10), false)).toEqual([])
    expect(clock.advance(s(100))).toEqual([]) // hidden: still 10 s
    expect(clock.setVisible(s(100), true)).toEqual([])
    expect(clock.advance(s(119))).toEqual([])
    expect(clock.advance(s(120))).toEqual(['time-30s'])
  })

  it('starts hidden when the page loads in a background tab', () => {
    const clock = new EngagementClock(0, false)
    expect(clock.advance(min(5))).toEqual([])
    clock.setVisible(min(5), true)
    expect(clock.advance(min(5) + s(30))).toEqual(['time-30s'])
  })

  it('stops counting after IDLE_AFTER_MS without input, and the idle gap never counts', () => {
    const clock = new EngagementClock(0, true)
    // 10 min of a watched pass with no input still counts...
    expect(clock.advance(min(60))).toEqual(['time-30s', 'time-2m', 'time-5m'])
    // ...then nothing until the visitor is back; the 50 idle minutes are not added.
    expect(clock.input(min(60))).toEqual([])
    expect(clock.advance(min(64) + s(59))).toEqual([])
    expect(clock.advance(min(65))).toEqual(['time-15m'])
    expect(IDLE_AFTER_MS).toBe(min(10))
  })

  it('counts coming back to the tab as input', () => {
    const clock = new EngagementClock(0, true)
    expect(clock.setVisible(min(1), false)).toEqual(['time-30s'])
    clock.setVisible(min(30), true) // long gone, back now: active again
    expect(clock.advance(min(39))).toEqual(['time-2m', 'time-5m'])
  })
})

interface FakeUmami {
  track: ReturnType<typeof vi.fn>
  captureException: ReturnType<typeof vi.fn>
}

/** A fresh copy of the module on a page in the given load state. */
async function load(readyState: DocumentReadyState, umami?: FakeUmami) {
  vi.resetModules()
  const listeners = new Map<string, () => void>()
  const win: { umami?: FakeUmami; addEventListener: (type: string, fn: () => void) => void } = {
    umami,
    addEventListener: (type, fn) => listeners.set(type, fn),
  }
  vi.stubGlobal('window', win)
  vi.stubGlobal('document', { readyState })
  const analytics = await import('./analytics')
  return { analytics, win, fireLoad: () => listeners.get('load')?.() }
}

const fakeUmami = (): FakeUmami => ({ track: vi.fn(), captureException: vi.fn() })

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('track', () => {
  it('holds events sent before the deferred tracker runs and flushes them on load', async () => {
    const { analytics, win, fireLoad } = await load('loading')
    analytics.track('session-start', { how: 'resumed', station: 'Bengaluru' })
    analytics.track('server-busy')
    const umami = fakeUmami()
    win.umami = umami
    fireLoad()
    expect(umami.track.mock.calls).toEqual([
      ['session-start', { how: 'resumed', station: 'Bengaluru' }],
      ['server-busy', undefined],
    ])
    analytics.track('sound-on') // the tracker is there now: straight through
    expect(umami.track).toHaveBeenCalledTimes(3)
  })

  it('drops the queue when the tracker never arrives (dev, blocked, no website id)', async () => {
    const { analytics, win, fireLoad } = await load('loading')
    analytics.track('server-busy')
    fireLoad()
    analytics.track('session-failed')
    const umami = fakeUmami()
    win.umami = umami
    analytics.track('sound-on')
    expect(umami.track.mock.calls).toEqual([['sound-on', undefined]])
  })

  it('sends only the declared fields', async () => {
    const { analytics } = await load('complete', fakeUmami())
    const typeChecks = () => {
      // @ts-expect-error never the coordinates
      analytics.track('session-start', { how: 'custom', station: 'Custom location', lat_deg: 12.97 })
      // @ts-expect-error never command parameters (free text for APRS messages)
      analytics.track('command-send', { satellite: 'ISS', command: 'APRS_MESSAGE', when: 'now', params: {} })
      // @ts-expect-error unknown events don't exist
      analytics.track('search-query', { q: 'iss' })
      // @ts-expect-error events without data take none
      analytics.track('server-busy', {})
    }
    expect(typeof typeChecks).toBe('function')
  })
})

describe('trackOnce', () => {
  it('sends each event and value once per page load, up to a cap per event', async () => {
    const umami = fakeUmami()
    const { analytics } = await load('complete', umami)
    analytics.trackOnce('panel-open', { panel: 'catalog' })
    analytics.trackOnce('panel-open', { panel: 'catalog' })
    analytics.trackOnce('panel-open', { panel: 'overhead' })
    analytics.trackOnce('sound-on')
    analytics.trackOnce('sound-on')
    expect(umami.track.mock.calls).toEqual([
      ['panel-open', { panel: 'catalog' }],
      ['panel-open', { panel: 'overhead' }],
      ['sound-on', undefined],
    ])

    umami.track.mockClear()
    for (let i = 0; i < analytics.MAX_ONCE_PER_EVENT + 5; i++) {
      analytics.trackOnce('satellite-select', { name: `SAT ${i}`, type: 'other' })
    }
    expect(umami.track).toHaveBeenCalledTimes(analytics.MAX_ONCE_PER_EVENT)
  })
})

describe('captureError', () => {
  it('reports through the tracker, tagged by feature, and is a no-op without it', async () => {
    const umami = fakeUmami()
    const { analytics, win } = await load('complete', umami)
    const error = new Error('WebGL context lost')
    analytics.captureError(error, 'globe')
    expect(umami.captureException).toHaveBeenCalledWith(error, { tags: { feature: 'globe' } })
    win.umami = undefined
    expect(() => analytics.captureError(error, 'globe')).not.toThrow()
  })
})
