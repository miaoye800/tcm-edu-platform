import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import './styles/tokens.scss'
import './styles/reset.scss'
import App from './App.vue'
import router from './router'
import { useAuthStore } from './stores/auth'
import { useProfileStore } from './stores/profile'

const showBootLoading = () => {
  const root = document.getElementById('app')
  if (!root) return
  root.innerHTML = `
    <style>
      @keyframes qh-boot-spin { to { transform: rotate(360deg); } }
    </style>
    <div
      style="min-height: 100vh; display: grid; place-items: center; background: #f7f3ea; color: #243b34; font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;"
      aria-busy="true"
    >
      <div style="display: grid; gap: 12px; place-items: center;">
        <div style="width: 28px; height: 28px; border: 3px solid rgba(36, 59, 52, 0.18); border-top-color: #2f7d69; border-radius: 999px; animation: qh-boot-spin 0.8s linear infinite;"></div>
        <div style="font-size: 14px; font-weight: 800;">正在校验登录状态...</div>
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
