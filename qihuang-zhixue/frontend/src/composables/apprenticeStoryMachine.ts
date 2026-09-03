import type { ProfileSessionResponse } from '../types/profile'
import { apprenticeScenes, sceneForStep, type ApprenticeScene } from '../config/apprenticeStory'

export type ApprenticeStoryState = {
  session: ProfileSessionResponse | null
  sceneId: string
  performanceIndex: number
  submittedStepIds: string[]
  completing: boolean
}

export const initialApprenticeStoryState = (): ApprenticeStoryState => ({
  session: null,
  sceneId: apprenticeScenes[0].sceneId,
  performanceIndex: 0,
  submittedStepIds: [],
  completing: false,
})

export const sceneForState = (state: ApprenticeStoryState): ApprenticeScene => {
  if (!state.session) return apprenticeScenes[state.performanceIndex] ?? apprenticeScenes[0]
  if (state.session.finished) return apprenticeScenes[apprenticeScenes.length - 1]
  return state.performanceIndex < 3 ? apprenticeScenes[state.performanceIndex] : sceneForStep(state.session.step)
}

export const advancePerformance = (state: ApprenticeStoryState): ApprenticeStoryState => {
  const nextIndex = Math.min(3, state.performanceIndex + 1)
  const scene = apprenticeScenes[nextIndex]
  return { ...state, performanceIndex: nextIndex, sceneId: scene.sceneId }
}

export const applySession = (state: ApprenticeStoryState, session: ProfileSessionResponse): ApprenticeStoryState => {
  const scene = session.finished ? apprenticeScenes[apprenticeScenes.length - 1] : sceneForStep(session.step)
  return { ...state, session, performanceIndex: 3, sceneId: scene.sceneId, completing: false }
}

export const canSubmitStep = (state: ApprenticeStoryState): boolean => {
  const stepId = state.session?.step?.id
  return Boolean(stepId && !state.completing && !state.submittedStepIds.includes(stepId))
}

export const beginStepSubmission = (state: ApprenticeStoryState): ApprenticeStoryState => {
  const stepId = state.session?.step?.id
  if (!stepId || !canSubmitStep(state)) return state
  return { ...state, completing: true, submittedStepIds: [...state.submittedStepIds, stepId] }
}

export const restoreApprenticeStoryState = (raw: string | null): ApprenticeStoryState | null => {
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as ApprenticeStoryState
    if (!parsed || !Array.isArray(parsed.submittedStepIds) || typeof parsed.sceneId !== 'string') return null
    return { ...parsed, completing: false }
  } catch {
    return null
  }
}
