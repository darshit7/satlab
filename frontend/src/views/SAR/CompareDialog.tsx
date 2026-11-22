import { ArrowDown, ArrowUp, MapPinned, TriangleAlert } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button } from '../../components/ui/Button'
import { cn } from '../../components/ui/cn'
import { Dialog } from '../../components/ui/Dialog'
import { daysApart, formatArea, formatDb, formatPercent, pairTitle, trackLabel } from '../../lib/sar'
import { useMission } from '../../store/mission'
import { useSar } from '../../store/sar'
import { useUi } from '../../store/ui'
import type { SarComparison, SarRegion } from '../../types/api'
import { loadImages, type ComparisonImages } from './images'
import { SwipeViewer, type ViewMode } from './SwipeViewer'

const muted = 'text-gray-500 dark:text-gray-400'
const OPACITY = { Low: 0.4, Mid: 0.7, High: 1 } as const
const SIGN_TEXT = {
  increase: 'text-fuchsia-700 dark:text-fuchsia-400',
  decrease: 'text-cyan-700 dark:text-cyan-400',
} as const

/** The compare dialog: the swipe viewer, the change heatmap and the flagged regions. */
export function CompareDialog() {
  const compareId = useUi((s) => s.compareId)
  const comparison = useSar((s) => s.comparisons.find((c) => c.id === compareId) ?? null)
  const close = () => useUi.getState().setCompareId(null)
  return (
    <Dialog
      open={compareId !== null && comparison !== null}
      onClose={close}
      title={comparison ? `Change · ${pairTitle(comparison.before.acquired_at, comparison.after.acquired_at)}` : 'Change'}
      className="w-[min(72rem,calc(100vw-2rem))]"
    >
      {comparison && <CompareBody comparison={comparison} />}
    </Dialog>
  )
}

function CompareBody({ comparison: c }: { comparison: SarComparison }) {
  const session = useMission((s) => s.session)
  const selected = useSar((s) => s.selectedRegion)
  const key = `${c.id}:${c.generation}`
  const [loaded, setLoaded] = useState<{ key: string; images?: ComparisonImages; error?: string } | null>(null)
  const images = loaded?.key === key ? (loaded.images ?? null) : null
  const error = loaded?.key === key ? (loaded.error ?? null) : null
  const [mode, setMode] = useState<ViewMode>('swipe')
  const [heatmap, setHeatmap] = useState(true)
  const [opacity, setOpacity] = useState<keyof typeof OPACITY>('Mid')
  const regions = c.regions ?? []

  useEffect(() => {
    if (!session) return
    const controller = new AbortController()
    const want = `${c.id}:${c.generation}`
    loadImages(session, c.id, c.generation, controller.signal)
      .then((imgs) => setLoaded({ key: want, images: imgs }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        setLoaded({ key: want, error: String(err).includes('404') ? 'The images have expired. Recompute it from the Imagery panel.' : 'The images could not be loaded.' })
      })
    return () => controller.abort()
  }, [session, c.id, c.generation])

  const label = `Radar images of ${c.aoi.size_km} km, ${pairTitle(c.before.acquired_at, c.after.acquired_at)}, ${regions.length} regions flagged`

  return (
    <div className="grid gap-5 md:grid-cols-[minmax(0,1fr)_18rem]">
      <div className="min-w-0">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <div role="group" aria-label="View" className="flex gap-1">
            {(['swipe', 'before', 'after'] as const).map((m) => (
              <Button key={m} className="px-2 py-0.5 text-xs capitalize" active={mode === m} aria-pressed={mode === m} onClick={() => setMode(m)}>
                {m}
              </Button>
            ))}
          </div>
          <Button className="px-2 py-0.5 text-xs" active={heatmap} aria-pressed={heatmap} onClick={() => setHeatmap(!heatmap)}>
            Change heatmap
          </Button>
          {heatmap && (
            <div role="group" aria-label="Heatmap opacity" className="flex gap-1">
              {(Object.keys(OPACITY) as (keyof typeof OPACITY)[]).map((o) => (
                <Button key={o} className="px-2 py-0.5 text-xs" active={opacity === o} aria-pressed={opacity === o} onClick={() => setOpacity(o)}>
                  {o}
                </Button>
              ))}
            </div>
          )}
        </div>
        {images ? (
          <SwipeViewer
            images={images}
            mode={mode}
            heatmap={heatmap}
            opacity={OPACITY[opacity]}
            regions={regions}
            selected={selected}
            onSelect={(id) => useSar.getState().selectRegion(id)}
            label={label}
          />
        ) : (
          <div className="flex h-[min(60vh,40rem)] min-h-64 items-center justify-center rounded-lg bg-gray-100 dark:bg-white/5">
            <p className={muted}>{error ?? 'Loading the images…'}</p>
          </div>
        )}
        <Legend c={c} />
      </div>
      <aside className="min-w-0 space-y-4 text-sm">
        <Summary c={c} />
        <RegionList c={c} regions={regions} selected={selected} />
        <p className={cn('text-xs', muted)}>
          Flags mark where the radar return changed, not what changed: soil moisture, crops, harvest or wind on water change it too. {c.attribution}; RTC
          processing by Catalyst on Microsoft Planetary Computer (CC BY 4.0).
        </p>
      </aside>
    </div>
  )
}

function Legend({ c }: { c: SarComparison }) {
  return (
    <div className={cn('mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs', muted)}>
      <span className={cn('flex items-center gap-1', SIGN_TEXT.increase)}>
        <ArrowUp size={14} strokeWidth={1.5} aria-hidden /> Brighter after (solid box)
      </span>
      <span className={cn('flex items-center gap-1', SIGN_TEXT.decrease)}>
        <ArrowDown size={14} strokeWidth={1.5} aria-hidden /> Darker after (dashed box)
      </span>
      <span>
        Lee-filtered γ⁰ {c.polarization},{' '}
        <span className="font-mono tabular-nums">
          {c.display.db_min} … {c.display.db_max} dB
        </span>
      </span>
    </div>
  )
}

function Summary({ c }: { c: SarComparison }) {
  const rows: [string, string][] = [
    ['Track', trackLabel(c.before)],
    ['Before', `${c.before.acquired_at.slice(0, 16).replace('T', ' ')} UTC · ${c.before.satellite_name}`],
    ['After', `${c.after.acquired_at.slice(0, 16).replace('T', ' ')} UTC · ${c.after.satellite_name}`],
    ['Apart', `${daysApart(c.before.acquired_at, c.after.acquired_at)} days`],
    ['Coverage', c.coverage != null ? formatPercent(c.coverage) : '—'],
    ['Threshold', c.threshold_db != null ? `${c.threshold_db.toFixed(1)} dB (Otsu ${c.otsu_db != null ? c.otsu_db.toFixed(1) : '—'}, floor ${c.floor_db.toFixed(1)})` : '—'],
    ['Brighter', c.increase_area_m2 != null ? formatArea(c.increase_area_m2) : '—'],
    ['Darker', c.decrease_area_m2 != null ? formatArea(c.decrease_area_m2) : '—'],
  ]
  return (
    <div>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className={muted}>{k}</dt>
            <dd className="font-mono text-xs tabular-nums">{v}</dd>
          </div>
        ))}
      </dl>
      {c.widespread && (
        <p className="mt-2 flex items-start gap-1 text-xs text-gray-700 dark:text-gray-300">
          <TriangleAlert size={14} strokeWidth={1.5} aria-hidden className="mt-0.5 shrink-0" />A large share of the area changed: seasons, wet ground or
          a different look can do this. Read the flags with caution.
        </p>
      )}
    </div>
  )
}

function RegionList({ c, regions, selected }: { c: SarComparison; regions: SarRegion[]; selected: number | null }) {
  const sar = useSar.getState()
  if (regions.length === 0) return <p className={muted}>No changes flagged above the threshold.</p>
  return (
    <div>
      <h3 className="mb-1 text-sm font-semibold">Flagged regions</h3>
      <ul className="max-h-72 space-y-0.5 overflow-y-auto pr-1">
        {regions.map((r) => (
          <li key={r.id}>
            <button
              type="button"
              aria-pressed={r.id === selected}
              onClick={() => sar.selectRegion(r.id === selected ? null : r.id)}
              className={cn(
                'flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-xs hover:bg-gray-50 dark:hover:bg-white/5',
                r.id === selected && 'bg-gray-100 ring-1 ring-gray-900/10 dark:bg-white/10 dark:ring-white/20',
              )}
            >
              <span className="w-5 font-mono tabular-nums">{r.id}</span>
              <span className={cn('flex items-center gap-0.5', SIGN_TEXT[r.sign])}>
                {r.sign === 'increase' ? <ArrowUp size={14} strokeWidth={1.5} aria-hidden /> : <ArrowDown size={14} strokeWidth={1.5} aria-hidden />}
                {r.sign === 'increase' ? 'Brighter' : 'Darker'}
              </span>
              <span className="ml-auto font-mono tabular-nums">{formatArea(r.area_m2)}</span>
              <span className="w-16 text-right font-mono tabular-nums">{formatDb(r.mean_db)}</span>
            </button>
          </li>
        ))}
      </ul>
      {c.regions_truncated && <p className={cn('mt-1 text-xs', muted)}>Smaller regions aren’t listed.</p>}
      {selected !== null && (
        <Button
          className="mt-2 px-2 py-0.5 text-xs"
          onClick={() => {
            const r = regions.find((x) => x.id === selected)
            if (!r) return
            sar.showOnMap(r.bbox)
            useUi.getState().setCompareId(null)
          }}
        >
          <MapPinned size={14} strokeWidth={1.5} aria-hidden />
          Show on map
        </Button>
      )}
    </div>
  )
}
