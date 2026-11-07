import { ref } from 'vue'

export type AppTheme = 'dark' | 'light'

const storageKey = 'qh-app-theme'

// 浅色（米白纸）是主设计，深色（近黑）是可选。默认值必须和 index.html
// 里的 pre-hydration 脚本保持一致，否则首帧会闪一下再跳到正确主题。
const DEFAULT_THEME: AppTheme = 'light'
const theme = ref<AppTheme>(DEFAULT_THEME)

const applyTheme = (next: AppTheme) => {
  const root = document.documentElement
  root.dataset.qhTheme = next
  root.style.colorScheme = next
}

export const initializeTheme = () => {
  const saved = window.localStorage.getItem(storageKey)
  theme.value = saved === 'dark' ? 'dark' : saved === 'light' ? 'light' : DEFAULT_THEME
  applyTheme(theme.value)
}

export const useTheme = () => {
  const toggleTheme = () => {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
    applyTheme(theme.value)
    window.localStorage.setItem(storageKey, theme.value)
  }
  return { theme, toggleTheme }
}
