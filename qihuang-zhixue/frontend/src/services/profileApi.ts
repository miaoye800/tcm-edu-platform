import type { ProfileBuildMode, ProfileNarrative, ProfileResponse, ProfileSessionResponse } from '../types/profile'
import { requestJson } from './request'

export const readProfile = (): Promise<ProfileResponse> => requestJson<ProfileResponse>('/api/profile')

export const readProfileNarrative = (): Promise<ProfileNarrative> =>
  requestJson<ProfileNarrative>('/api/profile/narrative')

export const startProfileSession = (mode: ProfileBuildMode): Promise<ProfileSessionResponse> =>
  requestJson<ProfileSessionResponse>('/api/profile/session/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  })

export const answerProfileSession = (
  sessionId: string,
  stepId: string,
  payload: Record<string, unknown>,
): Promise<ProfileSessionResponse> =>
  requestJson<ProfileSessionResponse>('/api/profile/session/answer', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, step_id: stepId, payload }),
  })

export const finishProfileSession = (sessionId: string): Promise<ProfileSessionResponse> =>
  requestJson<ProfileSessionResponse>('/api/profile/session/finish', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId }),
  })
