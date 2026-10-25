import { CalendarClock, Info, X } from 'lucide-react'
import { useMemo } from 'react'
import { Button } from '../../components/ui/Button'
import { cn } from '../../components/ui/cn'
import { RagIcon } from '../../components/ui/Rag'
import { RAG_TEXT, ragKey } from '../../components/ui/ragClasses'
import {
  bookedOutcomes,
  checkSummary,
  scheduledCommands,
  SEVERITY_RAG,
  SEVERITY_WORDS,
  sortAlarms,
  utcWhen,
} from '../../lib/schedule'
import { formatCountdown, formatLocalTime, formatUtcTime } from '../../lib/time'
import { useSimNow } from '../../lib/useSimNow'
import { useMission } from '../../store/mission'
import type { AlarmMessage, CommandStatus, CommandStatusMessage, Rag } from '../../types/ws'

const muted = 'text-gray-500 dark:text-gray-400'
const heading = 'mt-3 mb-1 flex items-baseline justify-between gap-2 text-sm font-semibold first:mt-0'
const OUTCOME_RAG: Partial<Record<CommandStatus, Rag>> = { acked: 'green', held: 'red', failed: 'red', aborted: 'amber' }

/** Satellite names and command labels from the packet templates (reference data). */
function useNames() {
  const templates = useMission((s) => s.packetTemplates)
  return useMemo(() => {
    const satellites = new Map(templates?.satellites.map((s) => [s.norad_id, s]) ?? [])
    return {
      satellite: (id: number | null) => (id === null ? null : (satellites.get(id)?.satellite_name ?? `#${id}`)),
      command: (id: number, name: string) => satellites.get(id)?.commands.find((c) => c.name === name)?.label ?? name,
    }
  }, [templates])
}

function AlarmRow({ alarm, satellite }: { alarm: AlarmMessage; satellite: string | null }) {
  const ackAlarm = useMission((s) => s.ackAlarm)
  const rag = SEVERITY_RAG[alarm.severity]
  return (
    <li className={cn('py-2', alarm.acknowledged && 'opacity-60')}>
      <div className="flex items-center gap-2 text-sm">
        <span className={cn('flex items-center gap-1 font-semibold', RAG_TEXT[ragKey(rag)])}>
          {rag ? <RagIcon rag={rag} size={14} /> : <Info size={14} strokeWidth={1.75} aria-hidden />}
          {SEVERITY_WORDS[alarm.severity]}
        </span>
        <span className="font-mono text-xs tabular-nums">{formatUtcTime(Date.parse(alarm.sim_time))} UTC</span>
        {satellite && <span className={cn('min-w-0 flex-1 truncate text-xs', muted)}>{satellite}</span>}
        {alarm.acknowledged ? (
          <span className={cn('ml-auto text-xs', muted)}>acknowledged</span>
        ) : (
          <Button className="ml-auto px-2 py-0.5 text-xs" onClick={() => ackAlarm(alarm.alarm_id)}>
            Acknowledge
          </Button>
        )}
      </div>
      <p className="mt-0.5 text-sm">{alarm.message}</p>
    </li>
  )
}

function WaitingRow({ command, satellite, label, simMs }: { command: CommandStatusMessage; satellite: string; label: string; simMs: number | null }) {
  const cancelCommand = useMission((s) => s.cancelCommand)
  const booked = command.scheduled_pass!
  const aos = Date.parse(booked.aos_time)
  return (
    <li className="flex items-start gap-2 py-2 text-sm">
      <CalendarClock size={16} strokeWidth={1.5} className={cn('mt-0.5 shrink-0', muted)} aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="truncate">
          <span className="font-semibold">{label}</span> <span className={muted}>·</span> {satellite}
        </div>
        <div className={cn('text-xs', muted)}>
          AOS <span className="font-mono whitespace-nowrap tabular-nums">{simMs !== null ? utcWhen(aos, simMs) : formatUtcTime(aos)}</span> (
          {formatLocalTime(aos)} local) · max <span className="font-mono tabular-nums">{booked.max_elev_deg.toFixed(0)}°</span>
          {simMs !== null && (
            <>
              {' '}
              · in <span className="font-mono whitespace-nowrap tabular-nums">{formatCountdown(aos - simMs)}</span>
            </>
          )}
        </div>
      </div>
      <Button variant="icon" aria-label={`Cancel ${label}`} title="Cancel" onClick={() => cancelCommand(command.command_id)}>
        <X size={16} strokeWidth={1.5} />
      </Button>
    </li>
  )
}

function OutcomeRow({ command, satellite, label }: { command: CommandStatusMessage; satellite: string; label: string }) {
  const rag = OUTCOME_RAG[command.status]
  const detail = command.check && command.status !== 'aborted' ? checkSummary(command.check) : command.reason
  return (
    <li className="py-1.5 text-sm">
      <div className="flex items-center gap-2">
        <span className={cn('flex items-center gap-1 text-xs font-semibold', RAG_TEXT[ragKey(rag)])}>
          {rag ? <RagIcon rag={rag} size={12} /> : null}
          {command.status}
        </span>
        <span className="min-w-0 flex-1 truncate">
          <span className="font-semibold">{label}</span> <span className={muted}>·</span> {satellite}
        </span>
        <span className="font-mono text-xs tabular-nums">{formatUtcTime(Date.parse(command.sim_time))}</span>
      </div>
      {detail && <p className={cn('truncate text-xs', muted)} title={detail}>{detail}</p>}
    </li>
  )
}

/**
 * Pass automation at a glance: alarms to acknowledge, commands waiting for their pass (cancel), and
 * what became of the booked ones. Booking happens in Communications; every decision here is the
 * server's.
 */
export function SchedulerPanel() {
  const commands = useMission((s) => s.commands)
  const alarms = useMission((s) => s.alarms)
  const simMs = useSimNow(1000)
  const names = useNames()

  const sortedAlarms = useMemo(() => sortAlarms(alarms), [alarms])
  const waiting = useMemo(() => scheduledCommands(commands), [commands])
  const outcomes = useMemo(() => bookedOutcomes(commands), [commands])
  const divided = 'divide-y divide-gray-200 dark:divide-gray-800'

  return (
    <div>
      <h3 className={heading}>Alarms</h3>
      {sortedAlarms.length > 0 ? (
        <ul className={divided} aria-label="Alarms">
          {sortedAlarms.map((a) => (
            <AlarmRow key={a.alarm_id} alarm={a} satellite={names.satellite(a.norad_id)} />
          ))}
        </ul>
      ) : (
        <p className={cn('text-sm', muted)}>No alarms.</p>
      )}

      <h3 className={heading}>Waiting for a pass</h3>
      {waiting.length > 0 ? (
        <ul className={divided} aria-label="Commands waiting for a pass">
          {waiting.map((c) => (
            <WaitingRow
              key={c.command_id}
              command={c}
              satellite={names.satellite(c.norad_id)!}
              label={names.command(c.norad_id, c.command)}
              simMs={simMs}
            />
          ))}
        </ul>
      ) : (
        <p className={cn('text-sm', muted)}>
          Nothing booked. To book a command, select a satellite and, in Communications, pick one of its passes under
          When.
        </p>
      )}

      {outcomes.length > 0 && (
        <>
          <h3 className={heading}>Recent</h3>
          <ul className={divided} aria-label="Booked commands">
            {outcomes.map((c) => (
              <OutcomeRow key={c.command_id} command={c} satellite={names.satellite(c.norad_id)!} label={names.command(c.norad_id, c.command)} />
            ))}
          </ul>
        </>
      )}

      <p className={cn('mt-3 text-xs', muted)}>
        A minute before AOS the antenna turns to the satellite (and a clock faster than 60× drops to real time). At AOS
        the system check sends the commands unless a subsystem is red; then they’re held and an alarm is raised. Uplink
        is simulated: nothing is transmitted.
      </p>
    </div>
  )
}
