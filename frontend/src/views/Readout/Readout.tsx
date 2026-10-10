import { X } from 'lucide-react'
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

// In view (above the horizon mask) is the "active" state, so it takes the accent.
const elevationTone = (sat: SatelliteState) =>
  sat.above_mask ? 'text-primary-700 dark:text-primary-400' : 'text-gray-500 dark:text-gray-400'

function Metric({ label, value, hint, className }: { label: string; value: string; hint?: string; className?: string }) {
  return (
    <div className="rounded-lg bg-gray-50 px-3 py-2 ring-1 ring-gray-900/5 dark:bg-white/5 dark:ring-white/10">
      <div className="text-sm text-gray-600 dark:text-gray-400">{label}</div>
      <div className={cn('font-mono text-base whitespace-nowrap tabular-nums', className)}>{value}</div>
      {hint && <div className="text-xs text-gray-500 dark:text-gray-400">{hint}</div>}
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
  return text && <span className="font-mono text-sm text-gray-600 tabular-nums dark:text-gray-400">{text}</span>
}

function SelectedSatellite({ sat, simMs, mask }: { sat: SatelliteState; simMs: number; mask: number }) {
  return (
    <div className="mb-5">
      <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1">
        <h2 className="text-xl font-semibold">{sat.name}</h2>
        <span className="font-mono text-sm text-gray-600 dark:text-gray-400">#{sat.norad_id}</span>
        <Pill size="sm">{TYPE_LABELS[sat.type]}</Pill>
        {sat.above_mask ? (
          <StatusText>in view</StatusText>
        ) : (
          <Pill size="sm">{sat.elevation_deg > 0 ? `below the ${mask}° mask` : 'below horizon'}</Pill>
        )}
        <PassCountdown noradId={sat.norad_id} />
        <span className="text-sm text-gray-600 dark:text-gray-400">
          TLE {formatAge(simMs - Date.parse(sat.tle_epoch))} old at sim time
        </span>
      </div>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(7.5rem,1fr))] gap-2">
        <Metric label="Azimuth" value={`${deg(sat.azimuth_deg)}°`} />
        <Metric label="Elevation" value={`${deg(sat.elevation_deg)}°`} className={elevationTone(sat)} />
        <Metric label="Slant range" value={`${km(sat.range_m)} km`} />
        <Metric
          label="Range rate"
          value={`${sat.range_rate_m_s >= 0 ? '+' : ''}${kmPerS(sat.range_rate_m_s)} km/s`}
          hint={sat.range_rate_m_s < 0 ? 'approaching' : 'receding'}
        />
        <Metric label="Latitude" value={`${deg(sat.lat_deg, 2)}°`} />
        <Metric label="Longitude" value={`${deg(sat.lon_deg, 2)}°`} />
        <Metric label="Altitude" value={`${km(sat.alt_m)} km`} />
      </div>
    </div>
  )
}

const th = 'px-3 py-2 text-sm font-semibold text-gray-600 dark:text-gray-400'
const td = 'px-3 py-2 font-mono tabular-nums text-right'

export function Readout() {
  const snapshot = useMission((s) => s.snapshot)
  const tracked = useMission((s) => s.tracked)
  const selectedId = useMission((s) => s.selectedId)
  const select = useMission((s) => s.select)
  const untrack = useMission((s) => s.untrack)

  const states = new Map(snapshot?.satellites.map((s) => [s.norad_id, s]))
  const simMs = snapshot ? Date.parse(snapshot.sim_time) : 0
  const mask = snapshot?.observer.horizon_mask_deg ?? 0
  const selected = selectedId !== null ? states.get(selectedId) : undefined

  if (tracked.length === 0 && !selected) {
    return (
      <Card as="div">
        <p className="text-gray-600 dark:text-gray-400">
          Nothing selected yet <Emoji name="satellite" label="satellite" /> — pick a dot in the sky views or a row
          in Overhead, or search the catalog and press{' '}
          <span className="font-semibold text-gray-900 dark:text-gray-100">Track</span> to keep a satellite on
          screen with its ground track.
        </p>
      </Card>
    )
  }

  return (
    <Card as="div" className="lg:overflow-auto">
      {selected ? (
        <SelectedSatellite sat={selected} simMs={simMs} mask={mask} />
      ) : (
        <p className="mb-3 text-sm text-gray-600 dark:text-gray-400">
          Select a satellite (a table row, a map dot or a dot in either sky view) for detailed readouts.
        </p>
      )}
      {tracked.length > 0 && (
        <div className="relative -mx-3 overflow-x-auto">
          <table className="w-full border-collapse text-sm whitespace-nowrap">
            <thead>
              <tr className="border-b border-gray-200 dark:border-gray-700">
                <th className={cn(th, 'text-left')}>Satellite</th>
                <th className={cn(th, 'text-right')}>Az °</th>
                <th className={cn(th, 'text-right')}>El °</th>
                <th className={cn(th, 'text-right')}>Range km</th>
                <th className={cn(th, 'text-right')}>Rate km/s</th>
                <th className={cn(th, 'text-right')}>Lat °</th>
                <th className={cn(th, 'text-right')}>Lon °</th>
                <th className={cn(th, 'text-right')}>Alt km</th>
                <th className={th}>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {tracked.map((t) => {
                const s = states.get(t.norad_id)
                const isSelected = t.norad_id === selectedId
                return (
                  <tr
                    key={t.norad_id}
                    className={cn(
                      'cursor-pointer transition-colors duration-150 hover:bg-gray-50 dark:hover:bg-white/5',
                      isSelected && 'bg-gray-50 dark:bg-white/5',
                    )}
                    onClick={() => select(t.norad_id)}
                  >
                    <td className="px-3 py-2">
                      <span className={cn('font-semibold', isSelected && 'text-primary-700 dark:text-primary-400')}>
                        {t.name}
                      </span>{' '}
                      <span className="font-mono text-gray-500 dark:text-gray-400">#{t.norad_id}</span>
                    </td>
                    {s ? (
                      <>
                        <td className={td}>{deg(s.azimuth_deg)}</td>
                        <td className={cn(td, elevationTone(s))}>{deg(s.elevation_deg)}</td>
                        <td className={td}>{km(s.range_m)}</td>
                        <td className={td}>{kmPerS(s.range_rate_m_s)}</td>
                        <td className={td}>{deg(s.lat_deg, 2)}</td>
                        <td className={td}>{deg(s.lon_deg, 2)}</td>
                        <td className={td}>{km(s.alt_m)}</td>
                      </>
                    ) : (
                      <td colSpan={7} className="px-3 py-2 text-gray-500 dark:text-gray-400">
                        waiting for data…
                      </td>
                    )}
                    <td className="px-3 py-2 text-right">
                      <button
                        type="button"
                        aria-label={`Untrack ${t.name}`}
                        className="rounded p-0.5 text-gray-500 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100"
                        onClick={(e) => {
                          e.stopPropagation()
                          untrack(t.norad_id)
                        }}
                      >
                        <X size={16} strokeWidth={1.5} />
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
