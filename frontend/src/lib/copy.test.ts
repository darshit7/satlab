import { describe, expect, it } from 'vitest'

// Every component's source, as text: UI copy must never name a build phase ("Phase 2").
const sources = import.meta.glob<string>('/src/**/*.tsx', { query: '?raw', import: 'default', eager: true })

/** Source without comments, which may mention phases. */
const withoutComments = (src: string) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')

describe('UI copy', () => {
  it('finds the components', () => {
    expect(Object.keys(sources).length).toBeGreaterThan(10)
  })

  it.each(Object.entries(sources))('%s never shows a phase number', (_path, src) => {
    expect(withoutComments(src)).not.toMatch(/\bphase\s*\d/i)
  })
})
