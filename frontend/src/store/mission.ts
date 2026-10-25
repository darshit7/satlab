import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import { api } from '../api/client'
import { appendPackets, mergeStatuses } from '../lib/comms'
import { DEFAULT_FILTERS, sanitizeFilters } from '../lib/filters'
import { followAutomation, mergeAlarms, shouldAnnounce } from '../lib/schedule'
import { formatUtcTime, type ClockSample } from '../lib/time'
import type {
  GroundStation,
  LinkProfiles,
  LocationInput,
  MissionSessionCreated,
  PacketTemplates,
  PassPrediction,
  SatelliteSummary,
  SessionSite,
} from '../types/api'
import type {
  AlarmMessage,
  CommandStatusMessage,
  GroundTrackMessage,
  PacketMessage,
  PassArcMessage,
  ServerMessage,
  SetClock,
  Snapshot,
  UpcomingMessage,
  VisibilityFilter,
} from '../types/ws'
import { SessionSocket, type ConnectionStatus } from '../ws/sessionSocket'

/** A custom location being edited (map click, geolocation, typed) before a session starts there. */
export type DraftLocation = LocationInput

export interface Notice {
  id: number
  message: string
  /** alert: something went wrong or needs attention · info: something happened, e.g. automation. */
  tone: 'alert' | 'info'
}

export interface PassSchedule {
  noradId: number
  loading: boolean
  data: PassPrediction | null
  error: string | null
}

interface MissionState {
  /** Preset stations; custom locations are never saved as stations. */
  stations: GroundStation[]
  /** Published link budgets in use and the margin → RAG thresholds (reference data). */
  linkProfiles: LinkProfiles | null
  /** Satellites with packet templates and the commands they take (reference data). */
  packetTemplates: PacketTemplates | null
  /** Where the current (or last) session observes from: resumed on reload. */
  site: SessionSite | null
  /** The live session, with its capability token: in memory only, never persisted. */
  session: MissionSessionCreated | null
  connection: ConnectionStatus
  snapshot: Snapshot | null
  clockSample: ClockSample | null
  tracks: Record<number, GroundTrackMessage>
  /** Sky arcs of the satellites in the latest snapshot. */
  arcs: Record<number, PassArcMessage>
  upcoming: UpcomingMessage | null
  /** The session's latest packets, oldest first. */
  packets: PacketMessage[]
  /** The latest status of each recent command, by command id. */
  commands: Record<number, CommandStatusMessage>
  /** Pass-automation alarms, by alarm id (the newest only). */
  alarms: Record<number, AlarmMessage>
  tracked: SatelliteSummary[]
  selectedId: number | null
  filters: VisibilityFilter
  passes: PassSchedule | null
  draft: DraftLocation | null
  notices: Notice[]

  loadStations: () => Promise<void>
  loadLinkProfiles: () => Promise<void>
  loadPacketTemplates: () => Promise<void>
  sendCommand: (noradId: number, command: string, params: Record<string, number | string>) => void
  scheduleCommand: (noradId: number, command: string, params: Record<string, number | string>, aosTime: string) => void
  cancelCommand: (commandId: number) => void
  ackAlarm: (alarmId: number) => void
  startSession: (site: SessionSite) => Promise<void>
  track: (sat: SatelliteSummary) => void
  untrack: (noradId: number) => void
  select: (noradId: number | null) => void
  setFilters: (change: Partial<VisibilityFilter>) => void
  loadPasses: (noradId: number, startIso: string) => Promise<void>
  setDraft: (draft: DraftLocation | null) => void
  setClock: (change: Omit<SetClock, 'type'>) => void
  notify: (message: string, tone?: Notice['tone']) => void
  dismiss: (id: number) => void
}

interface Persisted {
  site: SessionSite | null
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
// Arcs, packets and command statuses arrive in bursts just ahead of a snapshot: merge them in one
// update with it.
let pendingArcs: PassArcMessage[] = []
let pendingPackets: PacketMessage[] = []
let pendingStatuses: CommandStatusMessage[] = []
// The booked pass the client last followed (pass automation turns the antenna, and the selection).
let lastAutomationKey: string | null = null

const inRange = (v: unknown, min: number, max: number): v is number =>
  typeof v === 'number' && Number.isFinite(v) && v >= min && v <= max

function sanitizeSite(raw: unknown, legacyStationId: unknown): SessionSite | null {
  if (typeof raw === 'object' && raw !== null) {
    const r = raw as Record<string, unknown>
    if (Number.isInteger(r.station_id)) return { station_id: r.station_id as number }
    const loc = r.location as Record<string, unknown> | null | undefined
    if (
      typeof loc === 'object' &&
      loc !== null &&
      inRange(loc.lat_deg, -90, 90) &&
      inRange(loc.lon_deg, -180, 180) &&
      inRange(loc.alt_m, -500, 9000)
    ) {
      return { location: { lat_deg: loc.lat_deg, lon_deg: loc.lon_deg, alt_m: loc.alt_m } }
    }
  }
  // Before sessions carried their own location, only a station id was remembered.
  return Number.isInteger(legacyStationId) ? { station_id: legacyStationId as number } : null
}

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
    site: sanitizeSite(r.site, r.stationId),
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
        const comms =
          pendingPackets.length > 0 || pendingStatuses.length > 0
            ? { packets: appendPackets(get().packets, pendingPackets), commands: mergeStatuses(get().commands, pendingStatuses) }
            : {}
        pendingPackets = []
        pendingStatuses = []
        const follow = followAutomation(message.automation, lastAutomationKey, get().selectedId)
        lastAutomationKey = follow.key
        set({
          snapshot: message,
          arcs,
          clockSample: { simMs: Date.parse(message.sim_time), receivedAt: performance.now(), clock: message.clock },
          ...comms,
          // the server already selected it: no set_selected back
          ...(follow.select !== null ? { selectedId: follow.select } : {}),
        })
        const automation = message.automation
        if (follow.announce && automation) {
          const name = message.satellites.find((s) => s.norad_id === automation.norad_id)?.name ?? `#${automation.norad_id}`
          const aos = formatUtcTime(Date.parse(automation.aos_time)).slice(0, 5)
          get().notify(
            `Pass automation: the antenna is on ${name} for its ${aos} UTC pass${
              automation.clock_slowed ? ', and the clock dropped to real time for it' : ''
            }.`,
            'info',
          )
        }
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
          case 'packet':
            pendingPackets.push(message)
            break
          case 'command_status':
            pendingStatuses.push(message)
            break
          case 'alarm': {
            const announce = shouldAnnounce(message, get().alarms)
            set((s) => ({ alarms: mergeAlarms(s.alarms, [message]) }))
            if (announce) get().notify(`Alarm: ${message.message}`)
            break
          }
          case 'error':
            get().notify(message.message)
            break
        }
      }

      return {
        stations: [],
        linkProfiles: null,
        packetTemplates: null,
        site: null,
        session: null,
        connection: 'idle',
        snapshot: null,
        clockSample: null,
        tracks: {},
        arcs: {},
        upcoming: null,
        packets: [],
        commands: {},
        alarms: {},
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

        loadLinkProfiles: async () => {
          try {
            set({ linkProfiles: await api.linkProfiles() })
          } catch {
            // Reference data only (sources, legend thresholds): the live link state still streams.
          }
        },

        loadPacketTemplates: async () => {
          try {
            set({ packetTemplates: await api.packetTemplates() })
          } catch {
            // Reference data (commands, sources): without it the comms panel offers no commands.
          }
        },

        sendCommand: (noradId, command, params) => {
          socket?.send({ type: 'send_command', norad_id: noradId, command, params })
        },

        scheduleCommand: (noradId, command, params, aosTime) => {
          socket?.send({ type: 'schedule_command', norad_id: noradId, command, params, aos_time: aosTime })
        },

        cancelCommand: (commandId) => {
          socket?.send({ type: 'cancel_command', command_id: commandId })
        },

        ackAlarm: (alarmId) => {
          socket?.send({ type: 'ack_alarm', alarm_id: alarmId })
        },

        startSession: async (site) => {
          const generation = ++sessionGeneration
          const previous = get().session
          socket?.close()
          socket = null
          pendingArcs = []
          pendingPackets = []
          pendingStatuses = []
          lastAutomationKey = null
          if (previous && !previous.ended_at) void api.endSession(previous).catch(() => {})
          set({
            site,
            session: null,
            snapshot: null,
            clockSample: null,
            tracks: {},
            arcs: {},
            upcoming: null,
            passes: null,
            packets: [],
            commands: {},
            alarms: {},
          })

          let session: MissionSessionCreated
          try {
            session = await api.createSession(site)
          } catch (err) {
            if (generation !== sessionGeneration) return
            set({ connection: 'closed' })
            get().notify(`Could not start a mission session: ${String(err)}`)
            return
          }
          if (generation !== sessionGeneration) {
            void api.endSession(session).catch(() => {}) // superseded by a newer call
            return
          }
          // A custom location has been applied: it is no longer a draft.
          set((s) => ({ session, draft: 'location' in site ? null : s.draft }))
          const current = new SessionSocket(session.id, session.token, {
            onMessage,
            onStatus: (connection) => set({ connection }),
            onOpen: () => {
              sendFilters()
              sendTracked()
              sendSelected()
            },
            onSessionUnavailable: () => {
              // e.g. the server ended the session; start a fresh one at the same place
              if (socket === current) setTimeout(() => void get().startSession(site), 1000)
            },
            onServerBusy: () => get().notify('Mission control is at capacity. Retrying shortly…'),
          })
          socket = current
          current.connect()
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
            const data = await api.passes(session, noradId, startIso, 24, controller.signal)
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

        notify: (message, tone = 'alert') => {
          const id = ++noticeSeq
          set((s) => ({ notices: [...s.notices.slice(-4), { id, message, tone }] }))
          setTimeout(() => get().dismiss(id), 8000)
        },
        dismiss: (id) => set((s) => ({ notices: s.notices.filter((n) => n.id !== id) })),
      }
    },
    {
      name: 'satlab.mission',
      storage: createJSONStorage(() => localStorage),
      partialize: (s): Persisted => ({ site: s.site, tracked: s.tracked, filters: s.filters }),
      merge: (persisted, current) => ({ ...current, ...sanitizePersisted(persisted) }),
    },
  ),
)
