import { describe, expect, it } from 'vitest'
import type { PassWindow } from '../types/api'
import { passesAround } from './passes'

const pass = (aos: string, los: string): PassWindow => ({
  aos_time: aos,
  los_time: los,
  max_time: aos,
  max_elev_deg: 40,
  aos_az_deg: 10,
  los_az_deg: 170,
  duration_s: (Date.parse(los) - Date.parse(aos)) / 1000,
})

const schedule = [
  pass('2026-10-25T10:00:00Z', '2026-10-25T10:10:00Z'),
  pass('2026-10-25T11:30:00Z', '2026-10-25T11:38:00Z'),
  pass('2026-10-25T13:00:00Z', '2026-10-25T13:09:00Z'),
]

describe('passesAround', () => {
  it('finds the pass in progress and the one after it', () => {
    const { live, next } = passesAround(schedule, Date.parse('2026-10-25T10:05:00Z'))
    expect(live).toBe(schedule[0])
    expect(next).toBe(schedule[1])
  })

  it('between passes there is only a next one; AOS and LOS count as in the pass', () => {
    expect(passesAround(schedule, Date.parse('2026-10-25T10:20:00Z'))).toEqual({ live: null, next: schedule[1] })
    expect(passesAround(schedule, Date.parse('2026-10-25T11:30:00Z')).live).toBe(schedule[1])
    expect(passesAround(schedule, Date.parse('2026-10-25T11:38:00Z')).live).toBe(schedule[1])
  })

  it('after the last pass there is nothing', () => {
    expect(passesAround(schedule, Date.parse('2026-10-25T14:00:00Z'))).toEqual({ live: null, next: null })
    expect(passesAround([], 0)).toEqual({ live: null, next: null })
  })
})
