import { describe, expect, it, vi } from 'vitest'

const stored = new Map<string, string>([['satlab.mapMode', 'globe']]) // not a mode: e.g. hand-edited
vi.stubGlobal('localStorage', {
  getItem: (k: string) => stored.get(k) ?? null,
  setItem: (k: string, v: string) => void stored.set(k, v),
})

const { DEFAULT_MAP_MODE, parseMapMode, setMapMode } = await import('./mapMode')

describe('map mode preference', () => {
  it('defaults to the 3D globe', () => {
    expect(DEFAULT_MAP_MODE).toBe('3d')
  })

  it('accepts only the two modes from storage', () => {
    expect(parseMapMode('2d')).toBe('2d')
    expect(parseMapMode('3d')).toBe('3d')
    for (const junk of ['globe', '', null, 3, {}]) expect(parseMapMode(junk)).toBe('3d')
  })

  it('remembers the choice', () => {
    setMapMode('2d')
    expect(stored.get('satlab.mapMode')).toBe('2d')
  })
})
