import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Script, createContext } from 'node:vm'
import ts from 'typescript'
import { nextTick, ref, watch } from 'vue'

const root = process.cwd()
const modelPath = join(root, 'src/components/profile/profileDialogueModel.ts')
const componentPath = join(root, 'src/components/profile/ProfileDialogue.vue')
const onboardingPath = join(root, 'src/views/OnboardingView.vue')
const profileApiPath = join(root, 'src/services/profileApi.ts')
const profileTypesPath = join(root, 'src/types/profile.ts')
const streamStatePath = join(root, 'src/components/profile/profileDialogueStreamState.ts')
const onboardingStoragePath = join(root, 'src/services/onboardingStorage.ts')

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

const compileProfileApi = (requestOverrides = {}) => {
  const source = readFileSync(profileApiPath, 'utf8')
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      esModuleInterop: true,
    },
  }).outputText

  const sandbox = {
    exports: {},
    module: { exports: {} },
    TextDecoder,
    require: (specifier) => {
      if (specifier === './request') {
        return {
          authHeaders: () => ({}),
          handleUnauthorized: async () => undefined,
          requestJson: async () => ({}),
          requestResponse: async () => ({}),
          ...requestOverrides,
        }
      }
      return {}
    },
  }
  sandbox.exports = sandbox.module.exports
  new Script(compiled, { filename: profileApiPath }).runInContext(createContext(sandbox))
  return sandbox.module.exports
}

const compileOnboardingStorage = (window) => {
  const source = readFileSync(onboardingStoragePath, 'utf8')
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      esModuleInterop: true,
    },
  }).outputText
  const sandbox = { exports: {}, module: { exports: {} }, window, Date, require: () => ({}) }
  sandbox.exports = sandbox.module.exports
  new Script(compiled, { filename: onboardingStoragePath }).runInContext(createContext(sandbox))
  return sandbox.module.exports
}

const compileStreamState = () => {
  const source = readFileSync(streamStatePath, 'utf8')
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      esModuleInterop: true,
    },
  }).outputText

  const sandbox = {
    exports: {},
    module: { exports: {} },
    Date,
    require: () => ({}),
  }
  sandbox.exports = sandbox.module.exports
  new Script(compiled, { filename: streamStatePath }).runInContext(createContext(sandbox))
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

const asyncTest = async (name, fn) => {
  try {
    await fn()
    console.log(`ok - ${name}`)
  } catch (error) {
    console.error(`not ok - ${name}`)
    throw error
  }
}

const model = compileModel()
const component = readFileSync(componentPath, 'utf8')
const onboarding = readFileSync(onboardingPath, 'utf8')
const profileTypes = readFileSync(profileTypesPath, 'utf8')
const streamState = compileStreamState()

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

test('backend quickReplies drive quick chips before static fallback', () => {
  assert(component.includes('quickReplies?: string[]'), 'ProfileDialogue should accept backend quick replies')
  assert(component.includes('props.quickReplies.length > 0'), 'backend quick replies should take priority when present')
  assert(component.includes('v-for="chip in visibleQuickReplies"'), 'quick chips should render the computed backend-first list')
  assert(component.includes("createAnswerPayload('quick_reply', quickReply)"), 'quick replies should still emit quick_reply payloads')
  assert(onboarding.includes(':quick-replies="dialogueQuickReplies"'), 'Onboarding should pass step payload quick replies to ProfileDialogue')
  assert(onboarding.includes('activeStep.value?.payload.quick_replies ?? []'), 'quick replies should come from active step payload')
})

test('dialogue not-ready state surfaces an error and disables input', () => {
  assert(onboarding.includes("error.value = '对话尚未就绪，请稍候重试'"), 'missing session or active step should set a visible error')
  assert(onboarding.includes(':disabled="!activeStep"'), 'dialogue should stay usable while busy and only disable before step is ready')
  assert(component.includes(':disabled="disabled || pending || !draft.trim()"'), 'send button should be disabled when dialogue is not ready or a stream is pending')
  assert(component.includes(':disabled="disabled || pending"'), 'quick chips should receive disabled and pending state')
})

test('assistant bubbles render from real SSE message deltas', () => {
  assert(component.includes('streamEvents?: ProfileDialogueStreamUiEvent[]'), 'ProfileDialogue should accept a streamed UI event queue')
  assert(component.includes('const appendAssistantDelta = (delta: string)'), 'message deltas should append to the active assistant bubble')
  assert(readFileSync(streamStatePath, 'utf8').includes('state.messages[idx].text += delta'), 'streamed text should update through the shared message state')
  assert(component.includes('finishStreamingAssistant'), 'done should clear assistant typing state')
  assert(!component.includes('window.setInterval'), 'assistant typing must not use the old fake timer')
})

test('assistant bubbles include Xiaoqi avatar', () => {
  assert(component.includes("import xiaoqiAvatar from '../../assets/mascot/xiaoqi-avatar.png'"), 'component should reuse existing Xiaoqi avatar asset')
  assert(component.includes('class="bubble-avatar"'), 'assistant row should render an avatar element')
  assert(component.includes('v-if="message.role === \'assistant\'"'), 'avatar should be scoped to assistant messages')
})

test('dialogue pending renders a collapsible thinking panel instead of page overlay', () => {
  assert(component.includes('pending?: boolean'), 'ProfileDialogue should accept a pending prop')
  assert(profileTypes.includes('reasoning: string'), 'assistant messages should carry per-message reasoning text')
  assert(profileTypes.includes('reasoningStreaming: boolean'), 'assistant messages should track reasoning streaming state')
  assert(profileTypes.includes('reasoningCollapsed: boolean'), 'assistant messages should track reasoning collapsed state')
  assert(component.includes('appendAssistantReasoning'), 'thinking deltas should append to the active assistant message')
  assert(component.includes('message.reasoning'), 'reasoning should render inside each assistant bubble')
  assert(component.includes('toggleReasoning'), 'reasoning details should be collapsible per message')
  assert(component.includes('小岐正在思考'), 'streaming reasoning should render a visible label')
  assert(component.includes("event.type === 'thinking'"), 'thinking stream events should update per-message reasoning')
  assert(!component.includes('thinkingText'), 'global thinking text state should be removed')
  assert(!component.includes('thinking-panel'), 'global thinking panel should not remain')
  assert(onboarding.includes(':pending="dialoguePending"'), 'Onboarding should pass pending state into ProfileDialogue')
  assert(onboarding.includes(':stream-events="dialogueStreamEvents"'), 'Onboarding should pass the ordered stream event queue into ProfileDialogue')
  assert(onboarding.includes('const pageLoading = computed'), 'Onboarding should compute page loading separately')
  assert(onboarding.includes('v-loading="pageLoading"'), 'full-screen loading should use the computed page loading state')
})

test('dialogue chat box is height constrained and internally scrollable', () => {
  assert(component.includes('height: clamp(560px, calc(100dvh - 176px), 720px)'), 'desktop dialogue main should be viewport constrained')
  assert(component.includes('grid-template-rows: auto minmax(0, 1fr) auto'), 'chat row should be allowed to shrink')
  assert(component.includes('overflow-y: auto'), 'bubble flow should own vertical scrolling')
  assert(component.includes('flowPinned'), 'stream auto-scroll should respect whether the user is near the bottom')
  assert(component.includes('isFlowNearBottom'), 'component should test bottom stickiness before streaming scrolls')
  assert(component.includes('@scroll="updateFlowPinned"'), 'manual user scrolls should update bottom stickiness')
  assert(component.includes('height: clamp(520px, calc(100dvh - 148px), 680px)'), 'tablet dialogue height should use dvh without fixed overflow')
  assert(component.includes('height: clamp(460px, calc(100dvh - 128px), 620px)'), 'narrow screens should adapt to mobile dvh')
})

await asyncTest('profile dialogue SSE keeps split CJK thinking and message deltas intact', async () => {
  const { consumeProfileDialogueSseStream } = compileProfileApi()
  const thinking = '思考：春生、夏长、秋收、冬藏，先看节律。'
  const message = '回答：换季容易不舒服，可以先从作息和复盘节奏聊起。'
  const payload =
    `event: thinking\ndata: ${JSON.stringify({ delta: thinking })}\n\n` +
    `event: message\ndata: ${JSON.stringify({ delta: message })}\n\n` +
    'event: done\ndata: {}\n\n'
  const bytes = new TextEncoder().encode(payload)
  const cutPoints = [1, 4, 9, 13, 18, 27, 31, 42, 55, 80, 113, 150, bytes.length - 3, bytes.length]
  const chunks = []
  let offset = 0
  for (const point of cutPoints) {
    if (point > offset && point <= bytes.length) {
      chunks.push(bytes.slice(offset, point))
      offset = point
    }
  }
  if (offset < bytes.length) chunks.push(bytes.slice(offset))

  const stream = new ReadableStream({
    start(controller) {
      chunks.forEach((chunk) => controller.enqueue(chunk))
      controller.close()
    },
  })
  const events = []
  await consumeProfileDialogueSseStream(stream, (event) => events.push(event))

  assert(events.map((event) => event.event).join(',') === 'thinking,message,done', 'SSE events should survive arbitrary byte cuts')
  assert(events[0].data.delta === thinking, 'thinking CJK text should rebuild exactly')
  assert(events[1].data.delta === message, 'message CJK text should rebuild exactly')
})

await asyncTest('single ref bridge drops same-tick stream frames before the queue fix', async () => {
  const { consumeProfileDialogueSseStream } = compileProfileApi()
  const {
    applyProfileDialogueStreamEvent,
    createProfileDialogueMessageState,
  } = streamState
  const thinkingFull = '春夏养阳，秋冬养阴；先把季节节律理顺。'
  const messageFull = '换季容易不舒服，可以先看作息、复盘和每日节奏。'
  const events = [
    { event: 'thinking', data: { delta: thinkingFull.slice(0, 6) } },
    { event: 'thinking', data: { delta: thinkingFull.slice(6) } },
    { event: 'message', data: { delta: messageFull.slice(0, 7) } },
    { event: 'message', data: { delta: messageFull.slice(7) } },
    { event: 'message_done', data: {} },
  ]
  const payload = events
    .map((event) => `event: ${event.event}\ndata: ${JSON.stringify(event.data)}\n\n`)
    .join('')
  const bytes = new TextEncoder().encode(payload)
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(bytes)
      controller.close()
    },
  })
  const state = createProfileDialogueMessageState()
  const eventRef = ref(null)
  let id = 0
  watch(
    () => eventRef.value,
    (event) => {
      if (event) applyProfileDialogueStreamEvent(state, event)
    },
  )
  eventRef.value = { id: ++id, type: 'reset', delta: '' }
  await consumeProfileDialogueSseStream(stream, ({ event, data }) => {
    if (event === 'thinking') eventRef.value = { id: ++id, type: 'thinking', delta: String(data.delta ?? '') }
    if (event === 'message') eventRef.value = { id: ++id, type: 'message', delta: String(data.delta ?? '') }
    if (event === 'message_done') eventRef.value = { id: ++id, type: 'done', delta: '' }
  })
  await nextTick()
  const assistant = state.messages.find((message) => message.role === 'assistant')

  assert(
    !assistant || assistant.reasoning !== thinkingFull || assistant.text !== messageFull,
    'the old single-ref bridge should demonstrate why same-tick stream frames were lost',
  )
})

await asyncTest('queued parent-to-child stream bridge preserves same-chunk thinking and message order', async () => {
  const { consumeProfileDialogueSseStream } = compileProfileApi()
  const {
    applyProfileDialogueStreamEvent,
    createProfileDialogueMessageState,
  } = streamState
  const thinkingFull = '春夏养阳，秋冬养阴；先把季节节律理顺。'
  const messageFull = '换季容易不舒服，可以先看作息、复盘和每日节奏。'
  const events = [
    { event: 'thinking', data: { delta: thinkingFull.slice(0, 2) } },
    { event: 'thinking', data: { delta: thinkingFull.slice(2, 9) } },
    { event: 'thinking', data: { delta: thinkingFull.slice(9) } },
    { event: 'message', data: { delta: messageFull.slice(0, 3) } },
    { event: 'message', data: { delta: messageFull.slice(3, 11) } },
    { event: 'message', data: { delta: messageFull.slice(11) } },
    { event: 'message_done', data: {} },
  ]
  const payload = events
    .map((event) => `event: ${event.event}\ndata: ${JSON.stringify(event.data)}\n\n`)
    .join('')
  const bytes = new TextEncoder().encode(payload)
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(bytes)
      controller.close()
    },
  })
  const state = createProfileDialogueMessageState()
  const eventQueue = ref([])
  let consumed = 0
  let id = 0
  watch(
    () => eventQueue.value.length,
    () => {
      while (consumed < eventQueue.value.length) {
        applyProfileDialogueStreamEvent(state, eventQueue.value[consumed])
        consumed += 1
      }
    },
  )
  eventQueue.value.push({ id: ++id, type: 'reset', delta: '' })
  await consumeProfileDialogueSseStream(stream, ({ event, data }) => {
    if (event === 'thinking') eventQueue.value.push({ id: ++id, type: 'thinking', delta: String(data.delta ?? '') })
    if (event === 'message') eventQueue.value.push({ id: ++id, type: 'message', delta: String(data.delta ?? '') })
    if (event === 'message_done') eventQueue.value.push({ id: ++id, type: 'done', delta: '' })
  })
  await nextTick()
  const assistant = state.messages.find((message) => message.role === 'assistant')

  assert(assistant?.reasoning === thinkingFull, 'queued bridge should preserve complete reasoning text')
  assert(assistant?.text === messageFull, 'queued bridge should preserve complete message text')
  assert(assistant?.reasoningCollapsed === true, 'message_done should auto-collapse completed reasoning')
})

test('dialogue can finish at any turn and keeps input locked until state is committed', () => {
  assert(component.includes('finish-dialogue'), 'ProfileDialogue should render a persistent finish button')
  assert(component.includes('const finishDialogue = () =>'), 'ProfileDialogue should expose a finish handler')
  assert(onboarding.includes("event === 'message_done'"), 'Onboarding should handle visible message completion')
  const messageDoneBlock = onboarding.slice(onboarding.indexOf("event === 'message_done'"), onboarding.indexOf("event === 'evidence'"))
  assert(!messageDoneBlock.includes('dialoguePending.value = false'), 'visible completion must not unlock before evidence/radar/state arrive')
  assert(onboarding.includes('dialoguePending.value = false'), 'stream completion should clear pending state in finally')
  assert(onboarding.includes('dialogueStreamInFlight'), 'finish should be aware of in-flight background updates')
  assert(onboarding.includes("await router.replace('/profile')"), 'dialogue finish should jump to the profile page')
  assert(onboarding.includes('profileStore.setDialogueRadarChanges'), 'finish response should carry radar changes into the profile store')
  assert(!onboarding.includes("!dialogueConverged.value && activeStep.value?.kind !== 'confirm'"), 'finish must not require convergence')
})

await asyncTest('profile session restore adapter calls the owned backend session route', async () => {
  const calls = []
  const { readProfileSession } = compileProfileApi({
    requestJson: async (url) => {
      calls.push(url)
      return { session_id: 'session/with space', mode: 'dialogue', status: 'restored' }
    },
  })

  const restored = await readProfileSession('session/with space')

  assert(calls[0] === '/api/profile/session/session%2Fwith%20space', 'restore route must encode the persisted session id')
  assert(restored.status === 'restored', 'restore adapter must return the real backend payload')
})

await asyncTest('profile stream adapter maps backend outages to an actionable error', async () => {
  const { streamProfileDialogueChat } = compileProfileApi({
    requestResponse: async () => ({ ok: false, status: 500, body: null, json: async () => ({}) }),
  })

  let errorMessage = ''
  try {
    await streamProfileDialogueChat('session-a', '继续', () => undefined)
  } catch (error) {
    errorMessage = String(error?.message ?? error)
  }
  assert(errorMessage === '无法连接画像服务，请确认后端已启动后重试', 'server outages should use an actionable retry message')
})

await asyncTest('profile stream adapter sends one stable client turn id in the request body', async () => {
  let requestBody = null
  const streamBody = new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('event: done\ndata: {}\n\n'))
      controller.close()
    },
  })
  const { streamProfileDialogueChat } = compileProfileApi({
    requestResponse: async (_url, init) => {
      requestBody = JSON.parse(init.body)
      return { ok: true, status: 200, body: streamBody }
    },
  })

  await streamProfileDialogueChat('session-a', 'same answer', () => undefined, 'turn-a')
  assert(requestBody.session_id === 'session-a', 'stream body should include the session id')
  assert(requestBody.utterance === 'same answer', 'stream body should include the user utterance')
  assert(requestBody.client_turn_id === 'turn-a', 'stream retries should reuse the client turn id')
})

test('active profile session storage survives refresh and clears after finish', () => {
  const values = new Map()
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  }
  const module = compileOnboardingStorage({ localStorage: storage, sessionStorage: storage })

  module.saveActiveProfileSession('guest-a', { sessionId: 'session-a', mode: 'dialogue' })
  assert(module.readActiveProfileSession('guest-a').sessionId === 'session-a', 'refresh should recover the active session id')
  assert(module.readActiveProfileSession('guest-b') === null, 'active sessions must remain user-scoped')
  module.clearActiveProfileSession('guest-a')
  assert(module.readActiveProfileSession('guest-a') === null, 'finish should remove the active session marker')
})

test('pending dialogue turn storage preserves retry identity and remains user scoped', () => {
  const values = new Map()
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  }
  const module = compileOnboardingStorage({ localStorage: storage, sessionStorage: storage })
  const pending = { sessionId: 'session-a', utterance: 'same answer', clientTurnId: 'turn-a' }

  module.savePendingDialogueTurn('guest-a', pending)
  assert(module.readPendingDialogueTurn('guest-a').clientTurnId === 'turn-a', 'refresh should preserve the pending turn id')
  assert(
    module.resolvePendingDialogueTurn(module.readPendingDialogueTurn('guest-a'), 'session-a', 'same answer', () => 'turn-new').clientTurnId === 'turn-a',
    'an interrupted retry should reuse the stored turn id',
  )
  assert(
    module.resolvePendingDialogueTurn(module.readPendingDialogueTurn('guest-a'), 'session-a', 'new answer', () => 'turn-b').clientTurnId === 'turn-b',
    'a new user answer should create a new turn id',
  )
  assert(module.readPendingDialogueTurn('guest-b') === null, 'pending turns must remain user-scoped')
  module.clearPendingDialogueTurn('guest-a')
  assert(module.readPendingDialogueTurn('guest-a') === null, 'committed turns should clear their pending marker')
})

test('radar axes honor backend status states', () => {
  assert(component.includes('const axisStatus = (axis: RadarAxis)'), 'ProfileDialogue should derive axis status from backend radar axes')
  assert(component.includes("status === 'dark'"), 'dark axes should be recognized')
  assert(component.includes("return '待聊到'"), 'dark axes should label as waiting to be discussed')
  assert(component.includes("return `暂定 ${axis.value}`"), 'dashed axes should show provisional values')
})

test('dialogue radar side panel renders completion and evidence list', () => {
  assert(profileTypes.includes('export type ProfileProgress'), 'profile types should include structured progress payload')
  assert(profileTypes.includes('export type EvidencePanelDimension'), 'profile types should include evidence panel payload')
  assert(component.includes('profileProgress?: ProfileProgress | null'), 'ProfileDialogue should accept profile progress')
  assert(component.includes('evidencePanel?: EvidencePanelDimension[]'), 'ProfileDialogue should accept evidence panel data')
  assert(component.includes('画像完成度 {{ profileCompletionText }}'), 'radar panel should render x/7 completion')
  assert(component.includes('pendingDimensions'), 'radar panel should render pending dimensions')
  assert(component.includes('<details class="evidence-panel">'), 'evidence list should be a collapsible side panel')
  assert(component.includes('命格收藏册'), 'merged evidence panel summary should be visible')
  assert(component.includes('置信度{{ formatConfidence(group.confidence) }}'), 'merged panel should render confidence')
  assert(component.includes('“{{ quote }}”'), 'merged panel should render original quotes')
  assert(onboarding.includes(':profile-progress="dialogueProfileProgress"'), 'Onboarding should pass progress payload to ProfileDialogue')
  assert(onboarding.includes(':evidence-panel="dialogueEvidencePanel"'), 'Onboarding should pass evidence panel payload to ProfileDialogue')
  assert(onboarding.includes('session.value?.profile_progress ?? null'), 'progress should come from backend session state')
  assert(onboarding.includes('session.value?.evidence_panel ?? []'), 'evidence panel should come from backend session state')
})

test('dialogue evidence panel is upgraded into a grouped destiny collection book', () => {
  assert(component.includes('<details class="evidence-panel">'), 'collection book should reuse the existing evidence-panel surface')
  assert(component.includes('命格收藏册'), 'evidence panel summary should be renamed to the collection book')
  assert(component.includes('画像完成度 {{ profileCompletionText }}'), 'collection summary should preserve x/7 completion')
  assert(component.includes('class="collection-stack"'), 'revealed cards should enter a stacked collection area')
  assert(component.includes('revealedArchiveCards'), 'stack should be driven by revealed archived cards')
  assert(component.includes('collectionGroups'), 'expanded panel should render grouped dimensions')
  assert(component.includes('v-for="group in collectionGroups"'), 'expanded collection should iterate by dimension group')
  assert(component.includes('v-for="card in group.cards"'), 'each dimension group should render its cards')
  assert(component.includes('cardMetric'), 'cards should derive ratio or level score display text')
  assert(component.includes("dimension.kind === 'distribution'"), 'distribution cards should render ratio metrics')
  assert(component.includes("dimension.kind === 'level'"), 'numeric cards should render level plus score metrics')
  assert(component.includes('cardQuoteTexts'), 'card quotes should reuse identity card quotes with evidence fallback')
  assert(component.includes('evidenceQuoteTexts'), 'evidence-panel quotes should remain available as source fallback')
  assert(component.includes('.filter((dimension) => dimension.revealed)'), 'collection order should follow server-revealed identity dimensions')
})

test('unrevealed dimensions never enter the evidence collection book', () => {
  const collectionBlock = component.slice(component.indexOf('const collectionGroups'), component.indexOf('const hasDimensionCards'))
  assert(collectionBlock.includes('.filter((dimension) => dimension.revealed)'), 'collection groups must be limited to server-revealed dimensions')
  assert(!collectionBlock.includes('evidenceOnlyGroups'), 'pending evidence must not bypass the identity-card reveal gate')
})

test('dialogue identity cards consume Phase2 payload independently of text stream queue', () => {
  assert(profileTypes.includes('export type IdentityCardView'), 'profile types should model the backend identity card view')
  assert(profileTypes.includes('revealed_now: boolean'), 'profile types should retain the backend reveal event flag')
  assert(profileTypes.includes('items: IdentityCardItem[]'), 'new card content should use canonical items')
  assert(profileTypes.includes('identity_cards?: IdentityCardsDimensionView[]'), 'session response should carry identity_cards')
  assert(component.includes('identityCardsView?: IdentityCardsDimensionView[]'), 'ProfileDialogue should accept identity card dimensions')
  assert(component.includes('applyProfileDialogueIdentityCards'), 'card reveal state should use the shared reducer module')
  assert(onboarding.includes(':identity-cards-view="dialogueIdentityCards"'), 'Onboarding should pass identity_cards to the dialogue component')
  assert(onboarding.includes(':session-id="session.session_id"'), 'Onboarding should provide the active session identity to the reveal reducer')
  assert(onboarding.includes("event === 'identity_cards'"), 'Onboarding should consume identity_cards SSE events')
  assert(onboarding.includes('latestIdentityCards'), 'the following state event must preserve the dedicated reveal event snapshot')
  assert(!onboarding.includes("pushDialogueStreamEvent('identity_cards'"), 'identity card events must not enter the text stream queue')
  assert(component.includes('while (consumedStreamEventCount < props.streamEvents.length)'), 'message/thinking queue cursor should remain intact')
})

test('dialogue identity cards render one evidence-backed dimension card with accessible popover and reduced motion fallback', () => {
  assert(component.includes('class="identity-board"'), 'dimension card board should render when identity_cards exist')
  assert(component.includes('class="tarot-card-grid"'), 'card group should render as tarot cards')
  assert(component.includes('activeIdentityDimension.pending_reason'), 'unrevealed card backs should show only the backend pending reason')
  assert(component.includes('待取证'), 'unrevealed card backs should use a neutral evidence-pending status')
  assert(component.includes('<el-popover v-if="activeIdentityDimension.revealed"'), 'pending cards must not mount an evidence popover or trigger button')
  assert(component.includes('<div v-if="activeIdentityDimension.revealed" class="tarot-face tarot-front">'), 'pending cards must not retain score or distribution content in the DOM')
  assert(component.includes('<div v-if="!activeIdentityDimension.revealed || isActiveDimensionFlipping" class="tarot-face tarot-back"'), 'pending cards should render a separate safe card back')
  assert(component.includes('<el-popover'), 'the evidence explanation should be available in a popover')
  assert(component.includes('<InfoFilled />'), 'the evidence popover should use the existing icon library')
  assert(component.includes('activeIdentityDimension.explanation'), 'the popover should render the backend explanation')
  assert(component.includes('visibleQuotes'), 'the popover should display original quotes')
  assert(component.includes('provenanceText(activeIdentityDimension.provenance)'), 'the popover should distinguish backend provenance')
  assert(component.includes('@media (prefers-reduced-motion: reduce)'), 'reduced motion should disable flip animation')
  assert(component.includes('.identity-tarot-card.is-flipped .tarot-back'), 'reduced motion should reveal the front without a flip')
})

test('unrevealed legacy cards and summaries do not expose conclusions', () => {
  assert(component.includes('v-else-if="hasLegacyInference" class="card-grid"'), 'legacy cards should require a real backend inference')
  assert(component.includes('v-if="card.flipped" class="card-face card-front"'), 'an unrevealed legacy card front must not enter the DOM')
  assert(component.includes(':disabled="!card.flipped"'), 'an unrevealed legacy card must not be selectable')
  assert(component.includes('v-if="showConfirmPanel" class="confirm-panel"'), 'the legacy conclusion summary must stay hidden until it is eligible')
  assert(component.includes('props.converged || (!hasDimensionCards.value && hasLegacyInference.value)'), 'dimension-card sessions should reveal the summary only after backend convergence')
})

test('formal no-key narrative labels do not claim fixture data', () => {
  assert(onboarding.includes('规则模板叙述（未配置模型密钥）'), 'the no-key narrative source should describe the real rule fallback')
  assert(!onboarding.includes('MOCK 演示来源'), 'formal onboarding copy must not label a backend template as fixture data')
})

test('dialogue identity cards render distribution, level, tags, quotes, and no-key provenance from backend items', () => {
  assert(component.includes("activeIdentityDimension.kind === 'distribution'"), 'distribution cards should branch separately')
  assert(component.includes('distribution-bar'), 'distribution cards should render a proportional composition bar')
  assert(component.includes('ratioPercent(item)'), 'distribution cards should render server ratio percentages')
  assert(component.includes("activeIdentityDimension.kind === 'level'"), 'numeric dimensions should render level cards')
  assert(component.includes('item.daily_minutes'), 'pace cards should show daily minutes only when supplied')
  assert(component.includes('item.target_days'), 'pace cards should show target days only when supplied')
  assert(component.includes('tag-content'), 'tag cards should render backend weak-point items')
  assert(component.includes("provenance === 'no_key_rule_fallback'"), 'no-key rule fallback should have a distinct display path')
  assert(component.includes('规则评分（未配置模型密钥）'), 'no-key rule fallback should be clearly labeled')
})

test('identity card reducer consumes revealed_now once per session and never turns a state update into another flip', () => {
  const { applyProfileDialogueIdentityCards, createProfileDialogueIdentityCardState } = streamState
  const state = createProfileDialogueIdentityCardState()
  const pending = [
    {
      key: 'learning_goal',
      kind: 'distribution',
      revealed: false,
      revealed_now: false,
      confidence: 0.2,
      items: [],
      cards: [{ display_name: '应试提分', canonical_label: '应试提分', ratio: 1 }],
    },
  ]
  const revealedNow = [
    {
      key: 'learning_goal',
      kind: 'distribution',
      revealed: true,
      revealed_now: true,
      confidence: 0.7,
      items: [{ display_name: '应试提分', canonical_label: '应试提分', ratio: 0.72 }],
      cards: [{ display_name: '应试提分', canonical_label: '应试提分', ratio: 0.72 }],
    },
  ]
  const laterState = [
    {
      key: 'learning_goal',
      kind: 'distribution',
      revealed: true,
      revealed_now: false,
      confidence: 0.82,
      items: [{ display_name: '应试提分', canonical_label: '应试提分', ratio: 0.64 }],
      cards: [{ display_name: '应试提分', canonical_label: '应试提分', ratio: 0.64 }],
    },
  ]

  assert(applyProfileDialogueIdentityCards(state, pending, 'session-a')[0].revealAnimation === false, 'pending cards do not animate')
  assert(applyProfileDialogueIdentityCards(state, revealedNow, 'session-a')[0].revealAnimation === true, 'the SSE reveal event animates once')
  assert(applyProfileDialogueIdentityCards(state, revealedNow, 'session-a')[0].revealAnimation === false, 'duplicate identity_cards events do not replay')
  const afterState = applyProfileDialogueIdentityCards(state, laterState, 'session-a')[0]
  assert(afterState.revealed === true && afterState.revealAnimation === false, 'following state keeps the face visible without replaying')
  assert(afterState.items[0].ratio === 0.64, 'later server values still update the displayed face')
  assert(applyProfileDialogueIdentityCards(state, laterState, 'session-b')[0].revealAnimation === false, 'a refresh or reconnect state without revealed_now remains static')
  assert(applyProfileDialogueIdentityCards(createProfileDialogueIdentityCardState(), revealedNow, 'session-refresh')[0].revealAnimation === false, 'first render hydrates an already-revealed card without replaying its flip')
})

test('veiled identity cards can be replaced after refutation while verified faces remain revealed', () => {
  const { applyProfileDialogueIdentityCards, createProfileDialogueIdentityCardState } = streamState
  const state = createProfileDialogueIdentityCardState()
  const pending = [{ key: 'knowledge_base', kind: 'level', revealed: true, confidence: .3, pending_verification: true, claim_status: 'pending', cards: [{ level_name: '自评熟练', score: 82 }] }]
  const refuted = [{ key: 'knowledge_base', kind: 'level', revealed: true, confidence: .45, pending_verification: false, claim_status: 'refuted', cards: [{ level_name: '重新校准', score: 46 }] }]
  const verified = [{ key: 'knowledge_base', kind: 'level', revealed: true, confidence: .72, pending_verification: false, claim_status: 'verified', cards: [{ level_name: '实测确认', score: 68 }] }]

  assert(applyProfileDialogueIdentityCards(state, pending)[0].cards[0].level_name === '自评熟练', 'pending self-report should render its veiled face')
  assert(applyProfileDialogueIdentityCards(state, refuted)[0].cards[0].level_name === '重新校准', 'refuted veiled card should allow a replacement face')
  assert(applyProfileDialogueIdentityCards(state, verified)[0].revealed === true, 'verified face should remain revealed')
  assert(state.claimStatuses.knowledge_base === 'verified', 'claim state should remain observable without modifying card visibility')
})

test('refuted identity card replacement adds Xiaoqi discrepancy notice', () => {
  const { applyProfileDialogueIdentityCards, createProfileDialogueIdentityCardState, createProfileDialogueMessageState, addProfileDialogueAssistantPrompt } = streamState
  const cards = createProfileDialogueIdentityCardState()
  const messages = createProfileDialogueMessageState()
  const refuted = [{ key: 'knowledge_base', kind: 'level', revealed: true, confidence: .45, pending_verification: false, claim_status: 'refuted', cards: [{ level_name: '重新校准', score: 46 }] }]

  applyProfileDialogueIdentityCards(cards, refuted, () => addProfileDialogueAssistantPrompt(messages, '看来你的底子和你说的有些出入，师父重新看过了。'))

  assert(component.includes('identityCardRefutedDescriptionFor(dimension)'), 'ProfileDialogue should use the centralized refuted copy')
  assert(component.includes('addAssistantPrompt(identityCardRefutedDescriptionFor(dimension))'), 'refuted replacement should add a Xiaoqi bubble before card rendering')
  assert(messages.messages[0]?.text === '看来你的底子和你说的有些出入，师父重新看过了。', 'refuted replacement should show Xiaoqi discrepancy bubble')
})
