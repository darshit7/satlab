import { useEffect } from 'react'
import { cueFor } from '../../lib/audio'
import { PACKET_FLIGHT_MS } from '../../lib/comms'
import { useMission } from '../../store/mission'
import { useUi } from '../../store/ui'
import { commsSound } from './commsSound'

/** Cues that arrive together are spread this far apart (s), so each can be heard. */
const STAGGER_S = 0.25

/**
 * While the sound is on, the selection's command traffic plays its cues: a command as its dot
 * leaves the station, an answer as its dot lands back. Imperative like the globe: a store
 * subscription, no renders.
 */
export function useCommsSound(): void {
  const sound = useUi((s) => s.sound)

  useEffect(() => {
    const engine = sound ? commsSound() : null
    if (!engine) return
    // start from the newest packet: turning the sound on doesn't replay the log
    let lastSeq = useMission.getState().packets.at(-1)?.seq ?? 0

    const unsubscribe = useMission.subscribe((s, prev) => {
      if (s.packets === prev.packets) return
      let stagger = 0
      for (const packet of s.packets) {
        if (packet.seq <= lastSeq || packet.norad_id !== s.selectedId) continue
        const cue = cueFor(packet)
        if (!cue) continue
        engine.play(cue, (cue === 'sent' ? 0 : PACKET_FLIGHT_MS / 1000) + stagger)
        stagger += STAGGER_S
      }
      lastSeq = Math.max(lastSeq, s.packets.at(-1)?.seq ?? 0)
    })
    return unsubscribe
  }, [sound])
}
