import type { CSSProperties } from 'react'

/** Two 1px accent hairlines (top and left) that fade at both ends, inset from the corner by --offset. */
export function GradientBorder({ offset = '1rem' }: { offset?: string }) {
  return (
    <span aria-hidden className="pointer-events-none absolute inset-0" style={{ '--offset': offset } as CSSProperties}>
      <span className="absolute top-0 right-(--offset) left-(--offset) h-px bg-linear-to-r from-transparent via-primary-500 to-transparent dark:via-primary-400" />
      <span className="absolute top-(--offset) bottom-(--offset) left-0 w-px bg-linear-to-b from-transparent via-primary-500 to-transparent dark:via-primary-400" />
    </span>
  )
}
