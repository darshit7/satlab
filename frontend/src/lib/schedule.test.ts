import { describe, expect, it } from 'vitest'
import type { PassPrediction, PassWindow } from '../types/api'
import type { AlarmMessage, AutomationState, CommandStatusMessage, SystemCheck } from '../types/ws'
import {
  bookedOutcomes,
  checkSummary,
  followAutomation,
  mergeAlarms,
  passLabel,
  schedulablePasses,
  scheduledCommands,
  shouldAnnounce,
  sortAlarms,
  unacknowledgedCount,
} from './schedule'

const pass = (aos: string): PassWindow => ({
  aos_time: aos,
  los_time: aos.replace(':00:00', ':10:00'),
  max_time: aos,
  max_elev_deg: 40,
  aos_az_deg: 10,
  los_az_deg: 170,
  duration_s: 600,
})

const status = (id: number, over: Partial<CommandStatusMessage> = {}): CommandStatusMessage => ({
  type: 'command_status',
  command_id: id,
  norad_id: 43013,
  command: 'PING',
  status: 'scheduled',
  sim_time: '2026-10-10T16:00:00Z',
  scheduled_pass: { aos_time: '2026-10-10T19:00:00Z', los_time: '2026-10-10T19:10:00Z', max_elev_deg: 40 },
  ...over,
})

const alarm = (id: number, over: Partial<AlarmMessage> = {}): AlarmMessage => ({
  type: 'alarm',
  alarm_id: id,
  severity: 'critical',
  subsystem: 'attitude',
  norad_id: 25544,
  message: 'held',
  sim_time: '2026-10-10T19:00:00Z',
  acknowledged: false,
  ...over,
})

const check = (subsystems: Partial<SystemCheck['subsystems']>, go = true): SystemCheck => ({
  sim_time: '2026-10-10T19:00:05Z',
  norad_id: 43013,
  rollup: null,
  go,
  subsystems: { power: 'green', thermal: 'green', storage: 'green', attitude: 'green', rf: 'green', ...subsystems },
})

describe('scheduled commands', () => {
  it('lists those waiting by AOS, and the outcomes newest first', () => {
    const commands = {
      1: status(1, { scheduled_pass: { aos_time: '2026-10-10T21:00:00Z', los_time: '2026-10-10T21:10:00Z', max_elev_deg: 20 } }),
      2: status(2),
      3: status(3, { status: 'held' }),
      4: status(4, { status: 'acked' }),
      5: status(5, { status: 'acked', scheduled_pass: null }), // sent live: not a booking
    }
    expect(scheduledCommands(commands).map((c) => c.command_id)).toEqual([2, 1])
    expect(bookedOutcomes(commands).map((c) => c.command_id)).toEqual([4, 3])
  })
})

describe('alarms', () => {
  it('merges by id, keeps the newest, sorts unacknowledged first', () => {
    let alarms = mergeAlarms({}, [alarm(1), alarm(2)])
    alarms = mergeAlarms(alarms, [alarm(1, { acknowledged: true }), alarm(3, { severity: 'warning' })], 2)
    expect(Object.keys(alarms).map(Number)).toEqual([2, 3])
    expect(unacknowledgedCount(alarms)).toBe(2)
    expect(sortAlarms(mergeAlarms({}, [alarm(1, { acknowledged: true }), alarm(2), alarm(3)])).map((a) => a.alarm_id)).toEqual([3, 2, 1])
  })

  it('announces new unacknowledged warnings and criticals only', () => {
    expect(shouldAnnounce(alarm(1), {})).toBe(true)
    expect(shouldAnnounce(alarm(1), { 1: alarm(1) })).toBe(false) // re-offered on reconnect
    expect(shouldAnnounce(alarm(2, { acknowledged: true }), {})).toBe(false)
    expect(shouldAnnounce(alarm(3, { severity: 'info' }), {})).toBe(false)
  })
})

describe('checkSummary', () => {
  it('says what let it go and what wasn’t checked', () => {
    expect(checkSummary(check({}))).toBe('Go')
    expect(checkSummary(check({ rf: 'amber' }))).toBe('Go · amber: RF')
    expect(checkSummary(check({ rf: null, storage: null, power: 'amber' }))).toBe('Go · amber: power · not checked: storage, RF')
  })

  it('says what held it', () => {
    expect(checkSummary(check({ attitude: 'red' }, false))).toBe('Held · attitude red')
    expect(checkSummary(check({ power: 'red', rf: 'red' }, false))).toBe('Held · power, RF red')
    const blind = check({ power: null, thermal: null, storage: null, attitude: null, rf: null }, false)
    expect(checkSummary(blind)).toBe('Held · no system data')
  })
})

describe('schedulablePasses', () => {
  const prediction: PassPrediction = {
    session_id: 1,
    station_id: null,
    norad_id: 43013,
    name: 'NOAA 20',
    tle_epoch: '2026-10-09T00:00:00Z',
    horizon_mask_deg: 5,
    start: '2026-10-10T16:00:00Z',
    end: '2026-10-11T16:00:00Z',
    always_visible: false,
    passes: [pass('2026-10-10T17:00:00Z'), pass('2026-10-10T19:00:00Z')],
  }
  const at = Date.parse('2026-10-10T17:05:00Z')

  it('offers the satellite’s passes that haven’t started', () => {
    expect(schedulablePasses(prediction, 43013, at).map((p) => p.aos_time)).toEqual(['2026-10-10T19:00:00Z'])
  })

  it('offers nothing for another satellite, an always-visible one, or no schedule', () => {
    expect(schedulablePasses(prediction, 25544, at)).toEqual([])
    expect(schedulablePasses({ ...prediction, always_visible: true }, 43013, at)).toEqual([])
    expect(schedulablePasses(null, 43013, at)).toEqual([])
  })
})

describe('passLabel', () => {
  const sim = Date.parse('2026-10-10T17:05:00Z')
  it('names the next pass and dates passes on another UTC day', () => {
    expect(passLabel(pass('2026-10-10T19:00:00Z'), sim, true)).toBe('Next pass · 19:00 UTC · max 40°')
    expect(passLabel({ aos_time: '2026-10-11T06:09:15Z', max_elev_deg: 33.6 }, sim, false)).toBe('11 Oct 06:09 UTC · max 34°')
  })
})

describe('followAutomation', () => {
  const automation: AutomationState = {
    norad_id: 43013,
    aos_time: '2026-10-10T19:00:00Z',
    los_time: '2026-10-10T19:10:00Z',
    max_elev_deg: 40,
    commands: 1,
    checked: false,
  }

  it('selects the satellite once per pass and announces it once', () => {
    const first = followAutomation(automation, null, 25544)
    expect(first).toEqual({ key: '43013@2026-10-10T19:00:00Z', select: 43013, announce: true })
    // later snapshots of the same pass: the operator may have moved on, so don't select again
    expect(followAutomation({ ...automation, checked: true }, first.key, 25544)).toEqual({ key: first.key, select: null, announce: false })
  })

  it('announces without selecting when it is already the selection', () => {
    expect(followAutomation(automation, null, 43013)).toMatchObject({ select: null, announce: true })
  })

  it('keeps the last pass when there is none now', () => {
    expect(followAutomation(null, 'x', 1)).toEqual({ key: 'x', select: null, announce: false })
  })
})
