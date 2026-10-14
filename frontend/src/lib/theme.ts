import { useSyncExternalStore } from 'react'

export type ThemePreference = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

// Keep in sync with the pre-paint script in index.html.
const STORAGE_KEY = 'satlab.theme'
const DEFAULT_PREFERENCE: ThemePreference = 'dark'
const media = () => window.matchMedia('(prefers-color-scheme: dark)')
const listeners = new Set<() => void>()

function readPreference(): ThemePreference {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored
  } catch {
    // storage unavailable (private mode): fall back to the default
  }
  return DEFAULT_PREFERENCE
}

let preference: ThemePreference = readPreference()

export function resolveTheme(pref: ThemePreference, systemDark: boolean): ResolvedTheme {
  return pref === 'system' ? (systemDark ? 'dark' : 'light') : pref
}

function resolved(): ResolvedTheme {
  return resolveTheme(preference, media().matches)
}

function apply() {
  document.documentElement.classList.toggle('dark', resolved() === 'dark')
  listeners.forEach((l) => l())
}

media().addEventListener('change', () => {
  if (preference === 'system') apply()
})

export function setThemePreference(next: ThemePreference) {
  preference = next
  try {
    localStorage.setItem(STORAGE_KEY, next)
  } catch {
    // not persisted; still applied for this page
  }
  apply()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useThemePreference(): ThemePreference {
  return useSyncExternalStore(subscribe, () => preference)
}

export function useResolvedTheme(): ResolvedTheme {
  return useSyncExternalStore(subscribe, resolved)
}

/** Non-React subscription for imperative consumers (the MapLibre map). */
export function onThemeChange(listener: () => void): () => void {
  return subscribe(listener)
}

export { resolved as currentResolvedTheme }
