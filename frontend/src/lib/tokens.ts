/** Read a design-system color token (emitted by `@theme static` in index.css). Shared by the views
 *  that paint outside the DOM (map, globe, sky canvases), so they use the same colors as the UI. */
export function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(`--color-${name}`).trim()
}
