import { defineStore } from 'pinia'
import { ElMessage } from 'element-plus'
import { guestApi, loginApi, meApi, registerApi } from '../services/authApi'
import { clearStoredAuth, readStoredAuth, saveStoredAuth, type AuthSession, type AuthUser } from '../services/authStorage'

const stored = readStoredAuth()

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: stored?.token ?? '',
    user: stored?.user ?? null as AuthUser | null,
    loading: false,
    error: '',
  }),
  getters: {
    isAuthenticated: (state): boolean => Boolean(state.token && state.user),
    displayName: (state): string => state.user?.display_name || state.user?.username || '未登录',
  },
  actions: {
    setSession(session: AuthSession) {
      this.token = session.token
      this.user = session.user
      this.error = ''
      saveStoredAuth(session)
    },
    async login(username: string, password: string) {
      this.loading = true
      this.error = ''
      try {
        this.setSession(await loginApi({ username, password }))
      } catch (error) {
        this.error = error instanceof Error ? error.message : '登录失败'
        throw error
      } finally {
        this.loading = false
      }
    },
    async register(username: string, password: string, displayName?: string) {
      this.loading = true
      this.error = ''
      try {
        this.setSession(await registerApi({ username, password, display_name: displayName }))
      } catch (error) {
        this.error = error instanceof Error ? error.message : '注册失败'
        throw error
      } finally {
        this.loading = false
      }
    },
    async guest() {
      this.loading = true
      this.error = ''
      try {
        this.setSession(await guestApi())
      } catch (error) {
        this.error = error instanceof Error ? error.message : '游客登录失败'
        throw error
      } finally {
        this.loading = false
      }
    },
    async refreshMe() {
      if (!this.token) return
      this.user = await meApi()
      saveStoredAuth({ token: this.token, user: this.user })
    },
    logout() {
      this.token = ''
      this.user = null
      this.error = ''
      clearStoredAuth()
      ElMessage.success('已退出登录')
    },
  },
})
