import { clearStoredAuth, readStoredAuth } from './authStorage'

export type RequestOptions = {
  skipAuthRedirect?: boolean
  timeoutMs?: number
  timeoutMessage?: string
  abortMessage?: string
  networkMessage?: string
}

export const DEFAULT_REQUEST_TIMEOUT_MS = 10_000

export class RequestError extends Error {
  kind: 'timeout' | 'aborted' | 'network'

  constructor(message: string, kind: 'timeout' | 'aborted' | 'network') {
    super(message)
    this.name = 'RequestError'
    this.kind = kind
  }
}

export const authHeaders = (init?: HeadersInit): Headers => {
  const headers = new Headers(init)
  const session = readStoredAuth()
  if (session?.token) {
    headers.set('Authorization', `Bearer ${session.token}`)
  }
  return headers
}

export const requestResponse = async (url: string, init?: RequestInit, options?: RequestOptions): Promise<Response> => {
  const headers = authHeaders(init?.headers)
  if (!headers.has('Accept')) headers.set('Accept', 'application/json')

  const controller = new AbortController()
  const timeoutMs = options?.timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS
  let timedOut = false
  let abortedByCaller = false
  const cleanupExternalAbort = forwardAbort(init?.signal, controller, () => {
    abortedByCaller = true
  })
  const timeoutId = window.setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)

  try {
    return await fetch(url, {
      ...init,
      headers,
      signal: controller.signal,
    })
  } catch (error) {
    if (timedOut) {
      throw new RequestError(options?.timeoutMessage || `请求超时（${timeoutMs}ms）`, 'timeout')
    }
    if (abortedByCaller) {
      throw new RequestError(options?.abortMessage || '请求已取消', 'aborted')
    }
    throw new RequestError(options?.networkMessage || readFetchErrorMessage(error), 'network')
  } finally {
    cleanupExternalAbort()
    window.clearTimeout(timeoutId)
  }
}

export const requestJson = async <T>(url: string, init?: RequestInit, options?: RequestOptions): Promise<T> => {
  const response = await requestResponse(url, init, options)
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
    const payload = (await response.json()) as { detail?: string | Record<string, unknown> }
    if (typeof payload.detail === 'string') return payload.detail
    if (payload.detail && typeof payload.detail === 'object') return String(payload.detail.reason || payload.detail.message || JSON.stringify(payload.detail))
    return ''
  } catch {
    return ''
  }
}

const forwardAbort = (
  signal: AbortSignal | null | undefined,
  controller: AbortController,
  onAbort: () => void,
): (() => void) => {
  if (!signal) return () => undefined
  if (signal.aborted) {
    onAbort()
    controller.abort()
    return () => undefined
  }

  const abort = () => {
    onAbort()
    controller.abort()
  }
  signal.addEventListener('abort', abort, { once: true })
  return () => signal.removeEventListener('abort', abort)
}

const readFetchErrorMessage = (error: unknown): string => {
  if (error instanceof TypeError) return '无法连接后端，请确认服务已启动后重试'
  if (error instanceof Error && error.message) return error.message
  return '网络请求失败，请稍后重试'
}
