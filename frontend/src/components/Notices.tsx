import { CircleAlert, X } from 'lucide-react'
import { useMission } from '../store/mission'

export function Notices() {
  const notices = useMission((s) => s.notices)
  const dismiss = useMission((s) => s.dismiss)
  return (
    <div className="fixed right-4 bottom-4 z-30 flex w-[min(26rem,calc(100vw-2rem))] flex-col gap-2" role="status">
      {notices.map((n) => (
        <div
          key={n.id}
          className="flex items-start gap-3 rounded-lg bg-white p-3 text-sm shadow-sm ring-1 ring-gray-900/10 dark:bg-gray-800 dark:ring-white/10"
        >
          <CircleAlert size={18} strokeWidth={1.5} className="mt-0.5 shrink-0 text-red-700 dark:text-red-400" aria-hidden />
          <span className="flex-1">{n.message}</span>
          <button
            type="button"
            aria-label="Dismiss"
            onClick={() => dismiss(n.id)}
            className="rounded p-0.5 text-gray-500 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100"
          >
            <X size={16} strokeWidth={1.5} />
          </button>
        </div>
      ))}
    </div>
  )
}
