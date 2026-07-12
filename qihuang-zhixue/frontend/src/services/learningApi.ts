import type {
  PathResponse,
  QuizSubmitItem,
  QuizSubmitResponse,
  ResourceStreamHandlers,
  ResourceType,
  ReviewsResponse,
} from '../types/learning'
import { authHeaders, handleUnauthorized, requestJson, requestResponse } from './request'

const RESOURCE_STREAM_CONNECT_TIMEOUT_MS = 10_000

export const readLearningPath = (): Promise<PathResponse> => requestJson<PathResponse>('/api/path')

export const readReviews = (): Promise<ReviewsResponse> => requestJson<ReviewsResponse>('/api/reviews')

export const submitQuiz = (items: QuizSubmitItem[]): Promise<QuizSubmitResponse> =>
  requestJson<QuizSubmitResponse>('/api/quiz/submit', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ items }),
  })

export const streamResources = async (
  payload: { topic: string; types: ResourceType[] },
  handlers: ResourceStreamHandlers,
  signal?: AbortSignal,
): Promise<void> => {
  const headers = authHeaders({
    Accept: 'text/event-stream',
    'Content-Type': 'application/json',
  })
  const response = await requestResponse(
    '/api/resources/generate',
    {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
      signal,
    },
    {
      timeoutMs: RESOURCE_STREAM_CONNECT_TIMEOUT_MS,
      timeoutMessage: '资源生成连接超时，请稍后重试',
      abortMessage: '资源生成已取消',
    },
  )

  await handleUnauthorized(response)

  if (!response.ok || !response.body) {
    throw new Error(`资源生成失败：${response.status}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done })
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? ''

    for (const frame of frames) {
      dispatchSseFrame(frame, handlers)
    }

    if (done) break
  }

  if (buffer.trim()) {
    dispatchSseFrame(buffer, handlers)
  }
}

const dispatchSseFrame = (frame: string, handlers: ResourceStreamHandlers) => {
  const lines = frame.split('\n')
  const event = lines.find((line) => line.startsWith('event:'))?.slice(6).trim() ?? 'message'
  const dataText = lines
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trim())
    .join('\n')

  if (!dataText) return
  const payload = JSON.parse(dataText) as Record<string, unknown>

  if (event === 'node_start') {
    handlers.nodeStart?.(payload as Parameters<NonNullable<ResourceStreamHandlers['nodeStart']>>[0])
  } else if (event === 'token') {
    handlers.token?.(payload as Parameters<NonNullable<ResourceStreamHandlers['token']>>[0])
  } else if (event === 'resource_done') {
    handlers.resourceDone?.(payload as Parameters<NonNullable<ResourceStreamHandlers['resourceDone']>>[0])
  } else if (event === 'done') {
    handlers.done?.(payload as Parameters<NonNullable<ResourceStreamHandlers['done']>>[0])
  }
}
