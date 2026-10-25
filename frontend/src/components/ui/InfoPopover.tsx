import { Info } from 'lucide-react'
import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { focusRing } from './Button'
import { cn } from './cn'

interface Props {
  /** The button's and the dialog's accessible name (and the button's hover). */
  label: string
  iconSize?: number
  buttonClassName?: string
  /** Placement and size: the panel is positioned against the nearest positioned ancestor. */
  panelClassName: string
  /** A click on a button inside (picking something) closes the panel, focus back on the (i). */
  closeOnPick?: boolean
  children: ReactNode
}

/**
 * An (i) button with an explanatory panel, on the theme menu's surface and timing (opens in 100ms,
 * closes in 75ms). Escape or a click outside closes it; Escape hands focus back to the button. The
 * content stays mounted while closed (invisible), so it costs nothing to open.
 */
export function InfoPopover({ label, iconSize = 20, buttonClassName, panelClassName, closeOnPick = false, children }: Props) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const panelId = useId()

  useEffect(() => {
    if (!open) return
    const onPointer = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      setOpen(false)
      buttonRef.current?.focus()
    }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={rootRef}>
      <button
        ref={buttonRef}
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-controls={panelId}
        title={label}
        onClick={() => setOpen((o) => !o)}
        className={cn(
          'rounded hover:text-gray-900 dark:hover:text-gray-100',
          open ? 'text-primary-700 dark:text-primary-400' : 'text-gray-600 dark:text-gray-400',
          focusRing,
          buttonClassName,
        )}
      >
        <Info size={iconSize} strokeWidth={1.5} />
      </button>
      <div
        id={panelId}
        role="dialog"
        aria-label={label}
        onClick={(e) => {
          if (closeOnPick && e.target instanceof Element && e.target.closest('button')) {
            setOpen(false)
            buttonRef.current?.focus()
          }
        }}
        className={cn(
          'absolute z-30 overflow-y-auto rounded-md bg-white p-4 text-sm shadow-lg shadow-black/5 ring-1 ring-gray-900/5 dark:bg-gray-800 dark:ring-white/10',
          'transition ease-out',
          open ? 'visible scale-100 opacity-100 duration-100' : 'invisible scale-95 opacity-0 duration-75',
          panelClassName,
        )}
      >
        {children}
      </div>
    </div>
  )
}
