import type { ProfileBuildMode } from '../types/profile'

export type OnboardingState = {
  status: 'skipped' | 'completed'
  mode: ProfileBuildMode
  updatedAt: string
}

const keyFor = (userId: string): string => `qihuang-onboarding:${userId}`
const invitedKeyFor = (userId: string): string => `qihuang-onboarding-invited:${userId}`
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

/**
 * 画像入口只"邀请"一次。
 *
 * 旧逻辑是"没建画像就永远送回入口"，于是新账号必须点一次「跳过」才能去别的
 * 页面。现在只记录"已经主动把用户送到入口过"，之后是否继续画像完全由用户
 * 决定：点侧栏任何页面都算暂不绘制，不再回弹。
 *
 * 用 localStorage 而不是 sessionStorage：按标签页记的话，用户换个标签、或
 * 关掉浏览器再回来，会被再抓去一次入口——那仍然是"没画画像就别想好好用"。
 * 入口本身一直在侧栏里，用户想画的时候自己进去就行。
 */
export const readOnboardingInvite = (userId?: string): boolean => {
  if (!userId) return false
  return window.localStorage.getItem(invitedKeyFor(userId)) === '1'
}

export const markOnboardingInvited = (userId?: string): void => {
  if (!userId) return
  window.localStorage.setItem(invitedKeyFor(userId), '1')
}

export const clearOnboardingInvite = (userId?: string): void => {
  if (userId) window.localStorage.removeItem(invitedKeyFor(userId))
}

/**
 * 正在进行的画像会话按用户存在 localStorage 里。
 *
 * 以前存在 sessionStorage：关掉标签页、重开浏览器、或者中途从别的标签进入，
 * 都拿不到 sessionId，入口就退化成"重新开始"——之前的对话看起来全部消失了。
 * 会话本身在后端（profile_sessions + chat_sessions），这里只是把"我上次在哪
 * 一局"记久一点；真正的历史列表由 `/api/profile/sessions` 提供。
 */
export const readActiveProfileSession = (userId?: string): ActiveProfileSessionState | null => {
  if (!userId) return null
  const raw = window.localStorage.getItem(activeSessionKeyFor(userId))
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as ActiveProfileSessionState
    return parsed.sessionId && parsed.mode ? parsed : null
  } catch {
    return null
  }
}

export const saveActiveProfileSession = (userId: string, state: ActiveProfileSessionState) => {
  window.localStorage.setItem(activeSessionKeyFor(userId), JSON.stringify(state))
}

export const clearActiveProfileSession = (userId?: string) => {
  if (userId) window.localStorage.removeItem(activeSessionKeyFor(userId))
}

export const readPendingDialogueTurn = (userId?: string): PendingDialogueTurnState | null => {
  if (!userId) return null
  try {
    const parsed = JSON.parse(window.localStorage.getItem(pendingDialogueTurnKeyFor(userId)) || 'null') as PendingDialogueTurnState | null
    return parsed?.sessionId && parsed?.utterance && parsed?.clientTurnId ? parsed : null
  } catch {
    return null
  }
}

export const savePendingDialogueTurn = (userId: string, state: PendingDialogueTurnState) => {
  window.localStorage.setItem(pendingDialogueTurnKeyFor(userId), JSON.stringify(state))
}

export const clearPendingDialogueTurn = (userId?: string) => {
  if (userId) window.localStorage.removeItem(pendingDialogueTurnKeyFor(userId))
}
