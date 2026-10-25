import { LocateFixed, MapPin, MousePointerClick } from 'lucide-react'
import { useEffect, useState } from 'react'
import { NumberField } from '../../components/NumberField'
import { Button } from '../../components/ui/Button'
import { Select } from '../../components/ui/Select'
import { useMission } from '../../store/mission'
import { useUi } from '../../store/ui'

const muted = 'text-xs text-gray-500 dark:text-gray-400'

/**
 * The station panel's body: where this session observes from, a preset station or a custom
 * location (geolocation, a map click or typed lat/lon). A custom location belongs to the session
 * only; it is never saved. Map clicks place the location only while this panel is open, and an
 * unapplied draft goes when it closes.
 */
export function LocationPanel() {
  const stations = useMission((s) => s.stations)
  const session = useMission((s) => s.session)
  const draft = useMission((s) => s.draft)
  const setDraft = useMission((s) => s.setDraft)
  const startSession = useMission((s) => s.startSession)
  const closePanel = useUi((s) => s.closePanel)
  const [locating, setLocating] = useState(false)

  useEffect(() => () => useMission.getState().setDraft(null), [])
  const [geoError, setGeoError] = useState<string | null>(null)

  // The fields show the draft, or where the session observes from until the user edits them.
  const shown = draft ?? (session ? { lat_deg: session.lat_deg, lon_deg: session.lon_deg, alt_m: session.alt_m } : null)

  const updateDraft = (field: 'lat_deg' | 'lon_deg' | 'alt_m', value: number) => {
    setDraft({ lat_deg: 0, lon_deg: 0, alt_m: 0, ...shown, [field]: value })
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
    void startSession({ location: { lat_deg: draft.lat_deg, lon_deg: draft.lon_deg, alt_m: draft.alt_m } })
    closePanel()
  }

  return (
    <>
      {session ? (
        <p className="mb-2 flex items-center gap-1.5 text-sm">
          <MapPin size={14} strokeWidth={1.5} className="shrink-0 text-gray-500 dark:text-gray-400" aria-hidden />
          <span className="truncate font-semibold">{session.location_name}</span>
          <span className="ml-auto shrink-0 font-mono text-xs text-gray-600 tabular-nums dark:text-gray-400">
            {session.lat_deg.toFixed(3)}°, {session.lon_deg.toFixed(3)}° · {Math.round(session.alt_m)} m
          </span>
        </p>
      ) : (
        <p className="mb-2 text-sm text-gray-600 dark:text-gray-400">
          Pick a preset or set a location to start a session.
        </p>
      )}

      <Select
        label="Preset station"
        value={session?.station_id ?? null}
        placeholder={session ? 'Custom location' : 'Select…'}
        options={stations.map((s) => ({ value: s.id, label: s.name }))}
        onChange={(id) => {
          void startSession({ station_id: id })
          closePanel()
        }}
      />

      <p className={`mt-3 flex items-center gap-1.5 ${muted}`}>
        <MousePointerClick size={14} strokeWidth={1.5} className="shrink-0" aria-hidden />
        Or a custom location: click the map to place it, or type it.
      </p>

      <div className="mt-2 grid grid-cols-[1fr_1fr_4.5rem] gap-2">
        <NumberField label="Lat °" value={shown?.lat_deg ?? null} digits={4} min={-90} max={90} step={0.0001} onCommit={(v) => updateDraft('lat_deg', v)} />
        <NumberField label="Lon °" value={shown?.lon_deg ?? null} digits={4} min={-180} max={180} step={0.0001} onCommit={(v) => updateDraft('lon_deg', v)} />
        <NumberField label="Alt m" value={shown ? Math.round(shown.alt_m) : null} min={-500} max={9000} onCommit={(v) => updateDraft('alt_m', v)} />
      </div>
      {geoError && <p className="mt-2 text-sm text-red-700 dark:text-red-400">{geoError}</p>}
      <div className="mt-2 grid grid-cols-2 gap-2">
        <Button onClick={useMyLocation} disabled={locating}>
          <LocateFixed size={16} strokeWidth={1.5} aria-hidden />
          {locating ? 'Locating…' : 'My location'}
        </Button>
        <Button
          variant="primary"
          className="px-3 py-1.5"
          disabled={!valid}
          title="Observe from here for this session only; the location is not saved as a station"
          onClick={apply}
        >
          Use this location
        </Button>
      </div>
    </>
  )
}
