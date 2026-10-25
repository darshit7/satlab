import { Radio } from 'lucide-react'
import { useMemo } from 'react'
import { focusRing } from '../../components/ui/Button'
import { cn } from '../../components/ui/cn'
import { COMMS_CAPABILITY_DETAIL, commsSatellites } from '../../lib/comms'
import { useMission } from '../../store/mission'

const muted = 'text-gray-500 dark:text-gray-400'

/**
 * Every satellite comms works with (packet templates), what each supports and whether it has a
 * published link budget, from the reference data. Picking one selects it. Shown from the
 * Communications section's (i) and, inline, when the selected satellite has no comms.
 */
export function CommsSatellites() {
  const templates = useMission((s) => s.packetTemplates)
  const linkProfiles = useMission((s) => s.linkProfiles)
  const selectedId = useMission((s) => s.selectedId)
  const select = useMission((s) => s.select)
  const sats = useMemo(() => commsSatellites(templates, linkProfiles), [templates, linkProfiles])

  if (!templates) return <p className={cn('text-sm', muted)}>The list isn’t available right now.</p>
  const withBudget = sats.filter((s) => s.linkBudget).length

  return (
    <div>
      <p className="text-sm text-gray-700 dark:text-gray-300">
        You can communicate with the {sats.length} satellites that have packet templates.
        {linkProfiles &&
          ` Link OK (in Overhead) covers the ${withBudget} with a published link budget; the others work without a modelled channel.`}
      </p>
      <ul className="-mx-2 mt-2" aria-label="Satellites you can communicate with">
        {sats.map((sat) => {
          const selected = sat.noradId === selectedId
          return (
            <li key={sat.noradId}>
              <button
                type="button"
                aria-current={selected ? 'true' : undefined}
                onClick={() => select(sat.noradId)}
                className={cn(
                  'flex w-full items-start gap-2 rounded-lg px-2 py-1 text-left transition-colors duration-150 hover:bg-gray-50 dark:hover:bg-white/5',
                  selected && 'bg-gray-50 dark:bg-white/5',
                  focusRing,
                )}
              >
                <Radio size={14} strokeWidth={1.5} aria-hidden className={cn('mt-1 shrink-0', muted)} />
                <span className="min-w-0 flex-1">
                  <span className={cn('block truncate text-sm font-semibold', selected && 'text-primary-700 dark:text-primary-400')}>
                    {sat.name}
                  </span>
                  <span className={cn('block text-xs', muted)}>
                    {COMMS_CAPABILITY_DETAIL[sat.capability]}
                    {sat.linkBudget !== null && (sat.linkBudget ? ' · link budget' : ' · no RF data')}
                  </span>
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
