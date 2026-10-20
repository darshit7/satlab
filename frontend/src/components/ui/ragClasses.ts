import type { Rag } from '../../types/ws'

// Functional RAG colours (design-system.md §3): green is the primary accent, amber and red are the
// functional pair, each at 700 on light and 400 on dark. "No data" is neutral grey. Literal class
// strings, so Tailwind's scanner finds them.

export const RAG_TEXT: Record<Rag | 'none', string> = {
  green: 'text-primary-700 dark:text-primary-400',
  amber: 'text-amber-700 dark:text-amber-400',
  red: 'text-red-700 dark:text-red-400',
  none: 'text-gray-500 dark:text-gray-400',
}

export const RAG_BG: Record<Rag | 'none', string> = {
  green: 'bg-primary-700 dark:bg-primary-400',
  amber: 'bg-amber-700 dark:bg-amber-400',
  red: 'bg-red-700 dark:bg-red-400',
  none: 'bg-gray-300 dark:bg-gray-600',
}

export const ragKey = (rag: Rag | null | undefined): Rag | 'none' => rag ?? 'none'
