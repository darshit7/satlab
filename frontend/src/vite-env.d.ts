/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** MapLibre basemap style for the light theme. */
  readonly VITE_MAP_STYLE_URL_LIGHT?: string
  /** MapLibre basemap style for the dark theme. */
  readonly VITE_MAP_STYLE_URL_DARK?: string
  /** Font stack available on the basemap's glyph server, used for satellite labels. */
  readonly VITE_MAP_LABEL_FONT?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
