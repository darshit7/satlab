import { Button } from '../../components/ui/Button'
import { Select } from '../../components/ui/Select'
import { trackOnce } from '../../lib/analytics'
import { MIN_ELEVATION_OPTIONS, RISING_WITHIN_OPTIONS, TYPE_LABELS } from '../../lib/filters'
import { useMission } from '../../store/mission'
import { SATELLITE_TYPES, type SatelliteType } from '../../types/ws'

const toggle = 'px-2 py-0.5 text-xs'

/** Filters for what the server streams: they apply to the map, both sky views and the lists. */
export function Filters() {
  const filters = useMission((s) => s.filters)
  const setFilters = useMission((s) => s.setFilters)
  const mask = useMission((s) => s.snapshot?.observer.horizon_mask_deg ?? null)
  // Counts from the reference data, so the titles never go stale.
  const budgets = useMission((s) => s.linkProfiles?.profiles.length ?? null)
  const ownFormats = useMission((s) => s.packetTemplates?.satellites.length ?? null)
  const count = (n: number | null) => (n !== null ? ` (${n})` : '')

  const toggleType = (t: SatelliteType) => {
    if (!filters.types.includes(t)) trackOnce('filter-on', { filter: t })
    setFilters({
      types: filters.types.includes(t)
        ? filters.types.filter((x) => x !== t)
        : SATELLITE_TYPES.filter((x) => x === t || filters.types.includes(x)),
    })
  }
  const toggleOnly = (key: 'sar_only' | 'link_ok_only' | 'comms_only', filter: 'sar' | 'link-ok' | 'comms') => {
    if (!filters[key]) trackOnce('filter-on', { filter })
    setFilters({ [key]: !filters[key] })
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1" role="group" aria-label="Satellite types">
        {SATELLITE_TYPES.map((t) => {
          const on = filters.types.includes(t)
          return (
            <Button key={t} active={on} aria-pressed={on} className={toggle} onClick={() => toggleType(t)}>
              {TYPE_LABELS[t]}
            </Button>
          )
        })}
        <Button
          active={filters.sar_only}
          aria-pressed={filters.sar_only}
          className={toggle}
          title="Only satellites carrying a synthetic-aperture radar"
          onClick={() => toggleOnly('sar_only', 'sar')}
        >
          SAR-capable
        </Button>
        <Button
          active={filters.link_ok_only}
          aria-pressed={filters.link_ok_only}
          className={toggle}
          title={`Only satellites with a published link budget${count(budgets)}; of those in view, only with a usable margin (0 dB or better)`}
          onClick={() => toggleOnly('link_ok_only', 'link-ok')}
        >
          Link OK
        </Button>
        <Button
          active={filters.comms_only}
          aria-pressed={filters.comms_only}
          className={toggle}
          title={`Only satellites with packet formats of their own${count(ownFormats)}: the link-budget ones and those whose real frames are replayed from SatNOGS. Every other satellite but debris uses a generic, illustrative template`}
          onClick={() => toggleOnly('comms_only', 'comms')}
        >
          Own formats
        </Button>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Select
          label="Min elevation"
          value={filters.min_elevation_deg}
          options={MIN_ELEVATION_OPTIONS.map((v) => ({
            value: v,
            label: v === 0 ? `Mask${mask !== null ? ` (${mask}°)` : ''}` : `${v}°`,
          }))}
          onChange={(v) => setFilters({ min_elevation_deg: v })}
        />
        <Select
          label="Rising within"
          value={filters.rising_within_min}
          options={RISING_WITHIN_OPTIONS.map((v) => ({ value: v, label: v === 0 ? 'Up now only' : `${v} min` }))}
          onChange={(v) => setFilters({ rising_within_min: v })}
        />
      </div>
    </div>
  )
}
