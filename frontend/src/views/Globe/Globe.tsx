import './cesiumBase' // before @cesium/engine
import '@cesium/engine/Source/Widget/CesiumWidget.css'
import {
  BoundingSphere,
  Cartesian2,
  Cartesian3,
  Cartographic,
  Color,
  ColorGeometryInstanceAttribute,
  ComponentDatatype,
  CylinderOutlineGeometry,
  FixedFrameTransforms,
  Geometry,
  GeometryAttribute,
  GeometryAttributes,
  GeometryInstance,
  HeadingPitchRange,
  Matrix4,
  PrimitiveType,
} from '@cesium/core'
import {
  BillboardCollection,
  BlendOption,
  buildModuleUrl,
  CesiumWidget,
  DirectionalLight,
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
  type Billboard,
  type Label,
  type PointPrimitive,
  type Polyline,
} from '@cesium/engine'
import { useEffect, useRef } from 'react'
import { palette as skyPalette } from '../../components/skyDraw'
import { PACKET_FLIGHT_MS, packetOutcome } from '../../lib/comms'
import {
  altitudeOnTrack,
  positionOnTrack,
  smallCircle,
  subsolarAt,
  terminatorLine,
  unitVector,
  wrapLon,
  type LonLat,
  type TimedTrack,
} from '../../lib/geo'
import { currentResolvedTheme, onThemeChange, type ResolvedTheme } from '../../lib/theme'
import { estimateSimMs } from '../../lib/time'
import { token } from '../../lib/tokens'
import { useMission } from '../../store/mission'
import { useUi } from '../../store/ui'
import type { GroundTrackMessage, PacketMessage, Rag, SatelliteState, Snapshot } from '../../types/ws'
import { coneShapes, coneSurface, insideCone, type ConeShape } from './cone'
import { haloImage, stationIcon } from './markers'
import { orbitRuns, type OrbitPoint } from './orbit'

const FRAME_INTERVAL_MS = 50
const HOVER_INTERVAL_MS = 100
const CAMERA_RANGE_M = 13_000_000
// With nothing selected, the cone is drawn out to a typical low-Earth-orbit height.
const DEFAULT_SHELL_ALT_M = 1_000_000
// Rebuild the cone when the selected satellite's height changes by about this much.
const CONE_SHELL_STEP_M = 20_000
// The cones: a translucent surface fading from the station (apex) up to its rim, where a ring
// marks where a satellite at that height enters and leaves.
const CONE_FILL = { mask: { apex: 0.3, rim: 0.08 }, strong: { apex: 0.34, rim: 0.1 }, shown: { apex: 0.2, rim: 0.06 } } as const
const CONE_RIM_ALPHA = { mask: 0.55, strong: 0.8, shown: 0.6 } as const
// The camera looks at the station from the south and above, so the cone shows as a "V".
const CAMERA_VIEW = new HeadingPitchRange(0, -0.8, CAMERA_RANGE_M)
const LABEL_FONT = '600 12px "Nunito Variable", system-ui, sans-serif'
// While a satellite is selected, the other tracked ones (orbit, label) step back this far.
const DIM = 0.45
// Lines drawn on the ground sit a little above it, so the globe's surface can't hide them.
const TERMINATOR_HEIGHT_M = 5_000
const FOOTPRINT_HEIGHT_M = 2_000
// Redraw the terminator once the subsolar point has moved this far.
const TERMINATOR_STEP_DEG = 0.05

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
    /** The air's glow and haze, toned down to the greys like the imagery. */
    atmosphere: light ? { saturation: -0.8, brightness: -0.1 } : { saturation: -0.8, brightness: -0.3 },
    /** The selected orbit's glow: narrower on the light ground, where it reads stronger. */
    glowWidth: light ? 5 : 7,
    /** For the canvas-drawn station icon. */
    inkCss: sky.ink,
    groundCss: sky.ground,
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
const flat = (color: Color) => Material.fromType('Color', { color })
const toCartesian = ([lon, lat, alt]: OrbitPoint) => Cartesian3.fromDegrees(lon, lat, alt)

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

/** A cone's surface: its side only, fading from the station upwards, in east-north-up coordinates
 *  (per-vertex colour, so the fade needs no custom shader). */
function coneFillGeometry(shape: ConeShape, color: Color): Geometry {
  const fill = CONE_FILL[shape.kind]
  const surface = coneSurface(shape, fill.apex, fill.rim)
  const n = surface.alphas.length
  const rgba = new Uint8Array(n * 4)
  const [r, g, b] = [color.red, color.green, color.blue].map((v) => Math.round(v * 255))
  for (let i = 0; i < n; i++) rgba.set([r!, g!, b!, Math.round(surface.alphas[i]! * 255)], i * 4)
  const attributes = new GeometryAttributes()
  attributes.position = new GeometryAttribute({
    componentDatatype: ComponentDatatype.DOUBLE,
    componentsPerAttribute: 3,
    values: surface.positions,
  })
  attributes.color = new GeometryAttribute({
    componentDatatype: ComponentDatatype.UNSIGNED_BYTE,
    componentsPerAttribute: 4,
    normalize: true,
    values: rgba,
  })
  return new Geometry({
    attributes,
    indices: surface.indices,
    primitiveType: PrimitiveType.TRIANGLES,
    boundingSphere: BoundingSphere.fromVertices(Array.from(surface.positions)),
  })
}

/**
 * The 3D globe: Earth lit by the server's Sun, the station and its visibility cone, the satellites
 * in view, the orbits of tracked and selected ones and the selection's footprint. Imperative
 * CesiumJS (engine only: no Viewer widgets), driven like the 2D map by store subscriptions and a
 * ~20 fps frame loop. Presentation only: positions, in-view flags and crossings, link colours, cone
 * angles, the footprint radius and the Sun all come from the server.
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
    const globe = scene.globe
    scene.fog.enabled = false
    const failed = () => onErrorRef.current()
    scene.renderError.addEventListener(failed)

    // Day and night from the server's Sun (its direction is set every frame), never Cesium's own
    // wall-clock Sun: the night side is the shaded ground, and the sky atmosphere's glow at the limb
    // follows the same light. The shading holds at every zoom (by default Cesium fades it out as the
    // camera closes in). No ground atmosphere: from afar it paints the night black, close up it
    // washes it out, so the night would change with the zoom.
    const light = new DirectionalLight({ direction: new Cartesian3(-1, 0, 0) })
    scene.light = light
    globe.showGroundAtmosphere = false
    globe.dynamicAtmosphereLighting = true
    globe.dynamicAtmosphereLightingFromSun = false
    globe.lightingFadeOutDistance = 1
    globe.lightingFadeInDistance = 2
    let lit: boolean | null = null
    const setLit = (on: boolean) => {
      if (on === lit) return
      lit = on
      globe.enableLighting = on
    }
    setLit(false) // until a snapshot brings the Sun (an older server never does)

    let theme = currentResolvedTheme()
    let c = palette(theme)
    let frame = 0
    let lastFrameAt = 0
    const unsubscribers: (() => void)[] = []

    const orbits = scene.primitives.add(new PolylineCollection()) as PolylineCollection
    const lines = scene.primitives.add(new PolylineCollection()) as PolylineCollection
    const billboards = scene.primitives.add(new BillboardCollection()) as BillboardCollection
    const points = scene.primitives.add(new PointPrimitiveCollection({ blendOption: BlendOption.TRANSLUCENT })) as PointPrimitiveCollection
    const labels = scene.primitives.add(new LabelCollection()) as LabelCollection
    const satPoints = new Map<number, PointPrimitive>()
    const satLabels = new Map<number, Label>()
    let crossingMarks: PointPrimitive[] = []
    let cone: Primitive | null = null
    let coneFill: Primitive | null = null
    let coneFillShapes: ConeShape[] = []
    let coneToEnu = new Matrix4()
    let coneKey = ''
    let station: { position: Cartesian3; icon: Billboard; label: Label } | null = null
    let draftPoint: PointPrimitive | null = null
    const halo: Billboard = billboards.add({
      show: false,
      position: Cartesian3.ZERO,
      image: haloImage(),
      width: 30,
      height: 30,
      eyeOffset: new Cartesian3(0, 0, 50_000), // behind the dot it rings
    })
    const contactLine: Polyline = lines.add({ show: false, width: 1.5, positions: [Cartesian3.ZERO, Cartesian3.UNIT_X] })
    let contactColor: Color | null = null
    const footprint: Polyline = lines.add({ show: false, width: 1.5, positions: [Cartesian3.ZERO, Cartesian3.UNIT_X] })
    const terminator: Polyline = lines.add({ show: false, width: 1, positions: [Cartesian3.ZERO, Cartesian3.UNIT_X] })
    let terminatorAt: LonLat | null = null
    const hoverLabel: Label = labels.add({
      show: false,
      position: Cartesian3.ZERO,
      text: ' ',
      font: LABEL_FONT,
      outlineWidth: 3,
      style: LabelStyle.FILL_AND_OUTLINE,
      verticalOrigin: VerticalOrigin.BOTTOM,
      pixelOffset: new Cartesian2(0, -10),
    })
    let hoverId: number | null = null
    // Packets travelling the line of sight (slowed down to be seen, like the comms panel's strip).
    let lastPacketSeq: number | null = null
    const flights: { startedAt: number; up: boolean; point: PointPrimitive }[] = []
    const scratch = new Cartesian3()
    const cameraEnu = new Cartesian3()

    const applyTheme = () => {
      scene.backgroundColor = c.space
      const base = scene.imageryLayers.get(0)
      if (base) Object.assign(base, c.imagery)
      if (scene.skyAtmosphere) {
        scene.skyAtmosphere.saturationShift = c.atmosphere.saturation
        scene.skyAtmosphere.brightnessShift = c.atmosphere.brightness
      }
      halo.color = c.ink.withAlpha(0.35)
      footprint.material = flat(c.ink.withAlpha(0.6))
      terminator.material = flat(c.mask.withAlpha(0.5))
      hoverLabel.fillColor = c.ink
      hoverLabel.outlineColor = c.ground
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
      for (const mark of crossingMarks) points.remove(mark)
      crossingMarks = []
      const { tracks, selectedId, snapshot } = useMission.getState()
      const wanted = pinnedIds()
      for (const track of Object.values(tracks)) {
        if (!wanted.has(track.norad_id) || track.points.length < 2) continue
        const fallbackAlt = snapshot?.satellites.find((s) => s.norad_id === track.norad_id)?.alt_m ?? 0
        const selected = track.norad_id === selectedId
        // the selection's in-view arc is coloured by its link state (ink without RF data, as the
        // sky views draw its pass) and glows; other orbits step back while something is selected
        const dim = selectedId !== null && !selected ? DIM : 1
        for (const run of orbitRuns(track, fallbackAlt, selected)) {
          const positions = run.points.map(toCartesian)
          if (run.state === 'out') {
            orbits.add({ positions, width: selected ? 1.5 : 1, material: flat((selected ? c.ink : c.track).withAlpha(0.3 * dim)) })
          } else if (selected) {
            const color = run.state === 'in' ? c.ink : c.rag[run.state]
            orbits.add({
              positions,
              width: c.glowWidth,
              material: Material.fromType('PolylineGlow', { color, glowPower: 0.2, taperPower: 1 }),
            })
          } else {
            orbits.add({ positions, width: 1.75, material: flat(c.track.withAlpha(0.95 * dim)) })
          }
        }
        // where the selection's orbit enters and leaves the cone, as the sky views tick AOS / LOS
        if (selected) {
          for (const x of track.crossings ?? []) {
            crossingMarks.push(
              points.add({
                position: Cartesian3.fromDegrees(x.lon_deg, x.lat_deg, x.alt_m),
                pixelSize: 7,
                color: c.ground,
                outlineColor: c.ink,
                outlineWidth: 1.5,
              }),
            )
          }
        }
      }
      scene.requestRender()
    }

    const renderStation = () => {
      const { session } = useMission.getState()
      if (station) {
        billboards.remove(station.icon)
        labels.remove(station.label)
        station = null
      }
      if (session) {
        const position = Cartesian3.fromDegrees(session.lon_deg, session.lat_deg, session.alt_m)
        station = {
          position,
          icon: billboards.add({
            position,
            image: stationIcon(c.inkCss, c.groundCss),
            width: 26,
            height: 26,
            verticalOrigin: VerticalOrigin.BOTTOM, // the tower stands on the station
            eyeOffset: new Cartesian3(0, 0, -1_000), // ahead of the ground under it
          }),
          label: labels.add({
            position,
            text: session.location_name,
            font: LABEL_FONT,
            fillColor: c.ink,
            outlineColor: c.ground,
            outlineWidth: 3,
            style: LabelStyle.FILL_AND_OUTLINE,
            verticalOrigin: VerticalOrigin.TOP,
            pixelOffset: new Cartesian2(0, 6),
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

    const removeCone = () => {
      if (cone) scene.primitives.remove(cone)
      if (coneFill) scene.primitives.remove(coneFill)
      cone = coneFill = null
      coneFillShapes = []
    }

    /** The visibility cone over the station, sized to the selected satellite's height. */
    const renderCone = (snapshot: Snapshot | null, selected: SatelliteState | undefined) => {
      if (!station || !snapshot) {
        removeCone()
        coneKey = ''
        return
      }
      const mask = snapshot.observer.horizon_mask_deg
      const bands = selected?.link?.strong_link_elev_bands_deg ?? []
      // the Overhead filter's floor, as the server applies it (only drawn above the mask)
      const shown = snapshot.shown_min_elevation_deg ?? null
      const stationRadius = Cartesian3.magnitude(station.position)
      const shellAlt = selected ? selected.alt_m : DEFAULT_SHELL_ALT_M
      const key = `${mask}|${JSON.stringify(bands)}|${shown}|${Math.round(shellAlt / CONE_SHELL_STEP_M)}|${theme}`
      if (key === coneKey) return
      coneKey = key
      removeCone()
      const shapes = coneShapes(mask, bands, stationRadius, stationRadius + shellAlt, shown)
      if (shapes.length === 0) return
      const enu = FixedFrameTransforms.eastNorthUpToFixedFrame(station.position)
      coneToEnu = Matrix4.inverseTransformation(enu, new Matrix4())
      const colorOf = (s: ConeShape) => (s.kind === 'mask' ? c.mask : s.kind === 'shown' ? c.ink : c.rag.green)
      // The rim: where a satellite at this height enters (AOS) and leaves (LOS). A cylinder
      // outline with no vertical lines and a zero bottom radius is just that ring.
      const rim = (s: ConeShape) =>
        new GeometryInstance({
          geometry: new CylinderOutlineGeometry({
            length: s.heightM,
            topRadius: s.rimRadiusM,
            bottomRadius: 0,
            slices: 128,
            numberOfVerticalLines: 0,
          }),
          // apex at the station, rim up at the satellite's height
          modelMatrix: Matrix4.multiplyByTranslation(enu, new Cartesian3(0, 0, s.heightM / 2), new Matrix4()),
          attributes: {
            color: ColorGeometryInstanceAttribute.fromColor(colorOf(s).withAlpha(CONE_RIM_ALPHA[s.kind])),
          },
        })
      cone = scene.primitives.add(
        new Primitive({
          geometryInstances: shapes.map(rim),
          appearance: new PerInstanceColorAppearance({ flat: true, translucent: true }),
          asynchronous: false, // a handful of rings: build them now, on this thread
        }),
      ) as Primitive
      // The cone itself, fading upwards. Hidden while the camera is inside (see the frame loop):
      // a surface all around the camera, as a GEO cone's is, would tint the whole view; the rim
      // still shows where the cone ends.
      coneFill = scene.primitives.add(
        new Primitive({
          geometryInstances: shapes.map((s) => new GeometryInstance({ geometry: coneFillGeometry(s, colorOf(s)), modelMatrix: enu })),
          appearance: new PerInstanceColorAppearance({ flat: true, translucent: true, closed: false }),
          asynchronous: false,
        }),
      ) as Primitive
      coneFillShapes = shapes
    }

    /** The day/night line, redrawn when the subsolar point has moved enough to see. */
    const renderTerminator = (subsolar: LonLat) => {
      const last = terminatorAt
      if (last && Math.abs(wrapLon(subsolar[0] - last[0])) < TERMINATOR_STEP_DEG && Math.abs(subsolar[1] - last[1]) < TERMINATOR_STEP_DEG) return
      terminatorAt = subsolar
      terminator.positions = terminatorLine(subsolar, 2).map(([lon, lat]) => Cartesian3.fromDegrees(lon, lat, TERMINATOR_HEIGHT_M))
      terminator.show = true
    }

    const placeHover = () => {
      const point = hoverId !== null ? satPoints.get(hoverId) : undefined
      if (point) {
        hoverLabel.position = point.position
      } else {
        hoverId = null
        hoverLabel.show = false
      }
    }

    /** The name of a hovered dot that has no label of its own (only tracked and selected do). */
    const setHover = (id: number | null) => {
      const sat = id !== null && !pinnedIds().has(id) ? useMission.getState().snapshot?.satellites.find((s) => s.norad_id === id) : undefined
      if (sat?.norad_id === hoverId) return
      hoverId = sat ? sat.norad_id : null
      if (sat) {
        hoverLabel.text = sat.name
        hoverLabel.show = true
      }
      placeHover()
      scene.requestRender()
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
          contactLine.show = footprint.show = halo.show = false
          setHover(null)
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

      // The Sun, tweened along the server's rates between snapshots.
      if (snapshot.sun) {
        const subsolar = subsolarAt(snapshot.sun, clockSample.simMs, simMs)
        const [x, y, z] = unitVector(subsolar)
        light.direction = Cartesian3.fromElements(-x, -y, -z, light.direction)
        setLit(true)
        renderTerminator(subsolar)
      } else {
        setLit(false)
        terminator.show = false
        terminatorAt = null
      }

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
      let selectedLonLat: LonLat | null = null
      for (const sat of snapshot.satellites) {
        const isPinned = pinned.has(sat.norad_id)
        if (!fresh && !isPinned) continue // context dots move once per snapshot
        const track = isPinned ? tracks[sat.norad_id] : undefined
        const t = track && timed(track)
        const lonLat = (t && positionOnTrack(t, simMs)) ?? ([sat.lon_deg, sat.lat_deg] as LonLat)
        const alt = (t && altitudeOnTrack(t, simMs)) ?? sat.alt_m
        const position = Cartesian3.fromDegrees(lonLat[0], lonLat[1], alt)
        const isSelected = sat.norad_id === selectedId
        if (isSelected) {
          selectedPosition = position
          selectedLonLat = lonLat
        }

        let point = satPoints.get(sat.norad_id)
        if (!point) {
          point = points.add({ id: sat.norad_id })
          satPoints.set(sat.norad_id, point)
        }
        point.position = position
        // lock on the active contact: with a selection, everything else steps back
        const alpha = selectedId === null || isSelected ? 1 : isPinned ? 0.75 : 0.4
        if (fresh) {
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
              outlineWidth: 3,
              style: LabelStyle.FILL_AND_OUTLINE,
              verticalOrigin: VerticalOrigin.BOTTOM,
              pixelOffset: new Cartesian2(0, -10),
            })
            satLabels.set(sat.norad_id, label)
          }
          label.position = position
          if (fresh) {
            const labelAlpha = selectedId === null || isSelected ? 1 : 0.6
            label.fillColor = isSelected ? c.ink : c.label.withAlpha(labelAlpha)
            label.outlineColor = c.ground.withAlpha(labelAlpha)
          }
        }
      }
      if (fresh) {
        if (hoverId !== null && pinned.has(hoverId)) setHover(null) // it has its own label now
        placeHover()
      }

      // The selection: a soft halo, and its footprint on the ground (the server's radius).
      halo.show = selectedPosition !== null
      if (selectedPosition) halo.position = selectedPosition
      const radius = selected?.footprint_radius_deg
      if (selectedLonLat && radius) {
        footprint.positions = smallCircle(selectedLonLat, radius).map(([lon, lat]) => Cartesian3.fromDegrees(lon, lat, FOOTPRINT_HEIGHT_M))
        footprint.show = true
      } else {
        footprint.show = false
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
          contactLine.material = flat(color)
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
      if (coneFill) {
        Matrix4.multiplyByPoint(coneToEnu, scene.camera.positionWC, cameraEnu)
        const at = [cameraEnu.x, cameraEnu.y, cameraEnu.z] as const
        coneFill.show = !coneFillShapes.some((s) => insideCone(at, s))
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
      contactColor = null
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
      const ground = useUi.getState().panel === 'station' ? scene.camera.pickEllipsoid(e.position, globe.ellipsoid) : undefined
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
      const id = pickedId(e.endPosition)
      setHover(id)
      widget.canvas.style.cursor = id !== null ? 'pointer' : useUi.getState().panel === 'station' ? 'crosshair' : ''
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
      <GlobeLegend />
    </div>
  )
}

/** What the cones, the footprint and the selection's orbit colours mean. The inner cone and the
 *  link-coloured orbit only exist for a selected satellite with a link budget; the shown cone only
 *  while the Overhead filter's minimum elevation is above the mask. */
function GlobeLegend() {
  const mask = useMission((s) => s.snapshot?.observer.horizon_mask_deg)
  const shown = useMission((s) => s.snapshot?.shown_min_elevation_deg ?? null)
  const selected = (s: ReturnType<typeof useMission.getState>) => s.snapshot?.satellites.find((x) => x.norad_id === s.selectedId)
  const strong = useMission((s) => (selected(s)?.link?.strong_link_elev_bands_deg.length ?? 0) > 0)
  const linked = useMission((s) => Boolean(selected(s)?.link))
  const footprint = useMission((s) => Boolean(selected(s)?.footprint_radius_deg))
  if (mask === undefined) return null
  return (
    <div className="pointer-events-none absolute right-3 bottom-3 max-w-[17rem] space-y-1 rounded-lg bg-white/75 px-2.5 py-1.5 text-xs text-gray-700 backdrop-blur dark:bg-dark/75 dark:text-gray-300">
      <div className="flex items-center gap-2">
        <span className="size-2.5 shrink-0 rounded-full ring-2 ring-gray-400 dark:ring-gray-500" aria-hidden />
        In view: above the {mask}° horizon mask
      </div>
      {shown !== null && shown > mask && (
        <div className="flex items-center gap-2">
          <span className="size-2.5 shrink-0 rounded-full ring-2 ring-gray-900/60 dark:ring-gray-100/60" aria-hidden />
          Shown: above {shown}° (Overhead filter)
        </div>
      )}
      {strong && (
        <div className="flex items-center gap-2">
          <span className="size-2.5 shrink-0 rounded-full ring-2 ring-primary-600 dark:ring-primary-400" aria-hidden />
          Strong link: green margin for the selection
        </div>
      )}
      {footprint && (
        <div className="flex items-center gap-2">
          <span className="size-2.5 shrink-0 rounded-full ring-1 ring-gray-900/60 dark:ring-gray-100/60" aria-hidden />
          Footprint: where the selection is above the mask
        </div>
      )}
      {linked && (
        <div className="flex items-center gap-2">
          <span className="flex shrink-0 gap-px" aria-hidden>
            <span className="h-1 w-1.5 rounded-full bg-primary-600 dark:bg-primary-400" />
            <span className="h-1 w-1.5 rounded-full bg-amber-700 dark:bg-amber-400" />
            <span className="h-1 w-1.5 rounded-full bg-red-700 dark:bg-red-400" />
          </span>
          Orbit in view: the selection’s link margin
        </div>
      )}
      <div className="text-gray-500 dark:text-gray-400">True positions, not what the eye can see. Night from the computed Sun.</div>
    </div>
  )
}
