import type { IdentityCardsDimensionView, ProfileDialogueChatMessage, ProfileDialogueStreamUiEvent } from '../../types/profile'

export type ProfileDialogueMessageState = {
  messages: ProfileDialogueChatMessage[]
  messageId: number
  activeAssistantId: number | null
}

export type ProfileDialogueApplyResult = 'none' | 'created' | 'appended' | 'finished'

export type ProfileDialogueIdentityCardState = {
  revealedDimensions: Record<string, boolean>
  revealCounts: Record<string, number>
}

export const createProfileDialogueMessageState = (): ProfileDialogueMessageState => ({
  messages: [],
  messageId: 0,
  activeAssistantId: null,
})

export const createProfileDialogueIdentityCardState = (): ProfileDialogueIdentityCardState => ({
  revealedDimensions: {},
  revealCounts: {},
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
  if (event.type === 'done') {
    return finishProfileDialogueStreamingAssistant(state, now)
  }
  return 'none'
}

export const applyProfileDialogueIdentityCards = (
  state: ProfileDialogueIdentityCardState,
  dimensions: IdentityCardsDimensionView[],
): IdentityCardsDimensionView[] =>
  dimensions.map((dimension) => {
    const wasRevealed = Boolean(state.revealedDimensions[dimension.key])
    const shouldReveal = Boolean(dimension.revealed || wasRevealed)
    if (shouldReveal && !wasRevealed) {
      state.revealedDimensions[dimension.key] = true
      state.revealCounts[dimension.key] = (state.revealCounts[dimension.key] ?? 0) + 1
    }
    return {
      ...dimension,
      revealed: shouldReveal,
      cards: dimension.cards.map((card) => ({ ...card })),
    }
  })
