import { useId } from 'react'
import { cn } from './cn'

/** Texture, not decoration: an SVG grid skewed -18deg at a few percent opacity, fading downward. */
export function TiltedGridBackground({ className }: { className?: string }) {
  const id = useId()
  return (
    <div
      aria-hidden
      className={cn('pointer-events-none absolute inset-0 -z-10 overflow-hidden [contain:paint]', className)}
      style={{ maskImage: 'linear-gradient(white, transparent)', WebkitMaskImage: 'linear-gradient(white, transparent)' }}
    >
      <svg className="absolute -inset-1/2 h-[200%] w-[200%] -skew-y-[18deg] will-change-transform text-gray-900 opacity-[0.05] dark:text-white dark:opacity-[0.03]">
        <defs>
          <pattern id={id} width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M40 0H0V40" fill="none" stroke="currentColor" strokeWidth="1" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill={`url(#${id})`} />
      </svg>
    </div>
  )
}
