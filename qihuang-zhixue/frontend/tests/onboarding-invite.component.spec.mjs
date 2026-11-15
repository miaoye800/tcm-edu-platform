import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Script, createContext } from 'node:vm'
import ts from 'typescript'

const root = process.cwd()
const routerPath = join(root, 'src/router/index.ts')
const onboardingPath = join(root, 'src/views/OnboardingView.vue')
const storagePath = join(root, 'src/services/onboardingStorage.ts')
const streamStatePath = join(root, 'src/components/profile/profileDialogueStreamState.ts')
const boardPath = join(root, 'src/components/profile/DestinyCardBoard.vue')

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

const makeStorage = () => {
  const store = new Map()
  return {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key),
    clear: () => store.clear(),
    _keys: () => [...store.keys()],
  }
}

const compileTsModule = (path, globals = {}) => {
  const source = readFileSync(path, 'utf8')
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      esModuleInterop: true,
    },
  }).outputText

  const sandbox = { exports: {}, module: { exports: {} }, console, ...globals }
  sandbox.exports = sandbox.module.exports
  new Script(compiled, { filename: path }).runInContext(createContext(sandbox))
  return sandbox.module.exports
}

const router = readFileSync(routerPath, 'utf8')
const onboarding = readFileSync(onboardingPath, 'utf8')
const board = readFileSync(boardPath, 'utf8')

test('画像入口只邀请一次，之后不再拦截页面切换', () => {
  const guard = router.slice(router.indexOf('router.beforeEach'))
  assert(guard.includes('readOnboardingInvite'), 'guard must consult the one-shot invite flag')
  assert(guard.includes('markOnboardingInvited'), 'guard must record the invite when it redirects')
  assert(
    /!profile\.hasBuiltProfile[^\n]*readOnboardingInvite/.test(guard) || guard.includes('&& !readOnboardingInvite(userId)'),
    'redirect must be gated by "never invited before", not by "profile still empty"',
  )
})

test('离开画像页不等于跳过：不写 skipped、不建被动会话', () => {
  const deferBlock = onboarding.slice(onboarding.indexOf('const deferOnboarding'), onboarding.indexOf('const skipOnboarding'))
  assert(deferBlock.length > 0, 'deferOnboarding must exist')
  assert(!deferBlock.includes('saveOnboardingState'), 'leaving must not persist a skipped state')
  assert(!deferBlock.includes('startProfileSession'), 'leaving must not start a passive session')
  assert(deferBlock.includes('markOnboardingInvited'), 'leaving must still consume the invite flag')
  const leaveButtons = onboarding.split('deferOnboarding">').length - 1
  assert(leaveButtons >= 2, `both leave buttons must use deferOnboarding (found ${leaveButtons})`)
})

test('离开后仍可回到入口继续：入口文案不再承诺被动取证', () => {
  assert(!onboarding.includes('也可以先随便逛逛，系统只在你使用时做被动取证'), 'hero copy must not promise silent profiling')
  assert(onboarding.includes('画像会等你回来'), 'hero copy must state the entry stays available')
})

test('邀请标记按用户隔离，并可显式清除', () => {
  const window = { localStorage: makeStorage(), sessionStorage: makeStorage() }
  const storage = compileTsModule(storagePath, { window })
  assert(storage.readOnboardingInvite('u1') === false, 'fresh user must not be marked as invited')
  storage.markOnboardingInvited('u1')
  assert(storage.readOnboardingInvite('u1') === true, 'invite must be recorded per user')
  assert(storage.readOnboardingInvite('u2') === false, 'invite must not leak to another user')
  // 换个标签页（新 sessionStorage、同一 localStorage）不得再抓用户去一次入口。
  const nextTab = compileTsModule(storagePath, {
    window: { localStorage: window.localStorage, sessionStorage: makeStorage() },
  })
  assert(nextTab.readOnboardingInvite('u1') === true, 'a new tab must not send the user back to onboarding')
  storage.clearOnboardingInvite('u1')
  assert(storage.readOnboardingInvite('u1') === false, 'invite must be clearable')
  assert(storage.readOnboardingInvite() === false, 'missing user id must be a safe no-op')
})

const messageState = () => ({
  messages: [],
  messageId: 0,
  activeAssistantId: null,
})

test('replace 事件不会删掉用户已经看到的正文', () => {
  const stateModule = compileTsModule(streamStatePath)
  const state = messageState()
  stateModule.appendProfileDialogueAssistantDelta(state, '你的目标是考研，方向很清楚。')
  const visible = state.messages[0].text

  // 更短的重写（旧实现在这里把整段正文替换掉，用户看到"删掉重写"）。
  stateModule.replaceProfileDialogueAssistantText(state, '最近有没有什么变化？')
  assert(state.messages[0].text === visible, 'a shorter replace must never truncate the visible answer')

  // 相同文本是幂等同步。
  stateModule.replaceProfileDialogueAssistantText(state, visible)
  assert(state.messages[0].text === visible, 'identical replace must be a no-op')

  // 更长的同步只追加尾部，不重新吐一遍。
  stateModule.replaceProfileDialogueAssistantText(state, `${visible}你平时用什么材料复习？`)
  assert(state.messages[0].text === `${visible}你平时用什么材料复习？`, 'a longer replace must append only the tail')

  // 语义不同的整段改写也不允许吃掉正文。
  stateModule.replaceProfileDialogueAssistantText(state, '完全不同的另一句话')
  assert(state.messages[0].text.startsWith(visible), 'an unrelated replace must leave the visible answer intact')
})

test('牌面标题与提示都能给出牌名和组成，不再只显示维度名', () => {
  assert(board.includes('card_name'), 'board must read the derived card name')
  assert(board.includes('composition_summary'), 'board must surface the observed composition')
  assert(board.includes('boardTitle'), 'board head must render the derived title')
  assert(!board.includes('未成形</span>'), 'empty slot copy must not be a bare placeholder')
  assert(board.includes('组成待定'), 'an open card with no items must say the composition is pending')
})

console.log('onboarding-invite spec passed')
