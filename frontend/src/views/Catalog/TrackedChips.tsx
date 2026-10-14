import { X } from 'lucide-react'
import { focusRing } from '../../components/ui/Button'
import { cn } from '../../components/ui/cn'
import { useMission } from '../../store/mission'

/** Tracked satellites as chips: click to select, × to untrack. */
export function TrackedChips() {
  const tracked = useMission((s) => s.tracked)
  const selectedId = useMission((s) => s.selectedId)
  const select = useMission((s) => s.select)
  const untrack = useMission((s) => s.untrack)
  if (tracked.length === 0) return null
  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5">
      <span className="text-xs text-gray-500 dark:text-gray-400">Tracking</span>
      {tracked.map((t) => {
        const isSelected = t.norad_id === selectedId
        return (
          <span
            key={t.norad_id}
            className="inline-flex items-center rounded-lg ring-1 ring-gray-900/10 dark:ring-white/10"
          >
            <button
              type="button"
              aria-pressed={isSelected}
              onClick={() => select(t.norad_id)}
              className={cn(
                'rounded-l-lg py-0.5 pr-1 pl-2 text-xs font-semibold transition-colors duration-150 hover:bg-gray-50 dark:hover:bg-white/10',
                isSelected && 'text-primary-700 dark:text-primary-400',
                focusRing,
              )}
            >
              {t.name}
            </button>
            <button
              type="button"
              aria-label={`Untrack ${t.name}`}
              onClick={() => untrack(t.norad_id)}
              className={cn(
                'rounded-r-lg py-0.5 pr-1.5 pl-0.5 text-gray-500 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100',
                focusRing,
              )}
            >
              <X size={14} strokeWidth={1.5} />
            </button>
          </span>
        )
      })}
    </div>
  )
}
