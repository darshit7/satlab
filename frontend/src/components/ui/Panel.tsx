import { X } from 'lucide-react'
import { useEffect, useRef, type ReactNode } from 'react'
import { dialogOpen } from '../../store/ui'
import { Button } from './Button'

interface Props {
  id: string
  title: ReactNode
  /** Right-aligned header content (counts), before the close button. */
  aside?: ReactNode
  onClose: () => void
  children: ReactNode
}

/**
 * A floating, non-modal panel over the map, opened from the mission bar. It doesn't close on an
 * outside click: the map stays in use while it's open (placing the station, picking a dot), so
 * only Escape, × or its bar button close it. It fades in like the theme menu (100ms) and closes at
 * once: its content unmounts, so a hidden list doesn't keep re-rendering.
 *
 * Positioned against the map's frame (the nearest positioned ancestor), under its top-left corner.
 */
export function Panel({ id, title, aside, onClose, children }: Props) {
  const ref = useRef<HTMLDivElement>(null)
  const titleId = `${id}-title`

  // Focus moves in, unless a field inside already took it (autoFocus).
  useEffect(() => {
    const el = ref.current
    if (el && !el.contains(document.activeElement)) el.focus()
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // a Select inside closes its list first; a modal dialog handles its own Escape
      if (e.key === 'Escape' && !e.defaultPrevented && !dialogOpen()) onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      ref={ref}
      id={id}
      role="dialog"
      aria-modal="false"
      aria-labelledby={titleId}
      tabIndex={-1}
      className="absolute top-3 left-3 z-10 flex max-h-[calc(100%-1.5rem)] w-[min(24rem,calc(100%-1.5rem))] origin-top-left flex-col rounded-2xl bg-white shadow-lg shadow-black/5 ring-1 ring-gray-900/5 outline-hidden transition duration-100 ease-out starting:scale-95 starting:opacity-0 dark:bg-gray-800 dark:ring-white/10"
    >
      <div className="flex items-center gap-3 px-4 pt-3 pb-2">
        <h2 id={titleId} className="text-lg font-semibold">
          {title}
        </h2>
        <div className="ml-auto flex items-center gap-2">
          {aside}
          <Button variant="icon" aria-label="Close" title="Close (Esc)" onClick={onClose}>
            <X size={18} strokeWidth={1.5} />
          </Button>
        </div>
      </div>
      {/* positioned: sr-only helpers inside stay inside this scroll container */}
      <div className="relative min-h-0 overflow-y-auto px-4 pb-4">{children}</div>
    </div>
  )
}
