import { CircleCheck, CircleDashed, OctagonX, TriangleAlert } from 'lucide-react'
import { ragWord } from '../../lib/rag'
import type { Rag } from '../../types/ws'
import { cn } from './cn'
import { RAG_BG, RAG_TEXT, ragKey } from './ragClasses'

const ICONS = { green: CircleCheck, amber: TriangleAlert, red: OctagonX, none: CircleDashed }

/** A small status dot; the word is in its title and for screen readers. */
export function RagDot({ rag, label, className }: { rag: Rag | null | undefined; label?: string; className?: string }) {
  const word = label ?? ragWord(rag)
  return (
    <span className={cn('inline-block size-2 shrink-0 rounded-full', RAG_BG[ragKey(rag)], className)} title={word} role="img" aria-label={word} />
  )
}

/** Icon + colour for a state, so colour is never the only cue. */
export function RagIcon({ rag, size = 14, className }: { rag: Rag | null | undefined; size?: number; className?: string }) {
  const key = ragKey(rag)
  const Icon = ICONS[key]
  return <Icon size={size} strokeWidth={1.75} aria-hidden className={cn('shrink-0', RAG_TEXT[key], className)} />
}
