import { InfoPopover } from './ui/InfoPopover'

/**
 * What the numbers are, and aren't: real orbits and published link budgets, modelled subsystems,
 * a simulated uplink. One click away in the header rather than taking a footer's worth of screen.
 *
 * The panel is positioned against the nearest positioned ancestor, which the header makes its
 * right-hand icon group: aligned with the header's edge, it stays on screen at 400px wide.
 */
export function AboutData() {
  return (
    <InfoPopover
      label="About the data"
      buttonClassName="p-1.5"
      panelClassName="right-0 mt-2 max-h-[70vh] w-[min(28rem,calc(100vw-4rem))] origin-top-right"
    >
      <h2 className="mb-2 font-semibold">About the data</h2>
      <div className="space-y-2 text-gray-700 dark:text-gray-300">
        <p>
          <span className="font-semibold text-gray-900 dark:text-gray-100">Positions. </span>
          Positions and passes are computed server-side with SGP4 from real Celestrak TLEs, for your real location.
          “In view” means above the horizon mask, geometrically: most satellites shown are not visible to the naked
          eye. Accuracy degrades as a TLE ages.
        </p>
        <p>
          <span className="font-semibold text-gray-900 dark:text-gray-100">The globe. </span>
          Its imagery is Natural Earth II; its cone shows where a satellite is above the mask (and, for the
          selection, where its link margin is green), not what you could see.
        </p>
        <p>
          <span className="font-semibold text-gray-900 dark:text-gray-100">Links and systems. </span>
          Link margins use each satellite’s published RF budget and that source’s reference receiving station;
          satellites without one show “no RF data”. Power, thermal, storage and attitude are modelled from generic
          parameters, driven by real eclipse and pass geometry.
        </p>
        <p>
          <span className="font-semibold text-gray-900 dark:text-gray-100">Commands and telemetry. </span>
          Nothing here commands a real satellite: uplink is simulated and never transmitted. Downlink is either
          simulated from those models or real frames received by SatNOGS stations, replayed during your pass.
        </p>
        <p>
          <span className="font-semibold text-gray-900 dark:text-gray-100">Pass automation. </span>
          Commands can be booked for a satellite’s real predicted passes. At AOS the station checks the modelled systems
          and the link, and sends them unless one is red. The schedule, the check and the alarms are simulated, and
          nothing is transmitted.
        </p>
      </div>
    </InfoPopover>
  )
}
