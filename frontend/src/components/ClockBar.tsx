import { Pause, Play } from 'lucide-react'
import { formatLocal, formatUtc } from '../lib/time'
import { useSimNow } from '../lib/useSimNow'
import { useMission } from '../store/mission'
import type { ConnectionStatus } from '../ws/sessionSocket'
import { AboutData } from './AboutData'
import { Button } from './ui/Button'
import { cn } from './ui/cn'
import { ThemeSwitcher } from './ui/ThemeSwitcher'

const RATES = [1, 10, 60, 600, 3600]

const CONNECTION_DOT: Record<ConnectionStatus, string> = {
  open: 'bg-primary-700 dark:bg-primary-400',
  connecting: 'bg-gray-400',
  reconnecting: 'bg-gray-400',
  idle: 'bg-gray-300 dark:bg-gray-600',
  closed: 'bg-red-700 dark:bg-red-400',
}

/** The one Playpen Sans element in the app. */
function Wordmark() {
  return (
    <span className="bg-linear-to-r from-amber-600 to-primary-600 bg-clip-text font-greeting text-2xl font-extrabold text-transparent dark:from-lime-500">
      SatLab
    </span>
  )
}

export function ClockBar() {
  const simMs = useSimNow()
  const clock = useMission((s) => s.snapshot?.clock)
  const connection = useMission((s) => s.connection)
  const setClock = useMission((s) => s.setClock)
  const live = connection === 'open' && clock !== undefined

  return (
    <header className="sticky top-2 z-20 flex flex-wrap items-center gap-x-6 gap-y-3 bg-white/75 px-4 py-3 shadow-xs backdrop-blur md:rounded-2xl lg:top-3 dark:bg-dark/75">
      <Wordmark />

      <div className="min-w-[13rem]">
        <div className="font-mono text-base tabular-nums">{simMs !== null ? formatUtc(simMs) : '—'}</div>
        <div className="text-sm text-gray-600 dark:text-gray-400">
          {simMs !== null ? formatLocal(simMs) : 'No session yet'}
        </div>
      </div>

      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Simulation clock">
        <Button
          disabled={!live}
          onClick={() => setClock({ paused: !clock?.paused })}
          aria-label={clock?.paused ? 'Resume' : 'Pause'}
          title={clock?.paused ? 'Resume' : 'Pause'}
        >
          {clock?.paused ? <Play size={16} strokeWidth={1.5} /> : <Pause size={16} strokeWidth={1.5} />}
        </Button>
        {RATES.map((rate) => (
          <Button
            key={rate}
            disabled={!live}
            active={clock?.time_scale === rate}
            aria-pressed={clock?.time_scale === rate}
            className="tabular-nums"
            onClick={() => setClock({ time_scale: rate })}
          >
            {rate}×
          </Button>
        ))}
        <Button
          disabled={!live}
          title="Jump to real time at 1×"
          onClick={() => setClock({ sim_time: new Date().toISOString(), time_scale: 1, paused: false })}
        >
          Now
        </Button>
      </div>

      <div className="ml-auto flex items-center gap-3">
        <span className="flex items-center gap-2 text-sm text-gray-600 capitalize dark:text-gray-400" title="Live connection to the simulator">
          <span className={cn('size-2 rounded-full', CONNECTION_DOT[connection])} aria-hidden />
          {connection}
        </span>
        {/* positioned: the About popover aligns with this group's (the header's) right edge */}
        <div className="relative flex items-center gap-1">
          <AboutData />
          <ThemeSwitcher />
        </div>
      </div>
    </header>
  )
}
