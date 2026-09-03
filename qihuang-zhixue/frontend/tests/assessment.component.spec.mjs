import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const store = readFileSync(new URL('../src/stores/assessment.ts', import.meta.url), 'utf8')
const view = readFileSync(new URL('../src/views/AssessmentView.vue', import.meta.url), 'utf8')
const practice = readFileSync(new URL('../src/views/QuizResourceView.vue', import.meta.url), 'utf8')
assert.match(store, /combo = grade\.correct \? this\.combo \+ 1 : 0/)
assert.match(view, /remaining\.value <= 0/)
assert.match(view, /caseStep\.value < 2/)
assert.match(view, /store\.result\?\.passed/)
assert.doesNotMatch(practice, /finishAssessment/)
console.log('assessment component contract checks passed')
