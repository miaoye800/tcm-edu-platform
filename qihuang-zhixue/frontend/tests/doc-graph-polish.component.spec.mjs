import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const doc = readFileSync(new URL('../src/views/DocResourceView.vue', import.meta.url), 'utf8')
const graph = readFileSync(new URL('../src/components/graph/GraphView.vue', import.meta.url), 'utf8')
const visual = readFileSync(new URL('../src/config/stage4Visual.ts', import.meta.url), 'utf8')

assert.match(visual, /blockRevealMs: 250/)
assert.match(visual, /blockStaggerMs: 80/)
assert.match(doc, /@animationend="finishReveal\(index\)"/)
assert.match(doc, /next\.delete\(index\)/)
assert.match(doc, /router\.push\(\{ name: 'quiz' \}\)/)
assert.match(doc, /router\.push\(\{ name: 'atlas' \}\)/)
assert.match(doc, /router\.push\(\{ name: 'path' \}\)/)
assert.match(doc, /教材摘录/)
assert.match(doc, /alchemy-notes>div:before/)
assert.match(graph, /StateLoading/)
assert.match(graph, /StateEmpty/)
assert.match(graph, /StateError/)
console.log('document and graph polish contract checks passed')
