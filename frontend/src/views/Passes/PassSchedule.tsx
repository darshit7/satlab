import { CalendarClock, SkipForward } from 'lucide-react'
import { useEffect } from 'react'
import { Button } from '../../components/ui/Button'
import { cn } from '../../components/ui/cn'
import { Dialog } from '../../components/ui/Dialog'
import { passesAround } from '../../lib/passes'
import { formatAge, formatCountdown, formatLocalTime, formatUtcTime } from '../../lib/time'
import { useSimNow } from '../../lib/useSimNow'
import { useMission } from '../../store/mission'
import { useUi } from '../../store/ui'

const muted = 'text-sm text-gray-600 dark:text-gray-400'
const th = 'px-2 py-2 text-sm font-semibold text-gray-600 dark:text-gray-400'
const td = 'px-2 py-1.5 font-mono tabular-nums'
// Refetch once the sim clock is this close to the end of the predicted window (or leaves it).
const REFETCH_MARGIN_MS = 60 * 60_000
const JUMP_LEAD_MS = 60_000

/** A minute before AOS, so the pass starts in view. */
const jumpTo = (aosMs: number) =>
  useMission.getState().setClock({ sim_time: new Date(aosMs - JUMP_LEAD_MS).toISOString(), paused: false })

/** Keeps the selection's next 24 h of passes over the session's location loaded (server-predicted). */
function usePassSchedule() {
  const selectedId = useMission((s) => s.selectedId)
  const session = useMission((s) => s.session)
  const passes = useMission((s) => s.passes)
  const loadPasses = useMission((s) => s.loadPasses)
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

  return { current, simMs }
}

/**
 * One line under the readout: the pass in progress or the next one (with Jump), and the way to the
 * full schedule. Mounted with the selection, it keeps the schedule loaded for the dialog too.
 */
export function NextPass() {
  const { current, simMs } = usePassSchedule()
  const setPassesOpen = useUi((s) => s.setPassesOpen)
  const data = current?.data
  const { live, next } = data && simMs !== null ? passesAround(data.passes, simMs) : { live: null, next: null }
  const mono = 'font-mono tabular-nums text-gray-900 dark:text-gray-100'

  let text
  if (current?.error) text = <span className="text-red-700 dark:text-red-400">Couldn’t predict passes.</span>
  else if (!data || simMs === null) text = 'Predicting passes…'
  else if (data.always_visible) text = `Above the ${data.horizon_mask_deg}° mask all day: no AOS or LOS.`
  else if (live)
    text = (
      <>
        <span className="font-semibold text-primary-700 dark:text-primary-400">In a pass</span> · max{' '}
        <span className={mono}>{live.max_elev_deg.toFixed(0)}°</span> · ends in{' '}
        <span className={mono}>{formatCountdown(Date.parse(live.los_time) - simMs)}</span>
      </>
    )
  else if (next)
    text = (
      <>
        Next pass in <span className={mono}>{formatCountdown(Date.parse(next.aos_time) - simMs)}</span> · max{' '}
        <span className={mono}>{next.max_elev_deg.toFixed(0)}°</span> ·{' '}
        <span className={mono}>{formatCountdown(next.duration_s * 1000)}</span>
      </>
    )
  else text = `No passes above the ${data.horizon_mask_deg}° mask in the next 24 hours.`

  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1.5">
      <CalendarClock size={16} strokeWidth={1.5} className="shrink-0 text-gray-500 dark:text-gray-400" aria-hidden />
      <span className={cn(muted, 'min-w-0 flex-1')}>{text}</span>
      <span className="flex gap-1.5">
        {!live && next && (
          <Button title="Jump the sim clock to a minute before AOS" onClick={() => jumpTo(Date.parse(next.aos_time))}>
            <SkipForward size={14} strokeWidth={1.5} aria-hidden />
            Jump
          </Button>
        )}
        <Button disabled={!data && !current?.error} onClick={() => setPassesOpen(true)}>
          All passes
        </Button>
      </span>
    </div>
  )
}

/** The selection's passes over the next 24 hours, in a dialog opened from the Next pass line. */
export function PassScheduleDialog() {
  const open = useUi((s) => s.passesOpen)
  const setPassesOpen = useUi((s) => s.setPassesOpen)
  const selectedId = useMission((s) => s.selectedId)
  const passes = useMission((s) => s.passes)
  const simMs = useSimNow(1000)
  const close = () => setPassesOpen(false)

  const current = passes && passes.noradId === selectedId ? passes : null
  const data = current?.data

  return (
    <Dialog
      open={open && selectedId !== null}
      onClose={close}
      title={
        <>
          Pass schedule
          {data && <span className="ml-2 text-base font-normal text-gray-600 dark:text-gray-400">{data.name}</span>}
        </>
      }
      className="w-[min(40rem,calc(100vw-2rem))]"
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
                          title="Jump the sim clock to a minute before AOS"
                          onClick={() => {
                            jumpTo(aos)
                            close()
                          }}
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
    </Dialog>
  )
}
