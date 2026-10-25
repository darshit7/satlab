import { Crosshair, Radio, X } from 'lucide-react'
import { Metric } from '../../components/Metric'
import { Button } from '../../components/ui/Button'
import { cn } from '../../components/ui/cn'
import { Pill, StatusText } from '../../components/ui/Pill'
import { RagIcon } from '../../components/ui/Rag'
import { RAG_TEXT, ragKey } from '../../components/ui/ragClasses'
import { TYPE_LABELS } from '../../lib/filters'
import { formatDb, formatDopplerKHz, formatFrequency, formatMarginDb, ragWord } from '../../lib/rag'
import { formatAge, formatCountdown } from '../../lib/time'
import { useSimNow } from '../../lib/useSimNow'
import { useMission } from '../../store/mission'
import type { SatelliteState } from '../../types/ws'

const km = (m: number) => (m / 1000).toLocaleString(undefined, { maximumFractionDigits: 0 })
const kmPerS = (mps: number) => (mps / 1000).toFixed(2)
const deg = (d: number, digits = 1) => d.toFixed(digits)
const muted = 'text-xs text-gray-500 dark:text-gray-400'

// Below the mask the elevation reads muted. In view it stays neutral: the "in view" status carries
// the accent, and next to a margin tile emerald means a green link.
const elevationTone = (sat: SatelliteState) => (sat.above_mask ? undefined : 'text-gray-500 dark:text-gray-400')

/** Margin and Doppler from the published link budget, or a plain "no RF data". */
function LinkMetrics({ sat }: { sat: SatelliteState }) {
  const link = sat.link
  if (!link) {
    return <Metric wide label="Link" value="no RF data" className="text-gray-500 dark:text-gray-400" title="No published RF parameters for this satellite" />
  }
  return (
    <>
      <Metric
        label="Margin dB"
        value={formatMarginDb(link.margin_db)}
        className={RAG_TEXT[link.rag]}
        icon={<RagIcon rag={link.rag} size={13} />}
        title={`Link: ${ragWord(link.rag)}${link.ebn0_db !== null ? ` · Eb/N0 ${formatDb(link.ebn0_db)} dB` : ''}`}
      />
      <Metric
        label="Doppler kHz"
        value={formatDopplerKHz(link.doppler_hz)}
        title={`Shift of the ${formatFrequency(link.frequency_hz)} downlink (${link.doppler_hz > 0 ? 'approaching' : 'receding'})`}
      />
    </>
  )
}

/** "LOS in 4:12" / "AOS in 31:05" from the satellite's server-computed pass arc. */
function PassCountdown({ noradId }: { noradId: number }) {
  const arc = useMission((s) => s.arcs[noradId])
  const simMs = useSimNow(1000)
  if (!arc || simMs === null) return null
  const aos = arc.aos_time ? Date.parse(arc.aos_time) : null
  const los = arc.los_time ? Date.parse(arc.los_time) : null
  const text =
    aos !== null && simMs < aos
      ? `AOS in ${formatCountdown(aos - simMs)}`
      : los !== null && simMs <= los
        ? `LOS in ${formatCountdown(los - simMs)}`
        : null
  return text && <span className="font-mono text-xs text-gray-600 tabular-nums dark:text-gray-400">{text}</span>
}

/**
 * The inspector's sticky header for the selection: who it is, whether it's in view, the go/no-go
 * rollup and the contact, so they stay on screen while the details scroll under it.
 */
export function SelectionHeader({ sat }: { sat: SatelliteState }) {
  const mask = useMission((s) => s.snapshot?.observer.horizon_mask_deg ?? 0)
  const simMs = useMission((s) => (s.snapshot ? Date.parse(s.snapshot.sim_time) : null))
  const rollup = useMission((s) => (s.snapshot?.health?.norad_id === sat.norad_id ? s.snapshot.health.rollup : undefined))
  const contact = useMission((s) => (s.snapshot?.contact?.norad_id === sat.norad_id ? s.snapshot.contact.state : null))
  const isTracked = useMission((s) => s.tracked.some((t) => t.norad_id === sat.norad_id))
  const track = useMission((s) => s.track)
  const untrack = useMission((s) => s.untrack)
  const select = useMission((s) => s.select)

  return (
    <div className="z-10 rounded-2xl border border-gray-200 bg-white/75 px-4 py-2.5 backdrop-blur lg:sticky lg:top-0 dark:border-gray-800 dark:bg-dark/75">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1 leading-tight">
          <h2 className="truncate text-xl font-semibold" title={sat.name}>
            {sat.name}
          </h2>
          <p className={muted}>
            <span className="font-mono">#{sat.norad_id}</span> · {TYPE_LABELS[sat.type]}
            {simMs !== null && <span title="TLE age at sim time"> · TLE {formatAge(simMs - Date.parse(sat.tle_epoch))} old</span>}
          </p>
        </div>
        <Button
          active={isTracked}
          aria-pressed={isTracked}
          title={isTracked ? 'Stop tracking (also clears the selection)' : 'Track: keep its orbit and name on every view'}
          onClick={() =>
            isTracked
              ? untrack(sat.norad_id)
              : track({
                  norad_id: sat.norad_id,
                  name: sat.name,
                  type: sat.type,
                  is_sar_capable: sat.is_sar_capable,
                  tle_epoch: sat.tle_epoch,
                  fetched_at: null,
                })
          }
        >
          <Crosshair size={16} strokeWidth={1.5} aria-hidden />
          {isTracked ? 'Tracking' : 'Track'}
        </Button>
        <Button variant="icon" className="mt-1" aria-label="Clear the selection" title="Clear the selection" onClick={() => select(null)}>
          <X size={18} strokeWidth={1.5} />
        </Button>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
        {sat.above_mask ? (
          <StatusText>in view</StatusText>
        ) : (
          <Pill size="sm">{sat.elevation_deg > 0 ? `below the ${mask}° mask` : 'below horizon'}</Pill>
        )}
        <PassCountdown noradId={sat.norad_id} />
        {contact === 'active' && (
          <span className="flex items-center gap-1 text-xs font-semibold text-primary-700 dark:text-primary-400">
            <Radio size={14} strokeWidth={1.5} aria-hidden />
            In contact
          </span>
        )}
        {contact === 'too_fast' && (
          <span className={cn('flex items-center gap-1', muted)} title="Comms run at 60× or slower">
            <Radio size={14} strokeWidth={1.5} aria-hidden />
            Too fast for comms
          </span>
        )}
        {rollup !== undefined && (
          <span
            className={cn('ml-auto flex items-center gap-1 text-sm font-semibold', RAG_TEXT[ragKey(rollup)])}
            title="Systems: worst of the subsystems"
          >
            <RagIcon rag={rollup} size={16} />
            {ragWord(rollup)}
          </span>
        )}
      </div>
    </div>
  )
}

/** The selection's live geometry and link, as a grid of metric tiles. */
export function ReadoutMetrics({ sat }: { sat: SatelliteState }) {
  return (
    <dl className="grid grid-cols-3 gap-1.5">
      <Metric label="Az" value={`${deg(sat.azimuth_deg)}°`} />
      <Metric label="El" value={`${deg(sat.elevation_deg)}°`} className={elevationTone(sat)} />
      <Metric label="Range km" value={km(sat.range_m)} />
      <Metric
        label="Rate"
        value={`${sat.range_rate_m_s >= 0 ? '+' : ''}${kmPerS(sat.range_rate_m_s)} km/s`}
        title={sat.range_rate_m_s < 0 ? 'Approaching' : 'Receding'}
      />
      <LinkMetrics sat={sat} />
      <Metric wide label="Lat, lon" value={`${deg(sat.lat_deg, 2)}°, ${deg(sat.lon_deg, 2)}°`} title="Sub-satellite point" />
      <Metric label="Alt km" value={km(sat.alt_m)} title="Height above the WGS84 ellipsoid" />
    </dl>
  )
}
