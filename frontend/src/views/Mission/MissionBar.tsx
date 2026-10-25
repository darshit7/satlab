import { CalendarClock, MapPin, OctagonAlert, Search, SlidersHorizontal } from 'lucide-react'
import { useCallback, useEffect, type ReactNode } from 'react'
import { Button } from '../../components/ui/Button'
import { Panel } from '../../components/ui/Panel'
import { Pill } from '../../components/ui/Pill'
import { filterSummary } from '../../lib/filters'
import { scheduledCommands, unacknowledgedCount } from '../../lib/schedule'
import { useMission } from '../../store/mission'
import { dialogOpen, useUi, type PanelId } from '../../store/ui'
import { CATALOG_SEARCH_ID, CatalogSearch } from '../Catalog/CatalogSearch'
import { LocationPanel } from '../Location/LocationPanel'
import { SchedulerPanel } from '../Scheduler/SchedulerPanel'
import { VisibleList } from '../Visible/VisibleList'

const TITLES: Record<PanelId, string> = {
  station: 'Ground station',
  catalog: 'Satellite catalog',
  overhead: 'Overhead',
  schedule: 'Schedule',
}
const PANELS: Record<PanelId, () => ReactNode> = {
  station: () => <LocationPanel />,
  catalog: () => <CatalogSearch />,
  overhead: () => <VisibleList />,
  schedule: () => <SchedulerPanel />,
}
const panelId = (id: PanelId) => `panel-${id}`

// The bar's buttons, so a panel closed with Escape or × hands focus back to the one that opened it.
const triggers: Partial<Record<PanelId, HTMLButtonElement | null>> = {}

const isTyping = (target: EventTarget | null) =>
  target instanceof HTMLElement && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))

function BarButton({
  id,
  primary = false,
  className,
  title,
  children,
}: {
  id: PanelId
  /** The call to action while there's no session yet. */
  primary?: boolean
  className?: string
  title?: string
  children: ReactNode
}) {
  const open = useUi((s) => s.panel === id)
  const togglePanel = useUi((s) => s.togglePanel)
  return (
    <Button
      ref={(el) => {
        triggers[id] = el
      }}
      variant={primary ? 'primary' : 'secondary'}
      active={open}
      aria-expanded={open}
      aria-controls={open ? panelId(id) : undefined}
      title={title}
      className={className}
      onClick={() => togglePanel(id)}
    >
      {children}
    </Button>
  )
}

/** What the map, the sky views and the Overhead lists show, in words: always on screen. */
function FilterSummary() {
  const filters = useMission((s) => s.filters)
  const mask = useMission((s) => s.snapshot?.observer.horizon_mask_deg ?? null)
  const shown = useMission((s) => (s.snapshot ? s.snapshot.satellites.filter((x) => x.above_mask).length : null))
  const total = useMission((s) => s.snapshot?.visible_count ?? null)
  const upcoming = useMission((s) => s.upcoming)

  const parts = filterSummary(filters, mask)
  if (filters.rising_within_min > 0 && upcoming) {
    if (upcoming.status === 'ready') parts[parts.length - 1] += ` (${upcoming.total.toLocaleString()})`
    else if (upcoming.status === 'paused') parts[parts.length - 1] += ' (paused)'
  }
  return (
    <span className="min-w-0 text-left">
      Overhead
      <span className="font-normal">
        {': '}
        {shown !== null && total !== null && (
          <span className="tabular-nums">
            {shown.toLocaleString()}
            {shown !== total && ` of ${total.toLocaleString()}`} in view ·{' '}
          </span>
        )}
        {parts.join(' · ')}
      </span>
    </span>
  )
}

/** Pass automation: commands waiting for a pass and alarms not yet acknowledged (icon + number). */
function ScheduleSummary() {
  const waiting = useMission((s) => scheduledCommands(s.commands).length)
  const alarms = useMission((s) => unacknowledgedCount(s.alarms))
  return (
    <>
      <CalendarClock size={16} strokeWidth={1.5} className="shrink-0" aria-hidden />
      Schedule
      {waiting > 0 && (
        <span className="font-mono text-xs font-normal text-gray-500 tabular-nums dark:text-gray-400" title={`${waiting} waiting for a pass`}>
          <span className="sr-only">, </span>
          {waiting}
          <span className="sr-only"> waiting for a pass</span>
        </span>
      )}
      {alarms > 0 && (
        <span className="flex items-center gap-0.5 text-xs text-red-700 tabular-nums dark:text-red-400" title={`${alarms} unacknowledged alarms`}>
          <OctagonAlert size={14} strokeWidth={1.75} aria-hidden />
          <span className="sr-only">, </span>
          {alarms}
          <span className="sr-only"> unacknowledged alarms</span>
        </span>
      )}
    </>
  )
}

/**
 * The mission's context, one row above the map: where you observe from, the catalog, what is shown
 * (the filter summary, which opens the Overhead list) and pass automation (the Schedule). Each opens
 * a floating panel over the map. `/` opens the catalog search from anywhere but a text field.
 */
export function MissionBar() {
  const session = useMission((s) => s.session)
  const site = useMission((s) => s.site)
  const trackedCount = useMission((s) => s.tracked.length)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey || isTyping(e.target) || dialogOpen()) return
      e.preventDefault()
      const { panel, openPanel } = useUi.getState()
      if (panel === 'catalog') document.getElementById(CATALOG_SEARCH_ID)?.focus()
      else openPanel('catalog') // the search box takes focus as it mounts
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  return (
    <nav aria-label="Mission" className="flex flex-wrap items-center gap-2">
      <BarButton
        id="station"
        primary={!session && !site}
        className="max-w-[16rem]"
        title={session ? `Observing from ${session.location_name}` : 'Choose where to observe from'}
      >
        <MapPin size={16} strokeWidth={1.5} className="shrink-0" aria-hidden />
        <span className="truncate">{session ? session.location_name : site ? 'Ground station' : 'Set ground station'}</span>
      </BarButton>
      <BarButton id="catalog" title="Search the satellite catalog (/)">
        <Search size={16} strokeWidth={1.5} className="shrink-0" aria-hidden />
        Catalog
        {trackedCount > 0 && (
          <span className="font-mono text-xs font-normal text-gray-500 tabular-nums dark:text-gray-400" title={`${trackedCount} tracked`}>
            <span className="sr-only">, </span>
            {trackedCount}
            <span className="sr-only"> tracked</span>
          </span>
        )}
        <kbd className="hidden rounded px-1 font-mono text-xs font-normal text-gray-500 ring-1 ring-gray-900/10 lg:inline dark:text-gray-400 dark:ring-white/10">
          /
        </kbd>
      </BarButton>
      <BarButton id="overhead" className="max-w-full" title="What's up now and rising soon, and the filters">
        <SlidersHorizontal size={16} strokeWidth={1.5} className="shrink-0" aria-hidden />
        <FilterSummary />
      </BarButton>
      <BarButton id="schedule" title="Commands booked for a pass, and alarms">
        <ScheduleSummary />
      </BarButton>
    </nav>
  )
}

/** The open mission panel, floating over the map's top-left corner. */
export function MissionPanels() {
  const panel = useUi((s) => s.panel)
  const visibleCount = useMission((s) => s.snapshot?.visible_count ?? null)

  const close = useCallback(() => {
    const { panel: open, closePanel } = useUi.getState()
    closePanel()
    if (open) triggers[open]?.focus()
  }, [])

  if (!panel) return null
  return (
    <Panel
      key={panel}
      id={panelId(panel)}
      title={TITLES[panel]}
      aside={panel === 'overhead' && visibleCount !== null && <Pill size="sm">{visibleCount.toLocaleString()} in view</Pill>}
      onClose={close}
    >
      {PANELS[panel]()}
    </Panel>
  )
}
