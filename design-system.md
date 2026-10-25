# Design System — "Warm Notebook"

> SatLab's direction, carried over from the author's personal site. A handwritten wordmark over rounded sans, a single emerald accent, paper-soft cards on a tilted-grid ground.

The rule that produced it: **one accent, one grey family, one shape language.** Everything that wants to look active is emerald; everything neutral is `gray`; everything containing something is rounded. Personality comes from the handwritten display face and the Twemoji, not from adding colors.

This document describes the mission console in `frontend/`. The look and feel are the same as the source site's. What changed is where each rule applies (a single-screen console instead of a content site) and how it's wired (Vite + Tailwind v4 instead of Next.js + Tailwind v3).

---

## 1. Direction

- **Default theme:** `dark`, with an explicit light/dark/system switcher in the header. A stored choice (including `system`) wins.
- **Personality:** Warm, personal, unpretentious. A field notebook for watching satellites rather than a control-room wall of blue. It greets you with the handwritten wordmark, then gets out of the way and lets the data run.
- **One accent rule:** hierarchy comes from type weight, spacing, and the single emerald accent. The only sanctioned second colors are the far ends of the greeting gradient (`amber-600` on light, `lime-500` on dark), which are display-only (they paint the wordmark), and the functional colors of §3: red for errors and amber for marginal link and subsystem state.
- **Voice:** first person where there is copy to write, emoji-punctuated sparingly. The empty states are where the voice shows (`Nothing tracked yet 🛰️ — …`). Labels, units and readouts stay plain and exact.
- **Honesty is part of the voice.** The console says what it simulates and what it doesn't (geometric vs naked-eye visibility, TLE age). That copy is quiet `text-sm` muted text, never an alarm.

## 2. Typography

| Role                         | Face                            | Weights           | Tailwind              |
| ---------------------------- | ------------------------------- | ----------------- | --------------------- |
| Display (the wordmark, only) | "Playpen Sans"                  | 800               | `font-greeting`       |
| Body / UI                    | "Nunito", system-ui, sans-serif | 400–800, + italic | `font-sans` (default) |
| Code / data / mono           | "JetBrains Mono", ui-monospace  | 400–600, + italic | `font-mono`           |

All three are self-hosted through Fontsource packages imported at the top of `src/index.css` (`@fontsource-variable/nunito`, `@fontsource-variable/jetbrains-mono`, `@fontsource/playpen-sans` at 800, latin subset). There is no runtime request to Google, and Fontsource's `font-display: swap` keeps text visible while the fonts load. Unlike next/font, there is no automatic fallback-metric adjustment, so a small swap shift on first load is possible.

**Playpen Sans is rationed to exactly one element:** the `SatLab` wordmark in the header (`Wordmark` in `components/ClockBar.tsx`). It's the console's greeting. A handwritten face reads as charming once and as noise the second time; the moment it appears in a card heading or a button, the whole system tips into cutesy. `font-greeting` has one consumer by design.

**Mono carries data.** Every number a user reads off the console (sim clock, coordinates, az/el/range, NORAD IDs) is `font-mono tabular-nums`, so columns line up and values don't jitter as they tick.

Scale in use:

| Element                               | Size                                        |
| ------------------------------------- | ------------------------------------------- |
| Wordmark                              | `text-2xl` extrabold, greeting gradient     |
| Card heading (`Card` title)           | `text-xl` semibold (`dense` cards: `text-lg`) |
| Selected-satellite name               | `text-xl` semibold                          |
| Body, list primary lines              | `text-base`                                 |
| Readout values, sim clock             | `text-base` mono, tabular                   |
| Meta, labels, captions, table, footer | `text-sm` (`text-xs` only inside `sm` pills) |

There is no long-form prose in the console, so no typography plugin.

## 3. Color tokens

The Tailwind theme is the source of truth: the `@theme static` block in `src/index.css`. That block is Tailwind v4's replacement for `tailwind.config.ts`, and there is no second token layer. Two aliases carry the whole identity:

```css
@theme static {
  --color-*: initial;          /* every stock palette removed */
  --color-primary-*: …;        /* emerald — the single accent */
  --color-dark: #1f1f1f;       /* the dark page ground */
  --color-gray-*: …;           /* the one grey family */
  /* + amber-600 / lime-500 (greeting ends), red-400 / red-700 (functional) */
}
```

- **Values are the Tailwind v3 hexes** the system was specified and contrast-checked against. Tailwind v4's stock palette is oklch and slightly different, so it is overridden rather than used.
- **`--color-*: initial` removes every other palette.** `zinc-800`, `slate-500` and friends don't just look wrong here; they don't compile. A color that isn't in the block can't be used by accident.
- **`static`** emits every token as a CSS variable even when no class uses it. The map reads its colors from these variables (§8), so the map and the UI can't drift apart.

### The accent

| Token                         | Hex                   | Use                                                                         |
| ----------------------------- | --------------------- | --------------------------------------------------------------------------- |
| `primary-100` / `primary-200` | `#d1fae5` / `#a7f3d0` | Growing-underline wash, light                                               |
| `primary-300`                 | `#6ee7b7`             | Link hover on dark                                                          |
| `primary-400`                 | `#34d399`             | Links, accent text and active states on dark (8.57:1); hairline gradients, dark; map selection, dark |
| `primary-500`                 | `#10b981`             | Hairline gradients, focus ring; button hover on dark                        |
| `primary-600`                 | `#059669`             | Greeting gradient, green end; button ground on dark; map selection, light   |
| `primary-700`                 | `#047857`             | Links, accent text, active states and buttons on light (5.48:1)             |
| `primary-800` / `primary-900` | `#065f46` / `#064e3b` | Growing-underline wash, dark; link and button hover on light                |

`primary-50` and `primary-950` are defined to keep the ramp complete; nothing uses them.

**Why 700/400 and not 500 for text.** `emerald-500` on white is 2.54:1, nowhere near readable. The accent has to step to `700` on light and `400` on dark to clear WCAG AA for body text. Those two steps look like the same color at a glance, because each sits at a similar distance from its own ground. Any new accent-colored text follows the same rule: **`primary-700` on light, `primary-400` on dark.** Re-check with a contrast tool before introducing a different step. Map strokes and dots are not text, so they may use `600` on light, where it reads better against the positron basemap.

**What counts as "active" in the console:** the selected satellite (its name in the tables and lists, its track, dot and label on the map), the running clock rate, a satellite you are tracking, an included filter (toggles), the mission-bar button whose panel is open, the "in view" status, a pass in progress in the schedule, the live connection dot, and links. Everything else on the map — the hundreds of satellites merely in view — is grey: the accent is spent on what the operator chose.

**The sky views are the exception: there, emerald means a green link.** A dot's fill is its link state from the server (green / amber / red; grey for "no RF data", which is most satellites), the selected pass is coloured by the link state along it, and the strong-link region is a faint green band. The selection is marked by shape instead of the accent: an ink ring (`gray-900` / `gray-100`) on a ground-coloured gap, a larger dot and an ink label. Elevation numbers in the readout and the Overhead list are neutral, so emerald next to a margin always means "green".

### Grounds and ink

| Role                            | Light                      | Dark                                   |
| ------------------------------- | -------------------------- | -------------------------------------- |
| Page                            | `white`                    | `dark` (`#1f1f1f`)                     |
| Body text                       | `gray-900`                 | `gray-100`                             |
| Muted text (meta, descriptions) | `gray-600`                 | `gray-400`                             |
| Faint text (separators, icons)  | `gray-500`                 | `gray-400` / `gray-500`                |
| Hairline border                 | `gray-200`                 | `gray-700` (`gray-800` on card frames) |
| Raised surface (cards)          | `white` / `gray-50`        | `white/5`                              |
| Ring / outline                  | `gray-900/5`–`gray-900/20` | `white/10`–`white/20`                  |

**One grey family.** Tailwind ships `gray`, `zinc`, `neutral`, `slate` and `stone`. They differ by a few degrees of hue, which is exactly enough to look like a mistake when two of them meet in the same card. This system uses `gray` for every surface, border and piece of text, and here the others are not even defined.

### Functional colors

Rule 9.1 allows a functional color for a genuinely new semantic role. The console has two: errors, and link / subsystem state (RAG).

| Token          | Hex       | Use                                                                                           |
| -------------- | --------- | --------------------------------------------------------------------------------------------- |
| `red-700`      | `#b91c1c` | Error text, notice icon, closed-connection dot and RAG red on light (6.47:1 on white)          |
| `red-400`      | `#f87171` | The same on dark (5.96:1 on `#1f1f1f`)                                                         |
| `amber-700`    | `#b45309` | RAG amber on light: margin 0–6 dB, marginal subsystems (5.02:1 on white, 4.81:1 on `gray-50`) |
| `amber-400`    | `#fbbf24` | RAG amber on dark (9.87:1 on `#1f1f1f`)                                                        |
| `primary-700/400` | (accent) | RAG green: the accent itself (5.48:1 / 8.57:1), not a second green                       |

Notices stay on a neutral surface; only the icon carries the red (an information notice, such as pass automation taking the antenna, has a muted `CalendarClock` instead). `amber-600` stays out of RAG: that step is reserved for the greeting gradient. "No data" is neutral grey (`gray-300` / `gray-600` fills, muted text). **Colour is never the only cue:** every RAG state also carries an icon (`CircleCheck`, `TriangleAlert`, `OctagonX`, `CircleDashed`) and a word (Go, Marginal, No-go, No data). The class maps live in `components/ui/ragClasses.ts`; `RagDot` and `RagIcon` in `components/ui/Rag.tsx`.

### Code surfaces

The console renders no code blocks, so these tokens are **not defined** here. If code ever appears (a packet hex dump in Phase 4 is likely), add them exactly as specified and keep them paired with the matching Shiki themes:

| Token                | Hex       | Use                      |
| -------------------- | --------- | ------------------------ |
| `solarized-light`    | `#fdfaf6` | Code block ground, light |
| `github-dark-dimmed` | `#22272e` | Code block ground, dark  |
| `code-block`         | `#36313d` | Code text, light         |

The highlighted-line tint `#fbf0ea` is deliberately warm, so it sits on the warm `solarized-light` ground rather than fighting it.

## 4. Shape, depth and texture

- **Radii.** `rounded-2xl` for cards (inspector sections, the selection header), mission panels, dialogs, the map frame and the sticky header (`md:` and up). `rounded-lg` for buttons, inputs, selects, metric tiles, notices and the map's zoom control. `rounded-md` for the theme menu and select lists. Plain `rounded` for small icon buttons (theme toggle, untrack ×, dismiss ×). `rounded-full` for pills, status text and status dots. Rounded everywhere: nothing in this system has a square corner.
- **Borders over shadows.** Most separation is a hairline (`border-gray-200 dark:border-gray-700`, cards `dark:border-gray-800`) or a 1px ring, not a drop shadow. The header separates itself without a border at all: `backdrop-blur` over a 75%-opaque ground, plus the smallest shadow.
- **Shadow names moved in Tailwind v4; the shadows didn't.** v3 `shadow-sm` is v4 `shadow-xs`, and v3 `shadow` is v4 `shadow-sm`. In v3 terms the house steps are: `shadow-sm` on the header (v4 `shadow-xs`), `shadow` on the primary button and notices (v4 `shadow-sm`), and `shadow-lg` at low opacity (`shadow-black/5`) on the theme menu and select lists. The two bespoke profile-card shadows (`shadow-card`, `shadow-card-stack`) belong to the site's profile card; the console has no profile card and defines no custom shadows.
- **Floating surfaces.** The theme menu, select lists, the About popover, mission panels and dialogs share one surface: `bg-white` / `dark:bg-gray-800`, a `ring-1` (`gray-900/5` / `white/10`) and v3 `shadow-lg` at `shadow-black/5`. Menus and lists are `rounded-md`; panels and dialogs, which hold card-sized content, are `rounded-2xl`. A dialog's backdrop is `gray-900/40` (`black/60` on dark).
- **Tilted grid.** `components/ui/TiltedGridBackground.tsx` renders an SVG grid skewed `-18deg` at a few percent opacity (5% light, 3% dark) under a `linear-gradient(white, transparent)` mask. It sits behind the top `50vh` of the page and inside each card. It is texture, not decoration: at full opacity it would be a pattern; at a few percent it keeps large empty areas from reading as blank. The SVG is promoted to its own layer (`will-change-transform`, `contain: paint`) so the 2 Hz readout updates don't repaint it.
- **Gradient border.** `components/ui/GradientBorder.tsx` draws two 1px accent hairlines (top and left) that fade out at both ends, inset from the corner by `--offset` (default `1rem`). Every `Card` carries one, to imply a frame without drawing one.
- **One pill.** `components/ui/Pill.tsx` is the single rounded-label shape: `rounded-full`, `ring-1`, `bg-gray-100`/`dark:bg-white/5`, at `base` (`px-3 py-1 text-sm`) or `sm` (`px-2.5 py-0.5 text-xs`). Consumers: the catalog count, the "in view" count, a satellite's type, and "below horizon" / "below the mask". **Pills are always neutral.** They carry metadata, and a row of emerald labels would spend the entire accent budget on things nobody clicks.
- **Status text is the one exception to the pill.** "In view" on the selected satellite is accent-on-transparent with a ring (`StatusText`: `text-primary-700 ring-primary-700/30`, `dark:text-primary-400 ring-primary-400/30`) rather than a filled pill. It marks a state rather than labelling a topic, and there is never more than one on screen. Below the horizon it falls back to a neutral pill.

## 5. Motion

Restrained and mostly hover-driven. Nothing animates on scroll, and no page or section animates itself in.

- **Growing underline**, the signature interaction (`growing-underline` utility in `index.css`). A background gradient grows from `0px 50%` to `100% 50%` over 300ms on hover, so a link underlines itself by filling in rather than appearing. On a link that wraps, it goes on an inline span with `box-decoration-clone` so each line fills separately. Applied to text links (e.g. "Search all of Celestrak").
- **Color transitions** at 150ms on buttons and table rows.
- **The one exception:** the theme menu and select lists (100ms in / 75ms out, fade plus scale from the edge they open from). They're tied to something the visitor opened rather than to the page arriving. Mission panels and dialogs fade and scale in the same way over 100ms (Tailwind's `starting:` styles) and close at once: their content unmounts, so a hidden list doesn't keep re-rendering. A collapsible card turns its chevron (150ms) and nothing else moves.
- **Selects are `components/ui/Select.tsx`, never a native `<select>`.** The browser draws a native option list itself and ignores the theme (white-on-white in dark mode). `Select` has an input-style trigger with a chevron and a listbox on the theme menu's surface, with the selected option in the accent plus a check. The list is portalled and fixed-positioned, so a panel's or the inspector's scroll container can't clip it, and it flips upward when there's no room below. It isn't in the top layer, so it can't open inside a modal `Dialog`: keep selects out of dialogs.
- **Live data is content, not animation.** Satellite markers move along their tracks and the clock ticks because the simulation is running. That motion is exempt from the rules above, and it is never eased, bounced or decorated: markers move at the speed the physics says.
- Everything else is static.

## 6. Layout

- **Gutters.** `px-4 sm:px-6 xl:px-12`, the same padding scale as the site's container.
- **The console is full-bleed.** The site's `max-w-6xl` container is for reading pages; a ground-track map needs the width. If the app ever grows a reading page (about, help, a pass report), it uses `max-w-6xl`.
- **Header.** Sticky (`top-2 lg:top-3`), rounded at `md:` and up, translucent with `backdrop-blur`. Left to right: the wordmark, sim clock (UTC in mono, local time muted beneath), clock controls, then the connection status, the "About the data" (i) button and the theme switcher pushed right. It wraps on narrow screens rather than collapsing; there is no navigation to put in a dialog yet.
- **Desktop (`lg:`).** The page is exactly one viewport tall, in three levels: the app header (clock, connection, about, theme), the **mission bar** above the map (where you observe from, the catalog, what is shown), and the **inspector** beside the map (the sky and the selection). The map is the stage: it takes all the height under the mission bar. The inspector is `24rem` wide (`26rem` from `xl:`, `28rem` from `2xl:`), scrolls on its own and is a CSS container (`@container`), so what's inside lays out by its width rather than the viewport's.
- **Mission bar.** One row above the map, wrapping when narrow. The ground station (`MapPin` + the location name; a primary "Set ground station" before the first session), the catalog (`Search`, the tracked count, a `/` key hint), **Overhead**: `SlidersHorizontal`, the word Overhead and the filter summary, always on screen ("708 in view · All types · El ≥ mask (5°) · Rising ≤ 30 min (1,091)"; `filterSummary` in `lib/filters.ts`), and **Schedule**: `CalendarClock`, the number of commands waiting for a pass (muted mono) and, when any are unacknowledged, a red alarm count with its `OctagonAlert` icon. Each is a secondary `Button`, `active` while its panel is open.
- **Mission panels.** `components/ui/Panel.tsx`: one at a time, floating over the map's top-left corner (`24rem` wide, at most the map's height, scrolling inside). They're non-modal and an outside click doesn't close them, because the map stays in use while one is open (picking a dot, placing the station). Escape, × or the bar button close them, and focus goes back to that button.
  - **Ground station:** the current location, the preset `Select`, lat/lon/alt, My location and Use this location. Picking a preset or applying a location closes it. It opens by itself on a first visit. The map places a custom location only while it's open, with a crosshair cursor; the 2D map rings the draft and the globe draws the same ring.
  - **Satellite catalog:** the search, focused on open (`/` opens it from anywhere but a text field; the first Escape clears the search), results with Track, and the tracked chips (click selects, × untracks).
  - **Overhead:** filter toggles (`text-xs`: types, SAR-capable, Link OK, Communications) and selects, then "Up now" (sorted by elevation, LOS countdown; a RAG dot before the name of satellites with a link profile, then a muted `Radio` icon for satellites you can communicate with, its hover "Communications: commands and telemetry" or "Communications: receive-only") and "Rising within N min" (soonest first), each capped at 100 rows with a "narrow the filters" hint. The communications-satellite type is labelled **Telecom**, so "Communications" only ever means the Communications section and the satellites it can reach. The UI always spells the word out; "comms" is only the code's name for it.
  - **Schedule:** pass automation. "Alarms" (unacknowledged first: severity as RAG icon + word, mono UTC time, the satellite, the message, a small Acknowledge button; acknowledged ones fade), "Waiting for a pass" (`CalendarClock`, the command's label and satellite, AOS in UTC (dated when another day) and local, max elevation, a countdown, × to cancel), and "Recent" booked outcomes (status icon + word, the check in words: "Go · amber: RF", "Held · attitude red"). A muted footnote says what automation does and that uplink is simulated. Booking happens in Communications, not here.
- **Map chrome.** The 2D/3D toggle holds the top-right corner, MapLibre's zoom control and the globe's legend the bottom-right. The top-left corner belongs to the panels.
- **Inspector.** Top to bottom, each a direct child of the scrolling column (so the header can stay stuck however far it scrolls): the **sky dock**, then for the selection its **header**, the **readout** with the next pass, **Systems** and **Communications**. Sky, Systems and Communications are collapsible `Card dense`s; which are open is remembered (`satlab.ui`), and a collapsed card keeps its `aside` (the rollup word, the contact state) and its content (a half-typed command survives). With nothing selected, one card says how to pick a satellite and lists the tracked chips.
- **Sky dock.** The polar plot and the all-sky view, small and side by side, each with its caption and an enlarge button, over a compact link legend. Compact views draw no ring labels and no name tags (they'd clip at the edge, and the header names the selection). The map and both sky views stay on screen together, because they are linked: selecting in one highlights in all. Enlarged, a view fills a dialog (a square of `min(100dvh − 12rem, 100vw − 4.5rem, 56rem)`) with a switch to the other view and the full legend.
- **Selection header.** Sticky at the inspector's top, on the app header's ground (`bg-white/75` / `dark:bg-dark/75` with `backdrop-blur`, a hairline border). The name (`text-xl`, truncated, the full name on hover) over `#NORAD · type · TLE age`, then a Track toggle (`Button active`) and a clear (×). Below: the in-view status, the AOS/LOS countdown, "In contact" (accent) or "Too fast for communications" (muted), and the systems rollup (icon + word) on the right. While pass automation holds the antenna for the selection, one more `text-xs` line: `CalendarClock` and "Pass automation" in the accent, then muted "· system check at AOS in 0:42 · 1 command" (or "· checked at AOS"); its hover says that selecting another satellite before AOS cancels the commands.
- **Readout.** A three-column grid of metric tiles (`components/Metric.tsx`; `rounded-lg`, `bg-gray-50`/`white/5`, hairline ring; a muted label over a mono value): az, el, range; rate, link margin (RAG icon + colour), Doppler (or one wide muted "no RF data" tile); the sub-point (lat, lon; wide) and alt. Under it, the **next pass** line: the pass in progress ("In a pass · max 82° · ends in 4:12") or the next one with **Jump**, and **All passes**, which opens the 24-hour schedule in a dialog (AOS in UTC and local, duration, max el, azimuths, Jump).
- **Systems.** `views/Indicators/Indicators.tsx`, titled "Systems" with the rollup (icon + word, RAG-coloured) on the right. The rollup bar (five `h-1.5 rounded-full` RAG segments, one per subsystem) sits over five rows, each a RAG icon + label, a mono value and a muted detail: Power, Thermal, Storage, Attitude, RF. Attitude means orientation, not height (that's the readout's alt): its row reads "station off nadir · beam ±N°" and explains itself on hover. A muted line under it says what is modelled and links the RF source.
- **Communications.** `views/Comms/CommsPanel.tsx`, titled "Communications", under Systems, for the selection. Beside the title, a 16px (i) opens "Satellites you can communicate with" (`views/Comms/CommsSatellites.tsx`, an `InfoPopover` spanning the card under its header): a lead line on how that differs from Link OK, then the satellites with packet templates, commandable first, each with a muted `Radio`, its name (the selection in the accent) and "Commands and telemetry" or "Receive-only" · "link budget" or "no RF data"; picking one selects it and closes the popover. When the selected satellite has no templates, its Comms card says so and lists the same satellites inline. The contact state sits on the right (a radio icon and words; accent only while in contact). Then the packet-flow strip (station and satellite icons joined by a hairline, dashed out of contact; packets cross it as small dots, commands up in ink, frames down in the RAG colours for intact, corrupted and lost), the command composer (`Select` + inputs + a **When** `Select`: "Now", or one of the selection's passes from its 24-hour schedule, "Next pass · 19:31 UTC · max 62°"; then a primary Send, which reads Schedule with `CalendarClock` for a pass; Now is the default in contact, the next pass otherwise), recent command statuses (RAG icon + word; a booked one shows `CalendarClock`, its pass while waiting, then the check in words), the packet log (arrow, mono time, template, size, outcome) and the latest decoded frame (a mono definition list), side by side once the container is `36rem` wide. A muted footnote says the uplink is simulated, what the downlink is, and links the format sources. Colour is never the only cue: every outcome also has an icon and a word.
- **Dense cards.** Inspector sections use `Card dense` (`px-4 py-3`, `text-lg` heading); panels and dialogs pad the same way.
- **Below `lg`.** One column: the header, the mission bar, the map (`60vh`, panels full-width over it), then the inspector's sections stacked. The selection header doesn't stick there; the app header does.
- **About the data.** The honesty copy (what is real, modelled or simulated) lives in a popover behind an (i) icon in the header (`components/AboutData.tsx`), not in a footer: the theme menu's surface and timing, `text-sm`, a bold lead-in per topic, closed by Escape or a click outside. Panels keep their own one-line notes ("modelled", "uplink is simulated") where the numbers are.

## 7. Quality floor (non-negotiable)

- Body text and accent-on-ground meet **WCAG AA in both themes**; re-verify after any token edit. The current floor: `primary-700` on white = 5.48:1, `primary-400` on `#1f1f1f` = 8.57:1, `gray-600` on white = 7.56:1, `gray-400` on `#1f1f1f` = 6.49:1, and both greeting gradient stops clear 3:1 at display size. **Known gap, inherited from the site:** white button labels on the dark-mode `primary-600` ground are 3.77:1. That passes 3:1 for UI components but not 4.5:1 for small text. On light (`primary-700`) they are 5.48:1.
- Every view works at 400px wide with no horizontal page scroll. Wide content (the tracked-satellite table) scrolls inside its own container, and absolutely positioned helpers like `sr-only` labels must be inside a positioned scroll container, or they leak out of it.
- Keyboard focus is visible. `focusRing` in `components/ui/Button.tsx` defines the house style: `focus-visible:ring-2 ring-primary-500` with an offset that flips to the dark ground in dark mode. `Button` uses it, and so do inputs (`components/ui/Field.ts`), select triggers (`components/ui/Select.tsx`), theme-menu items and text-link buttons. Anything new that is focusable should use `Button` or `focusRing` rather than suppress the default.
- The console is a client-rendered SPA, so there is no SSR hydration hazard. Values that depend on the visitor's clock or timezone (local sim time) are formatted in the browser with `toLocaleString`, never hard-coded to a zone.
- **Map exception.** MapLibre draws labels from its basemap's glyph server, so satellite labels on the map use the basemap's font (Noto Sans), not Nunito. Everything else about the map (colors, selection, grounds) comes from the tokens.

## 8. Wiring into the stack

- **Styling:** Tailwind CSS v4 through `@tailwindcss/vite`. The entry is `src/index.css`: font imports, `@import 'tailwindcss'`, the class-based dark variant, the `@theme static` tokens, base layer, the `growing-underline` utility, and the MapLibre control overrides.
- **Theme switching:** `src/lib/theme.ts` (light / dark / system, persisted under `satlab.theme`, follows `prefers-color-scheme` when `system`) toggles `.dark` on `<html>`. `@custom-variant dark (&:where(.dark, .dark *))` makes `dark:` follow that class. An inline script in `index.html` applies the class before first paint, so there's no flash of the wrong theme. Keep it in sync with `theme.ts`.
- **Tokens:** `src/index.css` only. Imperative consumers read them as CSS variables through `token()` in `src/lib/tokens.ts` (`getComputedStyle(…).getPropertyValue('--color-…')`) rather than repeating hex values: the map's `palette()` in `views/GroundTrack/GroundTrackMap.tsx`, the sky views' `palette()` in `components/skyDraw.ts`, and the globe's, which builds on the sky views'. The sky disc is the raised surface (`gray-50`; `white` at 5% on dark), grid lines the hairline greys, the mask ring a dashed `gray-400`/`gray-500`, link state `rag.green` (`primary-600`/`primary-400`), `rag.amber` (`amber-700`/`amber-400`) and `rag.red` (`red-700`/`red-400`), and the selection's ring and label `ink` (`gray-900`/`gray-100`).
- **Sky views:** `components/SkyCanvas.tsx` owns the canvas (DPR sizing, ~20 fps frame loop, theme, click-to-select, a live `aria-label`); each view supplies only a module-level draw function. Canvas text uses the body font (Nunito), so the sky views need no map-style font exception.
- **Basemaps:** OpenFreeMap `positron` (light) and `dark`, swapped on theme change, with the console's layers re-added on `style.load`. Override with `VITE_MAP_STYLE_URL_LIGHT` / `VITE_MAP_STYLE_URL_DARK`.
- **Map panel:** `views/Map/MapPanel.tsx` holds the 3D globe (default) or the 2D map. The 2D/3D toggle is two `Button active` (the clock-rate pattern) in the top-right corner, on the header's ground (`bg-white/75` / `dark:bg-dark/75` with `backdrop-blur`) so it reads over either map.
- **Globe:** `views/Globe/Globe.tsx` follows the sky views, not the 2D map: a satellite's fill is its link state (grey = no RF data), so emerald there always means "green link", and the selection is marked by shape and ink (`gray-900`/`gray-100`): a larger dot with an ink ring and an ink label and orbit. The other satellites step back (lower opacity) while something is selected. Orbits are bright where the station sees the satellite above the mask and faded elsewhere. The cones are wireframes (rim and lines up from the station): the mask cone in the mask grey, the strong-link cone in `rag.green`. The space behind the Earth is `gray-50` / `dark`, and the bundled Natural Earth II imagery is toned down per theme. Cesium draws labels on a canvas, so they use Nunito like the sky views. A small legend sits bottom-right on the same 75% ground as the toggle.
- **Components:** `components/ui/` holds the system: `Button` (+ `focusRing`), `Card` (optionally collapsible: `open` + `onToggle`; `titleAction` sits beside the title, outside its disclosure button), `Panel` (the mission bar's floating panels), `InfoPopover` (an (i) button and an explanatory panel on the theme menu's surface and timing, placed by the caller; "About the data" and the Communications section's satellite list), `Dialog` (a modal on the native `<dialog>`: focus stays inside, Escape and a backdrop click close it), `Pill` / `StatusText`, `TiltedGridBackground`, `GradientBorder`, `ThemeSwitcher`, `Emoji`, `Field` (input classes), `cn`. Views compose these; they don't restyle them. Filter toggles are `Button active` (the clock-rate pattern), never pills.
- **Icons:** `lucide-react` at `strokeWidth={1.5}` for UI. Twemoji (CC-BY 4.0) for emoji in copy, self-hosted from `@twemoji/svg` through `components/ui/Emoji.tsx`, which registers each glyph it may render. Never rely on the platform's emoji font.

## 9. Extending this

Before adding anything, check it against the three rules:

1. **Does it need a new color?** Almost certainly not. Emerald plus the grey ramp covers state, emphasis and structure. A genuinely new semantic role (a warning, a destructive action, link-health RAG) is the only case that earns one, and it gets a _functional_ color, never a decorative one. Add it to `@theme` and to §3, with its contrast figures.
2. **Does it need a new typeface?** No. Three faces is already the ceiling, and one of them is rationed to a single element.
3. **Does it need a shadow?** Probably a hairline or a ring instead. If it genuinely lifts, take the smallest stock step that reads: v3 `shadow-sm` or `shadow`, which are v4 `shadow-xs` or `shadow-sm`.

---

_The running console is the reference implementation: `pnpm dev` in `frontend/`. Where this document and the code disagree, the code wins: fix the document._
