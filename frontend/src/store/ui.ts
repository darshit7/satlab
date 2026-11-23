import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import { DEFAULT_SECTIONS, sanitizeSections, type SectionId } from '../lib/sections'

/** The mission bar's floating panels: one is open at a time. */
export type PanelId = 'station' | 'catalog' | 'overhead' | 'schedule' | 'imagery'
export type SkyView = 'polar' | 'allsky'

interface UiState {
  panel: PanelId | null
  /** The sky view shown large in its dialog, if any. */
  skyDialog: SkyView | null
  passesOpen: boolean
  /** The SAR comparison shown in the compare dialog, if any. */
  compareId: number | null
  sections: Record<SectionId, boolean>
  /** Sound cues for command traffic. Not remembered: browsers start audio only after a click, so
   *  a stored "on" would sit silent until then. */
  sound: boolean

  togglePanel: (id: PanelId) => void
  openPanel: (id: PanelId) => void
  closePanel: () => void
  setSkyDialog: (view: SkyView | null) => void
  setPassesOpen: (open: boolean) => void
  setCompareId: (id: number | null) => void
  toggleSection: (id: SectionId) => void
  setSound: (on: boolean) => void
}

/**
 * View chrome, kept apart from the mission store: which panel or dialog is open, and which
 * inspector sections are collapsed. Only the sections are persisted (a preference).
 */
export const useUi = create<UiState>()(
  persist(
    (set) => ({
      panel: null,
      skyDialog: null,
      passesOpen: false,
      compareId: null,
      sections: DEFAULT_SECTIONS,
      sound: false,

      togglePanel: (id) => set((s) => ({ panel: s.panel === id ? null : id })),
      openPanel: (id) => set({ panel: id }),
      closePanel: () => set({ panel: null }),
      setSkyDialog: (skyDialog) => set({ skyDialog }),
      setPassesOpen: (passesOpen) => set({ passesOpen }),
      setCompareId: (compareId) => set({ compareId }),
      toggleSection: (id) => set((s) => ({ sections: { ...s.sections, [id]: !s.sections[id] } })),
      setSound: (sound) => set({ sound }),
    }),
    {
      name: 'satlab.ui',
      storage: createJSONStorage(() => localStorage),
      partialize: (s) => ({ sections: s.sections }),
      merge: (persisted, current) => ({
        ...current,
        sections: sanitizeSections((persisted as { sections?: unknown } | null)?.sections),
      }),
    },
  ),
)

/** True while a modal dialog is open: page-level shortcuts stand down. */
export const dialogOpen = () => {
  const { skyDialog, passesOpen, compareId } = useUi.getState()
  return skyDialog !== null || passesOpen || compareId !== null
}
