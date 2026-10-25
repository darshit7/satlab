import type { ComponentProps } from 'react'
import { cn } from './cn'

type Variant = 'primary' | 'secondary' | 'icon'

interface Props extends ComponentProps<'button'> {
  variant?: Variant
  /** Secondary only: marks the currently active option (e.g. the running clock rate). */
  active?: boolean
}

/** House focus style — every focusable control that isn't a plain link uses it. */
export const focusRing =
  'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2 focus-visible:ring-offset-white dark:focus-visible:ring-offset-dark'

const variants: Record<Variant, string> = {
  primary:
    'rounded-lg px-4 py-2 font-semibold text-white shadow-sm bg-primary-700 hover:bg-primary-800 dark:bg-primary-600 dark:hover:bg-primary-500',
  secondary:
    'rounded-lg px-3 py-1.5 font-semibold ring-1 bg-white ring-gray-900/10 text-gray-900 hover:bg-gray-50 dark:bg-white/5 dark:ring-white/10 dark:text-gray-100 dark:hover:bg-white/10',
  icon: 'rounded p-1 text-gray-500 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100',
}

const activeSecondary =
  'text-primary-700 ring-primary-700/30 dark:text-primary-400 dark:ring-primary-400/30'

export function Button({ variant = 'secondary', active, className, type, ...props }: Props) {
  return (
    <button
      type={type ?? 'button'}
      className={cn(
        'inline-flex items-center justify-center gap-1.5 text-sm transition-colors duration-150',
        'disabled:pointer-events-none disabled:opacity-50',
        focusRing,
        variants[variant],
        variant === 'secondary' && active && activeSecondary,
        className,
      )}
      {...props}
    />
  )
}
