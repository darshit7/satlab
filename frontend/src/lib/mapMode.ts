import { useSyncExternalStore } from 'react'

/** Which map fills the map panel: the 3D globe (default) or the 2D ground-track map. */
export type MapMode = '3d' | '2d'

const STORAGE_KEY = 'satlab.mapMode'
export const DEFAULT_MAP_MODE: MapMode = '3d'
const listeners = new Set<() => void>()

/** Stored values are untrusted (it's localStorage): anything unexpected means the default. */
export function parseMapMode(raw: unknown): MapMode {
  return raw === '3d' || raw === '2d' ? raw : DEFAULT_MAP_MODE
}

function readMode(): MapMode {
  try {
    return parseMapMode(localStorage.getItem(STORAGE_KEY))
  } catch {
    return DEFAULT_MAP_MODE // storage unavailable (private mode, tests)
  }
}

let mode: MapMode = readMode()

export function setMapMode(next: MapMode) {
  mode = next
  try {
    localStorage.setItem(STORAGE_KEY, next)
  } catch {
    // not persisted; still applied for this page
  }
  listeners.forEach((l) => l())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useMapMode(): MapMode {
  return useSyncExternalStore(subscribe, () => mode)
}

let webgl: boolean | undefined

/** Whether this browser can create a WebGL context at all (the globe needs one). Probed once. */
export function supportsWebGL(): boolean {
  if (webgl === undefined) {
    try {
      const canvas = document.createElement('canvas')
      webgl = Boolean(canvas.getContext('webgl2') ?? canvas.getContext('webgl'))
    } catch {
      webgl = false
    }
  }
  return webgl
}
