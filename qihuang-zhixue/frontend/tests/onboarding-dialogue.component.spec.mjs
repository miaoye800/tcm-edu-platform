import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Script, createContext } from 'node:vm'
import ts from 'typescript'

const root = process.cwd()
const onboardingPath = join(root, 'src/views/OnboardingView.vue')
const profileViewPath = join(root, 'src/views/ProfileView.vue')
const profileStorePath = join(root, 'src/stores/profile.ts')
const radarChartPath = join(root, 'src/components/profile/RadarChart.vue')
const dialogueConfigPath = join(root, 'src/config/dialogueQuestions.ts')
const dialogueComponentPath = join(root, 'src/components/profile/ProfileDialogue.vue')

const assert = (condition, message) => {
  if (!condition) throw new Error(message)
}

const test = (name, fn) => {
  try {
    fn()
    console.log(`ok - ${name}`)
  } catch (error) {
    console.error(`not ok - ${name}`)
    throw error
  }
}

const compileTsModule = (path) => {
  const source = readFileSync(path, 'utf8')
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      esModuleInterop: true,
    },
  }).outputText

  const sandbox = { exports: {}, module: { exports: {} } }
  sandbox.exports = sandbox.module.exports
  new Script(compiled, { filename: path }).runInContext(createContext(sandbox))
  return sandbox.module.exports
}

const onboarding = readFileSync(onboardingPath, 'utf8')
const profileView = readFileSync(profileViewPath, 'utf8')
const profileStore = readFileSync(profileStorePath, 'utf8')
const radarChart = readFileSync(radarChartPath, 'utf8')
const dialogueComponent = readFileSync(dialogueComponentPath, 'utf8')
const dialogueConfig = compileTsModule(dialogueConfigPath)
const modeCardsBlock = onboarding.slice(onboarding.indexOf('const modeCards'), onboarding.indexOf('const activeStep'))

test('dialogue replaces quickcard onboarding entry', () => {
  assert(modeCardsBlock.includes("mode: 'dialogue'"), 'mode cards should expose dialogue')
  assert(modeCardsBlock.includes("title: '对话问诊(dialogue)'"), 'dialogue card title missing')
  assert(!modeCardsBlock.includes("mode: 'quickcard'"), 'quickcard should not be visible as an onboarding card')
  assert(onboarding.includes("<ProfileDialogue"), 'onboarding should render ProfileDialogue for dialogue mode')
})

test('dialogue streams indefinitely and finishes by user action', () => {
  assert(onboarding.includes('const answerDialogue = async (payload: AnswerPayload)'), 'dialogue answer handler missing')
  assert(onboarding.includes('streamProfileDialogueChat(session.value.session_id, utterance'), 'dialogue answer should use the SSE chat endpoint')
  assert(onboarding.includes('dialogueUtteranceFromPayload'), 'dialogue answer should convert utterance or quick_reply into stream text')
  assert(onboarding.includes("event === 'thinking'"), 'dialogue stream should handle thinking events')
  assert(onboarding.includes("event === 'message'"), 'dialogue stream should handle message events')
  assert(onboarding.includes("event === 'message_done'"), 'dialogue stream should finish the visible assistant bubble')
  const messageDoneBlock = onboarding.slice(onboarding.indexOf("event === 'message_done'"), onboarding.indexOf("event === 'evidence'"))
  assert(!messageDoneBlock.includes('dialoguePending.value = false'), 'input should remain locked until the backend state event is committed')
  assert(onboarding.includes(':stream-events="dialogueStreamEvents"'), 'dialogue stream events should pass through an ordered queue prop')
  assert(onboarding.includes('const dialogueStreamEvents = ref'), 'dialogue stream events should be queued instead of stored in a single ref')
  assert(onboarding.includes('dialogueStreamEvents.value.push'), 'each SSE event should be appended so same-tick frames are not overwritten')
  assert(onboarding.includes("event === 'radar'"), 'dialogue stream should handle radar events')
  assert(onboarding.includes("event === 'state'"), 'dialogue stream should handle state events')
  assert(!onboarding.includes("!dialogueConverged.value && activeStep.value?.kind !== 'confirm'"), 'finish should not wait for convergence or confirm step')
  assert(onboarding.includes('const confirmDialogue = async (_payload: ConfirmPayload)'), 'dialogue confirm handler missing')
  assert(onboarding.includes('await finishCurrentSession()'), 'dialogue confirm should finish the profile session')
  assert(onboarding.includes('await dialogueStreamInFlight.catch'), 'dialogue finish should wait for in-flight background updates')
  assert(onboarding.includes("await router.replace('/profile')"), 'dialogue flow should jump to the profile page after finish')
  assert(onboarding.includes('profileStore.setDialogueRadarChanges'), 'dialogue finish should preserve radar changes for highlight')
  assert(onboarding.includes('readActiveProfileSession'), 'dialogue should restore its server-owned session after refresh')
  assert(onboarding.includes('readProfileSession'), 'refresh and disconnect recovery should rehydrate from the backend')
})

test('missing resources or keys can fall back to templates and manual card selection', () => {
  assert(dialogueConfig.dialogueQuestionTemplates.length === 6, 'six dialogue question templates are required')
  assert(
    dialogueConfig.dialogueQuestionTemplates.every((item) => item.questions.length > 0 && item.quickReplies.length > 0),
    'each dialogue template should include questions and quick replies',
  )
  assert(dialogueConfig.dialogueQuestionChips.length >= 6, 'quick bubbles should include at least six entries')
  assert(dialogueComponent.includes('dialogueQuestionChips'), 'ProfileDialogue should expose local quick bubbles')
  assert(dialogueComponent.includes('@click="selectCard(card.name)"'), 'manual card selection should remain available')
  assert(dialogueComponent.includes('createConfirmPayload(null)'), 'skip/manual fallback should not throw without inferred card')
})

test('dialogue finish highlights radar changes on profile page', () => {
  assert(profileStore.includes('latestRadarChanges'), 'profile store should keep latest dialogue radar changes')
  assert(profileStore.includes('setDialogueRadarChanges'), 'profile store should expose a setter for dialogue radar changes')
  assert(profileView.includes('dialogueSummaryVisible'), 'profile page should show a dialogue summary banner')
  assert(profileView.includes('highlightedRadarKeys'), 'profile page should derive highlighted radar keys')
  assert(profileView.includes('radarChangeText'), 'profile page should render change text')
  assert(profileView.includes(':highlighted-keys="highlightedRadarKeys"'), 'RadarChart should receive highlighted keys')
  assert(radarChart.includes('highlightedKeys?: string[]'), 'RadarChart should accept highlighted keys')
  assert(radarChart.includes('isHighlighted'), 'RadarChart should mark highlighted axes')
})
