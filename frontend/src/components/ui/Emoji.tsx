import satellite from '@twemoji/svg/1f6f0.svg?url'
import wave from '@twemoji/svg/1f44b.svg?url'

// Twemoji (CC-BY 4.0), self-hosted. Add glyphs here as the copy needs them.
const EMOJI = { satellite, wave } as const

export function Emoji({ name, label }: { name: keyof typeof EMOJI; label: string }) {
  return (
    <img
      src={EMOJI[name]}
      alt={label}
      draggable={false}
      className="inline-block h-[1.15em] w-[1.15em] align-[-0.2em]"
    />
  )
}
