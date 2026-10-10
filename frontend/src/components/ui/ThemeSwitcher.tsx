import { Monitor, Moon, Sun } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { setThemePreference, useResolvedTheme, useThemePreference, type ThemePreference } from '../../lib/theme'
import { focusRing } from './Button'
import { cn } from './cn'

const OPTIONS: { value: ThemePreference; label: string; Icon: typeof Sun }[] = [
  { value: 'light', label: 'Light', Icon: Sun },
  { value: 'dark', label: 'Dark', Icon: Moon },
  { value: 'system', label: 'System', Icon: Monitor },
]

/** Light / dark / system menu. Opens in 100ms, closes in 75ms. */
export function ThemeSwitcher() {
  const preference = useThemePreference()
  const resolved = useResolvedTheme()
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onPointer = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const CurrentIcon = resolved === 'dark' ? Moon : Sun

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-label="Theme"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className={cn('rounded p-1.5 text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100', focusRing)}
      >
        <CurrentIcon size={20} strokeWidth={1.5} />
      </button>
      <div
        role="menu"
        className={cn(
          'absolute right-0 z-30 mt-2 w-36 origin-top-right rounded-md bg-white p-1 shadow-lg shadow-black/5 ring-1 ring-gray-900/5 dark:bg-gray-800 dark:ring-white/10',
          'transition ease-out',
          open ? 'visible scale-100 opacity-100 duration-100' : 'invisible scale-95 opacity-0 duration-75',
        )}
      >
        {OPTIONS.map(({ value, label, Icon }) => (
          <button
            key={value}
            type="button"
            role="menuitemradio"
            aria-checked={preference === value}
            onClick={() => {
              setThemePreference(value)
              setOpen(false)
            }}
            className={cn(
              'flex w-full items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-gray-100 dark:hover:bg-white/10',
              preference === value ? 'text-primary-700 dark:text-primary-400' : 'text-gray-700 dark:text-gray-300',
              focusRing,
            )}
          >
            <Icon size={16} strokeWidth={1.5} />
            {label}
          </button>
        ))}
      </div>
    </div>
  )
}
