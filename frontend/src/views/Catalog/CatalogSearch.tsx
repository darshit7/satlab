import { useEffect, useState } from 'react'
import { Search } from 'lucide-react'
import { api } from '../../api/client'
import { Button, focusRing } from '../../components/ui/Button'
import { Card } from '../../components/ui/Card'
import { inputClass } from '../../components/ui/Field'
import { Pill } from '../../components/ui/Pill'
import { formatAge } from '../../lib/time'
import { useMission } from '../../store/mission'
import type { CatalogStatus, SatelliteSummary } from '../../types/api'

const DEBOUNCE_MS = 250

export function CatalogSearch() {
  const tracked = useMission((s) => s.tracked)
  const track = useMission((s) => s.track)
  const untrack = useMission((s) => s.untrack)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SatelliteSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [remoteDone, setRemoteDone] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<CatalogStatus | null>(null)
  const [now, setNow] = useState(() => Date.now()) // reference time for "… ago" labels

  // Catalog status: poll until the first ingest finishes, then occasionally.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>
    let cancelled = false
    const poll = async () => {
      try {
        const s = await api.catalogStatus()
        if (cancelled) return
        setStatus(s)
        setNow(Date.now())
        timer = setTimeout(poll, s.satellite_count === 0 || s.refreshing ? 3000 : 60_000)
      } catch {
        if (!cancelled) timer = setTimeout(poll, 5000)
      }
    }
    void poll()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [])

  // Local search as you type (debounced). Remote search only on request.
  useEffect(() => {
    const q = query.trim()
    if (!q) return
    const controller = new AbortController()
    const timer = setTimeout(async () => {
      setLoading(true)
      try {
        const r = await api.searchSatellites(q, false, controller.signal)
        setResults(r.results)
        setNow(Date.now())
        setError(null)
      } catch (err) {
        if (!controller.signal.aborted) setError(String(err))
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }, DEBOUNCE_MS)
    return () => {
      controller.abort()
      clearTimeout(timer)
    }
  }, [query])

  const q = query.trim()
  const canSearchRemote = /^\d+$/.test(q) || q.length >= 3

  const searchRemote = async () => {
    setLoading(true)
    try {
      const r = await api.searchSatellites(q, true)
      setResults(r.results)
      setNow(Date.now())
      setRemoteDone(true)
      setError(null)
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }

  const trackedIds = new Set(tracked.map((t) => t.norad_id))

  const onQueryChange = (value: string) => {
    setQuery(value)
    setRemoteDone(false)
    if (!value.trim()) setResults([])
  }

  const statusText =
    status === null
      ? 'Connecting to catalog…'
      : status.satellite_count === 0
        ? status.last_error
          ? `Catalog unavailable: ${status.last_error}`
          : 'Loading TLEs from Celestrak…'
        : `Celestrak “${status.group}”${
            status.last_fetched_at ? `, fetched ${formatAge(now - Date.parse(status.last_fetched_at))} ago` : ''
          }`

  return (
    <Card
      title="Satellite catalog"
      aside={status && status.satellite_count > 0 && <Pill size="sm">{status.satellite_count.toLocaleString()}</Pill>}
    >
      <p className="mb-3 text-sm text-gray-600 dark:text-gray-400">{statusText}</p>
      <div className="relative">
        <Search size={16} strokeWidth={1.5} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-gray-500 dark:text-gray-400" aria-hidden />
        <input
          type="search"
          className={`${inputClass} pl-9`}
          placeholder="Name or NORAD ID, e.g. ISS, NOAA 19, 25544"
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          aria-label="Search satellites"
        />
      </div>
      {error && <p className="mt-2 text-sm text-red-700 dark:text-red-400">{error}</p>}
      {results.length > 0 && (
        <ul className="mt-2 divide-y divide-gray-200 dark:divide-gray-700">
          {results.map((sat) => {
            const isTracked = trackedIds.has(sat.norad_id)
            const age = sat.tle_epoch ? formatAge(now - Date.parse(sat.tle_epoch)) : null
            return (
              <li key={sat.norad_id} className="flex items-center justify-between gap-3 py-2.5">
                <div className="min-w-0">
                  <div className="truncate font-semibold">{sat.name}</div>
                  <div className="text-sm text-gray-600 tabular-nums dark:text-gray-400">
                    <span className="font-mono">#{sat.norad_id}</span>
                    {age && ` · TLE ${age} old`}
                  </div>
                </div>
                <Button
                  active={isTracked}
                  aria-pressed={isTracked}
                  onClick={() => (isTracked ? untrack(sat.norad_id) : track(sat))}
                >
                  {isTracked ? 'Tracking' : 'Track'}
                </Button>
              </li>
            )
          })}
        </ul>
      )}
      {q && !loading && (
        <p className="mt-3 text-sm text-gray-600 dark:text-gray-400">
          {results.length === 0 && (remoteDone ? 'No match in the Celestrak catalog. ' : 'No local match. ')}
          {!remoteDone && canSearchRemote && (
            <button
              type="button"
              className={`rounded text-left font-semibold text-primary-700 hover:text-primary-900 dark:text-primary-400 dark:hover:text-primary-300 ${focusRing}`}
              onClick={() => void searchRemote()}
            >
              <span className="growing-underline box-decoration-clone">
                Search all of Celestrak (debris, rocket bodies, inactive)
              </span>
            </button>
          )}
        </p>
      )}
      {loading && <p className="mt-3 text-sm text-gray-600 dark:text-gray-400">Searching…</p>}
    </Card>
  )
}
