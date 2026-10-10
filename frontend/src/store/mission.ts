import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import { api } from '../api/client'
import { DEFAULT_FILTERS, sanitizeFilters } from '../lib/filters'
import type { ClockSample } from '../lib/time'
import type { GroundStation, GroundStationCreate, MissionSession, PassPrediction, SatelliteSummary } from '../types/api'
import type {
  GroundTrackMessage,
  PassArcMessage,
  ServerMessage,
  SetClock,
  Snapshot,
  UpcomingMessage,
  VisibilityFilter,
} from '../types/ws'
import { SessionSocket, type ConnectionStatus } from '../ws/sessionSocket'

export interface DraftLocation {
  lat_deg: number
  lon_deg: number
  alt_m: number
}

export interface Notice {
  id: number
  message: string
}

export interface PassSchedule {
  noradId: number
  loading: boolean
  data: PassPrediction | null
  error: string | null
}

interface MissionState {
  stations: GroundStation[]
  stationId: number | null
  session: MissionSession | null
  connection: ConnectionStatus
  snapshot: Snapshot | null
  clockSample: ClockSample | null
  tracks: Record<number, GroundTrackMessage>
  /** Sky arcs of the satellites in the latest snapshot. */
  arcs: Record<number, PassArcMessage>
  upcoming: UpcomingMessage | null
  tracked: SatelliteSummary[]
  selectedId: number | null
  filters: VisibilityFilter
  passes: PassSchedule | null
  draft: DraftLocation | null
  notices: Notice[]

  loadStations: () => Promise<void>
  startSession: (stationId: number) => Promise<void>
  createStationAndStart: (body: GroundStationCreate) => Promise<void>
  track: (sat: SatelliteSummary) => void
  untrack: (noradId: number) => void
  select: (noradId: number | null) => void
  setFilters: (change: Partial<VisibilityFilter>) => void
  loadPasses: (noradId: number, startIso: string) => Promise<void>
  setDraft: (draft: DraftLocation | null) => void
  setClock: (change: Omit<SetClock, 'type'>) => void
  notify: (message: string) => void
  dismiss: (id: number) => void
}

interface Persisted {
  stationId: number | null
  tracked: SatelliteSummary[]
  filters: VisibilityFilter
}

const FILTER_DEBOUNCE_MS = 200

// The live socket is not React state; it lives beside the store.
let socket: SessionSocket | null = null
let sessionGeneration = 0 // guards against overlapping startSession calls
let noticeSeq = 0
let filterTimer: ReturnType<typeof setTimeout> | null = null
let passesRequest: AbortController | null = null
// Arcs arrive in bursts just ahead of the snapshot that uses them: merge them in one update.
let pendingArcs: PassArcMessage[] = []

/** Persisted state is untrusted (it's localStorage): keep only well-formed values. */
function sanitizePersisted(raw: unknown): Partial<Persisted> {
  if (typeof raw !== 'object' || raw === null) return {}
  const r = raw as Record<string, unknown>
  const tracked = Array.isArray(r.tracked)
    ? (r.tracked as unknown[]).filter(
        (t): t is SatelliteSummary =>
          typeof t === 'object' &&
          t !== null &&
          Number.isInteger((t as SatelliteSummary).norad_id) &&
          typeof (t as SatelliteSummary).name === 'string',
      )
    : []
  return {
    stationId: Number.isInteger(r.stationId) ? (r.stationId as number) : null,
    tracked,
    filters: sanitizeFilters(r.filters),
  }
}

export const useMission = create<MissionState>()(
  persist(
    (set, get) => {
      const sendTracked = () =>
        socket?.send({ type: 'set_tracked', norad_ids: get().tracked.map((s) => s.norad_id) })
      const sendSelected = () => socket?.send({ type: 'set_selected', norad_id: get().selectedId })
      const sendFilters = () => socket?.send({ type: 'set_filter', ...get().filters })

      const onSnapshot = (message: Snapshot) => {
        const present = new Set(message.satellites.map((s) => s.norad_id))
        let arcs = get().arcs
        const stale = Object.keys(arcs).some((id) => !present.has(Number(id)))
        if (stale || pendingArcs.length > 0) {
          arcs = Object.fromEntries(Object.entries(arcs).filter(([id]) => present.has(Number(id))))
          for (const arc of pendingArcs) arcs[arc.norad_id] = arc
          pendingArcs = []
        }
        set({
          snapshot: message,
          arcs,
          clockSample: { simMs: Date.parse(message.sim_time), receivedAt: performance.now(), clock: message.clock },
        })
      }

      const onMessage = (message: ServerMessage) => {
        switch (message.type) {
          case 'snapshot':
            onSnapshot(message)
            break
          case 'pass_arc':
            pendingArcs.push(message)
            break
          case 'ground_track':
            set((s) => ({ tracks: { ...s.tracks, [message.norad_id]: message } }))
            break
          case 'upcoming':
            set({ upcoming: message })
            break
          case 'error':
            get().notify(message.message)
            break
        }
      }

      return {
        stations: [],
        stationId: null,
        session: null,
        connection: 'idle',
        snapshot: null,
        clockSample: null,
        tracks: {},
        arcs: {},
        upcoming: null,
        tracked: [],
        selectedId: null,
        filters: DEFAULT_FILTERS,
        passes: null,
        draft: null,
        notices: [],

        loadStations: async () => {
          try {
            set({ stations: await api.listStations() })
          } catch (err) {
            get().notify(`Could not load ground stations (${String(err)}). Is the backend running?`)
          }
        },

        startSession: async (stationId) => {
          const generation = ++sessionGeneration
          const previous = get().session
          socket?.close()
          socket = null
          pendingArcs = []
          if (previous && !previous.ended_at) void api.endSession(previous.id).catch(() => {})
          set({ stationId, session: null, snapshot: null, clockSample: null, tracks: {}, arcs: {}, upcoming: null, passes: null })

          let session: MissionSession
          try {
            session = await api.createSession({ station_id: stationId })
          } catch (err) {
            if (generation !== sessionGeneration) return
            set({ connection: 'closed' })
            get().notify(`Could not start a mission session: ${String(err)}`)
            return
          }
          if (generation !== sessionGeneration) {
            void api.endSession(session.id).catch(() => {}) // superseded by a newer call
            return
          }
          set({ session })
          const current = new SessionSocket(session.id, {
            onMessage,
            onStatus: (connection) => set({ connection }),
            onOpen: () => {
              sendFilters()
              sendTracked()
              sendSelected()
            },
            onSessionUnavailable: () => {
              // e.g. the server ended the session; start a fresh one at the same station
              if (socket === current) setTimeout(() => void get().startSession(stationId), 1000)
            },
          })
          socket = current
          current.connect()
        },

        createStationAndStart: async (body) => {
          try {
            const station = await api.createStation(body)
            set((s) => ({ stations: [...s.stations, station], draft: null }))
            await get().startSession(station.id)
          } catch (err) {
            get().notify(`Could not save ground station: ${String(err)}`)
          }
        },

        track: (sat) => {
          if (!get().tracked.some((s) => s.norad_id === sat.norad_id)) {
            set((s) => ({ tracked: [...s.tracked, sat] }))
            sendTracked()
          }
          get().select(sat.norad_id)
        },

        untrack: (noradId) => {
          set((s) => {
            const tracks = { ...s.tracks }
            delete tracks[noradId]
            return { tracked: s.tracked.filter((t) => t.norad_id !== noradId), tracks }
          })
          sendTracked()
          if (get().selectedId === noradId) get().select(null)
        },

        select: (selectedId) => {
          if (selectedId === get().selectedId) return
          set({ selectedId })
          sendSelected()
        },

        setFilters: (change) => {
          set((s) => ({ filters: { ...s.filters, ...change } }))
          if (filterTimer) clearTimeout(filterTimer)
          filterTimer = setTimeout(sendFilters, FILTER_DEBOUNCE_MS)
        },

        loadPasses: async (noradId, startIso) => {
          passesRequest?.abort()
          const controller = new AbortController()
          passesRequest = controller
          const session = get().session
          if (!session) return
          set((s) => ({
            passes: { noradId, loading: true, data: s.passes?.noradId === noradId ? s.passes.data : null, error: null },
          }))
          try {
            const data = await api.passes(session.id, noradId, startIso, 24, controller.signal)
            if (passesRequest === controller) set({ passes: { noradId, loading: false, data, error: null } })
          } catch (err) {
            if (controller.signal.aborted) return
            set({ passes: { noradId, loading: false, data: null, error: String(err) } })
          }
        },

        setDraft: (draft) => set({ draft }),
        setClock: (change) => {
          socket?.send({ type: 'set_clock', ...change })
        },

        notify: (message) => {
          const id = ++noticeSeq
          set((s) => ({ notices: [...s.notices.slice(-4), { id, message }] }))
          setTimeout(() => get().dismiss(id), 8000)
        },
        dismiss: (id) => set((s) => ({ notices: s.notices.filter((n) => n.id !== id) })),
      }
    },
    {
      name: 'satlab.mission',
      storage: createJSONStorage(() => localStorage),
      partialize: (s): Persisted => ({ stationId: s.stationId, tracked: s.tracked, filters: s.filters }),
      merge: (persisted, current) => ({ ...current, ...sanitizePersisted(persisted) }),
    },
  ),
)
