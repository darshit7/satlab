import { focusRing } from './Button'

/** Text inputs and selects: rounded-lg, hairline ring, house focus ring. */
export const inputClass = [
  'block w-full rounded-lg bg-white px-3 py-2 text-sm text-gray-900 ring-1 ring-gray-900/10 placeholder:text-gray-500',
  'dark:bg-white/5 dark:text-gray-100 dark:ring-white/10 dark:placeholder:text-gray-400',
  focusRing,
].join(' ')
