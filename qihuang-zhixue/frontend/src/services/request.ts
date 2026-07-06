import { clearStoredAuth, readStoredAuth } from './authStorage'

type RequestOptions = {
  skipAuthRedirect?: boolean
}

export const authHeaders = (init?: HeadersInit): Headers => {
  const headers = new Headers(init)
  const session = readStoredAuth()
  if (session?.token) {
    headers.set('Authorization', `Bearer ${session.token}`)
  }
  return headers
}

export const requestJson = async <T>(url: string, init?: RequestInit, options?: RequestOptions): Promise<T> => {
  const headers = authHeaders(init?.headers)
  if (!headers.has('Accept')) headers.set('Accept', 'application/json')

  const response = await fetch(url, {
    ...init,
    headers,
  })

  await handleUnauthorized(response, options)

  if (!response.ok) {
    const message = await readErrorMessage(response)
    throw new Error(message || `接口请求失败：${response.status}`)
  }

  return (await response.json()) as T
}

export const handleUnauthorized = async (response: Response, options?: RequestOptions): Promise<void> => {
  if (response.status !== 401 || options?.skipAuthRedirect) return
  clearStoredAuth()
  if (!options?.skipAuthRedirect && window.location.pathname !== '/login') {
    const redirect = encodeURIComponent(`${window.location.pathname}${window.location.search}${window.location.hash}`)
    window.location.assign(`/login?redirect=${redirect}`)
  }
  throw new Error('登录已过期，请重新登录')
}

const readErrorMessage = async (response: Response): Promise<string> => {
  try {
    const payload = (await response.json()) as { detail?: string }
    return payload.detail || ''
  } catch {
    return ''
  }
}
