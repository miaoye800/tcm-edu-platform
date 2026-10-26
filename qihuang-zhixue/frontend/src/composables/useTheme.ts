import { ref } from 'vue'

export type AppTheme = 'dark' | 'light'

const storageKey = 'qh-app-theme'
const theme = ref<AppTheme>('dark')

export const initializeTheme = () => {
  const saved = window.localStorage.getItem(storageKey)
  theme.value = saved === 'light' ? 'light' : 'dark'
  document.documentElement.dataset.qhTheme = theme.value
}

export const useTheme = () => {
  const toggleTheme = () => {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
    document.documentElement.dataset.qhTheme = theme.value
    window.localStorage.setItem(storageKey, theme.value)
  }
  return { theme, toggleTheme }
}
