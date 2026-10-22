import { Info } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { focusRing } from './ui/Button'
import { cn } from './ui/cn'

/**
 * What the numbers are, and aren't: real orbits and published link budgets, modelled subsystems,
 * a simulated uplink. One click away in the header rather than taking a footer's worth of screen.
 * Opens in 100ms, closes in 75ms, like the theme menu; Escape or a click outside closes it.
 *
 * The panel is positioned against the nearest positioned ancestor, which the header makes its
 * right-hand icon group: aligned with the header's edge, it stays on screen at 400px wide.
 */
export function AboutData() {
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
        aria-label="About the data"
        aria-expanded={open}
        aria-controls={panelId}
        title="About the data"
        onClick={() => setOpen((o) => !o)}
        className={cn(
          'rounded p-1.5 hover:text-gray-900 dark:hover:text-gray-100',
          open ? 'text-primary-700 dark:text-primary-400' : 'text-gray-600 dark:text-gray-400',
          focusRing,
        )}
      >
        <Info size={20} strokeWidth={1.5} />
      </button>
      <div
        id={panelId}
        role="dialog"
        aria-label="About the data"
        className={cn(
          'absolute right-0 z-30 mt-2 max-h-[70vh] w-[min(28rem,calc(100vw-4rem))] origin-top-right overflow-y-auto rounded-md bg-white p-4 text-sm shadow-lg shadow-black/5 ring-1 ring-gray-900/5 dark:bg-gray-800 dark:ring-white/10',
          'transition ease-out',
          open ? 'visible scale-100 opacity-100 duration-100' : 'invisible scale-95 opacity-0 duration-75',
        )}
      >
        <h2 className="mb-2 font-semibold">About the data</h2>
        <div className="space-y-2 text-gray-700 dark:text-gray-300">
          <p>
            <span className="font-semibold text-gray-900 dark:text-gray-100">Positions. </span>
            Positions and passes are computed server-side with SGP4 from real Celestrak TLEs, for your real location.
            “In view” means above the horizon mask, geometrically: most satellites shown are not visible to the naked
            eye. Accuracy degrades as a TLE ages.
          </p>
          <p>
            <span className="font-semibold text-gray-900 dark:text-gray-100">The globe. </span>
            Its imagery is Natural Earth II; its cone shows where a satellite is above the mask (and, for the
            selection, where its link margin is green), not what you could see.
          </p>
          <p>
            <span className="font-semibold text-gray-900 dark:text-gray-100">Links and systems. </span>
            Link margins use each satellite’s published RF budget and that source’s reference receiving station;
            satellites without one show “no RF data”. Power, thermal, storage and attitude are modelled from generic
            parameters, driven by real eclipse and pass geometry.
          </p>
          <p>
            <span className="font-semibold text-gray-900 dark:text-gray-100">Commands and telemetry. </span>
            Nothing here commands a real satellite: uplink is simulated and never transmitted. Downlink is either
            simulated from those models or real frames received by SatNOGS stations, replayed during your pass.
          </p>
        </div>
      </div>
    </div>
  )
}
