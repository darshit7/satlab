import { Crosshair } from 'lucide-react'
import type { ReactNode } from 'react'
import { Card } from '../../components/ui/Card'
import { cn } from '../../components/ui/cn'
import { Emoji } from '../../components/ui/Emoji'
import { Pill } from '../../components/ui/Pill'
import { RagDot } from '../../components/ui/Rag'
import { focusRing } from '../../components/ui/Button'
import { TYPE_LABELS } from '../../lib/filters'
import { formatMarginDb, ragWord } from '../../lib/rag'
import { formatCountdown } from '../../lib/time'
import { useSimNow } from '../../lib/useSimNow'
import { useMission } from '../../store/mission'
import type { LinkState, SatelliteState, SatelliteType, UpcomingPass } from '../../types/ws'
import { Filters } from './Filters'

const MAX_ROWS = 100
const muted = 'text-sm text-gray-600 dark:text-gray-400'
const heading = 'mt-3 mb-0.5 flex items-baseline justify-between gap-2 text-sm font-semibold'

function Row({
  noradId,
  name,
  type,
  detail,
  pinned,
  link,
}: {
  noradId: number
  name: string
  type: SatelliteType
  detail: ReactNode
  pinned: boolean
  /** The link state, for satellites with a published RF profile. */
  link?: LinkState | null
}) {
  const selected = useMission((s) => s.selectedId === noradId)
  const select = useMission((s) => s.select)
  return (
    <li>
      <button
        type="button"
        aria-pressed={selected}
        onClick={() => select(noradId)}
        className={cn(
          'flex w-full items-center gap-3 rounded-lg px-2 py-1 text-left transition-colors duration-150 hover:bg-gray-50 dark:hover:bg-white/5',
          selected && 'bg-gray-50 dark:bg-white/5',
          focusRing,
        )}
      >
        <span className="min-w-0 flex-1">
          <span className={cn('flex items-center gap-1.5 truncate text-sm font-semibold', selected && 'text-primary-700 dark:text-primary-400')}>
            {pinned && <Crosshair size={14} strokeWidth={1.5} className="shrink-0" aria-label="tracked" />}
            {link && <RagDot rag={link.rag} label={`Link ${ragWord(link.rag)}, ${formatMarginDb(link.margin_db)} dB`} />}
            <span className="truncate">{name}</span>
          </span>
          <span className="text-xs text-gray-500 dark:text-gray-400">
            {TYPE_LABELS[type]} · <span className="font-mono">#{noradId}</span>
          </span>
        </span>
        <span className="shrink-0 text-right font-mono text-sm tabular-nums">{detail}</span>
      </button>
    </li>
  )
}

function UpNow({ sats, simMs }: { sats: SatelliteState[]; simMs: number }) {
  const arcs = useMission((s) => s.arcs)
  const tracked = useMission((s) => s.tracked)
  const trackedIds = new Set(tracked.map((t) => t.norad_id))
  return (
    <ul className="-mx-2 max-h-60 overflow-y-auto">
      {sats.slice(0, MAX_ROWS).map((sat) => {
        const arc = arcs[sat.norad_id]
        const los = arc?.los_time
        return (
          <Row
            key={sat.norad_id}
            noradId={sat.norad_id}
            name={sat.name}
            type={sat.type}
            pinned={trackedIds.has(sat.norad_id)}
            link={sat.link}
            detail={
              <>
                <span>{sat.elevation_deg.toFixed(0)}°</span>
                <span className="text-gray-500 dark:text-gray-400"> az {sat.azimuth_deg.toFixed(0)}°</span>
                <span className="block text-xs text-gray-500 dark:text-gray-400">
                  {los
                    ? `LOS ${formatCountdown(Date.parse(los) - simMs)}`
                    : arc // the pass outlasts the arc's window (MEO, GEO)
                      ? `LOS > ${formatCountdown(Date.parse(arc.end_time) - simMs)}`
                      : '…'}
                </span>
              </>
            }
          />
        )
      })}
    </ul>
  )
}

function RisingSoon({ passes, simMs }: { passes: UpcomingPass[]; simMs: number }) {
  return (
    <ul className="-mx-2 max-h-48 overflow-y-auto">
      {passes.map((p) => (
        <Row
          key={p.norad_id}
          noradId={p.norad_id}
          name={p.name}
          type={p.type}
          pinned={false}
          detail={
            <>
              <span>AOS {formatCountdown(Date.parse(p.aos_time) - simMs)}</span>
              <span className="block text-xs text-gray-500 dark:text-gray-400">
                ≈{p.max_elev_deg.toFixed(0)}° max · az {p.aos_az_deg.toFixed(0)}°
              </span>
            </>
          }
        />
      ))}
    </ul>
  )
}

/** "What's up now, and what's coming": every satellite above the mask, and the next to rise. */
export function VisibleList() {
  const snapshot = useMission((s) => s.snapshot)
  const upcoming = useMission((s) => s.upcoming)
  const risingWithin = useMission((s) => s.filters.rising_within_min)
  const simMs = useSimNow(1000)

  const up = (snapshot?.satellites ?? [])
    .filter((s) => s.above_mask)
    .sort((a, b) => b.elevation_deg - a.elevation_deg)
  const rising = simMs === null ? [] : (upcoming?.passes ?? []).filter((p) => Date.parse(p.aos_time) > simMs)

  return (
    <Card dense title="Overhead" aside={snapshot && <Pill size="sm">{snapshot.visible_count.toLocaleString()} in view</Pill>}>
      <Filters />
      {!snapshot || simMs === null ? (
        <p className={cn(muted, 'mt-3')}>Set a ground station to see what's above it.</p>
      ) : (
        <>
          <h3 className={heading}>
            Up now
            <span className={muted}>
              {up.length > MAX_ROWS ? `${MAX_ROWS} of ${up.length.toLocaleString()} shown` : `${up.length} shown`}
            </span>
          </h3>
          {up.length === 0 ? (
            <p className={muted}>
              Nothing matching is above the {snapshot.observer.horizon_mask_deg}° mask right now{' '}
              <Emoji name="satellite" label="satellite" />
            </p>
          ) : (
            <UpNow sats={up} simMs={simMs} />
          )}
          {up.length > MAX_ROWS && <p className={cn(muted, 'mt-1')}>Narrow the filters to see the rest.</p>}

          {risingWithin > 0 && (
            <>
              <h3 className={heading}>
                Rising within {risingWithin} min
                {upcoming?.status === 'ready' && (
                  <span className={muted}>
                    {upcoming.total > upcoming.passes.length
                      ? `soonest ${upcoming.passes.length} of ${upcoming.total.toLocaleString()}`
                      : `${upcoming.total}`}
                  </span>
                )}
              </h3>
              {upcoming?.status === 'paused' ? (
                <p className={muted}>
                  Paused while the clock runs faster than 60×: the catalog sweep can't keep up. Slow down to see what's
                  rising.
                </p>
              ) : upcoming?.status !== 'ready' ? (
                <p className={muted}>Working out rise times across the whole catalog…</p>
              ) : rising.length === 0 ? (
                <p className={muted}>Nothing matching rises in that window.</p>
              ) : (
                <RisingSoon passes={rising} simMs={simMs} />
              )}
            </>
          )}
        </>
      )}
    </Card>
  )
}
