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
}

/** Rounded-2xl card: hairline frame, faint tilted grid inside, accent gradient border. */
export function Card({ title, aside, className, children, as: Tag = 'section' }: Props) {
  return (
    <Tag
      className={cn(
        'relative isolate rounded-2xl border border-gray-200 bg-white p-5 dark:border-gray-800 dark:bg-white/5',
        className,
      )}
    >
      <TiltedGridBackground className="rounded-2xl" />
      <GradientBorder />
      {(title || aside) && (
        <div className="mb-3 flex items-baseline justify-between gap-3">
          {title && <h2 className="text-xl font-semibold">{title}</h2>}
          {aside}
        </div>
      )}
      {children}
    </Tag>
  )
}
