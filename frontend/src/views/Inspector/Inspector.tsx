import { Card } from '../../components/ui/Card'
import { Emoji } from '../../components/ui/Emoji'
import { useMission } from '../../store/mission'
import { TrackedChips } from '../Catalog/TrackedChips'
import { CommsPanel } from '../Comms/CommsPanel'
import { Indicators } from '../Indicators/Indicators'
import { NextPass } from '../Passes/PassSchedule'
import { ReadoutMetrics, SelectionHeader } from '../Readout/Readout'
import { SkyDock } from '../Sky/SkyDock'

const strong = 'font-semibold text-gray-900 dark:text-gray-100'

/**
 * The right-hand column: the sky over the station, then the selected satellite — a sticky header
 * with its go/no-go, its live readout and next pass, then Systems and Comms. Each section is a
 * direct child of the scrolling column, so the header stays stuck however far it scrolls.
 */
export function Inspector() {
  const selectedId = useMission((s) => s.selectedId)
  const selected = useMission((s) => s.snapshot?.satellites.find((x) => x.norad_id === s.selectedId))

  return (
    <>
      <SkyDock />
      {selected ? (
        <>
          <SelectionHeader sat={selected} />
          <Card as="div" dense>
            <ReadoutMetrics sat={selected} />
            <NextPass />
          </Card>
          <Indicators />
          <CommsPanel />
        </>
      ) : (
        <Card as="div" dense>
          <p className="text-sm text-gray-600 dark:text-gray-400">
            {selectedId !== null ? (
              'Waiting for data…'
            ) : (
              <>
                Nothing selected <Emoji name="satellite" label="satellite" /> — pick a dot on the map or in the sky
                views, a row in <span className={strong}>Overhead</span>, or <span className={strong}>Track</span> a
                satellite from the <span className={strong}>Catalog</span>.
              </>
            )}
          </p>
          <TrackedChips />
        </Card>
      )}
    </>
  )
}
