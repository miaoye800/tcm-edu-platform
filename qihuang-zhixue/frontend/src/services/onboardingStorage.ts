import type { ProfileBuildMode } from '../types/profile'

export type OnboardingState = {
  status: 'skipped' | 'completed'
  mode: ProfileBuildMode
  updatedAt: string
}

const keyFor = (userId: string): string => `qihuang-onboarding:${userId}`
const activeSessionKeyFor = (userId: string): string => `qihuang-profile-session:${userId}`
const pendingDialogueTurnKeyFor = (userId: string): string => `qihuang-profile-dialogue-turn:${userId}`

export type ActiveProfileSessionState = {
  sessionId: string
  mode: ProfileBuildMode
}

export type PendingDialogueTurnState = {
  sessionId: string
  utterance: string
  clientTurnId: string
}

export const resolvePendingDialogueTurn = (
  pending: PendingDialogueTurnState | null,
  sessionId: string,
  utterance: string,
  createClientTurnId: () => string,
): PendingDialogueTurnState => {
  if (pending?.sessionId === sessionId && pending.utterance === utterance) return pending
  return { sessionId, utterance, clientTurnId: createClientTurnId() }
}

export const readOnboardingState = (userId?: string): OnboardingState | null => {
  if (!userId) return null
  const raw = window.localStorage.getItem(keyFor(userId))
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as OnboardingState
    if (parsed.status !== 'skipped' && parsed.status !== 'completed') return null
    return parsed
  } catch {
    return null
  }
}

export const saveOnboardingState = (userId: string, state: Pick<OnboardingState, 'status' | 'mode'>) => {
  window.localStorage.setItem(
    keyFor(userId),
    JSON.stringify({
      ...state,
      updatedAt: new Date().toISOString(),
    }),
  )
}

export const readActiveProfileSession = (userId?: string): ActiveProfileSessionState | null => {
  if (!userId) return null
  const raw = window.sessionStorage.getItem(activeSessionKeyFor(userId))
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as ActiveProfileSessionState
    return parsed.sessionId && parsed.mode ? parsed : null
  } catch {
    return null
  }
}

export const saveActiveProfileSession = (userId: string, state: ActiveProfileSessionState) => {
  window.sessionStorage.setItem(activeSessionKeyFor(userId), JSON.stringify(state))
}

export const clearActiveProfileSession = (userId?: string) => {
  if (userId) window.sessionStorage.removeItem(activeSessionKeyFor(userId))
}

export const readPendingDialogueTurn = (userId?: string): PendingDialogueTurnState | null => {
  if (!userId) return null
  try {
    const parsed = JSON.parse(window.sessionStorage.getItem(pendingDialogueTurnKeyFor(userId)) || 'null') as PendingDialogueTurnState | null
    return parsed?.sessionId && parsed?.utterance && parsed?.clientTurnId ? parsed : null
  } catch {
    return null
  }
}

export const savePendingDialogueTurn = (userId: string, state: PendingDialogueTurnState) => {
  window.sessionStorage.setItem(pendingDialogueTurnKeyFor(userId), JSON.stringify(state))
}

export const clearPendingDialogueTurn = (userId?: string) => {
  if (userId) window.sessionStorage.removeItem(pendingDialogueTurnKeyFor(userId))
}
