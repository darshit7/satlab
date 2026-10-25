import { Maximize2 } from 'lucide-react'
import { SkyCanvas, type SkyViewSpec } from '../../components/SkyCanvas'
import { Button } from '../../components/ui/Button'
import { Card } from '../../components/ui/Card'
import { Dialog } from '../../components/ui/Dialog'
import { RagDot } from '../../components/ui/Rag'
import { useMission } from '../../store/mission'
import { useUi, type SkyView } from '../../store/ui'
import { ALL_SKY_VIEW } from '../AllSky/AllSky'
import { POLAR_VIEW } from '../SkyPlot/SkyPlot'

const VIEWS: Record<SkyView, SkyViewSpec> = { polar: POLAR_VIEW, allsky: ALL_SKY_VIEW }
const ORDER: SkyView[] = ['polar', 'allsky']

/** What the dot colours mean, with the server's thresholds. */
function LinkLegend({ compact = false }: { compact?: boolean }) {
  const thresholds = useMission((s) => s.linkProfiles)
  const green = thresholds?.green_margin_db ?? 6
  const amber = thresholds?.amber_margin_db ?? 0
  const items = [
    ['green', `> ${green} dB`],
    ['amber', `${amber}–${green} dB`],
    ['red', compact ? `< ${amber} dB` : `< ${amber} dB or below mask`],
    [null, 'no RF data'],
  ] as const
  return (
    <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-gray-600 dark:text-gray-400" aria-label="Link margin legend">
      {items.map(([rag, text]) => (
        <li key={text} className="flex items-center gap-1.5">
          <RagDot rag={rag} />
          {text}
        </li>
      ))}
    </ul>
  )
}

/** One small sky view with its caption and a button that opens it large. */
function MiniView({ view }: { view: SkyView }) {
  const { title, caption, ...canvas } = VIEWS[view]
  const setSkyDialog = useUi((s) => s.setSkyDialog)
  return (
    <figure className="min-w-0">
      <div className="relative">
        <SkyCanvas {...canvas} compact />
        <Button
          variant="icon"
          className="absolute -top-1 -right-1"
          aria-label={`Enlarge the ${title.toLowerCase()}`}
          title="Enlarge"
          onClick={() => setSkyDialog(view)}
        >
          <Maximize2 size={16} strokeWidth={1.5} />
        </Button>
      </div>
      <figcaption className="mt-1 text-xs text-gray-600 dark:text-gray-400">
        <span className="font-semibold text-gray-900 dark:text-gray-100">{title}</span> · {caption}
      </figcaption>
    </figure>
  )
}

/**
 * The two sky views over the station, small and side by side at the top of the inspector, so all
 * three linked views (map, polar plot, all-sky) stay on screen together. Either opens large.
 */
export function SkyDock() {
  const open = useUi((s) => s.sections.sky)
  const toggleSection = useUi((s) => s.toggleSection)
  const where = useMission((s) => s.session?.location_name)
  return (
    <Card
      as="div"
      dense
      title="Sky"
      open={open}
      onToggle={() => toggleSection('sky')}
      aside={where && <span className="min-w-0 truncate text-sm text-gray-600 dark:text-gray-400">over {where}</span>}
    >
      <div className="grid grid-cols-2 gap-3">
        {ORDER.map((view) => (
          <MiniView key={view} view={view} />
        ))}
      </div>
      <LinkLegend compact />
    </Card>
  )
}

/** One sky view, large, with a switch to the other. */
export function SkyDialog() {
  const view = useUi((s) => s.skyDialog)
  const setSkyDialog = useUi((s) => s.setSkyDialog)
  const spec = view ? VIEWS[view] : null

  return (
    <Dialog
      open={spec !== null}
      onClose={() => setSkyDialog(null)}
      title={spec?.title ?? 'Sky'}
      aside={
        <div role="group" aria-label="Sky view" className="flex gap-1">
          {ORDER.map((option) => (
            <Button key={option} active={view === option} aria-pressed={view === option} onClick={() => setSkyDialog(option)}>
              {VIEWS[option].title}
            </Button>
          ))}
        </div>
      }
    >
      {spec && (
        // as large as the screen allows: the dialog's height less its header and the legend
        <div className="mx-auto w-[min(calc(100dvh-12rem),calc(100vw-4.5rem),56rem)]">
          <SkyCanvas label={spec.label} kind={spec.kind} eastLeft={spec.eastLeft} ringLabels={spec.ringLabels} draw={spec.draw} />
          <p className="mt-1 text-sm text-gray-600 dark:text-gray-400">{spec.caption}</p>
          <LinkLegend />
        </div>
      )}
    </Dialog>
  )
}
