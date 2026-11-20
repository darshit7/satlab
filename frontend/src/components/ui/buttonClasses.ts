import { cn } from './cn'

// Class recipes for Button and ButtonLink (design-system.md §8). Literal class strings, so
// Tailwind's scanner finds them.

export type ButtonVariant = 'primary' | 'secondary' | 'icon' | 'header'

/** House focus style — every focusable control that isn't a plain link uses it. */
export const focusRing =
  'focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2 focus-visible:ring-offset-white dark:focus-visible:ring-offset-dark'

const variants: Record<ButtonVariant, string> = {
  primary:
    'rounded-lg px-4 py-2 font-semibold text-white shadow-sm bg-primary-700 hover:bg-primary-800 dark:bg-primary-600 dark:hover:bg-primary-500',
  secondary:
    'rounded-lg px-3 py-1.5 font-semibold ring-1 bg-white ring-gray-900/10 text-gray-900 hover:bg-gray-50 dark:bg-white/5 dark:ring-white/10 dark:text-gray-100 dark:hover:bg-white/10',
  icon: 'rounded p-1 text-gray-500 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100',
  /** The header's quiet controls (docs, theme): muted until hovered, the size of the About (i). */
  header: 'rounded p-1.5 text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100',
}

const activeSecondary =
  'text-primary-700 ring-primary-700/30 dark:text-primary-400 dark:ring-primary-400/30'

/** The one class recipe behind `Button` and `ButtonLink` (and controls that can be neither). */
export function buttonClasses(variant: ButtonVariant = 'secondary', active?: boolean, className?: string): string {
  return cn(
    'inline-flex items-center justify-center gap-1.5 text-sm transition-colors duration-150',
    'disabled:pointer-events-none disabled:opacity-50',
    focusRing,
    variants[variant],
    variant === 'secondary' && active && activeSecondary,
    className,
  )
}
