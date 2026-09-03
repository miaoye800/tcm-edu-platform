import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const store = readFileSync(new URL('../src/stores/path.ts', import.meta.url), 'utf8')
const view = readFileSync(new URL('../src/views/MeridianMapView.vue', import.meta.url), 'utf8')
const visual = readFileSync(new URL('../src/config/stage4Visual.ts', import.meta.url), 'utf8')
const node = readFileSync(new URL('../src/components/meridian/MeridianNode.vue', import.meta.url), 'utf8')

// The mapping must keep the seven review-approved visual states addressable.
for (const status of ['recommended', 'current', 'mastered', 'free', 'deferred', 'exam_hot', 'custom']) assert.match(store, new RegExp(`'${status}'`))
assert.match(store, /state === 'completed'.*'mastered'/)
assert.match(store, /state === 'paused'.*'deferred'/)

// Reorder errors come from the API and are presented to the learner instead of silently reverting.
assert.match(view, /await path\.reorder\(ids\)/)
assert.match(view, /ElMessage\.warning/)

// Completion first applies every server state change, then schedules flow and bloom.
assert.match(view, /await path\.complete\(selected\.value\.id\)/)
assert.match(view, /motionPath/)
assert.match(view, /back\.out/)

// A first click expands a fuzzy direction through the lazy API flow.
assert.match(view, /await path\.expand\(node\.domainId\)/)
assert.match(view, /is-foggy/)

// The visual layer has six explicit, token-driven states while route semantics stay intact.
for (const state of ['locked', 'available', 'current', 'lit', 'deferred', 'priority']) assert.match(visual, new RegExp(`\\b${state}: \\{`))
assert.match(visual, /particleCount: 16/)
assert.match(view, /queueQiFlow/)
assert.match(view, /transitionSeconds/)
assert.match(node, /prefers-reduced-motion/)
assert.match(node, /aria-label/)
console.log('meridian-map component contract checks passed')
