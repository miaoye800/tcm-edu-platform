import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Script, createContext } from 'node:vm'
import ts from 'typescript'

const root = process.cwd()
const onboardingPath = join(root, 'src/views/OnboardingView.vue')
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
const dialogueComponent = readFileSync(dialogueComponentPath, 'utf8')
const dialogueConfig = compileTsModule(dialogueConfigPath)
const modeCardsBlock = onboarding.slice(onboarding.indexOf('const modeCards'), onboarding.indexOf('const activeStep'))

test('dialogue replaces quickcard onboarding entry', () => {
  assert(modeCardsBlock.includes("mode: 'dialogue'"), 'mode cards should expose dialogue')
  assert(modeCardsBlock.includes("title: '对话问诊(dialogue)'"), 'dialogue card title missing')
  assert(!modeCardsBlock.includes("mode: 'quickcard'"), 'quickcard should not be visible as an onboarding card')
  assert(onboarding.includes("<ProfileDialogue"), 'onboarding should render ProfileDialogue for dialogue mode')
})

test('dialogue advances multiple turns before confirm and finish', () => {
  assert(onboarding.includes('const answerDialogue = async (payload: AnswerPayload)'), 'dialogue answer handler missing')
  assert(onboarding.includes('answerProfileSession(session.value.session_id, activeStep.value.id'), 'dialogue answer should use active step id')
  assert(onboarding.includes('...payload'), 'dialogue answer should pass utterance or quick_reply payload through')
  assert(onboarding.includes("activeStep.value.kind !== 'confirm'"), 'confirm should wait for convergence confirm step')
  assert(onboarding.includes('const confirmDialogue = async (payload: ConfirmPayload)'), 'dialogue confirm handler missing')
  assert(onboarding.includes('await finishCurrentSession()'), 'dialogue confirm should finish the profile session')
  assert(onboarding.includes('await enterApp()'), 'dialogue flow should enter the main app after finish')
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
