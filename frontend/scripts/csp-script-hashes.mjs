// Print the CSP source ('sha256-…') of every inline <script> in a built index.html, space-separated.
// The Docker build bakes them into nginx's Content-Security-Policy, so the hash always matches the
// pre-paint theme script that actually ships (AGENTS.md §12.7). Usage: node csp-script-hashes.mjs <html>
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'

const html = readFileSync(process.argv[2], 'utf8')
const hashes = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(
  ([, body]) => `'sha256-${createHash('sha256').update(body, 'utf8').digest('base64')}'`,
)
if (hashes.length === 0) throw new Error('no inline script found: is the theme script gone?')
process.stdout.write(hashes.join(' '))
