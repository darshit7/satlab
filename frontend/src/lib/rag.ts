// Presentation of link and subsystem state. Every RAG value comes from the server; nothing here
// decides a colour from raw numbers.

import type { Rag } from '../types/ws'

/** Words for a state, so colour is never the only cue: the operator's go / no-go vocabulary. */
export const RAG_WORDS: Record<Rag, string> = { green: 'Go', amber: 'Marginal', red: 'No-go' }

export function ragWord(rag: Rag | null | undefined): string {
  return rag ? RAG_WORDS[rag] : 'No data'
}

/** Rounded first, so a value that shows as zero carries no sign. */
const signed = (value: number, digits: number) => {
  const rounded = Number(value.toFixed(digits))
  return `${rounded > 0 ? '+' : rounded < 0 ? '−' : ''}${Math.abs(rounded).toFixed(digits)}`
}

/** A level in dB with a real minus sign: "6.5" / "−1.1"; "—" without one. */
export function formatDb(value_db: number | null | undefined): string {
  return value_db === null || value_db === undefined ? '—' : `${value_db < 0 ? '−' : ''}${Math.abs(value_db).toFixed(1)}`
}

/** "+8.2" / "−0.4" (dB); "—" without a margin (below the horizon). */
export function formatMarginDb(margin_db: number | null | undefined): string {
  return margin_db === null || margin_db === undefined ? '—' : signed(margin_db, 1)
}

/** Doppler shift in kHz with its sign: "+171.2" approaching, "−52.0" receding. */
export function formatDopplerKHz(doppler_hz: number): string {
  return signed(doppler_hz / 1000, 1)
}

/** "7812 MHz" / "1686.6 MHz" / "437.5 MHz". */
export function formatFrequency(frequency_hz: number): string {
  return `${Number((frequency_hz / 1e6).toFixed(1))} MHz`
}

/** "15 Mbps" / "400 kbps" / "1200 bps". */
export function formatRate(bps: number): string {
  if (bps >= 1e6) return `${Number((bps / 1e6).toFixed(1))} Mbps`
  if (bps >= 1e3) return `${Number((bps / 1e3).toFixed(1))} kbps`
  return `${Math.round(bps)} bps`
}

/** Only https: links from server data are rendered as links (AGENTS.md §12.7). */
export function safeHttpsUrl(url: string): string | null {
  try {
    return new URL(url).protocol === 'https:' ? url : null
  } catch {
    return null
  }
}
