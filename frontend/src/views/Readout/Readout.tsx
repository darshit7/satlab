import { Card } from '../../components/ui/Card'
import { cn } from '../../components/ui/cn'
import { Emoji } from '../../components/ui/Emoji'
import { Pill, StatusText } from '../../components/ui/Pill'
import { TYPE_LABELS } from '../../lib/filters'
import { formatAge, formatCountdown } from '../../lib/time'
import { useSimNow } from '../../lib/useSimNow'
import { useMission } from '../../store/mission'
import type { SatelliteState } from '../../types/ws'

const km = (m: number) => (m / 1000).toLocaleString(undefined, { maximumFractionDigits: 0 })
const kmPerS = (mps: number) => (mps / 1000).toFixed(2)
const deg = (d: number, digits = 1) => d.toFixed(digits)
const muted = 'text-xs text-gray-500 dark:text-gray-400'

// In view (above the horizon mask) is the "active" state, so it takes the accent.
const elevationTone = (sat: SatelliteState) =>
  sat.above_mask ? 'text-primary-700 dark:text-primary-400' : 'text-gray-500 dark:text-gray-400'

/** Label over value: keeps the whole readout on one row. */
function Metric({ label, value, title, className }: { label: string; value: string; title?: string; className?: string }) {
  return (
    <div className="flex flex-col leading-tight" title={title}>
      <dt className={muted}>{label}</dt>
      <dd className={cn('font-mono text-sm tabular-nums', className)}>{value}</dd>
    </div>
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

function SelectedSatellite({ sat, simMs, mask }: { sat: SatelliteState; simMs: number; mask: number }) {
  return (
    <>
      <div className="flex shrink-0 flex-col leading-tight">
        <h2 className="font-semibold">{sat.name}</h2>
        <span className={muted}>
          <span className="font-mono">#{sat.norad_id}</span> · {TYPE_LABELS[sat.type]}
        </span>
      </div>
      <div className="flex shrink-0 flex-col items-start gap-0.5">
        {sat.above_mask ? (
          <StatusText>in view</StatusText>
        ) : (
          <Pill size="sm">{sat.elevation_deg > 0 ? `below the ${mask}° mask` : 'below horizon'}</Pill>
        )}
        <PassCountdown noradId={sat.norad_id} />
      </div>
      <dl className="flex shrink-0 gap-x-4">
        <Metric label="Az" value={`${deg(sat.azimuth_deg)}°`} />
        <Metric label="El" value={`${deg(sat.elevation_deg)}°`} className={elevationTone(sat)} />
        <Metric label="Range" value={`${km(sat.range_m)} km`} />
        <Metric
          label="Rate"
          value={`${sat.range_rate_m_s >= 0 ? '+' : ''}${kmPerS(sat.range_rate_m_s)} km/s`}
          title={sat.range_rate_m_s < 0 ? 'Approaching' : 'Receding'}
        />
        <Metric label="Lat" value={`${deg(sat.lat_deg, 2)}°`} />
        <Metric label="Lon" value={`${deg(sat.lon_deg, 2)}°`} />
        <Metric label="Alt" value={`${km(sat.alt_m)} km`} />
        <Metric label="TLE" value={formatAge(simMs - Date.parse(sat.tle_epoch))} title="TLE age at sim time" />
      </dl>
    </>
  )
}

/** One row over the map: the selected satellite's live geometry. Tracked chips live in the catalog. */
export function Readout() {
  const snapshot = useMission((s) => s.snapshot)
  const selectedId = useMission((s) => s.selectedId)

  const simMs = snapshot ? Date.parse(snapshot.sim_time) : 0
  const mask = snapshot?.observer.horizon_mask_deg ?? 0
  const selected = selectedId !== null ? snapshot?.satellites.find((s) => s.norad_id === selectedId) : undefined

  return (
    <Card as="div" dense>
      <div className="relative -mx-4 -my-3 flex min-h-14 items-center gap-x-5 overflow-x-auto rounded-2xl px-4 py-2 whitespace-nowrap">
        {selected ? (
          <SelectedSatellite sat={selected} simMs={simMs} mask={mask} />
        ) : (
          <p className="text-sm whitespace-normal text-gray-600 dark:text-gray-400">
            {selectedId !== null ? (
              'Waiting for data…'
            ) : (
              <>
                Nothing selected <Emoji name="satellite" label="satellite" /> — pick a dot on the map or in the sky
                views, a row in Overhead, or <span className="font-semibold text-gray-900 dark:text-gray-100">Track</span>{' '}
                a satellite from the catalog.
              </>
            )}
          </p>
        )}
      </div>
    </Card>
  )
}
