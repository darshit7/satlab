import { useEffect } from 'react'
import { ClockBar } from './components/ClockBar'
import { Notices } from './components/Notices'
import { TiltedGridBackground } from './components/ui/TiltedGridBackground'
import { useMission } from './store/mission'
import { useUi } from './store/ui'
import { Inspector } from './views/Inspector/Inspector'
import { MapPanel } from './views/Map/MapPanel'
import { MissionBar, MissionPanels } from './views/Mission/MissionBar'
import { PassScheduleDialog } from './views/Passes/PassSchedule'
import { SkyDialog } from './views/Sky/SkyDock'

let booted = false

export default function App() {
  useEffect(() => {
    if (booted) return // StrictMode runs effects twice in dev
    booted = true
    const { loadStations, loadLinkProfiles, loadPacketTemplates } = useMission.getState()
    void loadLinkProfiles()
    void loadPacketTemplates()
    void loadStations().then(() => {
      // Resume where the last session observed from: a custom location, or a preset that still exists.
      const { stations, site, startSession } = useMission.getState()
      if (site && ('location' in site || stations.some((s) => s.id === site.station_id))) void startSession(site)
      else useUi.getState().openPanel('station') // first visit: start by choosing where to observe from
    })
  }, [])

  return (
    <div className="relative isolate flex min-h-dvh flex-col gap-4 px-4 pt-2 pb-4 sm:px-6 lg:h-dvh lg:pt-3 xl:px-12">
      <TiltedGridBackground className="bottom-auto h-[50vh]" />
      <ClockBar />
      {/* the map is the stage; the inspector beside it (below it on narrow screens) scrolls on its own */}
      <div className="flex min-h-0 flex-1 flex-col gap-4 lg:grid lg:grid-cols-[minmax(0,1fr)_24rem] xl:grid-cols-[minmax(0,1fr)_26rem] 2xl:grid-cols-[minmax(0,1fr)_28rem]">
        <main className="flex min-h-0 min-w-0 flex-col gap-3">
          <MissionBar />
          {/* positioned: the mission panels float over the map's top-left corner */}
          <div className="relative grid h-[60vh] min-h-[22rem] lg:h-auto lg:flex-1">
            <MapPanel />
            <MissionPanels />
          </div>
        </main>
        <aside aria-label="Inspector" className="@container flex min-h-0 min-w-0 flex-col gap-4 lg:overflow-y-auto">
          <Inspector />
        </aside>
      </div>
      <Notices />
      <SkyDialog />
      <PassScheduleDialog />
    </div>
  )
}
