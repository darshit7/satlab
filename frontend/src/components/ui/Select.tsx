import { Check, ChevronDown } from 'lucide-react'
import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { cn } from './cn'
import { inputClass } from './Field'

export interface SelectOption<T extends string | number> {
  value: T
  label: string
}

interface Props<T extends string | number> {
  label: string
  value: T | null
  options: readonly SelectOption<T>[]
  onChange: (value: T) => void
  /** Shown on the trigger while `value` matches no option. */
  placeholder?: string
  className?: string
}

/**
 * Labelled dropdown in the house style. A native <select> can't be themed: its option list is
 * drawn by the browser and turned up white-on-white in dark mode. The trigger looks like an input;
 * the list is the theme menu's surface (100ms in / 75ms out). Keyboard: arrows, Home/End,
 * Enter/Space to pick, Escape/Tab to close, typing jumps to the first match.
 *
 * The list is portalled to <body> and positioned fixed under (or, without room, over) the trigger,
 * so the sidebar's scroll container can't clip it.
 */
const MAX_LIST_PX = 240

export function Select<T extends string | number>({ label, value, options, onChange, placeholder = 'Select…', className }: Props<T>) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const rootRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const [placement, setPlacement] = useState<{ style: CSSProperties; up: boolean }>({ style: {}, up: false })

  const selectedIndex = options.findIndex((o) => o.value === value)
  const selected = selectedIndex >= 0 ? options[selectedIndex] : null

  useEffect(() => {
    if (!open) return
    const onPointer = (e: PointerEvent) => {
      const target = e.target as Node
      if (!rootRef.current?.contains(target) && !listRef.current?.contains(target)) setOpen(false)
    }
    document.addEventListener('pointerdown', onPointer)
    return () => document.removeEventListener('pointerdown', onPointer)
  }, [open])

  // Place the list against the trigger, and follow it while anything scrolls or resizes.
  useLayoutEffect(() => {
    if (!open) return
    const place = () => {
      const button = buttonRef.current
      if (!button) return
      const rect = button.getBoundingClientRect()
      const gap = 4
      const edge = 8
      const wanted = Math.min(MAX_LIST_PX, listRef.current?.scrollHeight ?? MAX_LIST_PX)
      const below = window.innerHeight - rect.bottom - gap - edge
      const above = rect.top - gap - edge
      const up = below < wanted && above > below
      setPlacement({
        up,
        style: {
          left: rect.left,
          width: rect.width,
          maxHeight: Math.max(0, Math.min(MAX_LIST_PX, up ? above : below)),
          ...(up ? { bottom: window.innerHeight - rect.top + gap } : { top: rect.bottom + gap }),
        },
      })
    }
    place()
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [open])

  // Keep the highlighted option in view when the list scrolls (long station lists).
  useEffect(() => {
    if (open && active >= 0) listRef.current?.children[active]?.scrollIntoView({ block: 'nearest' })
  }, [open, active])

  const openList = (index = selectedIndex) => {
    setActive(index >= 0 ? index : 0)
    setOpen(true)
  }
  const pick = (index: number) => {
    const option = options[index]
    if (option && option.value !== value) onChange(option.value)
    setOpen(false)
    buttonRef.current?.focus()
  }

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (options.length === 0) return
    const last = options.length - 1
    const move = (index: number) => {
      e.preventDefault()
      if (open) setActive(index)
      else openList(index)
    }
    switch (e.key) {
      case 'ArrowDown':
        return move(open ? Math.min(active + 1, last) : selectedIndex >= 0 ? selectedIndex : 0)
      case 'ArrowUp':
        return move(open ? Math.max(active - 1, 0) : selectedIndex >= 0 ? selectedIndex : last)
      case 'Home':
        return move(0)
      case 'End':
        return move(last)
      case 'Enter':
      case ' ':
        e.preventDefault()
        return open ? pick(active) : openList()
      case 'Escape':
        if (open) {
          e.preventDefault()
          setOpen(false)
        }
        return
      case 'Tab':
        setOpen(false)
        return
      default:
        if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
          const key = e.key.toLowerCase()
          const start = open ? active : selectedIndex
          for (let step = 1; step <= options.length; step++) {
            const i = (start + step + options.length) % options.length
            if (options[i]?.label.toLowerCase().startsWith(key)) return move(i)
          }
        }
    }
  }

  return (
    <div ref={rootRef} className={cn('relative', className)}>
      <span id={`${id}-label`} className="mb-1 block text-sm text-gray-600 dark:text-gray-400">
        {label}
      </span>
      <button
        ref={buttonRef}
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={`${id}-list`}
        aria-labelledby={`${id}-label`}
        aria-activedescendant={open && active >= 0 ? `${id}-opt-${active}` : undefined}
        onClick={() => (open ? setOpen(false) : openList())}
        onKeyDown={onKeyDown}
        className={cn(inputClass, 'flex items-center justify-between gap-2 text-left')}
      >
        <span className={cn('truncate', !selected && 'text-gray-500 dark:text-gray-400')}>
          {selected ? selected.label : placeholder}
        </span>
        <ChevronDown
          size={16}
          strokeWidth={1.5}
          aria-hidden
          className={cn('shrink-0 text-gray-500 transition-transform duration-150 dark:text-gray-400', open && 'rotate-180')}
        />
      </button>
      {createPortal(
        <ul
          ref={listRef}
          id={`${id}-list`}
          role="listbox"
          aria-labelledby={`${id}-label`}
          style={placement.style}
          className={cn(
            'fixed z-50 overflow-y-auto rounded-md bg-white p-1 shadow-lg shadow-black/5 ring-1 ring-gray-900/5 dark:bg-gray-800 dark:ring-white/10',
            placement.up ? 'origin-bottom' : 'origin-top',
            'transition ease-out',
            open ? 'visible scale-100 opacity-100 duration-100' : 'invisible scale-95 opacity-0 duration-75',
          )}
        >
          {options.map((o, i) => {
            const isSelected = i === selectedIndex
            return (
              <li
                key={String(o.value)}
                id={`${id}-opt-${i}`}
                role="option"
                aria-selected={isSelected}
                onPointerMove={() => active !== i && setActive(i)}
                onClick={() => pick(i)}
                className={cn(
                  'flex cursor-pointer items-center justify-between gap-2 rounded px-2 py-1.5 text-sm',
                  i === active && 'bg-gray-100 dark:bg-white/10',
                  isSelected ? 'text-primary-700 dark:text-primary-400' : 'text-gray-700 dark:text-gray-300',
                )}
              >
                <span className="truncate">{o.label}</span>
                {isSelected && <Check size={16} strokeWidth={1.5} aria-hidden className="shrink-0" />}
              </li>
            )
          })}
        </ul>,
        document.body,
      )}
    </div>
  )
}
