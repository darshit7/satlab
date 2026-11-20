import type { ComponentProps } from 'react'
import { safeHttpsUrl } from '../../lib/rag'
import { buttonClasses, type ButtonVariant } from './buttonClasses'

interface Props extends ComponentProps<'button'> {
  variant?: ButtonVariant
  /** Secondary only: marks the currently active option (e.g. the running clock rate). */
  active?: boolean
}

export function Button({ variant = 'secondary', active, className, type, ...props }: Props) {
  return <button type={type ?? 'button'} className={buttonClasses(variant, active, className)} {...props} />
}

interface LinkProps extends Omit<ComponentProps<'a'>, 'href' | 'target' | 'rel'> {
  /** A same-origin path (e.g. from `appUrl`) or an https: URL; anything else renders no link. */
  href: string
  variant?: ButtonVariant
  /** Opens in a new tab, without giving it a handle on this one (AGENTS.md §12.7). */
  external?: boolean
}

const sameOriginPath = (href: string) => href.startsWith('/') && !href.startsWith('//')

/** A link that looks like a `Button` of the same variant. */
export function ButtonLink({ href, variant = 'secondary', external, className, ...props }: LinkProps) {
  const safe = sameOriginPath(href) ? href : safeHttpsUrl(href)
  return (
    <a
      href={safe ?? undefined}
      {...(external && { target: '_blank', rel: 'noopener noreferrer' })}
      className={buttonClasses(variant, false, className)}
      {...props}
    />
  )
}
