import { ArrowDown, ArrowUp, CalendarClock, Radio, Send, Volume2, VolumeX } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Button, focusRing } from '../../components/ui/Button'
import { Card } from '../../components/ui/Card'
import { cn } from '../../components/ui/cn'
import { inputClass } from '../../components/ui/Field'
import { InfoPopover } from '../../components/ui/InfoPopover'
import { RagIcon } from '../../components/ui/Rag'
import { RAG_TEXT } from '../../components/ui/ragClasses'
import { Select } from '../../components/ui/Select'
import { trackOnce } from '../../lib/analytics'
import {
  formatBer,
  formatLightTime,
  formatValue,
  latestDecoded,
  OUTCOME_WORDS,
  packetOutcome,
  parseParam,
  satnogsObservationUrl,
  type PacketOutcome,
} from '../../lib/comms'
import { formatDopplerKHz, safeHttpsUrl } from '../../lib/rag'
import { checkSummary, passLabel, schedulablePasses, utcWhen } from '../../lib/schedule'
import { useSimNow } from '../../lib/useSimNow'
import { useMission } from '../../store/mission'
import { useUi } from '../../store/ui'
import type { CommandTemplate, LinkSource } from '../../types/api'
import type { CommandStatus, ContactState, PacketMessage, Rag } from '../../types/ws'
import { CommsSatellites } from './CommsSatellites'
import { startCommsSound } from './commsSound'
import { PacketFlow } from './PacketFlow'
import { useCommsSound } from './useCommsSound'

const LOG_ROWS = 20
const muted = 'text-gray-500 dark:text-gray-400'

const OUTCOME_RAG: Record<PacketOutcome, Rag | null> = { sent: null, ok: 'green', corrupted: 'amber', lost: 'red' }
const STATUS_RAG: Record<CommandStatus, Rag | null> = {
  scheduled: null,
  queued: null,
  sent: null,
  acked: 'green',
  failed: 'red',
  aborted: 'amber',
  held: 'red',
}
const NOW = 'now'

const hms = (iso: string) => new Date(iso).toISOString().slice(11, 19)

function contactWords(contact: ContactState): { title: string; active: boolean } {
  switch (contact.state) {
    case 'active':
      return { title: 'In contact', active: true }
    case 'below_mask':
      return { title: 'Not in view: contact starts at AOS', active: false }
    case 'too_fast':
      return { title: 'Clock too fast for communications: slow it down', active: false }
    case 'no_template':
      return { title: 'No communications with this satellite', active: false }
  }
}

/** What the downlink is, said plainly (design §1.3: uplink simulated, downlink real where it can be). */
function downlinkLine(contact: ContactState): string | null {
  if (contact.downlink === 'simulated') {
    return contact.channel_modelled
      ? 'Downlink: simulated housekeeping from the modelled subsystems, in an illustrative layout; bit errors follow the published link margin.'
      : 'Downlink: simulated housekeeping from the modelled subsystems, in an illustrative layout.'
  }
  if (contact.downlink === 'satnogs') {
    switch (contact.archive) {
      case 'ok':
        return 'Downlink: real frames received by SatNOGS stations, replayed during this pass. No RF data, so the channel isn’t modelled.'
      case 'not_configured':
        return 'Downlink: real SatNOGS frames, but this server has no SatNOGS access configured, so nothing comes down.'
      case 'unavailable':
        return 'Downlink: real SatNOGS frames, but the archive can’t be reached right now.'
      default:
        return 'Downlink: real SatNOGS frames, but none usable have been fetched yet.'
    }
  }
  return null
}

/**
 * Compose a command and send it now (in contact) or book it for one of the satellite's passes from
 * the server's 24-hour schedule (pass automation sends it at AOS unless a subsystem is red).
 */
function Composer({ noradId, commands, enabled }: { noradId: number; commands: CommandTemplate[]; enabled: boolean }) {
  const sendCommand = useMission((s) => s.sendCommand)
  const scheduleCommand = useMission((s) => s.scheduleCommand)
  const prediction = useMission((s) => s.passes?.data)
  const simMs = useSimNow(5000)
  const [name, setName] = useState(commands[0]?.name ?? '')
  const [raw, setRaw] = useState<Record<string, string>>({})
  const [chosen, setChosen] = useState<string | null>(null)
  const template = commands.find((c) => c.name === name) ?? commands[0]
  if (!template) return null

  const passes = simMs !== null ? schedulablePasses(prediction, noradId, simMs) : []
  const whenOptions = [
    { value: NOW, label: enabled ? 'Now' : 'Now (needs contact)' },
    ...passes.map((p, i) => ({ value: p.aos_time, label: passLabel(p, simMs ?? 0, i === 0) })),
  ]
  // A pass that has started drops out of the list: fall back to the default.
  const fallback = enabled || passes.length === 0 ? NOW : passes[0]!.aos_time
  const when = chosen !== null && whenOptions.some((o) => o.value === chosen) ? chosen : fallback
  const booking = when !== NOW

  const parsed = template.params.map((p) => [p, parseParam(p, raw[p.name] ?? '')] as const)
  const valid = parsed.every(([, r]) => 'value' in r)
  const send = () => {
    if (!valid || (!booking && !enabled)) return
    const params = Object.fromEntries(parsed.map(([p, r]) => [p.name, 'value' in r ? r.value : '']))
    if (booking) scheduleCommand(noradId, template.name, params, when)
    else sendCommand(noradId, template.name, params)
  }

  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault()
        send()
      }}
    >
      <Select
        label="Command"
        value={template.name}
        options={commands.map((c) => ({ value: c.name, label: c.label }))}
        onChange={(value) => setName(value)}
        className="min-w-[14rem] flex-1"
      />
      {parsed.map(([param, result]) => (
        <label key={param.name} className="block min-w-[8rem] flex-1">
          <span className="mb-1 block text-sm text-gray-600 dark:text-gray-400">{param.label}</span>
          <input
            className={cn(inputClass, param.type === 'int' && 'font-mono tabular-nums')}
            inputMode={param.type === 'int' ? 'numeric' : 'text'}
            maxLength={param.type === 'text' ? param.max_length : undefined}
            value={raw[param.name] ?? ''}
            placeholder={param.type === 'int' ? `${param.min}–${param.max}` : undefined}
            aria-invalid={(raw[param.name] ?? '') !== '' && 'error' in result}
            onChange={(e) => setRaw((r) => ({ ...r, [param.name]: e.target.value }))}
          />
          {(raw[param.name] ?? '') !== '' && 'error' in result && (
            <span className="mt-1 block text-xs text-red-700 dark:text-red-400">{result.error}</span>
          )}
        </label>
      ))}
      <Select
        label="When"
        value={when}
        options={whenOptions}
        onChange={(value) => setChosen(value)}
        className="min-w-[14rem] flex-1"
      />
      <Button
        type="submit"
        variant="primary"
        disabled={!valid || (!booking && !enabled)}
        title={booking ? 'Sent at AOS unless a subsystem is red' : enabled ? undefined : 'Commands go out during a pass: pick one under When'}
      >
        {booking ? <CalendarClock size={16} strokeWidth={1.5} /> : <Send size={16} strokeWidth={1.5} />}
        {booking ? 'Schedule' : 'Send'}
      </Button>
    </form>
  )
}

function PacketRow({ packet }: { packet: PacketMessage }) {
  const outcome = packetOutcome(packet)
  const rag = OUTCOME_RAG[outcome]
  const Arrow = packet.direction === 'up' ? ArrowUp : ArrowDown
  return (
    <li className="flex items-center gap-2 py-1 text-sm">
      <Arrow size={14} strokeWidth={1.75} className={muted} aria-label={packet.direction === 'up' ? 'uplink' : 'downlink'} />
      <span className="font-mono text-xs tabular-nums">{hms(packet.sim_time)}</span>
      <span className="min-w-0 flex-1 truncate" title={packet.label}>
        {packet.template}
        {packet.source === 'satnogs' && <span className={cn('ml-1.5 text-xs', muted)}>SatNOGS</span>}
      </span>
      <span className={cn('font-mono text-xs tabular-nums', muted)}>{packet.size_bytes} B</span>
      <span className={cn('flex w-28 items-center justify-end gap-1 text-xs', rag ? RAG_TEXT[rag] : muted)}>
        {rag && <RagIcon rag={rag} size={12} />}
        {OUTCOME_WORDS[outcome]}
        {outcome === 'corrupted' && packet.bit_errors ? ` (${packet.bit_errors} bits)` : ''}
      </span>
    </li>
  )
}

function Telemetry({ packet }: { packet: PacketMessage }) {
  const origin = packet.origin
  const observation = satnogsObservationUrl(origin?.observation_id)
  return (
    <div className="min-w-0">
      <div className="text-sm font-semibold">{packet.label}</div>
      <div className={cn('mb-2 text-xs', muted)}>
        {hms(packet.sim_time)} UTC · {packet.framing === 'ax25' ? 'AX.25' : 'CCSDS'} · {packet.size_bytes} B
        {!packet.layout_sourced && ' · illustrative layout'}
        {packet.ber !== null && ` · BER ${formatBer(packet.ber)}`}
      </div>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-sm">
        {packet.decoded?.map((field) => (
          <div key={field.name} className="contents">
            <dt className={cn('truncate', muted)}>{field.name}</dt>
            <dd className="truncate font-mono tabular-nums" title={formatValue(field.value, field.unit)}>
              {formatValue(field.value, field.unit)}
            </dd>
          </div>
        ))}
      </dl>
      {origin && (
        <p className={cn('mt-2 text-xs', muted)}>
          Received by SatNOGS station {origin.station_id ?? '?'}
          {origin.received_at && ` on ${origin.received_at.slice(0, 10)}`}
          {observation && (
            <>
              {' · '}
              <a href={observation} target="_blank" rel="noopener noreferrer" className="growing-underline text-primary-700 dark:text-primary-400">
                observation {origin.observation_id}
              </a>
            </>
          )}
        </p>
      )}
    </div>
  )
}

/**
 * The link with the selected satellite: contact, packets up and down, the latest decoded frame and
 * a command composer. Every packet, check and value comes from the server; uplink is simulated.
 */
/** A downlink frequency to the kHz: "145.825 MHz", "7812 MHz". */
const megahertz = (hz: number) => `${Number((hz / 1e6).toFixed(3))} MHz`

/** Sound cues for command traffic, on or off. Turning them on starts the page's audio in this
 *  click, as browsers require. */
function SoundToggle() {
  const sound = useUi((s) => s.sound)
  const setSound = useUi((s) => s.setSound)
  const label = sound
    ? 'Turn command sounds off'
    : 'Turn command sounds on: a chirp when a command goes up, a chime when its answer comes back'
  return (
    <button
      type="button"
      aria-pressed={sound}
      aria-label={label}
      title={label}
      onClick={() => {
        if (!sound && !startCommsSound()) return // no Web Audio in this browser
        if (!sound) trackOnce('sound-on')
        setSound(!sound)
      }}
      className={cn(
        'rounded p-0.5 hover:text-gray-900 dark:hover:text-gray-100',
        sound ? 'text-primary-700 dark:text-primary-400' : 'text-gray-600 dark:text-gray-400',
        focusRing,
      )}
    >
      {sound ? <Volume2 size={16} strokeWidth={1.5} /> : <VolumeX size={16} strokeWidth={1.5} />}
    </button>
  )
}

/** The downlink the station listens on and its Doppler shift now (both from the server). */
function DownlinkLine({ contact }: { contact: ContactState }) {
  const { doppler_hz: shift, downlink_frequency_hz: frequency } = contact
  if (frequency == null) return null
  return (
    <p className={cn('mt-1 text-xs tabular-nums', muted)}>
      Downlink {megahertz(frequency)}
      {shift != null && ` · Doppler ${formatDopplerKHz(shift)} kHz`}
    </p>
  )
}

function SourceLinks({ sources }: { sources: LinkSource[] }) {
  return sources.map((src, i) => {
    const href = safeHttpsUrl(src.url)
    return (
      <span key={src.url}>
        {i > 0 && ', '}
        {href ? (
          <a href={href} target="_blank" rel="noopener noreferrer" className="growing-underline text-primary-700 dark:text-primary-400">
            {src.title}
          </a>
        ) : (
          src.title
        )}
      </span>
    )
  })
}

export function CommsPanel() {
  const selectedId = useMission((s) => s.selectedId)
  const simMs = useMission((s) => (s.snapshot ? Date.parse(s.snapshot.sim_time) : null))
  const contact = useMission((s) => s.snapshot?.contact ?? null)
  const templates = useMission((s) => s.packetTemplates)
  const allPackets = useMission((s) => s.packets)
  const commandStatuses = useMission((s) => s.commands)
  const open = useUi((s) => s.sections.comms)
  const toggleSection = useUi((s) => s.toggleSection)
  useCommsSound()

  const packets = useMemo(() => allPackets.filter((p) => p.norad_id === selectedId), [allPackets, selectedId])
  const sat = templates?.satellites.find((s) => s.norad_id === selectedId)
  const recentCommands = useMemo(
    () =>
      Object.values(commandStatuses)
        .filter((c) => c.norad_id === selectedId)
        .sort((a, b) => b.command_id - a.command_id)
        .slice(0, 4),
    [commandStatuses, selectedId],
  )

  if (selectedId === null || !contact || contact.norad_id !== selectedId) return null
  if (contact.state === 'no_template') {
    return (
      <Card as="div" dense title="Communications">
        <p className={cn('mb-2 text-sm', muted)}>No packet templates for this satellite, so the station can’t communicate with it.</p>
        <CommsSatellites />
      </Card>
    )
  }

  const words = contactWords(contact)
  const latest = latestDecoded(packets, selectedId)
  const downlink = downlinkLine(contact)
  const sources = sat ? [...sat.telemetry, ...sat.commands].flatMap((t) => t.sources) : []
  const uniqueSources = sources.filter((s, i) => sources.findIndex((x) => x.url === s.url) === i)

  return (
    <Card
      as="div"
      dense
      title="Communications"
      titleAction={
        <span className="flex items-center gap-1">
          <InfoPopover
            label="Satellites you can communicate with"
            iconSize={16}
            buttonClassName="p-0.5"
            panelClassName="inset-x-3 top-11 max-h-[min(28rem,60vh)] origin-top-left"
            closeOnPick
          >
            <CommsSatellites />
          </InfoPopover>
          <SoundToggle />
        </span>
      }
      open={open}
      onToggle={() => toggleSection('comms')}
      aside={
        <span className={cn('flex items-center gap-1.5 text-right text-sm font-semibold', words.active ? 'text-primary-700 dark:text-primary-400' : muted)}>
          <Radio size={16} strokeWidth={1.5} className="shrink-0" aria-hidden />
          {words.title}
        </span>
      }
    >
      <PacketFlow packets={packets} active={words.active} lightTime={formatLightTime(contact.one_way_light_time_s)} />
      <DownlinkLine contact={contact} />

      <div className="mt-3">
        {sat && sat.commands.length > 0 ? (
          <Composer key={selectedId} noradId={selectedId} commands={sat.commands} enabled={words.active} />
        ) : (
          <p className={cn('text-sm', muted)}>Receive-only: this satellite publishes no command set, so commanding isn’t simulated.</p>
        )}
        {recentCommands.length > 0 && (
          <ul className="mt-2 space-y-0.5 text-sm" aria-label="Recent commands">
            {recentCommands.map((c) => {
              const rag = STATUS_RAG[c.status]
              const booked = c.scheduled_pass
              const detail =
                c.status === 'scheduled' && booked && simMs !== null
                  ? `for the ${utcWhen(Date.parse(booked.aos_time), simMs)} pass`
                  : c.check && c.status !== 'aborted'
                    ? checkSummary(c.check)
                    : c.reason
              return (
                <li key={c.command_id} className="flex items-center gap-2">
                  <span className="font-mono text-xs tabular-nums">{hms(c.sim_time)}</span>
                  {booked && <CalendarClock size={12} strokeWidth={1.75} className={muted} aria-label="booked for a pass" />}
                  <span className="font-semibold">{c.command}</span>
                  <span className={cn('flex items-center gap-1 text-xs', rag ? RAG_TEXT[rag] : muted)}>
                    {rag && <RagIcon rag={rag} size={12} />}
                    {c.status}
                  </span>
                  {detail && (
                    <span className={cn('truncate text-xs', muted)} title={detail}>
                      {detail}
                    </span>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </div>

      {/* columns by the inspector's width (its container), not the viewport's */}
      <div className="mt-3 grid gap-4 @xl:grid-cols-2">
        <div className="min-w-0">
          <h3 className="mb-1 text-sm font-semibold">Packets</h3>
          {packets.length > 0 ? (
            <ol className="max-h-64 divide-y divide-gray-200 overflow-y-auto pr-1 dark:divide-gray-800" aria-label="Packet log">
              {packets
                .slice(-LOG_ROWS)
                .reverse()
                .map((p) => (
                  <PacketRow key={p.seq} packet={p} />
                ))}
            </ol>
          ) : (
            <p className={cn('text-sm', muted)}>{words.active ? 'Waiting for the next frame…' : 'Nothing yet this session.'}</p>
          )}
        </div>
        {latest ? <Telemetry packet={latest} /> : <p className={cn('text-sm', muted)}>No telemetry decoded yet.</p>}
      </div>

      <p className={cn('mt-3 text-xs', muted)}>
        Uplink is simulated: nothing is transmitted. {downlink}{' '}
        {uniqueSources.length > 0 && (
          <>
            Formats: <SourceLinks sources={uniqueSources} />.{' '}
          </>
        )}
        {sat?.downlink_sources && sat.downlink_sources.length > 0 && (
          <>
            Downlink frequency: <SourceLinks sources={sat.downlink_sources} />.
          </>
        )}
      </p>
    </Card>
  )
}
