import type { ProfileBuildMode, ProfileNarrative, ProfileResponse, ProfileSessionResponse } from '../types/profile'
import { authHeaders, handleUnauthorized, requestJson, requestResponse } from './request'

const PROFILE_TIMEOUT_MS = 800
const PROFILE_NARRATIVE_TIMEOUT_MS = 8_000
const PROFILE_STREAM_CONNECT_TIMEOUT_MS = 10_000

export const readProfile = (): Promise<ProfileResponse> =>
  requestJson<ProfileResponse>('/api/profile', undefined, {
    timeoutMs: PROFILE_TIMEOUT_MS,
    timeoutMessage: '画像档案加载超时，已切换为本地兜底数据',
  })

export const readProfileNarrative = (): Promise<ProfileNarrative> =>
  requestJson<ProfileNarrative>('/api/profile/narrative', undefined, {
    timeoutMs: PROFILE_NARRATIVE_TIMEOUT_MS,
    timeoutMessage: '画像叙述生成超时，请稍后重试',
  })

export const startProfileSession = (mode: ProfileBuildMode): Promise<ProfileSessionResponse> =>
  requestJson<ProfileSessionResponse>('/api/profile/session/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  })

export const answerProfileSession = (
  sessionId: string,
  stepId: string,
  payload: Record<string, unknown>,
): Promise<ProfileSessionResponse> =>
  requestJson<ProfileSessionResponse>('/api/profile/session/answer', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, step_id: stepId, payload }),
  })

export const finishProfileSession = (sessionId: string): Promise<ProfileSessionResponse> =>
  requestJson<ProfileSessionResponse>('/api/profile/session/finish', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId }),
  })

export type ProfileDialogueStreamEvent =
  | { event: 'thinking'; data: { delta?: string } }
  | { event: 'message'; data: { delta?: string } }
  | { event: 'message_done'; data: Record<string, never> }
  | { event: 'evidence'; data: { cards?: unknown[] } }
  | { event: 'identity_cards'; data: { items?: NonNullable<ProfileSessionResponse['identity_cards']> } }
  | { event: 'radar'; data: NonNullable<ProfileSessionResponse['radar']> }
  | { event: 'state'; data: Partial<ProfileSessionResponse> & { converged?: boolean; target?: string | null; target_label?: string | null; error?: string } }
  | { event: 'error'; data: { message?: string } }
  | { event: 'done'; data: Record<string, never> }
  | { event: string; data: Record<string, unknown> }

export const streamProfileDialogueChat = async (
  sessionId: string,
  utterance: string,
  onEvent: (event: ProfileDialogueStreamEvent) => void,
): Promise<void> => {
  const headers = authHeaders({ Accept: 'text/event-stream', 'Content-Type': 'application/json' })
  const response = await requestResponse(
    '/api/profile/dialogue/chat',
    {
      method: 'POST',
      headers,
      body: JSON.stringify({ session_id: sessionId, utterance }),
    },
    {
      timeoutMs: PROFILE_STREAM_CONNECT_TIMEOUT_MS,
      timeoutMessage: '画像对话连接超时，请稍后重试',
    },
  )

  await handleUnauthorized(response)

  if (!response.ok || !response.body) {
    throw new Error((await readStreamError(response)) || `对话流请求失败：${response.status}`)
  }

  await consumeProfileDialogueSseStream(response.body, onEvent)
}

export const consumeProfileDialogueSseStream = async (
  body: ReadableStream<Uint8Array>,
  onEvent: (event: ProfileDialogueStreamEvent) => void,
): Promise<void> => {
  const reader = body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  const flush = (chunk: string) => {
    buffer += chunk
    const frames = buffer.split(/\r?\n\r?\n/)
    buffer = frames.pop() ?? ''
    frames.forEach((frame) => emitSseFrame(frame, onEvent))
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    flush(decoder.decode(value, { stream: true }))
  }
  flush(decoder.decode())
  if (buffer.trim()) emitSseFrame(buffer, onEvent)
}

const emitSseFrame = (frame: string, onEvent: (event: ProfileDialogueStreamEvent) => void) => {
  const lines = frame.split(/\r?\n/)
  const event = lines.find((line) => line.startsWith('event:'))?.slice(6).trim() || 'message'
  const dataText = lines
    .filter((line) => line.startsWith('data:'))
    .map((line) => {
      const data = line.slice(5)
      return data.startsWith(' ') ? data.slice(1) : data
    })
    .join('\n')

  let data: Record<string, unknown> = {}
  if (dataText) {
    try {
      data = JSON.parse(dataText) as Record<string, unknown>
    } catch {
      data = { delta: dataText }
    }
  }
  onEvent({ event, data } as ProfileDialogueStreamEvent)
}

const readStreamError = async (response: Response): Promise<string> => {
  try {
    const payload = (await response.json()) as { detail?: string }
    return payload.detail || ''
  } catch {
    return ''
  }
}
