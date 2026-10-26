import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { Script, createContext } from 'node:vm'
import ts from 'typescript'

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')
const compile = (path, replacements = []) => {
  let source = read(path)
  replacements.forEach(([from, to]) => { source = source.replace(from, to) })
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText
  const sandbox = { exports: {}, module: { exports: {} } }
  sandbox.exports = sandbox.module.exports
  new Script(output, { filename: path }).runInContext(createContext(sandbox))
  return sandbox.module.exports
}
const test = (name, fn) => { try { fn(); console.log(`ok - ${name}`) } catch (error) { console.error(`not ok - ${name}`); throw error } }

const config = compile('../src/config/apprenticeStory.ts')
const assets = compile('../src/config/apprenticeStoryAssets.ts', [["if (!import.meta.env.DEV || !fixtureMode) return asset", 'if (!fixtureMode) return asset']])
const media = compile('../src/components/story/storyStageMedia.ts')
const machine = compile('../src/composables/apprenticeStoryMachine.ts', [[
  "import { apprenticeScenes, sceneForStep, type ApprenticeScene } from '../config/apprenticeStory'",
  `const apprenticeScenes = ${JSON.stringify(config.apprenticeScenes)}; const sceneForStep = (step) => apprenticeScenes.find((scene) => scene.requiredStepId === (step == null ? void 0 : step.id)) || apprenticeScenes[apprenticeScenes.length - 1]`,
]])
const view = read('../src/views/ApprenticeStoryView.vue')
const stage = read('../src/components/story/StoryStage.vue')

test('story preserves the required narrative order and skips only performance scenes', () => {
  const ids = config.apprenticeScenes.map((scene) => scene.sceneId)
  assert.equal(ids.slice(0, 4).join(','), 'wake-apothecary,injury-memory,meet-master,master-offer')
  assert.equal(config.apprenticeScenes.find((scene) => scene.sceneId === 'master-offer').canSkip, false)
  assert.equal(config.apprenticeScenes.find((scene) => scene.sceneId === 'prescription-counter').requiredStepId, 'apprentice_act2_prescription')
  const skipped = machine.advancePerformance({ ...machine.initialApprenticeStoryState(), performanceIndex: 2, sceneId: 'meet-master' })
  assert.equal(skipped.sceneId, 'master-offer')
})

test('a backend step is submitted once despite repeat clicks or rerender state', () => {
  const session = { session_id: 's1', mode: 'apprentice', finished: false, evidence_count: 0, step: { id: 'apprentice_prologue', kind: 'identity', title: '', prompt: '', payload: {} } }
  const prepared = machine.applySession(machine.initialApprenticeStoryState(), session)
  const locked = machine.beginStepSubmission(prepared)
  assert.equal(machine.canSubmitStep(locked), false)
  assert.equal(machine.beginStepSubmission(locked).submittedStepIds.length, 1)
  assert(view.includes('if (!current || !canSubmit.value) return'), 'view must gate duplicate submit calls')
  assert(view.includes('submittedStepIds.filter'), 'failed transport should release only the active step lock')
})

test('free prescription and method preferences post through the existing session API', () => {
  assert(view.includes('answerProfileSession(state.value.session!.session_id, current.id, stepPayload())'))
  assert(view.includes('preferences: prescriptionPreferences.value, preference_note: prescriptionPreferenceNote.value.trim()'))
  assert(!view.includes('evaluatePrescription'), 'frontend must not implement prescription grading')
})

test('herb recognition pauses the video and presents one photographic herb with dialogue choices', () => {
  assert(view.includes(':pause-after-seconds="scene.kind === \'quiz\' ? 3.6 : undefined"'), 'the story clip should play briefly before the herb question')
  assert(view.includes('@quiz-intro-complete="quizVideoPaused = true"'), 'show the herb question only after the clip has paused')
  assert(view.includes('v-else-if="scene.kind === \'quiz\' && currentQuestion"'), 'only the active herb question should be mounted')
  assert(!view.includes('v-for="(question, index) in questions"'), 'do not show every recognition question as a form list')
  assert(view.includes('这幅图中，${question.herb_name}的代表功效之一是？'))
  assert(view.includes('class="dialogue-option"'))
  assert(view.includes('我想自己作答 / 补充辨析'))
  assert(view.includes('activeQuestionIndex.value += 1'), 'confirmed answers should advance to the next herb')
  assert(view.includes('questionAnswers[question.id]?.trim() || \'\', free_text: questionNotes[question.id]?.trim() || \'\''), 'every answer and explanation should be submitted together for backend evidence collection')
  assert(view.includes('herb_jinyinhua: \'/story/herbs/jinyinhua.jpg\''), 'all diagnostic herb ids should have a matching photo')
  assert(!view.includes('component-strip'), 'component chips should not obscure the scene')
})

test('story answer UI uses the apothecary-paper redesign with immediate reply tags', () => {
  assert.equal(config.apprenticeVisual.replyCommitMs, 420)
  assert(view.includes('class="herb-sheet"'), 'herb questions should use the specimen sheet')
  assert(view.includes('class="story-tags"'), 'answers should render as the right-side tag stack')
  assert(view.includes('@click="finishDialogue"'), 'the paper dialogue should skip the typewriter on click')
  assert(view.includes('question.image_url ||'), 'per-question image URLs should take precedence')
  assert(view.includes('@click="chooseQuizOption(option)"'))
  assert(view.includes('@click="chooseIdentity(card)"'))
  assert(!view.includes('quiz-panel'), 'the old frosted quiz panel should be gone')
})

test('delivered chapter videos are mapped to their matching story beats', () => {
  const wake = assets.storyAssetForScene('wake-apothecary')
  const memory = assets.storyAssetForScene('injury-memory')
  const quiz = assets.storyAssetForScene('herb-dialogue')
  const prescription = assets.storyAssetForScene('prescription-counter')
  assert.equal(wake.delivery.fileName, 'section-one-1.mp4')
  assert.equal(quiz.delivery.fileName, 'section-two-1.mp4')
  assert.equal(prescription.delivery.fileName, 'section-three-1.mp4')
  assert.notEqual(wake.backdrop, memory.backdrop)
  assert.equal(wake.video.src, '/story/section-one-1.mp4')
  assert.equal(quiz.video.poster, '/story/posters/section-two-1-story.jpg')
  assert(config.apprenticeScenes.every((scene) => !Object.hasOwn(scene, 'video')), 'narrative config must not carry resource paths')
})

test('ready media uses its matching poster and missing or failed media falls back to static stage', () => {
  const ready = assets.storyAssetForStage('wake-apothecary', 'ready')
  const failed = assets.storyAssetForStage('injury-memory', 'failed')
  assert.equal(ready.video.src, '/story/__fixtures__/valid-video.mp4')
  assert.equal(ready.video.poster, '/story/__fixtures__/valid-video_poster.png')
  assert.equal(failed.video.src, '/story/__fixtures__/missing-video.mp4')
  assert.equal(media.deriveStoryMediaState(undefined, false, false, false), 'static-pending')
  assert.equal(media.deriveStoryMediaState(ready.video, false, true, false), 'video-failed')
  assert.equal(media.mediaStatusCopy('video-failed'), '视频未能播放，已切换静态演出')
  assert(stage.includes('@error="onVideoError"'))
  assert(stage.includes('object-fit:contain'))
  assert(stage.includes('video.pause()'), 'the question transition should stop playback on its frame')
  assert(!stage.includes('stage-grain'), 'decorative scan lines should not soften delivered footage')
})

test('reduced motion initializes synchronously and prevents the first video render', () => {
  const ready = assets.storyAssetForStage('wake-apothecary', 'ready')
  assert.equal(media.readInitialReducedMotionPreference(() => ({ matches: true })), true)
  assert.equal(media.readInitialReducedMotionPreference(() => ({ matches: false })), false)
  assert.equal(media.shouldRenderStoryVideo(ready.video, true, false), false, 'a reduced-motion first render must not create a video or its MP4 request')
  assert.equal(media.shouldRenderStoryVideo(ready.video, false, false), true, 'the normal ready fixture remains eligible to render')
  assert.equal(media.deriveStoryMediaState(ready.video, true, false, false), 'static-reduced-motion')
  assert.equal(media.mediaStatusCopy('static-reduced-motion'), '静态演出 · 已遵循减少动态设置')
  assert(stage.includes('const reducedMotion = ref(readInitialReducedMotionPreference())'), 'component must read the preference before its first render')
  assert(stage.includes('shouldRenderStoryVideo(asset.value.video, reducedMotion.value, didFail.value)'))
  assert(stage.includes('v-if="shouldRenderVideo"'), 'Vue must block video creation instead of relying on CSS')
  assert(stage.includes('motionQuery.addEventListener'), 'runtime preference changes must remain observed')
  assert(stage.includes('prefers-reduced-motion: reduce'))
})

test('production package and resource handoff notes are present without generated poster claims', () => {
  assert(existsSync(new URL('../../docs/apprentice-story-video-production.md', import.meta.url)))
  assert(existsSync(new URL('../public/story/README.md', import.meta.url)))
  const production = read('../../docs/apprentice-story-video-production.md')
  for (const sceneId of ['wake-apothecary', 'injury-memory', 'meet-master', 'master-offer', 'prescription-counter', 'apprentice-complete']) assert(production.includes(`\`${sceneId}\``))
  assert(production.includes('通用负向提示词'))
})
