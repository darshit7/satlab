import { SkipForward } from 'lucide-react'
import { useEffect } from 'react'
import { Button } from '../../components/ui/Button'
import { Card } from '../../components/ui/Card'
import { cn } from '../../components/ui/cn'
import { formatAge, formatCountdown, formatLocalTime, formatUtcTime } from '../../lib/time'
import { useSimNow } from '../../lib/useSimNow'
import { useMission } from '../../store/mission'

const muted = 'text-sm text-gray-600 dark:text-gray-400'
const th = 'px-2 py-2 text-sm font-semibold text-gray-600 dark:text-gray-400'
const td = 'px-2 py-1.5 font-mono tabular-nums'
// Refetch once the sim clock is this close to the end of the predicted window (or leaves it).
const REFETCH_MARGIN_MS = 60 * 60_000
const JUMP_LEAD_MS = 60_000

/** The next 24 h of passes of the selected satellite over the station (server-predicted). */
export function PassSchedule() {
  const selectedId = useMission((s) => s.selectedId)
  const session = useMission((s) => s.session)
  const passes = useMission((s) => s.passes)
  const loadPasses = useMission((s) => s.loadPasses)
  const setClock = useMission((s) => s.setClock)
  const name = useMission((s) =>
    selectedId === null ? null : (s.snapshot?.satellites.find((x) => x.norad_id === selectedId)?.name ?? null),
  )
  const simMs = useSimNow(1000)

  const current = passes && passes.noradId === selectedId ? passes : null
  const startMs = current?.data ? Date.parse(current.data.start) : null
  const endMs = current?.data ? Date.parse(current.data.end) : null
  const stale =
    simMs !== null && (startMs === null || endMs === null || simMs < startMs || simMs > endMs - REFETCH_MARGIN_MS)

  useEffect(() => {
    if (selectedId === null || session === null || simMs === null) return
    if (current?.loading || current?.error) return // after an error, wait for a new selection
    if (!current?.data || stale) void loadPasses(selectedId, new Date(simMs).toISOString())
  }, [selectedId, session, simMs, current, stale, loadPasses])

  const data = current?.data
  const title = 'Pass schedule'
  if (selectedId === null) {
    return (
      <Card as="div" title={title}>
        <p className={muted}>Select a satellite to see its passes over your station in the next 24 hours.</p>
      </Card>
    )
  }

  return (
    <Card
      as="div"
      title={title}
      aside={<span className={cn(muted, 'truncate')}>{data?.name ?? name ?? `#${selectedId}`}</span>}
      className="lg:overflow-auto"
    >
      {current?.error && <p className="text-sm text-red-700 dark:text-red-400">{current.error}</p>}
      {!data && !current?.error && <p className={muted}>Predicting passes…</p>}
      {data && data.always_visible && (
        <p className={muted}>
          Above the {data.horizon_mask_deg}° mask for the whole day (a geostationary-like orbit): no AOS or LOS.
        </p>
      )}
      {data && !data.always_visible && data.passes.length === 0 && (
        <p className={muted}>No passes above the {data.horizon_mask_deg}° mask in the next 24 hours.</p>
      )}
      {data && data.passes.length > 0 && (
        <div className="relative -mx-2 overflow-x-auto">
          <table className="w-full border-collapse text-sm whitespace-nowrap">
            <thead>
              <tr className="border-b border-gray-200 text-left dark:border-gray-700">
                <th className={th}>AOS (UTC)</th>
                <th className={cn(th, 'text-right')}>Duration</th>
                <th className={cn(th, 'text-right')}>Max el</th>
                <th className={cn(th, 'text-right')}>Az AOS → LOS</th>
                <th className={th}>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {data.passes.map((p) => {
                const aos = Date.parse(p.aos_time)
                const los = Date.parse(p.los_time)
                const now = simMs ?? 0
                const past = los < now
                const live = aos <= now && now <= los
                return (
                  <tr key={p.aos_time} className={cn(past && 'opacity-50')}>
                    <td className={td}>
                      {formatUtcTime(aos)}
                      <span className="block font-sans text-xs text-gray-500 dark:text-gray-400">
                        {live ? (
                          <span className="font-semibold text-primary-700 dark:text-primary-400">in view now</span>
                        ) : past ? (
                          'done'
                        ) : (
                          `in ${formatCountdown(aos - now)} · ${formatLocalTime(aos)} local`
                        )}
                      </span>
                    </td>
                    <td className={cn(td, 'text-right')}>{formatCountdown(p.duration_s * 1000)}</td>
                    <td className={cn(td, 'text-right')}>{p.max_elev_deg.toFixed(1)}°</td>
                    <td className={cn(td, 'text-right')}>
                      {p.aos_az_deg.toFixed(0)}° → {p.los_az_deg.toFixed(0)}°
                    </td>
                    <td className="px-2 py-1 text-right">
                      {!past && !live && (
                        <Button
                          className="px-2 py-1"
                          title="Jump the sim clock to a minute before AOS"
                          onClick={() => setClock({ sim_time: new Date(aos - JUMP_LEAD_MS).toISOString(), paused: false })}
                        >
                          <SkipForward size={14} strokeWidth={1.5} aria-hidden />
                          Jump
                        </Button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      {data && (
        <p className={cn(muted, 'mt-3')}>
          Above the {data.horizon_mask_deg}° horizon mask, predicted from a TLE{' '}
          {formatAge(Date.parse(data.start) - Date.parse(data.tle_epoch))} old at the start of the window; accuracy
          degrades as it ages.
        </p>
      )}
    </Card>
  )
}
