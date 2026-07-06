export type AuthUser = {
  user_id: string
  username: string
  display_name: string
  is_guest: boolean
  created_at: string
}

export type AuthSession = {
  token: string
  user: AuthUser
}

export const AUTH_STORAGE_KEY = 'qihuang-auth-v1'

export const readStoredAuth = (): AuthSession | null => {
  const raw = window.localStorage.getItem(AUTH_STORAGE_KEY)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as AuthSession
    if (!parsed.token || !parsed.user?.user_id) return null
    return parsed
  } catch {
    return null
  }
}

export const saveStoredAuth = (session: AuthSession) => {
  window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session))
}

export const clearStoredAuth = () => {
  window.localStorage.removeItem(AUTH_STORAGE_KEY)
}
