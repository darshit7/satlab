import { LocateFixed, MapPin } from 'lucide-react'
import { useState } from 'react'
import { NumberField } from '../../components/NumberField'
import { Button } from '../../components/ui/Button'
import { Card } from '../../components/ui/Card'
import { inputClass } from '../../components/ui/Field'
import { useMission } from '../../store/mission'

const fmt = (v: number, digits = 4) => v.toFixed(digits)

export function LocationPanel() {
  const stations = useMission((s) => s.stations)
  const session = useMission((s) => s.session)
  const draft = useMission((s) => s.draft)
  const setDraft = useMission((s) => s.setDraft)
  const startSession = useMission((s) => s.startSession)
  const createStationAndStart = useMission((s) => s.createStationAndStart)
  const [name, setName] = useState('')
  const [locating, setLocating] = useState(false)
  const [geoError, setGeoError] = useState<string | null>(null)

  const active = session?.station

  const updateDraft = (field: 'lat_deg' | 'lon_deg' | 'alt_m', value: number) => {
    setDraft({ lat_deg: 0, lon_deg: 0, alt_m: 0, ...draft, [field]: value })
  }

  const useMyLocation = () => {
    if (!('geolocation' in navigator)) {
      setGeoError('Geolocation is not available in this browser.')
      return
    }
    setLocating(true)
    setGeoError(null)
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocating(false)
        setDraft({
          lat_deg: pos.coords.latitude,
          lon_deg: pos.coords.longitude,
          alt_m: Math.round(pos.coords.altitude ?? 0),
        })
      },
      (err) => {
        setLocating(false)
        setGeoError(err.message || 'Location permission denied.')
      },
      { enableHighAccuracy: false, timeout: 15_000 },
    )
  }

  const valid = draft !== null && Math.abs(draft.lat_deg) <= 90 && Math.abs(draft.lon_deg) <= 180

  const apply = () => {
    if (!draft || !valid) return
    void createStationAndStart({
      name: name.trim() || `Custom ${fmt(draft.lat_deg, 3)}, ${fmt(draft.lon_deg, 3)}`,
      lat_deg: draft.lat_deg,
      lon_deg: draft.lon_deg,
      alt_m: draft.alt_m,
    })
    setName('')
  }

  return (
    <Card title="Ground station">
      {active ? (
        <div className="mb-4 flex gap-2">
          <MapPin size={20} strokeWidth={1.5} className="mt-0.5 shrink-0 text-gray-500 dark:text-gray-400" aria-hidden />
          <div>
            <div className="font-semibold">{active.name}</div>
            <div className="font-mono text-sm text-gray-600 tabular-nums dark:text-gray-400">
              {fmt(active.lat_deg)}°, {fmt(active.lon_deg)}° · {Math.round(active.alt_m)} m
            </div>
          </div>
        </div>
      ) : (
        <p className="mb-4 text-gray-600 dark:text-gray-400">
          Pick a station or set your own location to start a session.
        </p>
      )}

      <label className="block">
        <span className="mb-1 block text-sm text-gray-600 dark:text-gray-400">Saved stations</span>
        <select
          className={inputClass}
          value={active?.id ?? ''}
          onChange={(e) => e.target.value && void startSession(Number(e.target.value))}
        >
          <option value="" disabled>
            Select…
          </option>
          {stations.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </label>

      <div className="mt-5 border-t border-gray-200 pt-4 dark:border-gray-700">
        <div className="mb-3 flex items-center justify-between gap-2">
          <span className="text-sm font-semibold">Custom location</span>
          <span className="text-sm text-gray-500 dark:text-gray-400">or click the map</span>
        </div>
        <Button className="mb-3 w-full" onClick={useMyLocation} disabled={locating}>
          <LocateFixed size={16} strokeWidth={1.5} aria-hidden />
          {locating ? 'Locating…' : 'Use my location'}
        </Button>
        {geoError && <p className="mb-3 text-sm text-red-700 dark:text-red-400">{geoError}</p>}
        <div className="grid grid-cols-[1fr_1fr_0.8fr] gap-2">
          <NumberField label="Lat °" value={draft?.lat_deg ?? null} digits={4} min={-90} max={90} step={0.0001} onCommit={(v) => updateDraft('lat_deg', v)} />
          <NumberField label="Lon °" value={draft?.lon_deg ?? null} digits={4} min={-180} max={180} step={0.0001} onCommit={(v) => updateDraft('lon_deg', v)} />
          <NumberField label="Alt m" value={draft ? draft.alt_m : null} min={-500} max={9000} onCommit={(v) => updateDraft('alt_m', v)} />
        </div>
        <label className="mt-3 block">
          <span className="mb-1 block text-sm text-gray-600 dark:text-gray-400">Name (optional)</span>
          <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Home" />
        </label>
        <Button variant="primary" className="mt-4 w-full" disabled={!valid} onClick={apply}>
          Set as ground station
        </Button>
      </div>
    </Card>
  )
}
