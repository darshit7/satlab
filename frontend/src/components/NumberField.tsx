import { useState } from 'react'
import { inputClass } from './ui/Field'

interface Props {
  label: string
  value: number | null
  onCommit: (value: number) => void
  digits?: number
  min?: number
  max?: number
  step?: number
}

/** Numeric input that keeps the user's partial text while typing and commits only valid numbers. */
export function NumberField({ label, value, onCommit, digits = 0, min, max, step }: Props) {
  const [text, setText] = useState(value === null ? '' : value.toFixed(digits))
  const [lastValue, setLastValue] = useState(value)

  // Reflect external changes (map click, geolocation) without clobbering in-progress typing.
  if (value !== lastValue) {
    setLastValue(value)
    if (value === null) setText('')
    else if (Number(text) !== value || text.trim() === '') setText(value.toFixed(digits))
  }

  return (
    <label className="block">
      <span className="mb-1 block text-sm text-gray-600 dark:text-gray-400">{label}</span>
      <input
        type="number"
        inputMode="decimal"
        value={text}
        min={min}
        max={max}
        step={step}
        className={`${inputClass} font-mono tabular-nums`}
        onChange={(e) => {
          const raw = e.target.value
          setText(raw)
          const parsed = Number(raw)
          const inRange = (min === undefined || parsed >= min) && (max === undefined || parsed <= max)
          if (raw.trim() !== '' && Number.isFinite(parsed) && inRange) onCommit(parsed)
        }}
      />
    </label>
  )
}
