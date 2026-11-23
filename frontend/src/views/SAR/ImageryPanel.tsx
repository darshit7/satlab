import { CircleCheck, CircleDashed, Clock, HardDriveDownload, LocateFixed, OctagonX, RotateCw, ScanSearch, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { DateField } from '../../components/DateField'
import { NumberField } from '../../components/NumberField'
import { Button, ButtonLink } from '../../components/ui/Button'
import { cn } from '../../components/ui/cn'
import { Select } from '../../components/ui/Select'
import { trackOnce } from '../../lib/analytics'
import { datesProblem, daysApart, isoDay, isRunning, pairTitle, passLabel, statusLabel, trackLabel } from '../../lib/sar'
import { useMission } from '../../store/mission'
import { useSar } from '../../store/sar'
import { useUi } from '../../store/ui'
import type { AoiSizeKm, SarComparison, SarPass } from '../../types/api'

const muted = 'text-gray-500 dark:text-gray-400'
const heading = 'mb-2 text-sm font-semibold'
const SIZES: AoiSizeKm[] = [5, 10, 20]

/** The Imagery panel: an area and two dates → passes by track → a comparison. */
export function ImageryPanel() {
  const session = useMission((s) => s.session)
  const source = useSar((s) => s.source)
  const aoi = useSar((s) => s.aoi)
  const before = useSar((s) => s.before)
  const after = useSar((s) => s.after)
  const polarization = useSar((s) => s.polarization)
  const platform = useSar((s) => s.platform)
  const search = useSar((s) => s.search)
  const comparisons = useSar((s) => s.comparisons)
  const sar = useSar.getState()
  const [today] = useState(() => isoDay(Date.now()))

  useEffect(() => {
    useSar.getState().ensureAoi()
    void useSar.getState().loadComparisons()
  }, [session?.id])

  if (!session) return <p className={muted}>Start a session at a ground station first.</p>
  if (source && !source.enabled) return <p className={muted}>Archive imagery is off on this server.</p>

  const archiveStart = source?.archive_start ?? '2014-10-10'
  const problem = datesProblem(before, after, archiveStart, today)
  const platformName = platform ? (source?.platforms.find((p) => p.platform === platform)?.satellite_name ?? platform.toUpperCase()) : null

  return (
    <div className="space-y-5 text-sm">
      <p className={muted}>
        Real Sentinel-1 radar scenes from the public archive: pick an area and two dates, then compare the passes on one orbit track.
        Nothing is captured on request.
      </p>

      <section>
        <h3 className={heading}>Area</h3>
        <div className="grid grid-cols-2 gap-2">
          <NumberField label="Latitude °" value={aoi?.lat_deg ?? null} digits={4} min={-80} max={84} step={0.01} onCommit={(v) => sar.setAoiCentre(v, aoi?.lon_deg ?? 0)} />
          <NumberField label="Longitude °" value={aoi?.lon_deg ?? null} digits={4} min={-180} max={180} step={0.01} onCommit={(v) => sar.setAoiCentre(aoi?.lat_deg ?? 0, v)} />
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <div role="group" aria-label="Area size" className="flex gap-1">
            {SIZES.map((size) => (
              <Button key={size} className="px-2 py-0.5 text-xs" active={aoi?.size_km === size} aria-pressed={aoi?.size_km === size} onClick={() => sar.setSize(size)}>
                {size} km
              </Button>
            ))}
          </div>
          <Button className="px-2 py-0.5 text-xs" onClick={() => sar.setAoiCentre(session.lat_deg, session.lon_deg)} title="Centre the area on the ground station">
            <LocateFixed size={14} strokeWidth={1.5} aria-hidden />
            Station
          </Button>
        </div>
        <p className={cn('mt-1 text-xs', muted)}>Click the map or the globe to move the area.</p>
      </section>

      <section>
        <h3 className={heading}>Dates (UTC)</h3>
        <div className="grid grid-cols-2 gap-2">
          <DateField label="Before" value={before} min={archiveStart} max={today} onCommit={(v) => sar.setDates(v, after)} />
          <DateField label="After" value={after} min={archiveStart} max={today} onCommit={(v) => sar.setDates(before, v)} />
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <div role="group" aria-label="Polarization" className="flex gap-1">
            {(['VV', 'VH'] as const).map((p) => (
              <Button key={p} className="px-2 py-0.5 text-xs" active={polarization === p} aria-pressed={polarization === p} onClick={() => sar.setPolarization(p)}>
                {p}
              </Button>
            ))}
          </div>
          {platformName && (
            <Button className="px-2 py-0.5 text-xs" active aria-label={`Only ${platformName}: show every satellite`} onClick={() => sar.setPlatform(null)}>
              Only {platformName}
              <X size={14} strokeWidth={1.5} aria-hidden />
            </Button>
          )}
        </div>
        {problem && <p className={cn('mt-1 text-xs', muted)}>{problem}</p>}
      </section>

      <Button variant="primary" className="w-full justify-center" disabled={!aoi || problem !== null || search.loading} onClick={() => void sar.searchScenes()}>
        <ScanSearch size={16} strokeWidth={1.5} aria-hidden />
        {search.loading ? 'Searching the archive…' : 'Find scenes'}
      </Button>

      {search.error && <p className="text-red-700 dark:text-red-400">{search.error}</p>}
      {search.data && <Results />}

      <Comparisons comparisons={comparisons} />

      <p className={cn('text-xs', muted)}>
        Comparisons last for this visit: reloading starts a new session. Change flags mark where the radar return changed, not what changed.{' '}
        {source && (
          <>
            {source.attribution.join(' · ')}.{' '}
            <ButtonLink external href={source.dataset_url} className="text-xs" variant="secondary">
              Dataset
            </ButtonLink>
          </>
        )}
      </p>
    </div>
  )
}

function Results() {
  const data = useSar((s) => s.search.data)
  const choice = useSar((s) => s.choice)
  const busy = useSar((s) => s.busy)
  const sar = useSar.getState()
  if (!data) return null
  if (data.tracks.length === 0) {
    return (
      <p className={muted}>
        No two passes on one track around these dates. Sentinel-1 repeats each track every 6 or 12 days: try dates further apart, or another area.
      </p>
    )
  }
  const track = data.tracks.find((t) => t.key === choice?.track) ?? data.tracks[0]!
  const pick = (side: 'before' | 'after', id: string) => {
    const next = { track: track.key, before: choice?.before ?? track.before[0]!.id, after: choice?.after ?? track.after[0]!.id, [side]: id }
    sar.choose(next)
  }
  const pb = track.before.find((p) => p.id === choice?.before) ?? null
  const pa = track.after.find((p) => p.id === choice?.after) ?? null
  const ordered = pb && pa && Date.parse(pb.acquired_at) < Date.parse(pa.acquired_at)

  return (
    <section className="space-y-2">
      <h3 className={heading}>Passes</h3>
      <Select
        label="Orbit track"
        value={track.key}
        options={data.tracks.map((t) => ({
          value: t.key,
          label: `${trackLabel(t)} · ${t.before.length} before, ${t.after.length} after${t.key === data.proposed_track ? ' (closest to your dates)' : ''}`,
        }))}
        onChange={(key) => {
          const t = data.tracks.find((x) => x.key === key)!
          sar.choose({ track: key, before: t.proposed_before ?? t.before[0]!.id, after: t.proposed_after ?? t.after[0]!.id })
        }}
      />
      <Select label="Before" value={pb?.id ?? null} options={track.before.map((p) => ({ value: p.id, label: passLabel(p) }))} onChange={(id) => pick('before', id)} />
      <Select label="After" value={pa?.id ?? null} options={track.after.map((p) => ({ value: p.id, label: passLabel(p) }))} onChange={(id) => pick('after', id)} />
      {pb && pa && (
        <p className={cn('text-xs', muted)}>
          {ordered ? `${daysApart(pb.acquired_at, pa.acquired_at)} days apart` : 'The before pass must be earlier.'} · <SatelliteLink pass={pb} />
          {pa.satellite_name !== pb.satellite_name && (
            <>
              {' '}
              and <SatelliteLink pass={pa} />
            </>
          )}
          {data.truncated && ' · more passes than listed'}
        </p>
      )}
      <Button variant="primary" className="w-full justify-center" disabled={!ordered || busy} onClick={() => void sar.compare()}>
        Compare
      </Button>
    </section>
  )
}

/** The satellite that took a pass, selecting it when it is in the catalog. */
function SatelliteLink({ pass }: { pass: SarPass }) {
  const select = useMission((s) => s.select)
  if (pass.norad_id === null) return <span>{pass.satellite_name}</span>
  return (
    <button type="button" className="underline decoration-gray-400 underline-offset-2 hover:text-primary-700 dark:hover:text-primary-400" onClick={() => select(pass.norad_id)}>
      {pass.satellite_name}
    </button>
  )
}

function StatusIcon({ c }: { c: SarComparison }) {
  const props = { size: 14, strokeWidth: 1.5, 'aria-hidden': true } as const
  if (c.status === 'queued') return <Clock {...props} />
  if (c.status === 'reading') return <HardDriveDownload {...props} />
  if (c.status === 'detecting') return <ScanSearch {...props} />
  if (c.status === 'ready') return <CircleCheck {...props} />
  if (c.status === 'failed') return <OctagonX {...props} className="text-red-700 dark:text-red-400" />
  return <CircleDashed {...props} />
}

function Comparisons({ comparisons }: { comparisons: SarComparison[] }) {
  const sar = useSar.getState()
  if (comparisons.length === 0) return null
  return (
    <section>
      <h3 className={heading}>This visit’s comparisons</h3>
      <ul className="space-y-2">
        {comparisons.map((c) => (
          <li key={c.id} className="rounded-lg px-2 py-1.5 ring-1 ring-gray-900/5 dark:ring-white/10">
            <div className="flex items-center gap-2">
              <span className={cn('flex items-center gap-1 text-xs', c.status === 'failed' ? 'text-red-700 dark:text-red-400' : muted)}>
                <StatusIcon c={c} />
                {statusLabel(c)}
              </span>
              <span className="ml-auto flex gap-1">
                {c.status === 'ready' && (
                  <Button className="px-2 py-0.5 text-xs" onClick={() => sar.open(c.id)}>
                    Open
                  </Button>
                )}
                {(c.status === 'failed' || c.status === 'expired') && (
                  <Button className="px-2 py-0.5 text-xs" onClick={() => void sar.recompute(c.id)}>
                    <RotateCw size={14} strokeWidth={1.5} aria-hidden />
                    Recompute
                  </Button>
                )}
                <Button variant="icon" aria-label="Remove this comparison" title="Remove" onClick={() => void sar.remove(c.id)}>
                  <X size={16} strokeWidth={1.5} />
                </Button>
              </span>
            </div>
            <p className="mt-0.5">
              {pairTitle(c.before.acquired_at, c.after.acquired_at)}{' '}
              <span className={cn('font-mono text-xs tabular-nums', muted)}>
                {c.aoi.size_km} km · {c.polarization}
              </span>
            </p>
            {c.status === 'ready' && (
              <p className={cn('text-xs', muted)}>
                {(c.regions?.length ?? 0) === 0 ? 'No changes flagged' : `${c.regions!.length}${c.regions_truncated ? '+' : ''} regions flagged`}
              </p>
            )}
            {c.error && <p className="text-xs text-red-700 dark:text-red-400">{c.error}</p>}
            {isRunning(c) && <p className={cn('text-xs', muted)}>Reading a real archive takes a few seconds to a minute.</p>}
          </li>
        ))}
      </ul>
    </section>
  )
}

/** The inspector's link from a Sentinel-1 satellite to its archive passes. */
export function ArchiveImageryButton({ noradId }: { noradId: number }) {
  const platform = useSar((s) => s.source?.platforms.find((p) => p.norad_id === noradId) ?? null)
  if (!platform) return null
  return (
    <Button
      className="px-2 py-0.5 text-xs"
      title="Compare this satellite’s archived radar images of an area"
      onClick={() => {
        useSar.getState().setPlatform(platform.platform)
        useUi.getState().openPanel('imagery')
        trackOnce('sar-from-satellite', { platform: platform.platform })
      }}
    >
      Archive imagery
    </Button>
  )
}
