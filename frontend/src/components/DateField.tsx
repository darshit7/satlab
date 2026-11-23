import { inputClass } from './ui/Field'

interface Props {
  label: string
  /** `YYYY-MM-DD` (UTC day). */
  value: string
  onCommit: (value: string) => void
  min?: string
  max?: string
}

/**
 * A labelled UTC date: the native date input, styled like the other fields. The browser's own
 * picker follows the page's `color-scheme`, works by keyboard, and (unlike the portalled Select)
 * also works inside a modal dialog.
 */
export function DateField({ label, value, onCommit, min, max }: Props) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm text-gray-600 dark:text-gray-400">{label}</span>
      <input
        type="date"
        value={value}
        min={min}
        max={max}
        required
        className={`${inputClass} font-mono tabular-nums dark:[color-scheme:dark]`}
        onChange={(e) => {
          if (/^\d{4}-\d{2}-\d{2}$/.test(e.target.value)) onCommit(e.target.value)
        }}
      />
    </label>
  )
}
