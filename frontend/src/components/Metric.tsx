import type { ReactNode } from 'react'
import { cn } from './ui/cn'

/** A metric tile, label over value, for the readout grid. */
export function Metric({
  label,
  value,
  title,
  className,
  icon,
  wide = false,
}: {
  label: string
  value: string
  title?: string
  className?: string
  icon?: ReactNode
  /** Two columns of a metric grid. */
  wide?: boolean
}) {
  return (
    <div
      className={cn(
        'flex flex-col rounded-lg bg-gray-50 px-2 py-1 leading-tight ring-1 ring-gray-900/5 dark:bg-white/5 dark:ring-white/10',
        wide && 'col-span-2',
      )}
      title={title}
    >
      <dt className="text-xs text-gray-500 dark:text-gray-400">{label}</dt>
      <dd className={cn('flex items-center gap-1 font-mono text-sm tabular-nums', className)}>
        {icon}
        {value}
      </dd>
    </div>
  )
}
