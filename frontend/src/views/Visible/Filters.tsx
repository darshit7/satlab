import { Button } from '../../components/ui/Button'
import { inputClass } from '../../components/ui/Field'
import { MIN_ELEVATION_OPTIONS, RISING_WITHIN_OPTIONS, TYPE_LABELS } from '../../lib/filters'
import { useMission } from '../../store/mission'
import { SATELLITE_TYPES, type SatelliteType } from '../../types/ws'

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
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Satellite types">
        {SATELLITE_TYPES.map((t) => {
          const on = filters.types.includes(t)
          return (
            <Button key={t} active={on} aria-pressed={on} className="px-2.5 py-1" onClick={() => toggleType(t)}>
              {TYPE_LABELS[t]}
            </Button>
          )
        })}
        <Button
          active={filters.sar_only}
          aria-pressed={filters.sar_only}
          className="px-2.5 py-1"
          title="Only satellites carrying a synthetic-aperture radar"
          onClick={() => setFilters({ sar_only: !filters.sar_only })}
        >
          SAR-capable only
        </Button>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <label className="block">
          <span className="mb-1 block text-sm text-gray-600 dark:text-gray-400">Min elevation</span>
          <select
            className={inputClass}
            value={filters.min_elevation_deg}
            onChange={(e) => setFilters({ min_elevation_deg: Number(e.target.value) })}
          >
            {MIN_ELEVATION_OPTIONS.map((v) => (
              <option key={v} value={v}>
                {v === 0 ? `Mask${mask !== null ? ` (${mask}°)` : ''}` : `${v}°`}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="mb-1 block text-sm text-gray-600 dark:text-gray-400">Rising within</span>
          <select
            className={inputClass}
            value={filters.rising_within_min}
            onChange={(e) => setFilters({ rising_within_min: Number(e.target.value) })}
          >
            {RISING_WITHIN_OPTIONS.map((v) => (
              <option key={v} value={v}>
                {v === 0 ? 'Up now only' : `${v} min`}
              </option>
            ))}
          </select>
        </label>
      </div>
    </div>
  )
}
