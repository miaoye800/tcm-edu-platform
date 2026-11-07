import { readdirSync, readFileSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join, relative } from 'node:path'

const root = fileURLToPath(new URL('../src/', import.meta.url))
// Legacy Stage 1-4 surfaces are frozen; new work must use tokens. Keep the
// whitelist explicit so the scan remains deterministic and reviewable.
const allow = new Set([
  'styles/palette.scss',
  'styles/tokens.scss',
  'styles/reset.scss',
  'styles/story.scss',
  'main.ts',
])
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

// The story module is new work, so it gets a stricter second pass: the six-step
// type scale is the only font-size vocabulary, and the material shortcuts that
// made the old page look assembled are banned outright.
const storyRoots = ['components/story/', 'views/apprentice/']
const storyFiles = files.filter((file) => {
  const rel = relative(root, file).replaceAll('\\', '/')
  return storyRoots.some((prefix) => rel.startsWith(prefix)) || rel === 'views/ApprenticeStoryView.vue' || rel === 'views/StoryKitView.vue' || rel === 'views/StoryPreviewView.vue'
})
const allowedFontSizes = new Set([
  'var(--qh-text-display)',
  'var(--qh-text-title)',
  'var(--qh-text-lead)',
  'var(--qh-text-ui)',
  'var(--qh-text-option)',
  'var(--qh-text-footnote)',
])
const bannedPatterns = [
  [/filter:\s*drop-shadow/, 'drop-shadow stacking is banned'],
  [/clip-path:\s*polygon/, 'polygon clip-paths are banned'],
  [/inset 0 0 0 2px/, 'double inset outlines are banned'],
]
for (const file of storyFiles) {
  const rel = relative(root, file).replaceAll('\\', '/')
  readFileSync(file, 'utf8').split(/\r?\n/).forEach((line, index) => {
    for (const match of line.matchAll(/font-size:\s*([^;]+);/g)) {
      const value = match[1].trim()
      if (!allowedFontSizes.has(value)) violations.push(`${rel}:${index + 1} font-size:${value} is outside the six-step scale`)
    }
    for (const [pattern, reason] of bannedPatterns) {
      if (pattern.test(line)) violations.push(`${rel}:${index + 1} ${reason}`)
    }
  })
}

if (violations.length) { console.error(`Token violations (${violations.length})\n${violations.join('\n')}`); process.exitCode = 1 } else console.log('Token check passed: no unapproved color literals, font sizes, or material shortcuts in src/')
