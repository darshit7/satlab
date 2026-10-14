import type { ReactNode } from 'react'
import { cn } from './cn'
import { GradientBorder } from './GradientBorder'
import { TiltedGridBackground } from './TiltedGridBackground'

interface Props {
  title?: ReactNode
  /** Right-aligned header content (counts, actions). */
  aside?: ReactNode
  className?: string
  children: ReactNode
  as?: 'section' | 'div'
  /** Tighter padding and a smaller heading: sidebar panels and the readout strip. */
  dense?: boolean
}

/** Rounded-2xl card: hairline frame, faint tilted grid inside, accent gradient border. */
export function Card({ title, aside, className, children, as: Tag = 'section', dense = false }: Props) {
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
        <div className={cn('flex items-baseline justify-between gap-3', dense ? 'mb-2' : 'mb-3')}>
          {title && <h2 className={cn('font-semibold', dense ? 'text-lg' : 'text-xl')}>{title}</h2>}
          {aside}
        </div>
      )}
      {children}
    </Tag>
  )
}
