import type { ProfileNarrative, ProfileResponse } from '../types/profile'
import { requestJson } from './request'

export const readProfile = (): Promise<ProfileResponse> => requestJson<ProfileResponse>('/api/profile')

export const readProfileNarrative = (): Promise<ProfileNarrative> =>
  requestJson<ProfileNarrative>('/api/profile/narrative')
