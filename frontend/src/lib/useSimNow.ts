import { useEffect, useState } from 'react'
import { useMission } from '../store/mission'
import { estimateSimMs } from './time'

/** Re-render every `intervalMs` with the sim time advanced smoothly since the last snapshot. */
export function useSimNow(intervalMs = 100): number | null {
  const clockSample = useMission((s) => s.clockSample)
  const [now, setNow] = useState(() => performance.now())
  useEffect(() => {
    const id = setInterval(() => setNow(performance.now()), intervalMs)
    return () => clearInterval(id)
  }, [intervalMs])
  return clockSample ? estimateSimMs(clockSample, now) : null
}
