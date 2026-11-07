import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './styles/palette.scss'
import './styles/tokens.scss'
import './styles/reset.scss'
import './styles/story.scss'
import './styles/tailwind.css'
import App from './App.vue'
import router from './router'
import { useAuthStore } from './stores/auth'
import { useProfileStore } from './stores/profile'
import { initializeTheme } from './composables/useTheme'

const E2E_FORCE_MOCK_KEY = 'qihuang-e2e-force-mock'
initializeTheme()

// Read this before router installation so the guard sees it on its first pass.
if (new URLSearchParams(window.location.search).get('e2eForceMock') === '1') {
  window.sessionStorage.setItem(E2E_FORCE_MOCK_KEY, '1')
}

const showBootLoading = () => {
  const root = document.getElementById('app')
  if (!root) return
  // 用 token 而不是写死颜色，否则深色用户会先看到一帧浅色加载页。
  root.innerHTML = `
    <style>
      @keyframes qh-boot-spin { to { transform: rotate(360deg); } }
    </style>
    <div
      style="min-height: 100vh; display: grid; place-items: center; background: var(--qh-bg); color: var(--qh-fg); font-family: var(--qh-font-body);"
      aria-busy="true"
    >
      <div style="display: grid; gap: 14px; place-items: center;">
        <div style="width: 24px; height: 24px; border: 2px solid var(--qh-line-strong); border-top-color: var(--qh-accent); border-radius: 999px; animation: qh-boot-spin 0.8s linear infinite;"></div>
        <div style="font-size: 13px; font-weight: 600; color: var(--qh-fg-muted);">正在校验登录状态...</div>
      </div>
    </div>
  `
}

const bootstrap = async () => {
  showBootLoading()

  const app = createApp(App)
  const pinia = createPinia()

  app.use(pinia)

  const auth = useAuthStore(pinia)
  await auth.validateStoredSession()

  app.use(router).use(ElementPlus)
  await router.isReady()
  app.mount('#app')

  if (auth.isAuthenticated) {
    const profile = useProfileStore(pinia)
    window.setTimeout(() => {
      void profile.ensureNarrativeLoaded()
    }, 0)
  }
}

void bootstrap()
