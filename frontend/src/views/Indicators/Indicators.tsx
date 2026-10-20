import { Card } from '../../components/ui/Card'
import { cn } from '../../components/ui/cn'
import { RagIcon } from '../../components/ui/Rag'
import { RAG_BG, RAG_TEXT, ragKey } from '../../components/ui/ragClasses'
import { formatDb, formatFrequency, formatMarginDb, formatRate, ragWord, safeHttpsUrl } from '../../lib/rag'
import { useMission } from '../../store/mission'
import type { HealthState, Rag, SatelliteState } from '../../types/ws'

interface Segment {
  label: string
  rag: Rag | null
  value: string
  detail: string
}

const ACTIVITY: Record<NonNullable<HealthState['storage']['activity']>, string> = {
  filling: 'filling',
  downlinking: 'downlinking',
  full: 'full',
}

function segments(h: HealthState, sat: SatelliteState | undefined): Segment[] {
  const link = sat?.link
  return [
    {
      label: 'Power',
      rag: h.power.rag,
      value: `${h.power.soc_pct.toFixed(0)}%`,
      detail: h.power.illumination === 'penumbra' ? `penumbra ${(h.power.sunlit_fraction * 100).toFixed(0)}%` : h.power.illumination,
    },
    {
      label: 'Thermal',
      rag: h.thermal.rag,
      value: `${h.thermal.temperature_c.toFixed(1)} °C`,
      detail: `band ${h.thermal.band_min_c}…${h.thermal.band_max_c} °C`,
    },
    {
      label: 'Storage',
      rag: h.storage.rag,
      value: h.storage.fill_pct !== null ? `${h.storage.fill_pct.toFixed(0)}%` : '—',
      detail: h.storage.activity ? ACTIVITY[h.storage.activity] : 'no downlink',
    },
    {
      label: 'Attitude',
      rag: h.attitude.rag,
      value: h.attitude.off_nadir_deg !== null ? `${h.attitude.off_nadir_deg.toFixed(1)}°` : '—',
      detail: h.attitude.off_nadir_deg !== null ? `off nadir · beam ±${h.attitude.half_beamwidth_deg}°` : 'station not in view',
    },
    {
      label: 'RF',
      rag: h.rf.rag,
      value: h.rf.margin_db !== null ? `${formatMarginDb(h.rf.margin_db)} dB` : '—',
      detail: link
        ? link.ebn0_db !== null
          ? `Eb/N0 ${formatDb(link.ebn0_db)} dB`
          : 'below the horizon'
        : 'no RF data',
    },
  ]
}

/** Where the RF numbers come from: the published budget, linked (https only). */
function RfSource({ noradId }: { noradId: number }) {
  const profile = useMission((s) => s.linkProfiles?.profiles.find((p) => p.norad_id === noradId))
  if (!profile) return null
  return (
    <span>
      RF: {profile.label} ({formatFrequency(profile.frequency_hz)}, {formatRate(profile.data_rate_bps)}), from{' '}
      {profile.sources.map((src, i) => {
        const href = safeHttpsUrl(src.url)
        return (
          <span key={src.url}>
            {i > 0 && ', '}
            {href ? (
              <a href={href} target="_blank" rel="noopener noreferrer" className="growing-underline text-primary-700 dark:text-primary-400">
                {src.title}
              </a>
            ) : (
              src.title
            )}
          </span>
        )
      })}
      .
    </span>
  )
}

/**
 * The selected satellite's systems: a worst-of rollup over a segmented bar whose segments are the
 * subsystem indicators. Every state comes from the server; power, thermal, storage and attitude
 * are modelled, RF is the published link budget.
 */
export function Indicators() {
  const selectedId = useMission((s) => s.selectedId)
  const health = useMission((s) => s.snapshot?.health ?? null)
  const sat = useMission((s) => s.snapshot?.satellites.find((x) => x.norad_id === s.selectedId))
  if (selectedId === null || !health || health.norad_id !== selectedId) return null

  const rollup = health.rollup
  return (
    <Card
      as="div"
      dense
      title="Systems"
      aside={
        <span className={cn('flex items-center gap-1.5 text-sm font-semibold', RAG_TEXT[ragKey(rollup)])} title="Worst of the subsystems">
          <RagIcon rag={rollup} size={16} />
          {ragWord(rollup)}
        </span>
      }
    >
      <div className="relative -mx-4 overflow-x-auto px-4">
        <ol className="grid min-w-[30rem] grid-cols-5 gap-2" aria-label="Subsystems">
          {segments(health, sat).map((seg) => (
            <li key={seg.label} className="min-w-0">
              <div className={cn('h-1.5 rounded-full', RAG_BG[ragKey(seg.rag)])} aria-hidden />
              <div className="mt-1.5 flex items-center gap-1 text-xs text-gray-600 dark:text-gray-400">
                <RagIcon rag={seg.rag} size={12} />
                {seg.label}
                <span className="sr-only">: {ragWord(seg.rag)}</span>
              </div>
              <div className={cn('font-mono text-sm tabular-nums', seg.rag === null && 'text-gray-500 dark:text-gray-400')}>
                {seg.value}
              </div>
              <div className="truncate text-xs text-gray-500 dark:text-gray-400" title={seg.detail}>
                {seg.detail}
              </div>
            </li>
          ))}
        </ol>
      </div>
      <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
        Power, thermal, storage and attitude are modelled (v{health.model_version}).{' '}
        {sat?.link ? <RfSource noradId={selectedId} /> : 'No published RF parameters for this satellite.'}
      </p>
    </Card>
  )
}
