import { useEffect } from 'react'
import { ClockBar } from './components/ClockBar'
import { Notices } from './components/Notices'
import { TiltedGridBackground } from './components/ui/TiltedGridBackground'
import { useMission } from './store/mission'
import { AllSky } from './views/AllSky/AllSky'
import { CatalogSearch } from './views/Catalog/CatalogSearch'
import { GroundTrackMap } from './views/GroundTrack/GroundTrackMap'
import { LocationPanel } from './views/Location/LocationPanel'
import { PassSchedule } from './views/Passes/PassSchedule'
import { Readout } from './views/Readout/Readout'
import { SkyPlot } from './views/SkyPlot/SkyPlot'
import { VisibleList } from './views/Visible/VisibleList'

let booted = false

export default function App() {
  useEffect(() => {
    if (booted) return // StrictMode runs effects twice in dev
    booted = true
    const { loadStations } = useMission.getState()
    void loadStations().then(() => {
      // Resume at the last-used station, if it still exists.
      const { stations, stationId, startSession } = useMission.getState()
      if (stationId !== null && stations.some((s) => s.id === stationId)) void startSession(stationId)
    })
  }, [])

  return (
    <div className="relative isolate flex min-h-dvh flex-col gap-4 px-4 pt-2 pb-4 sm:px-6 lg:h-dvh lg:pt-3 xl:px-12">
      <TiltedGridBackground className="bottom-auto h-[50vh]" />
      <ClockBar />
      <div className="flex min-h-0 flex-1 flex-col gap-4 lg:grid lg:grid-cols-[22.5rem_minmax(0,1fr)]">
        <main className="order-first flex min-h-0 min-w-0 flex-col gap-4 lg:order-last lg:overflow-y-auto">
          {/* map + sky rail: side by side on xl, sky views paired under the map below that */}
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(17rem,22rem)]">
            <div className="grid h-[55vh] min-h-[22rem] xl:h-auto">
              <GroundTrackMap />
            </div>
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-1">
              <SkyPlot />
              <AllSky />
            </div>
          </div>
          <div className="grid gap-4 2xl:grid-cols-2 2xl:items-start">
            <Readout />
            <PassSchedule />
          </div>
        </main>
        <aside className="flex min-h-0 flex-col gap-4 lg:overflow-y-auto">
          <LocationPanel />
          <VisibleList />
          <CatalogSearch />
        </aside>
      </div>
      <footer className="text-sm text-gray-600 dark:text-gray-400">
        Positions and passes are computed server-side with SGP4 from real Celestrak TLEs, for your real location.
        “In view” means above the horizon mask, geometrically: most satellites shown are not visible to the naked
        eye. Accuracy degrades as a TLE ages.
      </footer>
      <Notices />
    </div>
  )
}
