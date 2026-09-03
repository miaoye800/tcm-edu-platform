import type { DimensionKey, IdentityCardView, IdentityCardsDimensionView, ProfileDialogueChatMessage, ProfileDialogueStreamUiEvent } from '../../types/profile'

export type ProfileDialogueMessageState = {
  messages: ProfileDialogueChatMessage[]
  messageId: number
  activeAssistantId: number | null
}

export type ProfileDialogueApplyResult = 'none' | 'created' | 'appended' | 'finished'

export type ProfileDialogueIdentityCardState = {
  sessionId: string | null
  hydrated: boolean
  playedRevealKeys: Record<string, boolean>
  claimStatuses: Record<string, string | null | undefined>
}

export type RenderedIdentityCardView = IdentityCardView & {
  /** UI-only marker: never sent back to, or used to alter, the backend state. */
  revealAnimation: boolean
}

export const createProfileDialogueMessageState = (): ProfileDialogueMessageState => ({
  messages: [],
  messageId: 0,
  activeAssistantId: null,
})

export const createProfileDialogueIdentityCardState = (): ProfileDialogueIdentityCardState => ({
  sessionId: null,
  hydrated: false,
  playedRevealKeys: {},
  claimStatuses: {},
})

export const createProfileDialogueAssistantMessage = (
  state: ProfileDialogueMessageState,
  text = '',
  typing = true,
): ProfileDialogueChatMessage => ({
  id: ++state.messageId,
  role: 'assistant',
  text,
  typing,
  reasoning: '',
  reasoningStreaming: false,
  reasoningCollapsed: false,
})

export const createProfileDialogueUserMessage = (
  state: ProfileDialogueMessageState,
  text: string,
): ProfileDialogueChatMessage => ({
  id: ++state.messageId,
  role: 'user',
  text,
  reasoning: '',
  reasoningStreaming: false,
  reasoningCollapsed: true,
})

export const addProfileDialogueAssistantPrompt = (state: ProfileDialogueMessageState, prompt: string): boolean => {
  const text = prompt.trim() || '先从一个轻松问题开始：你最近最想解决的学习困惑是什么？'
  if (!text || state.messages.some((message) => message.role === 'assistant' && message.text === text)) return false
  state.messages.push(createProfileDialogueAssistantMessage(state, text, false))
  state.activeAssistantId = null
  return true
}

export const addProfileDialogueUserMessage = (state: ProfileDialogueMessageState, text: string): void => {
  state.messages.push(createProfileDialogueUserMessage(state, text))
}

export const ensureProfileDialogueStreamingAssistant = (state: ProfileDialogueMessageState): number => {
  if (state.activeAssistantId !== null) {
    const existingIndex = state.messages.findIndex((message) => message.id === state.activeAssistantId)
    if (existingIndex !== -1) return existingIndex
  }
  const message = createProfileDialogueAssistantMessage(state)
  state.messages.push(message)
  state.activeAssistantId = message.id
  return state.messages.length - 1
}

export const appendProfileDialogueAssistantDelta = (
  state: ProfileDialogueMessageState,
  delta: string,
): ProfileDialogueApplyResult => {
  if (!delta) return 'none'
  const idx = ensureProfileDialogueStreamingAssistant(state)
  state.messages[idx].text += delta
  return 'appended'
}

export const appendProfileDialogueAssistantReasoning = (
  state: ProfileDialogueMessageState,
  delta: string,
  now = Date.now(),
): ProfileDialogueApplyResult => {
  const idx = ensureProfileDialogueStreamingAssistant(state)
  const message = state.messages[idx]
  if (!message.reasoningStartedAt) message.reasoningStartedAt = now
  message.reasoningStreaming = true
  message.reasoningCollapsed = false
  if (!delta) return 'created'
  message.reasoning += delta
  return 'appended'
}

export const finishProfileDialogueStreamingAssistant = (
  state: ProfileDialogueMessageState,
  now = Date.now(),
): ProfileDialogueApplyResult => {
  if (state.activeAssistantId !== null) {
    const idx = state.messages.findIndex((message) => message.id === state.activeAssistantId)
    if (idx !== -1) {
      const message = state.messages[idx]
      message.typing = false
      message.reasoningStreaming = false
      message.reasoningCollapsed = Boolean(message.reasoning)
      if (message.reasoningStartedAt && !message.reasoningDurationMs) {
        message.reasoningDurationMs = Math.max(0, now - message.reasoningStartedAt)
      }
    }
  }
  state.activeAssistantId = null
  return 'finished'
}

/** The visible answer is done; keep this bubble active for backend stages. */
export const markProfileDialogueVisibleMessageComplete = (state: ProfileDialogueMessageState): ProfileDialogueApplyResult => {
  if (state.activeAssistantId === null) return 'none'
  const idx = state.messages.findIndex((message) => message.id === state.activeAssistantId)
  if (idx === -1) return 'none'
  state.messages[idx].typing = false
  return 'finished'
}

export const applyProfileDialogueStreamEvent = (
  state: ProfileDialogueMessageState,
  event: ProfileDialogueStreamUiEvent,
  now = Date.now(),
): ProfileDialogueApplyResult => {
  if (event.type === 'reset') {
    ensureProfileDialogueStreamingAssistant(state)
    return 'created'
  }
  if (event.type === 'thinking') {
    return appendProfileDialogueAssistantReasoning(state, event.delta ?? '', now)
  }
  if (event.type === 'message') {
    return appendProfileDialogueAssistantDelta(state, event.delta ?? '')
  }
  if (event.type === 'message_done') return markProfileDialogueVisibleMessageComplete(state)
  if (event.type === 'done') {
    return finishProfileDialogueStreamingAssistant(state, now)
  }
  return 'none'
}

export const applyProfileDialogueIdentityCards = (
  state: ProfileDialogueIdentityCardState,
  dimensions: IdentityCardsDimensionView[],
  sessionId?: string | null | ((dimension: DimensionKey) => void),
  onRefuted?: (dimension: DimensionKey) => void,
): RenderedIdentityCardView[] => {
  const resolvedSessionId = typeof sessionId === 'string' ? sessionId : null
  const resolvedOnRefuted = typeof sessionId === 'function' ? sessionId : onRefuted
  if (resolvedSessionId && state.sessionId !== resolvedSessionId) {
    state.sessionId = resolvedSessionId
    state.hydrated = false
    state.playedRevealKeys = {}
    state.claimStatuses = {}
  }

  const isHydration = !state.hydrated
  const rendered = dimensions.map((dimension) => {
    const previousClaimStatus = state.claimStatuses[dimension.key]
    if (dimension.claim_status === 'refuted' && previousClaimStatus !== 'refuted') resolvedOnRefuted?.(dimension.key)
    state.claimStatuses[dimension.key] = dimension.claim_status
    const revealKey = `${state.sessionId ?? 'unknown'}:${dimension.key}`
    if (isHydration && dimension.revealed) state.playedRevealKeys[revealKey] = true
    const revealAnimation = Boolean(!isHydration && dimension.revealed && dimension.revealed_now && !state.playedRevealKeys[revealKey])
    if (revealAnimation) state.playedRevealKeys[revealKey] = true
    return {
      ...dimension,
      items: (dimension.items ?? []).map((item) => ({ ...item })),
      cards: dimension.cards?.map((card) => ({ ...card })),
      revealAnimation,
    }
  })
  state.hydrated = true
  return rendered
}
