import { useEffect } from 'react'
import { ClockBar } from './components/ClockBar'
import { Notices } from './components/Notices'
import { TiltedGridBackground } from './components/ui/TiltedGridBackground'
import { useMission } from './store/mission'
import { AllSky } from './views/AllSky/AllSky'
import { CatalogSearch } from './views/Catalog/CatalogSearch'
import { GroundTrackMap } from './views/GroundTrack/GroundTrackMap'
import { Indicators } from './views/Indicators/Indicators'
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
    const { loadStations, loadLinkProfiles } = useMission.getState()
    void loadLinkProfiles()
    void loadStations().then(() => {
      // Resume where the last session observed from: a custom location, or a preset that still exists.
      const { stations, site, startSession } = useMission.getState()
      if (site && ('location' in site || stations.some((s) => s.id === site.station_id))) void startSession(site)
    })
  }, [])

  return (
    <div className="relative isolate flex min-h-dvh flex-col gap-4 px-4 pt-2 pb-4 sm:px-6 lg:h-dvh lg:pt-3 xl:px-12">
      <TiltedGridBackground className="bottom-auto h-[50vh]" />
      <ClockBar />
      <div className="flex min-h-0 flex-1 flex-col gap-4 lg:grid lg:grid-cols-[22.5rem_minmax(0,1fr)]">
        <main className="order-first flex min-h-0 min-w-0 flex-col gap-4 lg:order-last lg:overflow-y-auto">
          <Readout />
          <Indicators />
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
          <PassSchedule />
        </main>
        <aside className="flex min-h-0 flex-col gap-4 lg:overflow-y-auto">
          <CatalogSearch />
          <LocationPanel />
          <VisibleList />
        </aside>
      </div>
      <footer className="space-y-1 text-sm text-gray-600 dark:text-gray-400">
        <p>
          Positions and passes are computed server-side with SGP4 from real Celestrak TLEs, for your real location.
          “In view” means above the horizon mask, geometrically: most satellites shown are not visible to the naked
          eye. Accuracy degrades as a TLE ages.
        </p>
        <p>
          Link margins use each satellite’s published RF budget and that source’s reference receiving station;
          satellites without one show “no RF data”. Power, thermal, storage and attitude are modelled from generic
          parameters, driven by real eclipse and pass geometry. Nothing here commands a real satellite.
        </p>
      </footer>
      <Notices />
    </div>
  )
}
