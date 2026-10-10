import { describe, expect, it, vi } from 'vitest'

vi.stubGlobal('window', {
  matchMedia: () => ({ matches: false, addEventListener: () => {} }),
})
vi.stubGlobal('document', { documentElement: { classList: { toggle: () => {} } } })
vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {} })

const { resolveTheme } = await import('./theme')

describe('resolveTheme', () => {
  it('follows the system only when the preference is system', () => {
    expect(resolveTheme('system', true)).toBe('dark')
    expect(resolveTheme('system', false)).toBe('light')
    expect(resolveTheme('light', true)).toBe('light')
    expect(resolveTheme('dark', false)).toBe('dark')
  })
})
