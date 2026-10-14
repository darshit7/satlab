/** Prefix a same-origin path (starting with `/`) with the app's base URL, e.g. `/api/x` → `/satlab/api/x`. */
export const appUrl = (path: string): string => import.meta.env.BASE_URL.replace(/\/$/, '') + path
