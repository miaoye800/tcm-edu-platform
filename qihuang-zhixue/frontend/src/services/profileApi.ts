import type { EvidenceCard, GoalCandidate, IdentityCardView, ProfileBuildMode, ProfileNarrative, ProfileResponse, ProfileSessionResponse, ProfileTrust } from '../types/profile'
import { authHeaders, handleUnauthorized, requestJson, requestResponse } from './request'

// 画像档案是整页的主数据，一次读要带上七维、证据与变迁录。它以前给了 800ms
// 预算，超时就把整页换成空白兜底——后端只要冷启动一次，用户就会看到"画像空
// 了、证据没了"。读取本身已经是纯读（毫秒级），这里给一个正常请求的预算即可。
const PROFILE_TIMEOUT_MS = 15_000
const PROFILE_NARRATIVE_TIMEOUT_MS = 8_000
const PROFILE_STREAM_CONNECT_TIMEOUT_MS = 10_000
// Finishing the story flow runs the evidence extraction (three batches, real
// model calls) before it answers. That is tens of seconds, so the 10s default
// aborted the request and the story dropped into "这一幕暂时无法继续" with the
// profile never built.
const PROFILE_SESSION_FINISH_TIMEOUT_MS = 90_000

export const readProfile = (pagination?: { limit?: number; offset?: number }): Promise<ProfileResponse> =>
  requestJson<ProfileResponse>(`/api/profile?limit=${pagination?.limit ?? 20}&offset=${pagination?.offset ?? 0}`, undefined, {
    timeoutMs: PROFILE_TIMEOUT_MS,
    timeoutMessage: '画像档案加载超时，请稍后重试',
  })

export const readProfileTrust = (): Promise<ProfileTrust> => requestJson<ProfileTrust>('/api/profile/trust')

export const proposeProfileGoal = (rawText: string): Promise<GoalCandidate> =>
  requestJson<GoalCandidate>('/api/profile/goal', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ raw_text: rawText }),
  })

export const confirmProfileGoal = (candidate: GoalCandidate): Promise<{ profile: ProfileResponse; restatement: string }> =>
  requestJson('/api/profile/goal/confirm', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ candidate }),
  })

export type CalibrationPlan = {
  claim_id: string | null
  dimension: string | null
  questions: Array<{ question_id: string; prompt: string; options: string[]; difficulty?: string }>
}

export type CalibrationSettlement = {
  claim_id: string
  status: 'pending' | 'verified' | 'refuted'
  accuracy: number
  trust: number
  reason?: string
  added_questions?: number
}

export const readNextCalibration = (): Promise<CalibrationPlan> => requestJson<CalibrationPlan>('/api/profile/calibration/next')
export const submitCalibration = (claimId: string, answers: Array<{ question_id: string; answer: string[] }>): Promise<CalibrationSettlement> =>
  requestJson<CalibrationSettlement>('/api/profile/calibration/submit', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ claim_id: claimId, answers }),
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

export const readProfileSession = (sessionId: string): Promise<ProfileSessionResponse> =>
  requestJson<ProfileSessionResponse>(`/api/profile/session/${encodeURIComponent(sessionId)}`)

export type ProfileSessionHistoryItem = {
  session_id: string
  mode: ProfileBuildMode
  finished: boolean
  turn_index: number
  created_at: string
  updated_at: string
  evidence_count: number
  message_count: number
  last_user_message: string
}

/**
 * 历史会话由后端按 user_id 给出。
 *
 * 会话 id 以前只存在前端 sessionStorage 里，关掉浏览器就再也回不到那次对话。
 */
export const listProfileSessions = (mode?: ProfileBuildMode, limit = 20): Promise<{ items: ProfileSessionHistoryItem[]; total: number }> =>
  requestJson(`/api/profile/sessions?limit=${limit}${mode ? `&mode=${encodeURIComponent(mode)}` : ''}`)

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
  }, {
    timeoutMs: PROFILE_SESSION_FINISH_TIMEOUT_MS,
    timeoutMessage: '画像整理超时，五幕答卷已保存，可以稍后在画像页继续',
  })

export type ProfileDialogueStreamEvent =
  | { event: 'thinking'; data: { delta?: string } }
  | { event: 'message'; data: { delta?: string } }
  | { event: 'replace'; data: { text?: string } }
  | { event: 'message_done'; data: Record<string, never> }
  | { event: 'evidence'; data: { cards?: EvidenceCard[] } }
  | { event: 'identity_cards'; data: { items?: IdentityCardView[] } }
  | { event: 'radar'; data: NonNullable<ProfileSessionResponse['radar']> }
  | { event: 'state'; data: Partial<ProfileSessionResponse> & { converged?: boolean; target?: string | null; target_label?: string | null; error?: string } }
  | { event: 'error'; data: { message?: string } }
  | { event: 'done'; data: Record<string, never> }
  | { event: string; data: Record<string, unknown> }

export type ProfileDialogueRequest = {
  session_id: string
  utterance: string
  client_turn_id?: string
}

export const streamProfileDialogueChat = async (
  sessionId: string,
  utterance: string,
  onEvent: (event: ProfileDialogueStreamEvent) => void,
  clientTurnId?: string,
): Promise<void> => {
  const headers = authHeaders({ Accept: 'text/event-stream', 'Content-Type': 'application/json' })
  let response: Response
  try {
    response = await requestResponse(
      '/api/profile/dialogue/chat',
      {
        method: 'POST',
        headers,
        body: JSON.stringify({ session_id: sessionId, utterance, ...(clientTurnId ? { client_turn_id: clientTurnId } : {}) } satisfies ProfileDialogueRequest),
      },
      {
        timeoutMs: PROFILE_STREAM_CONNECT_TIMEOUT_MS,
        timeoutMessage: '画像对话连接超时，请稍后重试',
      },
    )
  } catch (error) {
    if (error instanceof Error && error.message.includes('超时')) throw error
    throw new Error('无法连接画像服务，请确认后端已启动后重试')
  }

  await handleUnauthorized(response)

  if (!response.ok || !response.body) {
    const detail = await readStreamError(response)
    if (response.status === 409) throw new Error(detail || '本轮对话正在处理中，请勿重复提交')
    if (response.status >= 500) throw new Error('无法连接画像服务，请确认后端已启动后重试')
    if (response.status === 404) throw new Error(detail || '画像会话已过期，请重新开始')
    throw new Error(detail || `对话流请求失败：${response.status}`)
  }

  try {
    await consumeProfileDialogueSseStream(response.body, onEvent)
  } catch (error) {
    if (error instanceof Error && /fetch|network|connection|socket|sse stream ended/i.test(error.message)) {
      throw new Error('对话连接已中断，正在尝试恢复会话')
    }
    throw error
  }
}

export const consumeProfileDialogueSseStream = async (
  body: ReadableStream<Uint8Array>,
  onEvent: (event: ProfileDialogueStreamEvent) => void,
): Promise<void> => {
  const reader = body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let sawDone = false

  const flush = (chunk: string) => {
    buffer += chunk
    const frames = buffer.split(/\r?\n\r?\n/)
    buffer = frames.pop() ?? ''
    frames.forEach((frame) => {
      const event = emitSseFrame(frame, onEvent)
      if (event === 'done' || event === 'message_done') sawDone = true
    })
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    flush(decoder.decode(value, { stream: true }))
  }
  flush(decoder.decode())
  if (buffer.trim()) {
    const event = emitSseFrame(buffer, onEvent)
    if (event === 'done' || event === 'message_done') sawDone = true
  }
  if (!sawDone) throw new Error('sse stream ended before done event')
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
  return event
}

const readStreamError = async (response: Response): Promise<string> => {
  try {
    const payload = (await response.json()) as { detail?: string }
    return payload.detail || ''
  } catch {
    return ''
  }
}
