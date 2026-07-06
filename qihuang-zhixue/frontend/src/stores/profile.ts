import { defineStore } from 'pinia'
import { readProfile, readProfileNarrative } from '../services/profileApi'
import { createMockNarrative, createMockProfile } from '../mocks/profile'
import { useAuthStore } from './auth'
import type { ProfileNarrative, ProfileResponse } from '../types/profile'

export const useProfileStore = defineStore('profile', {
  state: () => ({
    activeUserId: '',
    profile: createMockProfile('') as ProfileResponse,
    narrative: createMockNarrative('') as ProfileNarrative,
    loading: false,
    error: '',
    usingMock: true,
  }),
  actions: {
    syncCurrentUser() {
      const auth = useAuthStore()
      this.activeUserId = auth.user?.user_id ?? ''
    },
    async loadProfile() {
      this.syncCurrentUser()
      this.loading = true
      this.error = ''

      try {
        this.profile = await readProfile()
        this.usingMock = false
        this.activeUserId = this.profile.user_id

        try {
          this.narrative = await readProfileNarrative()
        } catch (error) {
          this.narrative = createMockNarrative(this.activeUserId)
          this.error = error instanceof Error ? error.message : '画像叙述加载失败'
        }
      } catch (error) {
        this.profile = createMockProfile(this.activeUserId)
        this.narrative = createMockNarrative(this.activeUserId)
        this.usingMock = true
        this.error = error instanceof Error ? error.message : '画像加载失败，已切换为 mock 数据'
      } finally {
        this.loading = false
      }
    },
  },
})
