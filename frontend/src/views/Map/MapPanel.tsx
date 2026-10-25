import { Globe as GlobeIcon, Map as MapIcon } from 'lucide-react'
import { Component, lazy, Suspense, useCallback, useState, type ReactNode } from 'react'
import { Button } from '../../components/ui/Button'
import { setMapMode, supportsWebGL, useMapMode, type MapMode } from '../../lib/mapMode'
import { useMission } from '../../store/mission'
import { GroundTrackMap } from '../GroundTrack/GroundTrackMap'

// The globe and all of Cesium load only when 3D is shown.
const Globe = lazy(() => import('../Globe/Globe').then((m) => ({ default: m.Globe })))

/** Falls back to 2D if the globe's chunk can't load or it throws while rendering. */
class GlobeBoundary extends Component<{ onError: () => void; children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  componentDidCatch() {
    this.props.onError()
  }
  render() {
    return this.state.failed ? null : this.props.children
  }
}

const OPTIONS: { mode: MapMode; label: string; Icon: typeof GlobeIcon }[] = [
  { mode: '3d', label: '3D', Icon: GlobeIcon },
  { mode: '2d', label: '2D', Icon: MapIcon },
]

/**
 * The map slot: the 3D globe (default) or the 2D ground-track map, switched by a toggle and
 * remembered per browser. Both draw the same streamed state, selection and filters, so switching
 * never changes what is shown. Without WebGL, or if the globe fails, the 2D map takes over.
 */
export function MapPanel() {
  const preference = useMapMode()
  const [webgl] = useState(supportsWebGL)
  const [globeFailed, setGlobeFailed] = useState(false)
  const can3d = webgl && !globeFailed
  const mode: MapMode = preference === '3d' && can3d ? '3d' : '2d'

  const onGlobeError = useCallback(() => {
    setGlobeFailed((already) => {
      if (!already) useMission.getState().notify('The 3D globe could not start in this browser; showing the 2D map.')
      return true
    })
  }, [])

  return (
    <div className="relative h-full min-h-[300px] w-full">
      {mode === '3d' ? (
        <GlobeBoundary onError={onGlobeError}>
          <Suspense
            fallback={
              <div className="grid h-full place-items-center rounded-2xl border border-gray-200 text-sm text-gray-500 dark:border-gray-800 dark:text-gray-400">
                Loading the globe…
              </div>
            }
          >
            <Globe onError={onGlobeError} />
          </Suspense>
        </GlobeBoundary>
      ) : (
        <GroundTrackMap />
      )}
      <div
        role="group"
        aria-label="Map view"
        // top-right: the mission panels open over the top-left corner
        className="absolute top-3 right-3 z-10 flex gap-1 rounded-lg bg-white/75 p-1 backdrop-blur dark:bg-dark/75"
      >
        {OPTIONS.map(({ mode: option, label, Icon }) => {
          const unavailable = option === '3d' && !can3d
          return (
            <Button
              key={option}
              active={mode === option}
              aria-pressed={mode === option}
              disabled={unavailable}
              title={unavailable ? (webgl ? 'The 3D globe failed to start' : '3D needs WebGL') : `${label} view`}
              onClick={() => setMapMode(option)}
            >
              <Icon size={16} strokeWidth={1.5} />
              {label}
            </Button>
          )
        })}
      </div>
    </div>
  )
}
