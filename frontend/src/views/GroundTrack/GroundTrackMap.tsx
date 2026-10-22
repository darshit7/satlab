import 'maplibre-gl/dist/maplibre-gl.css'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import {
  Map as MapLibreMap,
  NavigationControl,
  setWorkerUrl,
  type GeoJSONSource,
  type StyleSpecification,
} from 'maplibre-gl'
import type { Feature, FeatureCollection } from 'geojson'
import { useEffect, useRef } from 'react'
import { positionOnTrack, splitAntimeridian, wrapLon, type TimedTrack } from '../../lib/geo'
import { currentResolvedTheme, onThemeChange, type ResolvedTheme } from '../../lib/theme'
import { estimateSimMs } from '../../lib/time'
import { token } from '../../lib/tokens'
import { useMission } from '../../store/mission'
import type { GroundTrackMessage, Snapshot } from '../../types/ws'

// MapLibre resolves its worker dynamically, which Vite can't see; hand it the bundled URL.
setWorkerUrl(workerUrl)

const STYLE_URLS: Record<ResolvedTheme, string> = {
  light: import.meta.env.VITE_MAP_STYLE_URL_LIGHT ?? 'https://tiles.openfreemap.org/styles/positron',
  dark: import.meta.env.VITE_MAP_STYLE_URL_DARK ?? 'https://tiles.openfreemap.org/styles/dark',
}
// Map labels are drawn by MapLibre from the basemap's glyph server, so they use its font.
const LABEL_FONT = import.meta.env.VITE_MAP_LABEL_FONT ?? 'Noto Sans Regular'
const FRAME_INTERVAL_MS = 50

/** Map colors per theme, from the same tokens as the UI: accent for selection, gray for the rest. */
function palette(theme: ResolvedTheme) {
  const light = theme === 'light'
  return {
    ground: token(light ? 'white' : 'dark'),
    track: token(light ? 'gray-400' : 'gray-500'),
    accent: token(light ? 'primary-600' : 'primary-400'),
    sat: token(light ? 'gray-700' : 'gray-300'),
    visible: token(light ? 'gray-400' : 'gray-500'),
    station: token(light ? 'gray-900' : 'gray-100'),
    draft: token(light ? 'gray-500' : 'gray-400'),
    label: token(light ? 'gray-700' : 'gray-300'),
  }
}
type Palette = ReturnType<typeof palette>

const emptyFC = (): FeatureCollection => ({ type: 'FeatureCollection', features: [] })

const timedTracks = new WeakMap<GroundTrackMessage, TimedTrack>()
function timed(track: GroundTrackMessage): TimedTrack {
  let t = timedTracks.get(track)
  if (!t) {
    t = { startMs: Date.parse(track.start_time), endMs: Date.parse(track.end_time), points: track.points }
    timedTracks.set(track, t)
  }
  return t
}

interface LoadedStyle {
  style: StyleSpecification
  labels: boolean
}

const styleCache = new Map<ResolvedTheme, Promise<LoadedStyle>>()
function loadStyle(theme: ResolvedTheme): Promise<LoadedStyle> {
  let pending = styleCache.get(theme)
  if (!pending) {
    pending = fetch(STYLE_URLS[theme])
      .then(async (response) => {
        if (!response.ok) throw new Error(String(response.status))
        const style = (await response.json()) as StyleSpecification
        return { style, labels: Boolean(style.glyphs) }
      })
      .catch(() => {
        styleCache.delete(theme) // retry on the next theme switch
        // Basemap unavailable: a plain ground keeps tracks and markers usable.
        const style: StyleSpecification = {
          version: 8,
          sources: {},
          layers: [{ id: 'background', type: 'background', paint: { 'background-color': palette(theme).ground } }],
        }
        return { style, labels: false }
      })
    styleCache.set(theme, pending)
  }
  return pending
}

function addLayers(map: MapLibreMap, labels: boolean, c: Palette) {
  for (const id of ['tracks', 'visible', 'station', 'draft', 'sats']) {
    map.addSource(id, { type: 'geojson', data: emptyFC() })
  }
  map.addLayer({
    id: 'tracks-line',
    type: 'line',
    source: 'tracks',
    layout: { 'line-join': 'round', 'line-cap': 'round' },
    paint: {
      'line-color': ['case', ['get', 'selected'], c.accent, c.track],
      'line-width': ['case', ['get', 'selected'], 2.2, 1.2],
      'line-opacity': ['case', ['get', 'selected'], 0.95, 0.7],
    },
  })
  // Everything else in view: small, muted, unlabelled context dots.
  map.addLayer({
    id: 'visible-dot',
    type: 'circle',
    source: 'visible',
    paint: { 'circle-radius': 2.5, 'circle-color': c.visible, 'circle-opacity': 0.85 },
  })
  map.addLayer({
    id: 'draft-dot',
    type: 'circle',
    source: 'draft',
    paint: {
      'circle-radius': 6,
      'circle-color': 'rgba(0,0,0,0)',
      'circle-stroke-color': c.draft,
      'circle-stroke-width': 2,
    },
  })
  map.addLayer({
    id: 'station-dot',
    type: 'circle',
    source: 'station',
    paint: { 'circle-radius': 5, 'circle-color': c.station, 'circle-stroke-color': c.ground, 'circle-stroke-width': 2 },
  })
  map.addLayer({
    id: 'sats-halo',
    type: 'circle',
    source: 'sats',
    filter: ['get', 'selected'],
    paint: { 'circle-radius': 11, 'circle-color': c.accent, 'circle-opacity': 0.2 },
  })
  map.addLayer({
    id: 'sats-dot',
    type: 'circle',
    source: 'sats',
    paint: {
      'circle-radius': 5,
      'circle-color': ['case', ['get', 'selected'], c.accent, c.sat],
      'circle-stroke-color': c.ground,
      'circle-stroke-width': 1.5,
    },
  })
  if (labels) {
    map.addLayer({
      id: 'sats-label',
      type: 'symbol',
      source: 'sats',
      layout: {
        'text-field': ['get', 'name'],
        'text-font': [LABEL_FONT],
        'text-size': 12,
        'text-offset': [0, 1.2],
        'text-anchor': 'top',
        'text-allow-overlap': true,
      },
      paint: {
        'text-color': ['case', ['get', 'selected'], c.accent, c.label],
        'text-halo-color': c.ground,
        'text-halo-width': 1.4,
      },
    })
  }
}

export function GroundTrackMap() {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    let map: MapLibreMap | null = null
    let theme = currentResolvedTheme()
    let labels = false
    let frame = 0
    let lastFrameAt = 0
    let disposed = false
    const unsubscribers: (() => void)[] = []

    const source = (id: string) => map?.getSource<GeoJSONSource>(id)

    const renderTracks = () => {
      const { tracks, tracked, selectedId } = useMission.getState()
      const wanted = new Set(tracked.map((t) => t.norad_id))
      if (selectedId !== null) wanted.add(selectedId)
      const features: Feature[] = Object.values(tracks)
        .filter((t) => wanted.has(t.norad_id))
        .map((t) => ({
          type: 'Feature',
          properties: { norad_id: t.norad_id, selected: t.norad_id === selectedId },
          geometry: { type: 'MultiLineString', coordinates: splitAntimeridian(t.points) },
        }))
      // draw the selected track last so it sits on top
      features.sort((a, b) => Number(a.properties?.selected) - Number(b.properties?.selected))
      void source('tracks')?.setData({ type: 'FeatureCollection', features })
    }

    const renderStation = () => {
      const { session, draft } = useMission.getState()
      void source('station')?.setData(
        session
          ? {
              type: 'Feature',
              properties: { name: session.location_name },
              geometry: { type: 'Point', coordinates: [session.lon_deg, session.lat_deg] },
            }
          : emptyFC(),
      )
      void source('draft')?.setData(
        draft
          ? { type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: [draft.lon_deg, draft.lat_deg] } }
          : emptyFC(),
      )
    }

    // Tracked and selected satellites tween along their server-sampled ground tracks at the
    // estimated sim time. The rest of the visible set (often hundreds) is drawn at its snapshot
    // sub-point and refreshed once per snapshot.
    let renderedSnapshot: Snapshot | null = null
    let renderedSelection: number | null = null
    let renderedTracked: unknown = null
    const renderSatellites = (now: number) => {
      frame = requestAnimationFrame(renderSatellites)
      if (now - lastFrameAt < FRAME_INTERVAL_MS) return
      lastFrameAt = now
      const { snapshot, clockSample, tracks, tracked, selectedId } = useMission.getState()
      if (!snapshot || !clockSample) {
        void source('sats')?.setData(emptyFC())
        void source('visible')?.setData(emptyFC())
        renderedSnapshot = null
        return
      }
      const pinned = new Set(tracked.map((t) => t.norad_id))
      if (selectedId !== null) pinned.add(selectedId)
      const simMs = estimateSimMs(clockSample, now)
      const features: Feature[] = snapshot.satellites
        .filter((sat) => pinned.has(sat.norad_id))
        .map((sat) => {
          const track = tracks[sat.norad_id]
          const position = (track && positionOnTrack(timed(track), simMs)) ?? [sat.lon_deg, sat.lat_deg]
          return {
            type: 'Feature',
            properties: { norad_id: sat.norad_id, name: sat.name, selected: sat.norad_id === selectedId },
            geometry: { type: 'Point', coordinates: position },
          }
        })
      void source('sats')?.setData({ type: 'FeatureCollection', features })

      if (snapshot !== renderedSnapshot || selectedId !== renderedSelection || tracked !== renderedTracked) {
        renderedSnapshot = snapshot
        renderedSelection = selectedId
        renderedTracked = tracked
        const others: Feature[] = snapshot.satellites
          .filter((sat) => !pinned.has(sat.norad_id))
          .map((sat) => ({
            type: 'Feature',
            properties: { norad_id: sat.norad_id },
            geometry: { type: 'Point', coordinates: [sat.lon_deg, sat.lat_deg] },
          }))
        void source('visible')?.setData({ type: 'FeatureCollection', features: others })
      }
    }

    const switchTheme = async () => {
      const next = currentResolvedTheme()
      if (!map || next === theme) return
      theme = next
      const loaded = await loadStyle(next)
      if (disposed || theme !== next) return
      labels = loaded.labels
      map.setStyle(loaded.style, { diff: false }) // custom layers are re-added on style.load
    }

    void loadStyle(theme).then((loaded) => {
      if (disposed) return
      labels = loaded.labels
      const m = new MapLibreMap({
        container,
        style: loaded.style,
        center: [useMission.getState().session?.lon_deg ?? 0, 15],
        zoom: 1.3,
        renderWorldCopies: true,
        attributionControl: { compact: true },
      })
      map = m
      m.addControl(new NavigationControl({ showCompass: false }), 'top-right')

      // Fires for the first style and after every theme switch.
      m.on('style.load', () => {
        addLayers(m, labels, palette(theme))
        renderTracks()
        renderStation()
        renderedSnapshot = null // the new style's sources start empty
      })
      m.once('load', () => {
        frame = requestAnimationFrame(renderSatellites)
      })

      unsubscribers.push(
        useMission.subscribe((s, prev) => {
          if (s.tracks !== prev.tracks || s.tracked !== prev.tracked || s.selectedId !== prev.selectedId) {
            renderTracks()
          }
          if (s.session !== prev.session || s.draft !== prev.draft) renderStation()
          const here = s.session
          const before = prev.session
          if (here && (here.lat_deg !== before?.lat_deg || here.lon_deg !== before?.lon_deg)) {
            // the visible set clusters around the observer: bring it into view
            m.easeTo({ center: [here.lon_deg, Math.max(-60, Math.min(60, here.lat_deg))], duration: 600 })
          }
        }),
        onThemeChange(() => void switchTheme()),
      )

      for (const layer of ['sats-dot', 'visible-dot']) {
        m.on('mouseenter', layer, () => (m.getCanvas().style.cursor = 'pointer'))
        m.on('mouseleave', layer, () => (m.getCanvas().style.cursor = ''))
      }
      m.on('click', (e) => {
        const layers = ['sats-dot', 'visible-dot'].filter((id) => m.getLayer(id))
        // a few pixels of slack: the context dots are small
        const box: [[number, number], [number, number]] = [
          [e.point.x - 4, e.point.y - 4],
          [e.point.x + 4, e.point.y + 4],
        ]
        const [hit] = layers.length ? m.queryRenderedFeatures(box, { layers }) : []
        const { select, setDraft, draft } = useMission.getState()
        if (hit) {
          select(Number(hit.properties.norad_id))
        } else {
          setDraft({ lat_deg: e.lngLat.lat, lon_deg: wrapLon(e.lngLat.lng), alt_m: draft?.alt_m ?? 0 })
        }
      })
    })

    return () => {
      disposed = true
      cancelAnimationFrame(frame)
      unsubscribers.forEach((u) => u())
      map?.remove()
    }
  }, [])

  return (
    // MapLibre sets position/overflow on its container, so the rounded frame is the container itself.
    <div
      ref={containerRef}
      aria-label="Ground track map"
      className="isolate h-full min-h-[300px] w-full overflow-hidden rounded-2xl border border-gray-200 dark:border-gray-800"
    />
  )
}
