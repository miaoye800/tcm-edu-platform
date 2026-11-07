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
const identity = read('../src/views/apprentice/IdentityScene.vue')
const quiz = read('../src/views/apprentice/QuizScene.vue')
const resource = read('../src/views/apprentice/ResourceScene.vue')
const prescription = read('../src/views/apprentice/PrescriptionScene.vue')
const rule = read('../src/views/apprentice/RuleScene.vue')
const stage = read('../src/components/story/StoryStageFrame.vue')
const dialogueBox = read('../src/components/story/StoryDialogueBox.vue')
const bubble = read('../src/components/story/StoryDiscipleBubble.vue')

test('story preserves the required narrative order and carries no dead skip flag', () => {
  const ids = config.apprenticeScenes.map((scene) => scene.sceneId)
  assert.equal(ids.join(','), [
    'wake-apothecary',
    'injury-memory',
    'meet-master',
    'master-offer',
    'herb-dialogue',
    'prescription-counter',
    'choose-keepsake',
    'make-rule',
    'apprentice-complete',
  ].join(','))
  assert(config.apprenticeScenes.every((scene) => !('canSkip' in scene)), 'canSkip was never consumed and must stay removed')
  assert(config.apprenticeScenes.filter((scene) => scene.speaker === '老中医').length >= 3, 'the master is credited on the shots where he is the one talking')
})

test('scene step bindings and timing constants are stable', () => {
  const prescription = config.apprenticeScenes.find((scene) => scene.sceneId === 'prescription-counter')
  assert.equal(prescription.requiredStepId, 'apprentice_act2_prescription')
  const identityScene = config.apprenticeScenes.find((scene) => scene.sceneId === 'master-offer')
  assert.equal(identityScene.requiredStepId, 'apprentice_prologue')
  assert.equal(config.apprenticeVisual.replyCommitMs, 420)
  assert.equal(config.apprenticeVisual.autoPerformanceMs, 5200)
  assert(!('sceneTransitionMs' in config.apprenticeVisual), 'the unused transition constant is gone')
})

test('sceneForStep resolves every step and falls back to the closing scene', () => {
  for (const stepId of ['apprentice_prologue', 'apprentice_act1_quiz', 'apprentice_act2_prescription', 'apprentice_act3_resource', 'apprentice_act4_rule']) {
    assert.notEqual(config.sceneForStep({ id: stepId }).sceneId, 'apprentice-complete', `${stepId} should map to its own scene`)
  }
  assert.equal(config.sceneForStep(null).sceneId, 'apprentice-complete')
})

test('the dialogue bubble follows the video and is not a click target', () => {
  assert(!dialogueBox.includes('@click'), 'the dialogue box must not respond to clicks')
  assert(!dialogueBox.includes('story-dialogue-box__hit'), 'the click overlay is gone')
  assert(dialogueBox.includes('watch(() => props.text, start'), 'swapping the line re-runs the typewriter')
  assert(dialogueBox.includes('story-dialogue-box__next'), 'the decorative corner diamond stays')
  assert(view.includes(':text="dialogueText"'), 'playback driven copy feeds the box')
  assert(!view.includes('点击对白继续'), 'no click-to-continue affordance')
})

test('the answer field is a disciple bubble, not a form', () => {
  assert(bubble.includes('@keydown.enter.exact.prevent="submit"'), 'Enter submits')
  assert(bubble.includes(`@keydown.esc.prevent="emit('cancel')"`), 'Esc collapses')
  assert(!bubble.includes('<label'), 'no form labels')
  assert(!bubble.includes('字数'), 'no character counter')
  assert(!bubble.includes('呈上此答'), 'no separate submit button')
  assert(identity.includes("icon: 'brush'") && quiz.includes("icon: 'brush'") && resource.includes("icon: 'brush'") && prescription.includes("icon: 'brush'") && rule.includes("icon: 'brush'"), 'free answers open through a brush pill on every answer screen')
})

test('choice screens reuse the shared story components', () => {
  for (const file of [identity, quiz, resource, prescription, rule]) {
    assert(file.includes('StoryChoiceStack'), 'renders through the shared stack')
    assert(file.includes('StoryDiscipleBubble'), 'mounts the disciple bubble')
  }
  assert(quiz.includes('StorySpecimenCard'), 'the quiz shows the herb specimen card')
  assert(!quiz.includes('v-for="(question, index) in questions"'), 'questions are asked one at a time')
  // The prescription and the pact used to be full-screen cream forms. Both now
  // open one field at a time from the same right-hand pill menu.
  for (const file of [prescription, rule]) {
    assert(!file.includes('<textarea'), 'the fields live in the disciple bubble')
    assert(!file.includes('<form'), 'no full-page form')
  }
  assert(!prescription.includes('preferences'), 'the method stamps are gone')
  assert(!rule.includes('accept-challenge') && !rule.includes('learning-note'), 'the pact asks for two numbers, nothing else')
})

test('answer payload keeps the backend contract', () => {
  assert(view.includes('answerProfileSession(state.value.session!.session_id, current.id, stepPayload())'))
  assert(view.includes("case: caseText.value, prescription: prescription.value"), 'the prescription sends exactly the two fields the screen asks for')
  assert(view.includes('daily_minutes: dailyMinutes.value, target_days: targetDays.value'), 'the pact sends exactly the two numbers the screen asks for')
  assert(!view.includes('accept_challenge') && !view.includes('learning_note'), 'the pact no longer claims a challenge opt-in nobody was asked about')
  assert(view.includes("identity_card: identityCard.value, identity_custom: '', utterance: utterance.value.trim()"))
  assert(view.includes('answers: questions.value.map((question) => ({ question_id: question.id'))
  assert(!view.includes('evaluatePrescription'), 'the frontend never grades the prescription itself')
})

test('the quiz never names the herb it is asking about', () => {
  // The whole point of the screen is that the disciple recognises the herb from
  // the photograph. Any rendered herb name turns it into a reading exercise.
  assert(!quiz.includes('herb_name'), 'the specimen card must not receive the herb name')
  assert(!quiz.includes('herb_hint'), 'the nature/flavour hint would give the herb away too')
  assert(!quiz.includes('太子参'), 'the answer bubble placeholder must not name a herb')
  assert(!/\$\{question\.herb_name\}/.test(view), 'the dialogue must not interpolate the herb name')
  assert(view.includes('apprenticeQuizPrompt'), 'the question text comes from the shared constant')
})

test('reaction time is measured per question instead of hardcoded', () => {
  assert(view.includes('questionResponseTimes[question.id]'), 'each answer carries its own measured time')
  assert(view.includes('stopQuizClock(question.id)'), 'the clock stops when an answer is given')
  assert(!view.includes('response_time_ms: responseTime })) }'), 'the quiz must not fall back to the 1000ms constant')
})

test('every scene reports a real reaction time instead of a constant', () => {
  // `const responseTime = 1000` made every single act look like a 1s answer,
  // which the backend then bucketed as "fast" across the board.
  assert(!view.includes('const responseTime = 1000'), 'the hardcoded 1000ms must be gone')
  assert(view.includes('let stepShownAt: number | null = null'), 'the step clock is a real timestamp')
  assert(view.includes('stepShownAt = Date.now()'), 'the clock starts when the question becomes answerable')
  assert(view.includes('const elapsed = stepElapsedMs()'), 'the payload reads the measured elapsed time')
  assert(view.includes('elapsed === null ? {} : { response_time_ms: elapsed }'), 'an unmeasured answer must omit the field rather than send 0')
})

test('extra story questions are collected and sent back with the step', () => {
  assert(view.includes(':questions="storyQuestions"'), 'the追问 panel is driven by the step payload')
  assert(view.includes('step.value?.payload.story_questions'), 'questions come from the backend, never hardcoded')
  assert(view.includes('const extras = Object.keys(storyAnswers).length ? { story_answers: { ...storyAnswers } } : {}'), 'answers travel as story_answers')
  assert(view.includes('Object.keys(storyAnswers).forEach((key) => delete storyAnswers[key])'), 'answers are cleared with the rest of the step fields')
  const extra = read('../src/components/story/StoryExtraQuestions.vue')
  assert(!extra.includes('component'), 'the component tag must stay server-side')
  assert(!extra.includes('dimension'), 'the dimension tag must stay server-side')
  assert(extra.includes('可留白'), 'a disciple may skip a follow-up question')
})

test('loading and disabled submission guards remain', () => {
  assert(view.includes(':aria-busy="submitting"') || prescription.includes(':aria-busy="submitting"'), 'submitting state is exposed')
  assert(prescription.includes('disabled: !props.canSubmit') && rule.includes('disabled: !props.canSubmit'), 'the prescription and pact submit pills stay locked until the answer is complete')
  assert(prescription.includes(':disabled="submitting"') && rule.includes(':disabled="submitting"'), 'every prescription and pact pill locks while a submit is in flight')
  assert(view.includes('beginStepSubmission(state.value)'), 'the idempotency lock is applied before the request')
  assert(view.includes('submittedStepIds.filter((id) => id !== current.id)'), 'a transport error releases the lock so the step is retryable')
})

test('the media state machine keeps five states and their copy', () => {
  for (const state of ['static-pending', 'video-loading', 'video-ready', 'video-failed', 'static-reduced-motion']) {
    assert.equal(media.deriveStoryMediaState(undefined, false, false, false), 'static-pending')
    assert(media.mediaStatusCopy(state).length > 0, `${state} needs copy`)
  }
  assert.equal(media.deriveStoryMediaState({ src: 'a', poster: 'b', durationSeconds: 1 }, true, false, true), 'static-reduced-motion')
  assert.equal(media.deriveStoryMediaState({ src: 'a', poster: 'b', durationSeconds: 1 }, false, true, false), 'video-failed')
  assert.equal(media.mediaStatusCopy('video-failed'), '视频未能播放，已切换静态演出')
  assert.equal(media.shouldRenderStoryVideo(undefined, false, false), false)
  assert.equal(media.shouldRenderStoryVideo({ src: 'a', poster: 'b', durationSeconds: 1 }, true, false), false)
  assert(media.readInitialReducedMotionPreference((query) => ({ matches: query === '(prefers-reduced-motion: reduce)' })), 'the first frame already respects reduced motion')
})

test('the stage fills the frame and honours reduced motion', () => {
  assert(stage.includes('object-fit: cover'), 'the 540p source covers the stage instead of letterboxing')
  assert(!stage.includes('stage-grain'), 'no faux film grain')
  assert(stage.includes('@error="onVideoError"'), 'a broken source falls back to the static stage')
  assert(stage.includes('motionQuery.addEventListener'), 'live reduced-motion changes are observed')
  assert(stage.includes('videoElement.value?.pause()'), 'unmount stops playback')
})

test('the story machine keeps restore and idempotency behaviour', () => {
  const initial = machine.initialApprenticeStoryState()
  assert.equal(initial.performanceIndex, 0)
  const restored = machine.restoreApprenticeStoryState(JSON.stringify(initial))
  assert(restored && restored.performanceIndex === 0, 'a persisted state round-trips')
  assert.equal(machine.restoreApprenticeStoryState('not json'), null, 'garbage storage is rejected')
  const locked = machine.beginStepSubmission({ ...initial, session: { session_id: 's', step: { id: 'apprentice_prologue' } } })
  assert(machine.canSubmitStep(locked) === false, 'a step under submission cannot be submitted twice')
  assert(machine.sceneForState(initial).sceneId === 'wake-apothecary', 'a fresh state opens on the prologue')
})

test('asset mapping still points at the delivered chapter videos', () => {
  assert.equal(assets.storyAssetForScene('wake-apothecary').video.src, '/story/section-one-1.mp4')
  assert.equal(assets.storyAssetForScene('herb-dialogue').video.src, '/story/section-two-1.mp4')
  assert.equal(assets.storyAssetForScene('prescription-counter').video.poster, '/story/posters/section-three-1-story.jpg')
  // The whole prologue is one continuous shot. Every beat must point at the same
  // source, otherwise the <video> element is remounted between beats and the shot
  // restarts from zero - the flash the viewer used to see.
  const prologue = ['wake-apothecary', 'injury-memory', 'meet-master', 'master-offer']
  assert.equal(new Set(prologue.map((sceneId) => assets.storyAssetForScene(sceneId).video.src)).size, 1, 'the prologue beats share one film')
  assert.equal(assets.storyAssetForScene('unknown').sceneId, 'fallback')
  assert(!('fullStoryVideo' in assets), 'the unreferenced compilation is gone')
  // The three chapters that fade to black say where the stage comes to rest after
  // the fade; the rest end on a usable frame and need no rest mark.
  assert.equal(assets.storyAssetForScene('prescription-counter').video.restFrameSeconds, 13.8)
  assert.equal(assets.storyAssetForScene('choose-keepsake').video.restFrameSeconds, 13.9)
  assert.equal(assets.storyAssetForScene('make-rule').video.restFrameSeconds, 8.9)
  for (const sceneId of ['wake-apothecary', 'herb-dialogue', 'apprentice-complete']) {
    assert.equal(assets.storyAssetForScene(sceneId).video.restFrameSeconds, undefined, `${sceneId} ends on a usable frame`)
  }
  // A rest mark must sit inside its own film and before the fade, or the stage
  // would come to rest on a black frame anyway.
  for (const [sceneId, asset] of Object.entries(assets.apprenticeStoryAssets)) {
    const rest = asset.video?.restFrameSeconds
    if (rest === undefined) continue
    assert(rest < asset.video.durationSeconds, `${sceneId} 的定格秒数必须落在片长之内`)
  }
})

test('the film must finish before the question appears', () => {
  // Nothing interactive renders until the stage reports the end of the shot.
  assert(stage.includes("emit('videoEnded', played)"), 'the stage reports the end of playback')
  assert(view.includes('@video-ended="onVideoEnded"'), 'the view listens for it')
  assert(view.includes("const INTERACTIVE_KINDS = ['identity', 'quiz', 'prescription', 'resource', 'rule']"))
  assert(view.includes('videoEnded.value && INTERACTIVE_KINDS.includes(scene.value.kind)'), 'the answer layer is gated on the film finishing')
  assert(view.includes('<template v-if="interactionReady">'), 'the whole answer layer is behind that gate')
  // No timer may cut a shot short: the only remaining one is the no-film fallback.
  assert(!view.includes('pauseAfterSeconds') && !view.includes('holdAtEnd'), 'nothing truncates a shot')
  assert(!stage.includes('pauseAfterSeconds') && !stage.includes('holdAtEnd'), 'the stage has no truncation knobs left')
  assert(view.includes('apprenticeVisual.autoPerformanceMs'), 'a beat with no film still gets reading time')
  // Nothing may stop a film before its own `ended`: the stage only reports the
  // clock upward now, and never pauses or seeks on a timer.
  assert(!stage.includes('holdAt'), 'the hold-frame knob is gone; every shot plays out')
  assert(!/const onVideoTimeUpdate[\s\S]{0,200}announceEnded/.test(stage), 'the clock handler never fakes an end')
  // After the fade the stage comes to rest on the last readable frame, behind a
  // veil, so the question is not asked in front of a dead black screen.
  assert(stage.includes('const holdRestFrame'), 'the stage parks the shot after the fade')
  assert(stage.includes('video.currentTime = rest'), 'the rest frame is a seek, not a cut')
  assert(stage.includes("video.addEventListener('seeked', uncover"), 'the veil lifts only once the frame is painted')
  assert(stage.includes('story-stage-frame__veil'), 'the seek is hidden behind a veil')
  // A `canplay` fired by that seek must not restart the chapter.
  assert(/if \(didEnd\.value\) \{[\s\S]{0,80}video\.pause\(\)[\s\S]{0,20}return/.test(stage), 'a finished shot never resumes')
})

test('the prologue beats ride one timeline instead of cutting the shot', () => {
  assert(view.includes('const PROLOGUE_BEATS = [4, 14]'), 'the beats are timed against the film, on the cue boundaries')
  assert(view.includes('watch(playbackSeconds'), 'playback time drives the beat changes')
  assert(view.includes('while (state.value.performanceIndex < target) state.value = advancePerformance(state.value)'), 'a beat change never remounts the stage')
  // A surviving element is already loaded; clearing the flag fades the picture
  // out with no way back.
  assert(stage.includes('didLoad.value = video.readyState >= 2'), 'a reused video keeps its loaded state')
})

test('the master speaks through the dialogue box, the narrator asks', () => {
  const cues = read('../src/config/apprenticeStoryCues.ts')
  const cueModule = compile('../src/config/apprenticeStoryCues.ts')
  assert(cues.includes('apprenticeStoryNarration'), 'the question copy is kept apart from the spoken lines')
  assert(cues.includes("speaker: '老中医'"), 'the master lines are attributed to him, not to the narrator')
  assert(view.includes('dialogueAt(scene.value, playbackSeconds.value)'), 'while the film runs the box follows the film')
  assert(view.includes('apprenticeStoryNarration[scene.value.sceneId]'), 'once it ends the narrator asks')
  assert(view.includes("speakerName.value === '旁白'"), 'the name plate switches tone with the speaker')

  // The prologue's three beats share one cue table, so a beat change never drops
  // the master's line back to the scene default.
  const scene = config.apprenticeScenes.find((item) => item.sceneId === 'wake-apothecary')
  for (const sceneId of ['wake-apothecary', 'injury-memory', 'meet-master']) {
    assert.equal(cueModule.apprenticeStoryCues[sceneId], cueModule.apprenticeStoryCues['wake-apothecary'], `${sceneId} shares the prologue cues`)
  }
  assert.equal(cueModule.dialogueAt(scene, 0).text, scene.dialogue, 'before the first cue the scene line stands')
  assert.equal(cueModule.dialogueAt(scene, 5).text, '眼前的景象渐渐远去——你开始想起，自己是如何在雨后的山阶上受伤的。')
  assert.equal(cueModule.dialogueAt(scene, 5).speaker, '旁白')
  assert.equal(cueModule.dialogueAt(scene, 17).text, '快醒醒')
  assert.equal(cueModule.dialogueAt(scene, 17).speaker, '老中医')
  assert.equal(cueModule.dialogueAt(scene, 99).text, '可愿随我习这岐黄之术')
  // An unlisted scene degrades to its own line instead of throwing.
  const unlisted = { sceneId: 'not-in-the-table', dialogue: '（这一拍还没有台词表。）', speaker: '老中医' }
  assert.equal(cueModule.dialogueAt(unlisted, 12).text, unlisted.dialogue)
  assert.equal(cueModule.dialogueAt(unlisted, 12).speaker, '老中医')
  // Every other scene opens on its own 0-second cue, in text and in speaker. The
  // box falls back to `dialogue` for the instant before the first `timeupdate`,
  // so a mismatch here is the "two lines fighting over one beat" bug again.
  const shared = new Set(['wake-apothecary', 'injury-memory', 'meet-master'])
  for (const item of config.apprenticeScenes) {
    if (shared.has(item.sceneId)) continue
    const opening = cueModule.dialogueAt(item, 0)
    assert.equal(opening.text, item.dialogue, `${item.sceneId} 的 0 秒台词必须等于 dialogue`)
    assert.equal(opening.speaker, item.speaker ?? '旁白', `${item.sceneId} 的 0 秒说话人必须等于 scene.speaker`)
  }
})

test('the later chapters speak on the seconds the film speaks on', () => {
  // The seconds come from the burned-in subtitles of each chapter. The master has
  // not opened his mouth before these marks, so the box holds a stage direction
  // instead of putting his line up early.
  const cueModule = compile('../src/config/apprenticeStoryCues.ts')
  const scene = (sceneId) => config.apprenticeScenes.find((item) => item.sceneId === sceneId)
  const opening = (sceneId, seconds) => cueModule.dialogueAt(scene(sceneId), seconds)

  assert.equal(opening('herb-dialogue', 6).text, '小友，先识药，再辨性，这几味药你可认得')
  assert.equal(opening('herb-dialogue', 6).speaker, '老中医')
  assert.equal(opening('herb-dialogue', 5.9).speaker, '旁白', '他第 6 秒才开口，之前不许抢话')

  assert.equal(opening('prescription-counter', 0).text, '病人印堂发黑，有点畏寒', '第三段一开口就在报病例')
  assert.equal(opening('prescription-counter', 5).text, '头部发晕，数盲')
  assert.equal(opening('prescription-counter', 10).text, '照方抓药，剂量与配伍，皆马虎不得')
  assert.equal(opening('prescription-counter', 10).speaker, '老中医')

  assert.equal(opening('choose-keepsake', 10).text, '挑一件趁手的法门带走，日后自有用处')
  assert.equal(opening('choose-keepsake', 9.9).speaker, '旁白')

  assert.equal(opening('make-rule', 3).text, '学医贵在有恒')
  assert.equal(opening('make-rule', 6).text, '你且定下每日功课与目标')
  assert.equal(opening('make-rule', 2.9).speaker, '旁白')

  assert.equal(opening('apprentice-complete', 2).text, '拜师礼成')
  assert.equal(opening('apprentice-complete', 4).text, '此乃你此刻之画像，随你修行，自会不断生长')
  assert.equal(opening('apprentice-complete', 2).speaker, '老中医')

  // Every cue must land inside its own film, otherwise the line never shows.
  const films = compile('../src/config/apprenticeStoryAssets.ts', [["if (!import.meta.env.DEV || !fixtureMode) return asset", 'if (!fixtureMode) return asset']])
  for (const [sceneId, cues] of Object.entries(cueModule.apprenticeStoryCues)) {
    const length = films.storyAssetForScene(sceneId).video?.durationSeconds ?? 0
    for (const cue of cues) {
      assert(cue.at < length, `${sceneId} 的 ${cue.at}s 台词落在 ${length}s 的片子之外，永远说不出来`)
    }
  }
})

test('prologue beat lines agree with the cue table', () => {
  // The beat boundary and the cue boundary have to land on the same second. When
  // they drift, the box shows the scene's own line for the instant before the cue
  // catches up - two lines fighting over one beat, which is what the viewer saw.
  const cueModule = compile('../src/config/apprenticeStoryCues.ts')
  const beats = ['wake-apothecary', 'injury-memory', 'meet-master']
  // Read the real cut points instead of restating them, so a change to one side
  // fails here rather than on screen.
  const cutPoints = view.match(/const PROLOGUE_BEATS = \[([\d,\s]+)\]/)
  assert(cutPoints, 'the view declares its beat cut points')
  const beatSeconds = [0, ...cutPoints[1].split(',').map((value) => Number(value.trim()))]
  assert.equal(beatSeconds.length, beats.length, 'one cut point per beat change')
  beats.forEach((sceneId, index) => {
    const scene = config.apprenticeScenes.find((item) => item.sceneId === sceneId)
    const line = cueModule.dialogueAt(scene, beatSeconds[index])
    assert.equal(line.text, scene.dialogue, `${sceneId} 在 ${beatSeconds[index]}s 的台词必须等于场景自带的 dialogue`)
  })
  // The old leftovers must not come back: they contradicted the cue table.
  assert(!config.apprenticeScenes.some((scene) => scene.dialogue.includes('湿滑的青石阶')), '死掉的旧台词已删')
  assert(!config.apprenticeScenes.some((scene) => scene.dialogue.includes('煎药守了半夜')), '死掉的旧台词已删')
})

test('the playback clock survives a beat change inside one shot', () => {
  // Resetting the clock on every scene change is what made the box fall back to
  // the scene's own line mid-shot. It may only reset when the film itself changes.
  assert(view.includes('const sceneSource = computed('), 'the view watches the film, not the scene id')
  assert(view.includes('watch(sceneSource, (source, previous) => {'), 'the reset is keyed on the source')
  assert(view.includes('if (source === previous) return'), 'a beat change inside one shot keeps the clock and the finished flag')
  assert(!/watch\(\(\) => scene\.value\.sceneId,[\s\S]{0,200}playbackSeconds\.value = 0/.test(view), 'no scene-id watcher resets the clock')
})


test('the closing beat ends on the master, with no card in the way', () => {
  // The certificate card was removed outright: the last beat is just the film and
  // the master's closing line in the dialogue box, like every beat before it.
  assert(!existsSync(new URL('../src/components/story/StoryCert.vue', import.meta.url)), '结业卷组件已删除')
  assert(!view.includes('StoryCert'), '视图里不再引用结业卷')
  assert(!view.includes('story-cert'), '收徒礼成那屏不再有卡片')
  assert(view.includes("'rule', 'complete'].includes(scene.value.kind)"), '收徒礼成仍然要有对话框')
  // No answer on this beat, so no narrator instruction either.
  assert(!read('../src/config/apprenticeStoryCues.ts').includes("'apprentice-complete': '今日所答"), '收徒礼成没有旁白操作指引')
  const cueModule = compile('../src/config/apprenticeStoryCues.ts')
  const closing = config.apprenticeScenes.find((scene) => scene.sceneId === 'apprentice-complete')
  assert.equal(cueModule.dialogueAt(closing, 99).text, '此乃你此刻之画像，随你修行，自会不断生长', '气泡停在老中医最后一句')
  assert.equal(cueModule.dialogueAt(closing, 99).speaker, '老中医')
})

test('the herb photo map covers the five illustrated specimens', () => {
  for (const herb of ['taizishen', 'dangshen', 'huangqi', 'chenpi', 'jinyinhua']) {
    assert(view.includes(`herb_${herb}: '/story/herbs/${herb}.jpg'`), `${herb} needs a specimen photo`)
  }
  assert(view.includes("|| '/story/herbs/field-card.svg'"), 'unknown herbs fall back to the placeholder card')
})

test('the production docs the story depends on still exist', () => {
  assert(existsSync(new URL('../../docs/apprentice-story-video-production.md', import.meta.url)))
  assert(existsSync(new URL('../public/story/README.md', import.meta.url)))
})
