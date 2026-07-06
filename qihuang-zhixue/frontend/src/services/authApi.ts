import { requestJson, type RequestOptions } from './request'
import type { AuthSession, AuthUser } from './authStorage'

type LoginPayload = {
  username: string
  password: string
}

type RegisterPayload = LoginPayload & {
  display_name?: string
}

export const loginApi = (payload: LoginPayload): Promise<AuthSession> =>
  requestJson<AuthSession>(
    '/api/auth/login',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
    { skipAuthRedirect: true },
  )

export const registerApi = (payload: RegisterPayload): Promise<AuthSession> =>
  requestJson<AuthSession>(
    '/api/auth/register',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
    { skipAuthRedirect: true },
  )

export const guestApi = (): Promise<AuthSession> =>
  requestJson<AuthSession>(
    '/api/auth/guest',
    {
      method: 'POST',
    },
    { skipAuthRedirect: true },
  )

export const meApi = (options?: RequestOptions): Promise<AuthUser> =>
  requestJson<AuthUser>('/api/auth/me', undefined, options)
