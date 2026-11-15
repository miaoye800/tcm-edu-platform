import { defineStore } from 'pinia'
import { readProfile, readProfileNarrative } from '../services/profileApi'
import { createMockNarrative, createMockProfile } from '../mocks/profile'
import { useAuthStore } from './auth'
import type { ProfileNarrative, ProfileResponse, RadarChange } from '../types/profile'
import { dimensionKeys } from '../types/profile'
import { confidenceFor } from '../utils/profileMath'

export const useProfileStore = defineStore('profile', {
  state: () => ({
    activeUserId: '',
    profile: createMockProfile('') as ProfileResponse,
    narrative: createMockNarrative('') as ProfileNarrative,
    loading: false,
    narrativeLoading: false,
    loaded: false,
    narrativeLoaded: false,
    error: '',
    narrativeError: '',
    /**
     * `usingMock` 只表示"这一份是本地演示数据"，由 e2e 开关显式打开。
     *
     * 它过去在请求失败时也会被置真，于是后端的真实画像换成了七维全空的
     * 占位数据——用户看到的是"画像空了、证据没了"，而不是"没加载出来"。
     * 现在失败只记在 `loadFailed` 上，屏幕上的画像保持原样。
     */
    usingMock: false,
    loadFailed: false,
    latestRadarChanges: [] as RadarChange[],
    latestDialogueSummary: '',
    radarHighlightActive: false,
  }),
  getters: {
    overallConfidence: (state): number => {
      const total = dimensionKeys.reduce((sum, key) => sum + confidenceFor(state.profile, state.narrative, key), 0)
      return total / dimensionKeys.length
    },
    hasBuiltProfile: (state): boolean => {
      const hasConfidence = dimensionKeys.some((key) => confidenceFor(state.profile, state.narrative, key) > 0)
      const hasEvidence = dimensionKeys.some((key) => (state.profile[key].evidence ?? []).length > 0)
      return hasConfidence || hasEvidence
    },
    isOverallLowConfidence(): boolean {
      return this.overallConfidence < 0.5
    },
  },
  actions: {
    syncCurrentUser() {
      const auth = useAuthStore()
      this.activeUserId = auth.user?.user_id ?? ''
    },
    resetNarrativeState(userId?: string) {
      const nextUserId = userId ?? this.activeUserId
      this.narrative = createMockNarrative(nextUserId)
      this.narrativeLoading = false
      this.narrativeLoaded = false
      this.narrativeError = ''
    },
    async loadProfile() {
      this.syncCurrentUser()
      this.loading = true
      this.error = ''
      this.loadFailed = false

      // E2E screenshots exercise the same local fallback used by offline users.
      // The flag is opt-in and never changes route-guard behavior in production.
      if (window.sessionStorage.getItem('qihuang-e2e-force-mock') === '1' || window.localStorage.getItem('qihuang-e2e-force-mock') === '1') {
        this.profile = createMockProfile(this.activeUserId)
        this.resetNarrativeState(this.activeUserId)
        this.usingMock = true
        this.loaded = true
        this.loading = false
        return
      }

      try {
        this.profile = await readProfile()
        this.usingMock = false
        this.activeUserId = this.profile.user_id
        this.loaded = true
        if (this.narrative.user_id !== this.activeUserId) {
          this.resetNarrativeState(this.activeUserId)
        }
      } catch (error) {
        // 失败时保留上一次读到的真实画像（同一个人），绝不替换成空白占位：
        // 一次超时不该让用户以为自己的证据丢了。
        this.usingMock = false
        this.loadFailed = true
        this.loaded = true
        this.error = error instanceof Error ? error.message : '画像加载失败，请稍后重试'
        if (this.profile.user_id !== this.activeUserId) {
          this.profile = createMockProfile(this.activeUserId)
          this.resetNarrativeState(this.activeUserId)
        }
      } finally {
        this.loading = false
      }
    },
    async loadNarrative(force = false) {
      this.syncCurrentUser()
      const userId = this.activeUserId
      if (!userId || this.usingMock || this.loadFailed) {
        this.resetNarrativeState(userId)
        return
      }
      if (!force && this.narrativeLoading) return
      if (!force && this.narrativeLoaded && this.narrative.user_id === userId) return

      this.narrativeLoading = true
      this.narrativeError = ''

      try {
        this.narrative = await readProfileNarrative()
        this.narrativeLoaded = true
      } catch (error) {
        this.narrative = createMockNarrative(userId)
        this.narrativeLoaded = false
        this.narrativeError = error instanceof Error ? error.message : '画像叙述加载失败'
      } finally {
        this.narrativeLoading = false
      }
    },
    async ensureNarrativeLoaded() {
      if (!this.loaded || this.loading || this.usingMock || this.loadFailed) return
      await this.loadNarrative()
    },
    setDialogueRadarChanges(changes: RadarChange[] = [], summary = '') {
      this.latestRadarChanges = changes
      this.latestDialogueSummary = summary
      this.radarHighlightActive = changes.length > 0 || Boolean(summary)
    },
    fadeDialogueRadarChanges() {
      this.radarHighlightActive = false
    },
    clearDialogueRadarChanges() {
      this.latestRadarChanges = []
      this.latestDialogueSummary = ''
      this.radarHighlightActive = false
    },
  },
})
