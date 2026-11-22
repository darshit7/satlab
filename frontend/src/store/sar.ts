import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import { api } from '../api/client'
import { trackOnce } from '../lib/analytics'
import { defaultDates, isRunning } from '../lib/sar'
import type { AoiSizeKm, SarComparison, SarPolarization, SarSearch, SarSource } from '../types/api'
import { useMission } from './mission'
import { useUi } from './ui'

/** How often the comparisons are polled while one is queued or running. */
const POLL_MS = 2000

export interface SarAoiDraft {
  lat_deg: number
  lon_deg: number
  size_km: AoiSizeKm
}

interface SearchState {
  loading: boolean
  data: SarSearch | null
  error: string | null
}

/** The pair picked in the results: a track and one pass on each side (pass ids). */
export interface SarChoice {
  track: string
  before: string
  after: string
}

interface SarState {
  source: SarSource | null
  /** The area; null until the panel first opens (then it starts at the ground station). */
  aoi: SarAoiDraft | null
  before: string
  after: string
  polarization: SarPolarization
  /** Only this satellite's passes (from a Sentinel-1 inspector's "Archive imagery"). */
  platform: string | null
  search: SearchState
  choice: SarChoice | null
  comparisons: SarComparison[]
  busy: boolean
  /** The comparison whose area and regions the maps draw. */
  shownId: number | null
  selectedRegion: number | null
  /** A request for the maps to frame a lon/lat box (bumped seq = a new request). */
  focus: { bounds: [number, number, number, number]; seq: number } | null

  loadSource: () => Promise<void>
  ensureAoi: () => void
  setAoiCentre: (lat: number, lon: number) => void
  setSize: (size: AoiSizeKm) => void
  setDates: (before: string, after: string) => void
  setPolarization: (p: SarPolarization) => void
  setPlatform: (platform: string | null) => void
  searchScenes: () => Promise<void>
  choose: (choice: SarChoice) => void
  compare: () => Promise<void>
  loadComparisons: () => Promise<void>
  recompute: (id: number) => Promise<void>
  remove: (id: number) => Promise<void>
  open: (id: number) => void
  selectRegion: (id: number | null) => void
  showOnMap: (bounds: [number, number, number, number]) => void
  reset: () => void
}

let searchRequest: AbortController | null = null
let listRequest: AbortController | null = null
let pollTimer: ReturnType<typeof setTimeout> | null = null
let focusSeq = 0

const sizes: readonly AoiSizeKm[] = [5, 10, 20]

function sanitize(raw: unknown): Partial<Pick<SarState, 'aoi' | 'before' | 'after' | 'polarization'>> {
  if (!raw || typeof raw !== 'object') return {}
  const r = raw as Record<string, unknown>
  const out: Partial<Pick<SarState, 'aoi' | 'before' | 'after' | 'polarization'>> = {}
  const aoi = r.aoi as Record<string, unknown> | null | undefined
  if (
    aoi &&
    typeof aoi.lat_deg === 'number' &&
    typeof aoi.lon_deg === 'number' &&
    Math.abs(aoi.lat_deg) <= 84 &&
    Math.abs(aoi.lon_deg) <= 180 &&
    sizes.includes(aoi.size_km as AoiSizeKm)
  ) {
    out.aoi = { lat_deg: aoi.lat_deg, lon_deg: aoi.lon_deg, size_km: aoi.size_km as AoiSizeKm }
  }
  const day = /^\d{4}-\d{2}-\d{2}$/
  if (typeof r.before === 'string' && day.test(r.before)) out.before = r.before
  if (typeof r.after === 'string' && day.test(r.after)) out.after = r.after
  if (r.polarization === 'VV' || r.polarization === 'VH') out.polarization = r.polarization
  return out
}

/**
 * SAR archive imagery: the panel's form, the last search, and this session's comparisons. Fed
 * by REST (not the socket): searches and comparisons run outside the simulation loop. The form
 * (area, dates, polarization) is remembered as a preference; results last for the session.
 */
export const useSar = create<SarState>()(
  persist(
    (set, get) => {
      const session = () => useMission.getState().session

      const schedulePoll = () => {
        if (pollTimer !== null) clearTimeout(pollTimer)
        pollTimer = null
        if (get().comparisons.some(isRunning)) pollTimer = setTimeout(() => void get().loadComparisons(), POLL_MS)
      }

      const fail = (err: unknown) => {
        const text = String(err).replace(/^Error: /, '').replace(/^\d{3}: /, '')
        useMission.getState().notify(text)
      }

      return {
        source: null,
        aoi: null,
        ...defaultDates(Date.now()),
        polarization: 'VV',
        platform: null,
        search: { loading: false, data: null, error: null },
        choice: null,
        comparisons: [],
        busy: false,
        shownId: null,
        selectedRegion: null,
        focus: null,

        loadSource: async () => {
          try {
            set({ source: await api.sarSource() })
          } catch {
            // reference data only: the panel says the archive is unavailable
          }
        },

        ensureAoi: () => {
          if (get().aoi !== null) return
          const s = session()
          if (s) set({ aoi: { lat_deg: clampLat(s.lat_deg), lon_deg: s.lon_deg, size_km: 10 } })
        },

        setAoiCentre: (lat, lon) =>
          set((st) => ({ aoi: { lat_deg: clampLat(lat), lon_deg: lon, size_km: st.aoi?.size_km ?? 10 }, search: { ...st.search, data: null }, choice: null })),
        setSize: (size_km) => set((st) => (st.aoi ? { aoi: { ...st.aoi, size_km }, search: { ...st.search, data: null }, choice: null } : {})),
        setDates: (before, after) => set((st) => ({ before, after, search: { ...st.search, data: null }, choice: null })),
        setPolarization: (polarization) => set((st) => ({ polarization, search: { ...st.search, data: null }, choice: null })),
        setPlatform: (platform) => set((st) => ({ platform, search: { ...st.search, data: null }, choice: null })),

        searchScenes: async () => {
          const s = session()
          const { aoi, before, after, polarization, platform } = get()
          if (!s || !aoi) return
          searchRequest?.abort()
          const controller = new AbortController()
          searchRequest = controller
          set({ search: { loading: true, data: null, error: null }, choice: null })
          try {
            const data = await api.sarScenes(s, { ...aoi, before, after, polarization, platform }, controller.signal)
            if (searchRequest !== controller) return
            const track = data.tracks.find((t) => t.key === data.proposed_track)
            set({
              search: { loading: false, data, error: null },
              choice: track && track.proposed_before && track.proposed_after ? { track: track.key, before: track.proposed_before, after: track.proposed_after } : null,
            })
            trackOnce('sar-search', { result: data.proposed_track ? 'pairs' : 'none' })
          } catch (err) {
            if (controller.signal.aborted) return
            set({ search: { loading: false, data: null, error: String(err).replace(/^Error: /, '').replace(/^\d{3}: /, '') } })
            trackOnce('sar-search', { result: 'failed' })
          }
        },

        choose: (choice) => set({ choice }),

        compare: async () => {
          const s = session()
          const { aoi, search, choice, polarization, before, after } = get()
          const track = search.data?.tracks.find((t) => t.key === choice?.track)
          const pb = track?.before.find((p) => p.id === choice?.before)
          const pa = track?.after.find((p) => p.id === choice?.after)
          if (!s || !aoi || !pb || !pa) return
          set({ busy: true })
          try {
            const created = await api.sarCompare(s, {
              aoi,
              polarization,
              before: pb.frames,
              after: pa.frames,
              requested_before: before,
              requested_after: after,
            })
            set((st) => ({ comparisons: [created, ...st.comparisons.filter((c) => c.id !== created.id)], shownId: created.id }))
            trackOnce('sar-compare', { size_km: String(aoi.size_km) as '5' | '10' | '20', polarization })
            schedulePoll()
          } catch (err) {
            fail(err)
          } finally {
            set({ busy: false })
          }
        },

        loadComparisons: async () => {
          const s = session()
          if (!s) return
          listRequest?.abort()
          const controller = new AbortController()
          listRequest = controller
          try {
            const list = await api.sarComparisons(s, controller.signal)
            if (listRequest !== controller) return
            const previous = new Map(get().comparisons.map((c) => [c.id, c.status]))
            for (const c of list.comparisons) {
              const was = previous.get(c.id)
              if (was && isRunning({ status: was }) && (c.status === 'ready' || c.status === 'failed')) {
                trackOnce('sar-result', { status: c.status })
              }
            }
            set({ comparisons: list.comparisons })
          } catch {
            if (controller.signal.aborted) return
          }
          schedulePoll()
        },

        recompute: async (id) => {
          const s = session()
          if (!s) return
          try {
            const out = await api.sarRecompute(s, id)
            set((st) => ({ comparisons: st.comparisons.map((c) => (c.id === id ? out : c)) }))
            schedulePoll()
          } catch (err) {
            fail(err)
          }
        },

        remove: async (id) => {
          const s = session()
          if (!s) return
          try {
            await api.sarRemove(s, id)
            set((st) => ({
              comparisons: st.comparisons.filter((c) => c.id !== id),
              shownId: st.shownId === id ? null : st.shownId,
            }))
            if (useUi.getState().compareId === id) useUi.getState().setCompareId(null)
          } catch (err) {
            fail(err)
          }
        },

        open: (id) => {
          set({ shownId: id, selectedRegion: null })
          useUi.getState().setCompareId(id)
          trackOnce('sar-open')
        },

        selectRegion: (selectedRegion) => set({ selectedRegion }),

        showOnMap: (bounds) => {
          focusSeq += 1
          set({ focus: { bounds, seq: focusSeq } })
        },

        reset: () => {
          searchRequest?.abort()
          listRequest?.abort()
          if (pollTimer !== null) clearTimeout(pollTimer)
          pollTimer = null
          set({ search: { loading: false, data: null, error: null }, choice: null, comparisons: [], shownId: null, selectedRegion: null, busy: false })
          useUi.getState().setCompareId(null)
        },
      }
    },
    {
      name: 'satlab.imagery',
      storage: createJSONStorage(() => localStorage),
      partialize: (s) => ({ aoi: s.aoi, before: s.before, after: s.after, polarization: s.polarization }),
      merge: (persisted, current) => ({ ...current, ...sanitize(persisted) }),
    },
  ),
)

function clampLat(lat: number): number {
  return Math.min(Math.max(lat, -80), 84)
}

// A new session (a reload, a new station) starts with no results: comparisons belong to a session.
let lastSessionId: number | null = null
useMission.subscribe((s) => {
  const id = s.session?.id ?? null
  if (id !== lastSessionId) {
    lastSessionId = id
    useSar.getState().reset()
  }
})
