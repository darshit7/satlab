import './cesiumBase' // before @cesium/engine
import '@cesium/engine/Source/Widget/CesiumWidget.css'
import {
  BoundingSphere,
  Cartesian2,
  Cartesian3,
  Cartographic,
  Color,
  ColorGeometryInstanceAttribute,
  CylinderOutlineGeometry,
  FixedFrameTransforms,
  GeometryInstance,
  HeadingPitchRange,
  Matrix4,
} from '@cesium/core'
import {
  BlendOption,
  buildModuleUrl,
  CesiumWidget,
  ImageryLayer,
  LabelCollection,
  LabelStyle,
  Material,
  PerInstanceColorAppearance,
  PointPrimitiveCollection,
  PolylineCollection,
  Primitive,
  ScreenSpaceEventType,
  TileMapServiceImageryProvider,
  VerticalOrigin,
  type Label,
  type PointPrimitive,
  type Polyline,
} from '@cesium/engine'
import { useEffect, useRef } from 'react'
import { palette as skyPalette } from '../../components/skyDraw'
import { PACKET_FLIGHT_MS, packetOutcome } from '../../lib/comms'
import { altitudeOnTrack, inViewRuns, positionOnTrack, wrapLon, type TimedTrack } from '../../lib/geo'
import { currentResolvedTheme, onThemeChange, type ResolvedTheme } from '../../lib/theme'
import { estimateSimMs } from '../../lib/time'
import { token } from '../../lib/tokens'
import { useMission } from '../../store/mission'
import { useUi } from '../../store/ui'
import type { GroundTrackMessage, PacketMessage, Rag, SatelliteState, Snapshot } from '../../types/ws'
import { coneShapes, type ConeShape } from './cone'

const FRAME_INTERVAL_MS = 50
const HOVER_INTERVAL_MS = 100
const CAMERA_RANGE_M = 13_000_000
// With nothing selected, the cone is drawn out to a typical low-Earth-orbit height.
const DEFAULT_SHELL_ALT_M = 1_000_000
// Rebuild the cone when the selected satellite's height changes by about this much.
const CONE_SHELL_STEP_M = 20_000
// Lines from the station up to each cone's rim.
const CONE_LINES = 8
// The camera looks at the station from the south and above, so the cone shows as a "V".
const CAMERA_VIEW = new HeadingPitchRange(0, -0.8, CAMERA_RANGE_M)
const LABEL_FONT = '600 12px "Nunito Variable", system-ui, sans-serif'

/** Globe colors per theme, from the same tokens as the sky views: a satellite's fill is its link
 *  state (grey = no RF data), so emerald always means "green link"; the selection is ink. */
function palette(theme: ResolvedTheme) {
  const light = theme === 'light'
  const sky = skyPalette(theme)
  const css = Color.fromCssColorString
  return {
    space: css(token(light ? 'gray-50' : 'dark')),
    ground: css(sky.ground),
    ink: css(sky.ink),
    label: css(sky.label),
    noRf: css(sky.sat),
    track: css(token(light ? 'gray-500' : 'gray-400')),
    /** A custom location being placed, as the 2D map rings it. */
    draft: css(token(light ? 'gray-500' : 'gray-400')),
    mask: css(sky.mask),
    rag: {
      green: css(sky.rag.green),
      amber: css(sky.rag.amber),
      red: css(sky.rag.red),
    } satisfies Record<Rag, Color>,
    imagery: light ? { brightness: 1.05, saturation: 0.55 } : { brightness: 0.6, saturation: 0.5 },
  }
}
type Palette = ReturnType<typeof palette>

const timedTracks = new WeakMap<GroundTrackMessage, TimedTrack>()
function timed(track: GroundTrackMessage): TimedTrack {
  let t = timedTracks.get(track)
  if (!t) {
    t = {
      startMs: Date.parse(track.start_time),
      endMs: Date.parse(track.end_time),
      points: track.points,
      alt_m: track.alt_m ?? undefined,
    }
    timedTracks.set(track, t)
  }
  return t
}

const linkColor = (sat: SatelliteState, c: Palette): Color => (sat.link ? c.rag[sat.link.rag] : c.noRf)

/** A packet in flight on the line of sight: commands up in ink, frames down by what arrived. */
function packetColor(packet: PacketMessage, c: Palette): Color {
  switch (packetOutcome(packet)) {
    case 'sent':
      return c.ink
    case 'ok':
      return c.rag.green
    case 'corrupted':
      return c.rag.amber
    case 'lost':
      return c.rag.red
  }
}

/**
 * The 3D globe: Earth, the station and its visibility cone, the satellites in view and the orbits
 * of tracked and selected ones. Imperative CesiumJS (engine only: no Viewer widgets), driven like
 * the 2D map by store subscriptions and a ~20 fps frame loop. Presentation only: positions,
 * in-view flags, link colours and cone angles all come from the server.
 */
export function Globe({ onError }: { onError: () => void }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const onErrorRef = useRef(onError)
  useEffect(() => {
    onErrorRef.current = onError
  })

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    let widget: CesiumWidget
    try {
      widget = new CesiumWidget(container, {
        baseLayer: ImageryLayer.fromProviderAsync(
          TileMapServiceImageryProvider.fromUrl(buildModuleUrl('Assets/Textures/NaturalEarthII')),
        ),
        skyBox: false, // also no Sun or Moon: they'd follow the wall clock, not the sim clock
        skyAtmosphere: false,
        scene3DOnly: true,
        requestRenderMode: true,
        maximumRenderTimeChange: Infinity,
        showRenderLoopErrors: false,
      })
    } catch {
      onErrorRef.current() // no usable WebGL after all
      return
    }
    const scene = widget.scene
    scene.globe.showGroundAtmosphere = false
    scene.fog.enabled = false
    const failed = () => onErrorRef.current()
    scene.renderError.addEventListener(failed)

    let theme = currentResolvedTheme()
    let c = palette(theme)
    let frame = 0
    let lastFrameAt = 0
    const unsubscribers: (() => void)[] = []

    const orbits = scene.primitives.add(new PolylineCollection()) as PolylineCollection
    const lines = scene.primitives.add(new PolylineCollection()) as PolylineCollection
    const points = scene.primitives.add(new PointPrimitiveCollection({ blendOption: BlendOption.TRANSLUCENT })) as PointPrimitiveCollection
    const labels = scene.primitives.add(new LabelCollection()) as LabelCollection
    const satPoints = new Map<number, PointPrimitive>()
    const satLabels = new Map<number, Label>()
    let cone: Primitive | null = null
    let coneKey = ''
    let station: { position: Cartesian3; point: PointPrimitive; label: Label } | null = null
    let draftPoint: PointPrimitive | null = null
    const contactLine: Polyline = lines.add({ show: false, width: 1.5, positions: [Cartesian3.ZERO, Cartesian3.UNIT_X] })
    let contactColor: Color | null = null
    // Packets travelling the line of sight (slowed down to be seen, like the comms panel's strip).
    let lastPacketSeq: number | null = null
    const flights: { startedAt: number; up: boolean; point: PointPrimitive }[] = []
    const scratch = new Cartesian3()

    const applyTheme = () => {
      scene.backgroundColor = c.space
      const base = scene.imageryLayers.get(0)
      if (base) Object.assign(base, c.imagery)
    }
    applyTheme()

    const pinnedIds = () => {
      const { tracked, selectedId } = useMission.getState()
      const ids = new Set(tracked.map((t) => t.norad_id))
      if (selectedId !== null) ids.add(selectedId)
      return ids
    }

    const renderTracks = () => {
      orbits.removeAll()
      const { tracks, selectedId, snapshot } = useMission.getState()
      const wanted = pinnedIds()
      for (const track of Object.values(tracks)) {
        if (!wanted.has(track.norad_id) || track.points.length < 2) continue
        const fallbackAlt = snapshot?.satellites.find((s) => s.norad_id === track.norad_id)?.alt_m ?? 0
        const positions = track.points.map(([lon, lat], i) => Cartesian3.fromDegrees(lon, lat, track.alt_m?.[i] ?? fallbackAlt))
        const selected = track.norad_id === selectedId
        const color = selected ? c.ink : c.track
        const runs = inViewRuns(track.above_mask, positions.length)
        // Without in-view flags (an older server) the whole orbit is drawn faded.
        const spans = runs.length ? runs : [{ inView: false, start: 0, end: positions.length - 1 }]
        for (const run of spans) {
          orbits.add({
            positions: positions.slice(run.start, run.end + 1),
            width: (selected ? 1 : 0) + (run.inView ? 1.75 : 1),
            material: Material.fromType('Color', { color: color.withAlpha(run.inView ? 0.95 : 0.3) }),
          })
        }
      }
      scene.requestRender()
    }

    const renderStation = () => {
      const { session } = useMission.getState()
      if (station) {
        points.remove(station.point)
        labels.remove(station.label)
        station = null
      }
      if (session) {
        const position = Cartesian3.fromDegrees(session.lon_deg, session.lat_deg, session.alt_m)
        station = {
          position,
          point: points.add({ position, pixelSize: 8, color: c.ink, outlineColor: c.ground, outlineWidth: 2 }),
          label: labels.add({
            position,
            text: session.location_name,
            font: LABEL_FONT,
            fillColor: c.ink,
            outlineColor: c.ground,
            outlineWidth: 3,
            style: LabelStyle.FILL_AND_OUTLINE,
            verticalOrigin: VerticalOrigin.TOP,
            pixelOffset: new Cartesian2(0, 10),
          }),
        }
      }
      coneKey = '' // the cone stands on the station
      scene.requestRender()
    }

    /** A custom location being placed (station panel open), as a ring like the 2D map's. */
    const renderDraft = () => {
      const { draft } = useMission.getState()
      if (draftPoint) points.remove(draftPoint)
      draftPoint = draft
        ? points.add({
            position: Cartesian3.fromDegrees(draft.lon_deg, draft.lat_deg, draft.alt_m),
            pixelSize: 12,
            color: Color.TRANSPARENT,
            outlineColor: c.draft,
            outlineWidth: 2,
          })
        : null
      scene.requestRender()
    }

    /** The visibility cone over the station, sized to the selected satellite's height. */
    const renderCone = (snapshot: Snapshot | null, selected: SatelliteState | undefined) => {
      if (!station || !snapshot) {
        if (cone) scene.primitives.remove(cone)
        cone = null
        coneKey = ''
        return
      }
      const mask = snapshot.observer.horizon_mask_deg
      const bands = selected?.link?.strong_link_elev_bands_deg ?? []
      const stationRadius = Cartesian3.magnitude(station.position)
      const shellAlt = selected ? selected.alt_m : DEFAULT_SHELL_ALT_M
      const key = `${mask}|${JSON.stringify(bands)}|${Math.round(shellAlt / CONE_SHELL_STEP_M)}|${theme}`
      if (key === coneKey) return
      coneKey = key
      if (cone) scene.primitives.remove(cone)
      const shapes = coneShapes(mask, bands, stationRadius, stationRadius + shellAlt)
      const enu = FixedFrameTransforms.eastNorthUpToFixedFrame(station.position)
      // Wireframe only (rim + lines from the station up to it, the "V" in profile): a filled cone
      // big enough to hold the camera, as a GEO one is, would tint the whole view.
      const instance = (s: ConeShape) =>
        new GeometryInstance({
          geometry: new CylinderOutlineGeometry({
            length: s.heightM,
            topRadius: s.rimRadiusM,
            bottomRadius: 0,
            slices: 128,
            numberOfVerticalLines: CONE_LINES,
          }),
          // apex at the station, rim up at the satellite's height
          modelMatrix: Matrix4.multiplyByTranslation(enu, new Cartesian3(0, 0, s.heightM / 2), new Matrix4()),
          attributes: {
            color: ColorGeometryInstanceAttribute.fromColor(
              (s.kind === 'mask' ? c.mask : c.rag.green).withAlpha(s.kind === 'mask' ? 0.7 : 0.9),
            ),
          },
        })
      cone = scene.primitives.add(
        new Primitive({
          geometryInstances: shapes.map(instance),
          appearance: new PerInstanceColorAppearance({ flat: true, translucent: true }),
          asynchronous: false, // a handful of outlines: build them now, on this thread
        }),
      ) as Primitive
    }

    // Tracked and selected satellites tween along their server-sampled orbits at the estimated sim
    // time; the rest of the visible set sits at its snapshot position, refreshed per snapshot.
    let renderedSnapshot: Snapshot | null = null
    let renderedPinned = ''
    const renderSatellites = (now: number) => {
      frame = requestAnimationFrame(renderSatellites)
      if (now - lastFrameAt < FRAME_INTERVAL_MS) return
      lastFrameAt = now
      const { snapshot, clockSample, tracks, selectedId } = useMission.getState()
      if (!snapshot || !clockSample) {
        if (renderedSnapshot) {
          for (const p of satPoints.values()) points.remove(p)
          for (const l of satLabels.values()) labels.remove(l)
          satPoints.clear()
          satLabels.clear()
          contactLine.show = false
          renderCone(null, undefined)
          renderedSnapshot = null
          scene.requestRender()
        }
        return
      }
      const pinned = pinnedIds()
      const pinnedKey = `${[...pinned].join(',')}|${selectedId}`
      const fresh = snapshot !== renderedSnapshot || pinnedKey !== renderedPinned
      const simMs = estimateSimMs(clockSample, now)
      const selected = snapshot.satellites.find((s) => s.norad_id === selectedId)

      if (fresh) {
        const present = new Set(snapshot.satellites.map((s) => s.norad_id))
        for (const [id, p] of satPoints) {
          if (!present.has(id)) {
            points.remove(p)
            satPoints.delete(id)
          }
        }
        for (const [id, l] of satLabels) {
          if (!present.has(id) || !pinned.has(id)) {
            labels.remove(l)
            satLabels.delete(id)
          }
        }
      }

      let selectedPosition: Cartesian3 | null = null
      for (const sat of snapshot.satellites) {
        const isPinned = pinned.has(sat.norad_id)
        if (!fresh && !isPinned) continue // context dots move once per snapshot
        const track = isPinned ? tracks[sat.norad_id] : undefined
        const t = track && timed(track)
        const lonLat = (t && positionOnTrack(t, simMs)) ?? [sat.lon_deg, sat.lat_deg]
        const alt = (t && altitudeOnTrack(t, simMs)) ?? sat.alt_m
        const position = Cartesian3.fromDegrees(lonLat[0], lonLat[1], alt)
        const isSelected = sat.norad_id === selectedId
        if (isSelected) selectedPosition = position

        let point = satPoints.get(sat.norad_id)
        if (!point) {
          point = points.add({ id: sat.norad_id })
          satPoints.set(sat.norad_id, point)
        }
        point.position = position
        if (fresh) {
          // lock on the active contact: with a selection, everything else steps back
          const alpha = selectedId === null || isSelected ? 1 : isPinned ? 0.75 : 0.4
          point.color = linkColor(sat, c).withAlpha(alpha)
          point.pixelSize = isSelected ? 10 : isPinned ? 7 : 5
          point.outlineColor = isSelected ? c.ink : c.ground.withAlpha(alpha)
          point.outlineWidth = isSelected ? 2 : 1
        }

        if (isPinned) {
          let label = satLabels.get(sat.norad_id)
          if (!label) {
            label = labels.add({
              position,
              text: sat.name,
              font: LABEL_FONT,
              outlineColor: c.ground,
              outlineWidth: 3,
              style: LabelStyle.FILL_AND_OUTLINE,
              verticalOrigin: VerticalOrigin.BOTTOM,
              pixelOffset: new Cartesian2(0, -10),
            })
            satLabels.set(sat.norad_id, label)
          }
          label.position = position
          label.fillColor = isSelected ? c.ink : c.label
        }
      }

      // New packets of the selected satellite set off along the line of sight.
      const { packets } = useMission.getState()
      const newest = packets.at(-1)?.seq ?? 0
      if (lastPacketSeq !== null && newest > lastPacketSeq) {
        for (const p of packets) {
          if (p.seq > lastPacketSeq && p.norad_id === selectedId) {
            flights.push({ startedAt: now, up: p.direction === 'up', point: points.add({ pixelSize: 6, color: packetColor(p, c) }) })
          }
        }
      }
      lastPacketSeq = Math.max(newest, lastPacketSeq ?? 0)
      for (let i = flights.length - 1; i >= 0; i--) {
        const flight = flights[i]!
        const t = (now - flight.startedAt) / PACKET_FLIGHT_MS
        if (t >= 1 || !station || !selectedPosition) {
          points.remove(flight.point)
          flights.splice(i, 1)
        } else {
          const from = flight.up ? station.position : selectedPosition
          const to = flight.up ? selectedPosition : station.position
          flight.point.position = Cartesian3.lerp(from, to, t, scratch)
        }
      }

      // The line of sight to the active contact, in its link colour, while it is in view.
      if (station && selected && selectedPosition && selected.above_mask) {
        contactLine.positions = [station.position, selectedPosition]
        const color = linkColor(selected, c).withAlpha(0.85)
        if (!contactColor || !Color.equals(color, contactColor)) {
          contactLine.material = Material.fromType('Color', { color })
          contactColor = color
        }
        contactLine.show = true
      } else {
        contactLine.show = false
      }

      if (fresh) {
        renderCone(snapshot, selected)
        renderedSnapshot = snapshot
        renderedPinned = pinnedKey
      }
      scene.requestRender()
    }

    const flyToStation = (duration: number) => {
      const { session } = useMission.getState()
      if (!session) return
      const target = new BoundingSphere(Cartesian3.fromDegrees(session.lon_deg, session.lat_deg, session.alt_m), 0)
      scene.camera.flyToBoundingSphere(target, { offset: CAMERA_VIEW, duration })
    }

    const switchTheme = () => {
      const next = currentResolvedTheme()
      if (next === theme) return
      theme = next
      c = palette(theme)
      applyTheme()
      for (const l of satLabels.values()) labels.remove(l)
      satLabels.clear()
      renderStation()
      renderDraft()
      renderTracks()
      renderedSnapshot = null // recolour everything on the next frame
    }

    renderStation()
    renderDraft()
    renderTracks()
    flyToStation(0)
    frame = requestAnimationFrame(renderSatellites)

    unsubscribers.push(
      useMission.subscribe((s, prev) => {
        if (s.tracks !== prev.tracks || s.tracked !== prev.tracked || s.selectedId !== prev.selectedId) renderTracks()
        if (s.draft !== prev.draft) renderDraft()
        if (s.session !== prev.session) {
          renderStation()
          if (s.session && (s.session.lat_deg !== prev.session?.lat_deg || s.session.lon_deg !== prev.session?.lon_deg)) {
            flyToStation(1.2)
          }
        }
      }),
      onThemeChange(switchTheme),
    )

    const pickedId = (position: Cartesian2): number | null => {
      const picked = scene.pick(position) as { id?: unknown } | undefined
      return typeof picked?.id === 'number' ? picked.id : null
    }
    const handler = widget.screenSpaceEventHandler
    handler.setInputAction((e: { position: Cartesian2 }) => {
      const { select, setDraft, draft } = useMission.getState()
      const hit = pickedId(e.position)
      if (hit !== null) {
        select(hit)
        return
      }
      // empty ground places the station, only while its panel is open
      const ground = useUi.getState().panel === 'station' ? scene.camera.pickEllipsoid(e.position, scene.globe.ellipsoid) : undefined
      if (ground) {
        const where = Cartographic.fromCartesian(ground)
        setDraft({
          lat_deg: (where.latitude * 180) / Math.PI,
          lon_deg: wrapLon((where.longitude * 180) / Math.PI),
          alt_m: draft?.alt_m ?? 0,
        })
      }
    }, ScreenSpaceEventType.LEFT_CLICK)
    let lastHoverAt = 0
    handler.setInputAction((e: { endPosition: Cartesian2 }) => {
      const now = performance.now()
      if (now - lastHoverAt < HOVER_INTERVAL_MS) return
      lastHoverAt = now
      widget.canvas.style.cursor =
        pickedId(e.endPosition) !== null ? 'pointer' : useUi.getState().panel === 'station' ? 'crosshair' : ''
    }, ScreenSpaceEventType.MOUSE_MOVE)

    return () => {
      cancelAnimationFrame(frame)
      unsubscribers.forEach((u) => u())
      scene.renderError.removeEventListener(failed)
      widget.destroy()
    }
  }, [])

  return (
    // The rounded frame clips the canvas; Cesium owns the inner container.
    <div className="relative isolate h-full min-h-[300px] w-full overflow-hidden rounded-2xl border border-gray-200 dark:border-gray-800">
      <div ref={containerRef} aria-label="3D globe" className="absolute inset-0" />
      <ConeLegend />
    </div>
  )
}

/** What the two cones mean. The inner one only exists for a selected satellite with a link budget. */
function ConeLegend() {
  const mask = useMission((s) => s.snapshot?.observer.horizon_mask_deg)
  const strong = useMission((s) => {
    const sat = s.snapshot?.satellites.find((x) => x.norad_id === s.selectedId)
    return (sat?.link?.strong_link_elev_bands_deg.length ?? 0) > 0
  })
  if (mask === undefined) return null
  return (
    <div className="pointer-events-none absolute right-3 bottom-3 max-w-[16rem] space-y-1 rounded-lg bg-white/75 px-2.5 py-1.5 text-xs text-gray-700 backdrop-blur dark:bg-dark/75 dark:text-gray-300">
      <div className="flex items-center gap-2">
        <span className="size-2.5 shrink-0 rounded-full ring-2 ring-gray-400 dark:ring-gray-500" aria-hidden />
        In view: above the {mask}° horizon mask
      </div>
      {strong && (
        <div className="flex items-center gap-2">
          <span className="size-2.5 shrink-0 rounded-full ring-2 ring-primary-600 dark:ring-primary-400" aria-hidden />
          Strong link: green margin for the selection
        </div>
      )}
      <div className="text-gray-500 dark:text-gray-400">True positions, not what the eye can see.</div>
    </div>
  )
}
