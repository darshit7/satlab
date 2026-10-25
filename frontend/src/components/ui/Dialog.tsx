import { X } from 'lucide-react'
import { useEffect, useId, useRef, type ReactNode } from 'react'
import { Button } from './Button'
import { cn } from './cn'

interface Props {
  open: boolean
  onClose: () => void
  title: ReactNode
  /** Header content between the title and the close button (a view switch). */
  aside?: ReactNode
  className?: string
  children: ReactNode
}

/**
 * Modal dialog on the native <dialog> (showModal): the browser keeps focus inside, puts it in the
 * top layer and closes it on Escape. A click on the backdrop closes it too. It fades in over
 * 100ms, like the menus; its content mounts only while open.
 *
 * Portalled lists (Select) are not in the top layer and would open under it: don't put one inside.
 */
export function Dialog({ open, onClose, title, aside, className, children }: Props) {
  const ref = useRef<HTMLDialogElement>(null)
  const titleId = useId()

  useEffect(() => {
    const dialog = ref.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    else if (!open && dialog.open) dialog.close()
  }, [open])

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose() // the backdrop
      }}
      className={cn(
        // preflight zeroes the margin the browser centres a modal with
        'm-auto max-h-[calc(100dvh-2rem)] max-w-[calc(100vw-2rem)] rounded-2xl bg-white p-0 text-gray-900 shadow-lg shadow-black/5 ring-1 ring-gray-900/5 dark:bg-gray-800 dark:text-gray-100 dark:ring-white/10',
        'backdrop:bg-gray-900/40 dark:backdrop:bg-black/60',
        'transition duration-100 ease-out starting:scale-95 starting:opacity-0',
        className,
      )}
    >
      {open && (
        <div className="flex max-h-[calc(100dvh-2rem)] flex-col">
          <div className="flex items-center gap-3 px-5 pt-4 pb-3">
            <h2 id={titleId} className="min-w-0 truncate text-xl font-semibold">
              {title}
            </h2>
            <div className="ml-auto flex shrink-0 items-center gap-2">
              {aside}
              <Button variant="icon" aria-label="Close" title="Close (Esc)" onClick={onClose}>
                <X size={20} strokeWidth={1.5} />
              </Button>
            </div>
          </div>
          <div className="relative min-h-0 overflow-y-auto px-5 pb-5">{children}</div>
        </div>
      )}
    </dialog>
  )
}
