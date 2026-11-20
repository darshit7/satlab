// Copy what the documentation site (docs/) needs from this package's lockfile into its _static/vendor:
// Mermaid's ESM build (diagrams render in the browser, from our own origin: no CDN, AGENTS.md §12.8),
// the app's typefaces (fontsource; latin + latin-ext only) and the favicon. The docs build fails
// without them (docs/_ext/satlab_reference). Usage: node docs-vendor.mjs [out dir]
import { copyFileSync, cpSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const out = resolve(process.argv[2] ?? join(here, '../../docs/_static/vendor'))
const require = createRequire(import.meta.url)
const pkgDir = (name) => dirname(require.resolve(`${name}/package.json`))
const version = (name) => JSON.parse(readFileSync(join(pkgDir(name), 'package.json'), 'utf8')).version

rmSync(out, { recursive: true, force: true })
mkdirSync(join(out, 'fonts'), { recursive: true })

// Mermaid: the entry module and the chunks it imports (relative paths), without source maps.
const mermaid = join(pkgDir('mermaid'), 'dist')
mkdirSync(join(out, 'mermaid'))
copyFileSync(join(mermaid, 'mermaid.esm.min.mjs'), join(out, 'mermaid', 'mermaid.esm.min.mjs'))
cpSync(join(mermaid, 'chunks', 'mermaid.esm.min'), join(out, 'mermaid', 'chunks', 'mermaid.esm.min'), {
  recursive: true,
  filter: (src) => !src.endsWith('.map'),
})

// Fonts: the same variable faces the app imports in src/index.css, rewritten into one fonts.css.
const FONTS = [
  ['@fontsource-variable/nunito', ['wght.css', 'wght-italic.css']],
  ['@fontsource-variable/jetbrains-mono', ['wght.css', 'wght-italic.css']],
]
const SUBSETS = /-(latin|latin-ext)-wght-(normal|italic)\.woff2$/
let css = ''
for (const [name, sheets] of FONTS) {
  for (const sheet of sheets) {
    const text = readFileSync(join(pkgDir(name), sheet), 'utf8')
    for (const [face] of text.matchAll(/@font-face\s*{[^}]*}/g)) {
      const file = face.match(/url\(\.\/files\/([^)]+)\)/)?.[1]
      if (!file || !SUBSETS.test(file)) continue
      copyFileSync(join(pkgDir(name), 'files', file), join(out, 'fonts', file))
      css += face.replace(`./files/${file}`, `fonts/${file}`) + '\n'
    }
  }
}
if (!css) throw new Error('no latin font faces found: did the fontsource layout change?')
writeFileSync(join(out, 'fonts.css'), css)

copyFileSync(join(here, '../public/favicon.svg'), join(out, 'favicon.svg'))

const versions = Object.fromEntries(['mermaid', ...FONTS.map(([name]) => name)].map((n) => [n, version(n)]))
writeFileSync(join(out, 'VERSIONS.json'), JSON.stringify(versions, null, 2) + '\n')
process.stdout.write(`vendored into ${out}: ${JSON.stringify(versions)}\n`)
