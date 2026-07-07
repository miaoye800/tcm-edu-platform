import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Script, createContext } from 'node:vm'
import ts from 'typescript'

const root = process.cwd()
const modelPath = join(root, 'src/components/profile/profileDialogueModel.ts')
const componentPath = join(root, 'src/components/profile/ProfileDialogue.vue')

const compileModel = () => {
  const source = readFileSync(modelPath, 'utf8')
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      esModuleInterop: true,
    },
  }).outputText

  const sandbox = { exports: {}, module: { exports: {} } }
  sandbox.exports = sandbox.module.exports
  new Script(compiled, { filename: modelPath }).runInContext(createContext(sandbox))
  return sandbox.module.exports
}

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

const model = compileModel()
const component = readFileSync(componentPath, 'utf8')

test('翻牌随 cardScores', () => {
  assert(model.cardIsFlipped('考前冲刺', { 考前冲刺: 0.72 }) === true, '0.72 should flip and normalize to 72')
  assert(model.cardScoreFor('考前冲刺', { 考前冲刺: 0.72 }) === 72, 'ratio score should be percent')
  assert(model.cardIsFlipped('研读派', { 考前冲刺: 0.72 }) === false, 'missing score should stay on back')
})

test('emit payload 正确', () => {
  assert(
    JSON.stringify(model.createAnswerPayload('utterance', '我想提分')) === JSON.stringify({ utterance: '我想提分' }),
    'free text payload mismatch',
  )
  assert(
    JSON.stringify(model.createAnswerPayload('quick_reply', '病例推演')) === JSON.stringify({ quick_reply: '病例推演' }),
    'quick reply payload mismatch',
  )
  assert(
    JSON.stringify(model.createConfirmPayload('临床学徒')) === JSON.stringify({ identity_card: '临床学徒' }),
    'confirm payload mismatch',
  )
})

test('降级可选卡', () => {
  assert(model.createConfirmPayload(null).identity_card === null, 'skip should emit null identity_card')
  assert(model.bestScoredCard({ 研读派: 91, 临床学徒: 0.4 }) === '研读派', 'direct selection fallback should use top score')
  assert(component.includes('@click=\"selectCard(card.name)\"'), 'component should allow changing the selected card')
  assert(component.includes('skipSelection'), 'component should expose skip fallback')
})
