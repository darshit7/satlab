import { ChevronDown } from 'lucide-react'
import { useId, type ReactNode } from 'react'
import { focusRing } from './Button'
import { cn } from './cn'
import { GradientBorder } from './GradientBorder'
import { TiltedGridBackground } from './TiltedGridBackground'

interface Props {
  title?: ReactNode
  /** Right-aligned header content (counts, actions). Stays visible while collapsed. */
  aside?: ReactNode
  className?: string
  children: ReactNode
  as?: 'section' | 'div'
  /** Tighter padding and a smaller heading: inspector sections and panels. */
  dense?: boolean
  /** With `onToggle`, the title becomes a disclosure button and the body shows only while open. */
  open?: boolean
  onToggle?: () => void
}

/** Rounded-2xl card: hairline frame, faint tilted grid inside, accent gradient border. */
export function Card({ title, aside, className, children, as: Tag = 'section', dense = false, open = true, onToggle }: Props) {
  const bodyId = useId()
  const collapsible = onToggle !== undefined
  const shown = !collapsible || open
  return (
    <Tag
      className={cn(
        'relative isolate rounded-2xl border border-gray-200 bg-white dark:border-gray-800 dark:bg-white/5',
        dense ? 'px-4 py-3' : 'p-5',
        className,
      )}
    >
      <TiltedGridBackground className="rounded-2xl" />
      <GradientBorder />
      {(title || aside) && (
        <div className={cn('flex items-baseline justify-between gap-3', shown && (dense ? 'mb-2' : 'mb-3'))}>
          {title && (
            <h2 className={cn('font-semibold', dense ? 'text-lg' : 'text-xl')}>
              {collapsible ? (
                <button
                  type="button"
                  aria-expanded={open}
                  aria-controls={bodyId}
                  onClick={onToggle}
                  className={cn('-ml-1 flex items-center gap-1 rounded pr-1', focusRing)}
                >
                  <ChevronDown
                    size={18}
                    strokeWidth={1.5}
                    aria-hidden
                    className={cn('shrink-0 text-gray-500 transition-transform duration-150 dark:text-gray-400', !open && '-rotate-90')}
                  />
                  {title}
                </button>
              ) : (
                title
              )}
            </h2>
          )}
          {aside}
        </div>
      )}
      {/* collapsed content stays mounted (hidden), so a half-typed command survives a collapse */}
      {collapsible ? (
        <div id={bodyId} hidden={!open}>
          {children}
        </div>
      ) : (
        children
      )}
    </Tag>
  )
}
