/** Inspector sections that collapse; which are open is remembered per browser. */
export type SectionId = 'sky' | 'systems' | 'comms'

export const DEFAULT_SECTIONS: Record<SectionId, boolean> = { sky: true, systems: true, comms: true }

/** Stored values are untrusted (it's localStorage): a section is closed only if stored as false. */
export function sanitizeSections(raw: unknown): Record<SectionId, boolean> {
  const r = typeof raw === 'object' && raw !== null ? (raw as Record<string, unknown>) : {}
  return { sky: r.sky !== false, systems: r.systems !== false, comms: r.comms !== false }
}
