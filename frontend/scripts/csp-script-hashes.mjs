// Print the CSP source ('sha256-…') of every distinct inline <script> in built HTML, space-separated.
// The Docker build bakes them into nginx's Content-Security-Policy, so the hashes always match the
// inline scripts that actually ship: the app's pre-paint theme script, and the docs site's (AGENTS.md
// §12.7). Usage: node csp-script-hashes.mjs <html file or directory>...
import { createHash } from 'node:crypto'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

const htmlFiles = (path) =>
  statSync(path).isDirectory()
    ? readdirSync(path, { recursive: true })
        .filter((f) => f.endsWith('.html'))
        .sort()
        .map((f) => join(path, f))
    : [path]

const hashes = new Set()
for (const file of process.argv.slice(2).flatMap(htmlFiles)) {
  const html = readFileSync(file, 'utf8')
  for (const [, body] of html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)) {
    hashes.add(`'sha256-${createHash('sha256').update(body, 'utf8').digest('base64')}'`)
  }
}
if (hashes.size === 0) throw new Error('no inline script found: is the theme script gone?')
process.stdout.write([...hashes].join(' '))
