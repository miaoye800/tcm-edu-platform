import { authHeaders, handleUnauthorized, requestJson, requestResponse } from './request'
import type { ResourceFilters, ResourceItem, ResourceListResponse, ResourcePlan, ResourceTaskEvent, ResourceTaskSnapshot, ResourceType } from '../types/resource'

export const listResources = (filters: ResourceFilters = {}) => { const params = new URLSearchParams(); if (filters.type) params.set('type', filters.type); if (filters.nodeId) params.set('node_id', filters.nodeId); if (filters.favorited) params.set('favorited', 'true'); if (filters.q) params.set('q', filters.q); if (filters.offset) params.set('offset', String(filters.offset)); if (filters.limit) params.set('limit', String(filters.limit)); return requestJson<ResourceListResponse>(`/api/resources?${params}`) }
export const getResource = (id: string) => requestJson<ResourceItem>(`/api/resources/${id}`)
/** `params` 目前只被 graph 与 mindmap 消费；doc/quiz/story 传了也只会原样存进 meta。 */
export const createResourceTask = (nodeId: string, resourceType: ResourceType, params: Record<string, unknown> = {}) => requestJson<{ task_id: string }>('/api/resource/tasks', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ node_id: nodeId, resource_type: resourceType, params }) })
/** 任务快照：刷新后恢复「已确认的方案」，不必重新提案。 */
export const getResourceTask = (taskId: string) => requestJson<ResourceTaskSnapshot>(`/api/resource/tasks/${taskId}`)
/**
 * 修订方案。`plan` 是用户在面板里已经改过的那一版（不能一按修订就丢手改）；
 * `goal` 非空表示推翻重提，后端按新目标整份重拟。
 */
export const reviseResourcePlan = (taskId: string, feedback: string, options: { plan?: ResourcePlan; goal?: string } = {}) => requestJson<{ plan: ResourcePlan }>(`/api/resource/tasks/${taskId}/plan/revise`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ feedback, plan: options.plan, goal: options.goal || '' }) })
/** 确认方案。服务端会强校验（空方案、超长单元）后才推进到生成步。 */
/** 确认方案与设计单。两者必须同批提交——只交方案，服务端只能自己再猜一遍版式。 */
export const confirmResourcePlan = (taskId: string, plan: ResourcePlan, design?: unknown) =>
  requestJson<{ ok: boolean; current_step: number; plan: ResourcePlan; design?: unknown }>(
    `/api/resource/tasks/${taskId}/plan/confirm`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ plan, design: design ?? null }) },
  )
export const toggleResourceFavorite = (id: string) => requestJson<ResourceItem>(`/api/resources/${id}/favorite`, { method: 'POST' })
export const patchMindmapTemplate = (id: string, templateId: string) => requestJson<ResourceItem>(`/api/resources/${id}/mindmap`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ template_id: templateId }) })
export const expandMindmap = (id: string, nodePath: string[]) => requestJson<{ node_path: string[]; added_nodes: unknown[]; mindmap_data: unknown; idempotent: boolean }>(`/api/mindmap/${id}/expand`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ node_path: nodePath }) })

export interface SaveDocumentPayload {
  blocks: unknown[]
  version: number
  meta?: Record<string, unknown>
}

/**
 * 保存文档块。版本不符时后端返回 409，前端据此提示「已被重新生成」。
 * 这里不用 requestJson：它只抛出字符串化错误，无法区分 409。
 */
export const saveDocument = async (id: string, payload: SaveDocumentPayload): Promise<{ version: number }> => {
  const response = await requestResponse(`/api/resources/${id}/document`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }, { timeoutMs: 15_000 })
  await handleUnauthorized(response)
  if (response.status === 409) throw new Error('VERSION_CONFLICT')
  if (!response.ok) {
    const detail = await response.json().catch(() => ({})) as { detail?: string }
    throw new Error(detail?.detail || `保存失败：${response.status}`)
  }
  return (await response.json()) as { version: number }
}

/** 导出 Markdown：带鉴权的下载必须走 fetch，普通 <a href> 拿不到 Bearer 头 */
export const downloadDocumentMarkdown = async (id: string): Promise<Blob> => {
  const response = await requestResponse(`/api/resources/${id}/document.md`)
  await handleUnauthorized(response)
  if (!response.ok) throw new Error(`导出失败：${response.status}`)
  return response.blob()
}
export interface ResourceStreamOptions { signal?: AbortSignal; onConnection?: (state: 'offline' | 'reconnected') => void; retries?: number }

/** Fetch-based SSE supports auth headers; a reconnect asks the resumable server for the persisted remaining events. */
export const streamResourceTask = async (taskId: string, onEvent: (event: ResourceTaskEvent) => void, options: ResourceStreamOptions = {}) => {
  let attempt = 0
  while (!options.signal?.aborted) {
    try {
      const response = await fetch(`/api/resource/tasks/${taskId}/stream`, { headers: authHeaders({ Accept: 'text/event-stream' }), signal: options.signal })
      if (!response.ok || !response.body) throw new Error('resource stream unavailable')
      if (attempt) options.onConnection?.('reconnected')
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''
      while (true) {
        const { value, done } = await reader.read(); buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
        let split = buffer.indexOf('\n\n')
        while (split >= 0) { const packet = buffer.slice(0, split); buffer = buffer.slice(split + 2); const raw = packet.match(/^data:\s*(.+)$/m)?.[1]; if (raw) { const event = JSON.parse(raw) as ResourceTaskEvent; onEvent(event); if (event.event === 'done' || event.event === 'error' || event.event === 'pause') return }; split = buffer.indexOf('\n\n') }
        if (done) throw new Error('stream closed before terminal event')
      }
    } catch (error) {
      if (options.signal?.aborted) return
      if (attempt >= (options.retries ?? 3)) throw error
      attempt += 1; options.onConnection?.('offline')
      await new Promise(resolve => window.setTimeout(resolve, Math.min(1200 * attempt, 3600)))
    }
  }
}
