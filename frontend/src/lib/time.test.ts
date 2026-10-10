import { describe, expect, it } from 'vitest'
import { estimateSimMs, formatAge, formatCountdown, formatUtcTime } from './time'

describe('estimateSimMs', () => {
  const base = { simMs: 1_000_000, receivedAt: 500 }

  it('advances at the time scale', () => {
    expect(estimateSimMs({ ...base, clock: { time_scale: 60, paused: false } }, 1500)).toBe(1_060_000)
  })

  it('holds while paused', () => {
    expect(estimateSimMs({ ...base, clock: { time_scale: 60, paused: true } }, 9999)).toBe(1_000_000)
  })
})

describe('formatAge', () => {
  it('picks sensible units', () => {
    expect(formatAge(5 * 60_000)).toBe('5 min')
    expect(formatAge(5 * 3_600_000)).toBe('5 h')
    expect(formatAge(3 * 86_400_000)).toBe('3.0 d')
  })
})

describe('formatCountdown', () => {
  it('shows m:ss under an hour and h/min beyond', () => {
    expect(formatCountdown(42_000)).toBe('0:42')
    expect(formatCountdown(725_400)).toBe('12:05')
    expect(formatCountdown(72 * 60_000)).toBe('1 h 12 min')
    expect(formatCountdown(-5000)).toBe('0:00')
  })
})

describe('formatUtcTime', () => {
  it('formats the UTC clock time', () => {
    expect(formatUtcTime(Date.parse('2014-01-23T11:18:07Z'))).toBe('11:18:07')
  })
})
