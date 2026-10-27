import { beforeEach, describe, expect, it, vi } from 'vitest'

const stored = new Map<string, string>()
vi.stubGlobal('window', {
  matchMedia: () => ({ matches: false, addEventListener: () => {} }),
})
vi.stubGlobal('document', { documentElement: { classList: { toggle: () => {} } } })
vi.stubGlobal('localStorage', {
  getItem: (key: string) => stored.get(key) ?? null,
  setItem: (key: string, value: string) => stored.set(key, value),
  removeItem: (key: string) => stored.delete(key),
})

stored.set('satlab.theme', 'light') // a choice stored before the key was renamed
const { STORAGE_KEY, readPreference, resolveTheme } = await import('./theme')
const legacyAfterLoad = stored.get('satlab.theme')

describe('resolveTheme', () => {
  it('follows the system only when the preference is system', () => {
    expect(resolveTheme('system', true)).toBe('dark')
    expect(resolveTheme('system', false)).toBe('light')
    expect(resolveTheme('light', true)).toBe('light')
    expect(resolveTheme('dark', false)).toBe('dark')
  })
})

describe('readPreference', () => {
  it('forgets the old key when the module loads', () => {
    expect(legacyAfterLoad).toBeUndefined()
  })

  beforeEach(() => stored.clear())

  it('defaults to dark, ignoring a choice stored under the old key', () => {
    stored.set('satlab.theme', 'light')
    expect(readPreference()).toBe('dark')
  })

  it('honours a choice stored under the current key', () => {
    stored.set(STORAGE_KEY, 'light')
    expect(readPreference()).toBe('light')
    stored.set(STORAGE_KEY, 'bogus')
    expect(readPreference()).toBe('dark')
  })
})
