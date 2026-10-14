import { Button } from '../../components/ui/Button'
import { Select } from '../../components/ui/Select'
import { MIN_ELEVATION_OPTIONS, RISING_WITHIN_OPTIONS, TYPE_LABELS } from '../../lib/filters'
import { useMission } from '../../store/mission'
import { SATELLITE_TYPES, type SatelliteType } from '../../types/ws'

const toggle = 'px-2 py-0.5 text-xs'

/** Filters for what the server streams: they apply to the map, both sky views and the lists. */
export function Filters() {
  const filters = useMission((s) => s.filters)
  const setFilters = useMission((s) => s.setFilters)
  const mask = useMission((s) => s.snapshot?.observer.horizon_mask_deg ?? null)

  const toggleType = (t: SatelliteType) =>
    setFilters({
      types: filters.types.includes(t)
        ? filters.types.filter((x) => x !== t)
        : SATELLITE_TYPES.filter((x) => x === t || filters.types.includes(x)),
    })

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
          onClick={() => setFilters({ sar_only: !filters.sar_only })}
        >
          SAR-capable
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
