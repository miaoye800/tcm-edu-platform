import type { ProfileBuildMode } from '../types/profile'

export type OnboardingState = {
  status: 'skipped' | 'completed'
  mode: ProfileBuildMode
  updatedAt: string
}

const keyFor = (userId: string): string => `qihuang-onboarding:${userId}`

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
