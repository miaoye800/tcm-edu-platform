import { authHeaders, handleUnauthorized, requestJson, requestResponse } from './request'
import type {
  ArtifactSummary,
  AssistantEvent,
  ChatTurnPayload,
  ConversationDetail,
  ConversationSummary,
} from '../types/assistant'

/**
 * 助手对话的 API。
 *
 * **SSE 走 POST**：`EventSource` 只能 GET、也带不了 Bearer 头。所以用 fetch +
 * reader，与 `streamResourceTask` 同一套解析。两处的解析逻辑不抽公共函数是有
 * 意的——它们的事件形状将来会分叉（资源流有工序，对话流有 token），过早合并
 * 只会得到一个「什么都能解析一点」的函数。
 */

export const listConversations = () =>
  requestJson<{ items: ConversationSummary[] }>('/api/assistant/conversations')

export const createConversation = (title = '') =>
  requestJson<ConversationSummary>('/api/assistant/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })

export const getConversation = (id: string) =>
  requestJson<ConversationDetail>(`/api/assistant/conversations/${encodeURIComponent(id)}`)

export const renameConversation = (id: string, title: string) =>
  requestJson<ConversationSummary>(`/api/assistant/conversations/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })

export const cancelRun = (runId: string) =>
  requestJson<{ ok: boolean; status: string }>(`/api/assistant/runs/${encodeURIComponent(runId)}/cancel`, {
    method: 'POST',
  })

export const confirmAssistantPlan = (taskId: string, plan: unknown, design?: unknown) =>
  requestJson<{ ok: boolean; current_step: number; plan: unknown; design?: unknown }>(
    `/api/assistant/artifacts/${encodeURIComponent(taskId)}/plan/confirm`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan, design: design ?? null }),
    },
  )

interface StreamOptions {
  signal?: AbortSignal
  onConnection?: (state: 'reconnected') => void
}

const readEventStream = async (response: Response, onEvent: (event: AssistantEvent) => void): Promise<void> => {
  if (!response.ok || !response.body) {
    throw new Error(`对话流不可用：${response.status}`)
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let eventName = ''
  let dataLines: string[] = []

  const flush = () => {
    if (!eventName && !dataLines.length) return
    const raw = dataLines.join('\n')
    const name = eventName || 'message'
    eventName = ''
    dataLines = []
    if (!raw) return
    try {
      const parsed = JSON.parse(raw) as AssistantEvent
      // 后端的 data 已经是完整信封；这里只兜底补 event 字段。
      onEvent({ ...parsed, event: parsed.event || name })
    } catch {
      // 坏一行不该中断整条流：丢掉它，继续读。
    }
  }

  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
    let index = buffer.indexOf('\n')
    while (index >= 0) {
      const line = buffer.slice(0, index).replace(/\r$/, '')
      buffer = buffer.slice(index + 1)
      if (line === '') flush()
      else if (line.startsWith('event:')) eventName = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
      index = buffer.indexOf('\n')
    }
    if (done) {
      flush()
      return
    }
  }
}

/** 发一句话，消费一条对话流。返回时流已结束（done / error / 连接关闭）。 */
export const streamAssistantTurn = async (
  payload: ChatTurnPayload,
  onEvent: (event: AssistantEvent) => void,
  options: StreamOptions = {},
): Promise<void> => {
  const response = await fetch('/api/assistant/chat/stream', {
    method: 'POST',
    headers: authHeaders({ Accept: 'text/event-stream', 'Content-Type': 'application/json' }),
    body: JSON.stringify(payload),
    signal: options.signal,
  })
  await handleUnauthorized(response)
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new Error(detail.detail || `发送失败：${response.status}`)
  }
  await readEventStream(response, onEvent)
}

/** 断线重连：补发这一轮已经产生的事件。 */
export const resumeRun = async (runId: string, onEvent: (event: AssistantEvent) => void, options: StreamOptions = {}) => {
  const response = await fetch(`/api/assistant/runs/${encodeURIComponent(runId)}/stream`, {
    headers: authHeaders({ Accept: 'text/event-stream' }),
    signal: options.signal,
  })
  await handleUnauthorized(response)
  options.onConnection?.('reconnected')
  await readEventStream(response, onEvent)
}

/** 确认方案之后的续流。事件形状与对话流一致。 */
export const streamAssistantArtifact = async (
  taskId: string,
  onEvent: (event: AssistantEvent) => void,
  options: StreamOptions = {},
): Promise<void> => {
  const response = await fetch(`/api/assistant/artifacts/${encodeURIComponent(taskId)}/stream`, {
    headers: authHeaders({ Accept: 'text/event-stream' }),
    signal: options.signal,
  })
  await handleUnauthorized(response)
  if (!response.ok) throw new Error(`续流不可用：${response.status}`)
  await readEventStream(response, onEvent)
}

export const artifactsOf = (detail: ConversationDetail): ArtifactSummary[] => detail.artifacts || []
