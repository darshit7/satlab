import { describe, expect, it } from 'vitest'
import { formatDb, formatDopplerKHz, formatFrequency, formatMarginDb, formatRate, ragWord, safeHttpsUrl } from './rag'

describe('rag presentation', () => {
  it('names every state, including no data', () => {
    expect(ragWord('green')).toBe('Go')
    expect(ragWord('amber')).toBe('Marginal')
    expect(ragWord('red')).toBe('No-go')
    expect(ragWord(null)).toBe('No data')
    expect(ragWord(undefined)).toBe('No data')
  })

  it('formats margins and Doppler with a real minus sign', () => {
    expect(formatMarginDb(8.24)).toBe('+8.2')
    expect(formatMarginDb(-0.36)).toBe('−0.4')
    expect(formatMarginDb(0)).toBe('0.0')
    expect(formatMarginDb(null)).toBe('—')
    expect(formatDb(6.54)).toBe('6.5')
    expect(formatDb(-1.06)).toBe('−1.1')
    expect(formatDb(null)).toBe('—')
    expect(formatDopplerKHz(171_240)).toBe('+171.2')
    expect(formatDopplerKHz(-52_000)).toBe('−52.0')
    expect(formatDopplerKHz(12)).toBe('0.0') // geostationary: no sign on a zero
    expect(formatDopplerKHz(-30)).toBe('0.0')
  })

  it('formats frequencies and rates', () => {
    expect(formatFrequency(7.812e9)).toBe('7812 MHz')
    expect(formatFrequency(1.6866e9)).toBe('1686.6 MHz')
    expect(formatFrequency(437.5e6)).toBe('437.5 MHz')
    expect(formatRate(15_000_000)).toBe('15 Mbps')
    expect(formatRate(13_117_647)).toBe('13.1 Mbps')
    expect(formatRate(400_000)).toBe('400 kbps')
    expect(formatRate(1200)).toBe('1.2 kbps')
    expect(formatRate(300)).toBe('300 bps')
  })

  it('only lets https links through', () => {
    expect(safeHttpsUrl('https://ospo.noaa.gov/a.pdf')).toBe('https://ospo.noaa.gov/a.pdf')
    expect(safeHttpsUrl('http://example.com')).toBeNull()
    expect(safeHttpsUrl('javascript:alert(1)')).toBeNull()
    expect(safeHttpsUrl('not a url')).toBeNull()
  })
})
