import { authHeaders, requestJson } from './request'
import type { ResourceFilters, ResourceItem, ResourceListResponse, ResourceTaskEvent, ResourceType } from '../types/resource'

export const listResources = (filters: ResourceFilters = {}) => { const params = new URLSearchParams(); if (filters.type) params.set('type', filters.type); if (filters.nodeId) params.set('node_id', filters.nodeId); if (filters.favorited) params.set('favorited', 'true'); if (filters.q) params.set('q', filters.q); return requestJson<ResourceListResponse>(`/api/resources?${params}`) }
export const getResource = (id: string) => requestJson<ResourceItem>(`/api/resources/${id}`)
export const createResourceTask = (nodeId: string, resourceType: ResourceType) => requestJson<{ task_id: string }>('/api/resource/tasks', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ node_id: nodeId, resource_type: resourceType }) })
export const toggleResourceFavorite = (id: string) => requestJson<ResourceItem>(`/api/resources/${id}/favorite`, { method: 'POST' })
export const patchMindmapTemplate = (id: string, templateId: string) => requestJson<ResourceItem>(`/api/resources/${id}/mindmap`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ template_id: templateId }) })
export const expandMindmap = (id: string, nodePath: string[]) => requestJson<{ node_path: string[]; added_nodes: unknown[]; mindmap_data: unknown; idempotent: boolean }>(`/api/mindmap/${id}/expand`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ node_path: nodePath }) })
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
        while (split >= 0) { const packet = buffer.slice(0, split); buffer = buffer.slice(split + 2); const raw = packet.match(/^data:\s*(.+)$/m)?.[1]; if (raw) { const event = JSON.parse(raw) as ResourceTaskEvent; onEvent(event); if (event.event === 'done' || event.event === 'error') return }; split = buffer.indexOf('\n\n') }
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
