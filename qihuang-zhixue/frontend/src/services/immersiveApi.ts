import { authHeaders, requestJson } from './request'
import type { ImmersiveChatEvent, ImmersiveRecommendation, ImmersiveSession } from '../types/immersive'

const api = '/api/immersive'
export const startImmersiveSession = (node_id: string) => requestJson<ImmersiveSession>(`${api}/session`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ node_id }) })
export const confirmImmersiveResources = (session_id: string, resources: ImmersiveRecommendation[]) => requestJson<{ task_ids: string[] }>(`${api}/confirm`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id, resources }) })

// POST SSE needs a request body, so native EventSource cannot be used. This mirrors
// its named-event model while keeping auth and reconnect hooks available for session 5.
export const streamImmersiveChat = async (session_id: string, message: string, onEvent: (event: ImmersiveChatEvent) => void, signal?: AbortSignal) => {
  const response = await fetch(`${api}/chat`, { method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json', Accept: 'text/event-stream' }), body: JSON.stringify({ session_id, message }), signal })
  if (!response.ok || !response.body) throw new Error('对话暂不可用，请稍后再试')
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''
  while (true) { const { value, done } = await reader.read(); buffer += decoder.decode(value || new Uint8Array(), { stream: !done }); let split = buffer.indexOf('\n\n'); while (split >= 0) { const block = buffer.slice(0, split); buffer = buffer.slice(split + 2); const event = block.match(/^event:\s*(.+)$/m)?.[1] || 'message'; const raw = block.match(/^data:\s*(.+)$/m)?.[1] || '{}'; onEvent({ event: event as ImmersiveChatEvent['event'], ...JSON.parse(raw) }); split = buffer.indexOf('\n\n') } if (done) break }
}
