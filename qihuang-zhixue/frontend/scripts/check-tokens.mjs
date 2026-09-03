import { readdirSync, readFileSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join, relative } from 'node:path'

const root = fileURLToPath(new URL('../src/', import.meta.url))
// Legacy Stage 1-4 surfaces are frozen; new work must use tokens. Keep the
// whitelist explicit so the scan remains deterministic and reviewable.
const allow = new Set(['styles/tokens.scss', 'styles/reset.scss', 'main.ts'])
const legacy = /^(components\/|views\/|config\/)/
const literal = /#(?:[0-9a-f]{3,8})\b|rgba?\([^)]*\)/gi
const files = []
function walk(dir) { for (const name of readdirSync(dir)) { const file = join(dir, name); if (statSync(file).isDirectory()) walk(file); else if (/\.(vue|ts|scss|css)$/.test(name)) files.push(file) } }
walk(root)
const violations = []
for (const file of files) {
  const rel = relative(root, file).replaceAll('\\', '/')
  if (allow.has(rel) || legacy.test(rel)) continue
  const lines = readFileSync(file, 'utf8').split(/\r?\n/)
  lines.forEach((line, index) => { for (const match of line.matchAll(literal)) violations.push(`${relative(root, file).replaceAll('\\', '/')}:${index + 1} ${match[0]}`) })
}
if (violations.length) { console.error(`Token violations (${violations.length})\n${violations.join('\n')}`); process.exitCode = 1 } else console.log('Token check passed: no unapproved color literals in src/')
