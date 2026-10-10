import type { ReactNode } from 'react'
import { cn } from './cn'

/** The single rounded-label shape. Always neutral: pills carry metadata, never state. */
export function Pill({ size = 'base', className, children }: { size?: 'base' | 'sm'; className?: string; children: ReactNode }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full ring-1 bg-gray-100 ring-gray-900/10 text-gray-700 dark:bg-white/5 dark:ring-white/10 dark:text-gray-300',
        size === 'base' ? 'px-3 py-1 text-sm' : 'px-2.5 py-0.5 text-xs',
        className,
      )}
    >
      {children}
    </span>
  )
}

/** Status text — the one exception to the pill: accent on transparent with a ring. One per screen. */
export function StatusText({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 text-primary-700 ring-primary-700/30 dark:text-primary-400 dark:ring-primary-400/30">
      {children}
    </span>
  )
}
