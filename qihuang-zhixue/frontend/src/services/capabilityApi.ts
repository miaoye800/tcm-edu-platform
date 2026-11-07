import { requestJson, requestResponse, handleUnauthorized } from './request'
import type { CapabilityIndex, MindmapAccepted, MindmapCallParams } from '../types/capability'

/**
 * 能力契约的调用口。
 *
 * 这一层的存在，是为了让左对话栏从第一天起就**只认契约**，而不认某个
 * 具体的资源端点。将来接通用对话 Agent 时，换掉的是调用方，不是这里。
 *
 * 注意 202 与 4xx 的处理方式不同：202 是一次成功的受理（有 task_id），
 * 4xx 是契约层拒绝（有 code），调用方要按 code 决定重试还是改参数。
 */

export const listCapabilities = () => requestJson<CapabilityIndex>('/api/capabilities')

export const describeCapability = (name: string) => requestJson<Record<string, unknown>>(`/api/capabilities/${encodeURIComponent(name)}`)

export class CapabilityError extends Error {
  code: string
  remedy: string
  constructor(message: string, code: string, remedy = '') {
    super(message)
    this.name = 'CapabilityError'
    this.code = code
    this.remedy = remedy
  }
}

/** 生成导图。202 + task_id；重放时 `replayed` 为真，task_id 与上次相同。 */
export const generateMindmap = async (params: MindmapCallParams): Promise<MindmapAccepted> => {
  const response = await requestResponse('/api/capabilities/mindmap.generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  }, { timeoutMs: 20_000 })
  await handleUnauthorized(response)
  const body = await response.json().catch(() => ({})) as Partial<MindmapAccepted> & { code?: string; message?: string; remedy?: string }
  if (!response.ok || !body.task_id) {
    throw new CapabilityError(body.message || `生成没被受理（${response.status}）`, body.code || 'capability.unknown', body.remedy || '')
  }
  return body as MindmapAccepted
}

/** 只换样子：结构 / 配色。同步 200。 */
export const presentMindmap = (resourceId: string, patch: { structure?: string; theme_id?: string }) =>
  requestJson<{ ok: boolean; resource_id: string; structure: string; theme_id: string; changed: string[] }>('/api/capabilities/mindmap.present', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ resource_id: resourceId, ...patch }),
  })

/** 读一张图：规模、三档依据、问法判定。 */
export const readMindmap = (resourceId: string) =>
  requestJson<{
    ok: boolean
    resource_id: string
    title: string
    nodes: number
    depth: number
    evidence: { textbook: number; graph: number; model: number }
    intent: { kind: string; label: string; note: string; degraded: boolean }
    structure: string
    theme_id: string
    open_url: string
  }>('/api/capabilities/mindmap.get', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ resource_id: resourceId }),
  })
