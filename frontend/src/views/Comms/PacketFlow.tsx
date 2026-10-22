import { RadioTower, Satellite } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { cn } from '../../components/ui/cn'
import { RAG_BG } from '../../components/ui/ragClasses'
import { PACKET_FLIGHT_MS, packetOutcome } from '../../lib/comms'
import type { PacketMessage } from '../../types/ws'

const MAX_FLIGHTS = 8

interface Flight {
  seq: number
  up: boolean
  color: string
}

function flightColor(packet: PacketMessage): string {
  switch (packetOutcome(packet)) {
    case 'sent':
      return 'bg-gray-900 dark:bg-gray-100'
    case 'ok':
      return RAG_BG.green
    case 'corrupted':
      return RAG_BG.amber
    case 'lost':
      return RAG_BG.red
  }
}

/**
 * Packets crossing between the station and the satellite as they happen: up (ink) for commands,
 * down coloured by what arrived (intact, corrupted, lost). Works without the globe (2D mode too).
 */
export function PacketFlow({ packets, active, lightTime }: { packets: PacketMessage[]; active: boolean; lightTime: string }) {
  const [flights, setFlights] = useState<Flight[]>([])
  const lastSeq = useRef<number | null>(null)

  useEffect(() => {
    const latest = packets.at(-1)?.seq ?? 0
    const since = lastSeq.current
    lastSeq.current = Math.max(latest, since ?? 0)
    if (since === null) return // the history already shown in the log isn't replayed as flights
    const fresh = packets.filter((p) => p.seq > since).slice(-MAX_FLIGHTS)
    if (fresh.length === 0) return
    setFlights((current) =>
      [...current, ...fresh.map((p) => ({ seq: p.seq, up: p.direction === 'up', color: flightColor(p) }))].slice(-MAX_FLIGHTS),
    )
  }, [packets])

  const land = (seq: number) => setFlights((current) => current.filter((f) => f.seq !== seq))

  return (
    <div>
      <div className="flex items-center gap-3">
        <RadioTower size={18} strokeWidth={1.5} className="shrink-0" aria-label="Ground station" />
        <div className="relative h-4 flex-1" aria-hidden>
          <div
            className={cn(
              'absolute top-1/2 w-full border-t',
              active ? 'border-gray-400 dark:border-gray-500' : 'border-dashed border-gray-300 dark:border-gray-700',
            )}
          />
          {flights.map((f) => (
            <span
              key={f.seq}
              className={cn('packet-flight absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full', f.color)}
              style={{ animation: `${f.up ? 'packet-up' : 'packet-down'} ${PACKET_FLIGHT_MS}ms linear forwards` }}
              onAnimationEnd={() => land(f.seq)}
            />
          ))}
        </div>
        <Satellite size={18} strokeWidth={1.5} className="shrink-0" aria-label="Satellite" />
      </div>
      <p className="mt-1 text-center text-xs text-gray-500 dark:text-gray-400">
        One-way light time {lightTime} · shown slowed down
      </p>
    </div>
  )
}
