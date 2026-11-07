import { handleUnauthorized, requestJson, requestResponse } from './request'
import type { ResourceItem, ResourceListResponse } from '../types/resource'

export interface SaveMindmapTreePayload {
  version: number
  nodeData: unknown
  template_id?: string | null
}

export interface SaveMindmapTreeResult {
  version: number
  node_count: number
  depth: number
  template_id: string | null
}

/**
 * 保存失败一律带状态码。
 *
 * 不能只抛字符串：调用方要区分三种完全不同的处置——409（别人改过，要用户选）、
 * 422（结构非法，要提示具体原因）、其它（网络/服务端，可重试）。
 */
export class MindmapSaveError extends Error {
  readonly status: number
  readonly detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'MindmapSaveError'
    this.status = status
    this.detail = detail
  }
}

export const saveMindmapTree = async (id: string, payload: SaveMindmapTreePayload): Promise<SaveMindmapTreeResult> => {
  const response = await requestResponse(`/api/resources/${id}/mindmap/tree`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }, { timeoutMs: 15_000 })
  await handleUnauthorized(response)
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: string }
    throw new MindmapSaveError(response.status, body?.detail || `保存失败：${response.status}`)
  }
  return (await response.json()) as SaveMindmapTreeResult
}

/** 左栏「最近导图」：只取前几条，用来提供「继续编辑」的入口。 */
export const listRecentMindmaps = async (limit = 30): Promise<ResourceItem[]> => {
  const response = await requestJson<ResourceListResponse>(`/api/resources?type=mindmap&limit=${limit}`)
  return response.items ?? []
}

/**
 * 保存结构 / 配色。
 *
 * 与整树保存分开：换结构只改视图、不动树，也就不该让版本号 +1。
 * 否则两个标签页各点一次配色就会互相触发 409。
 */
export const saveMindmapAppearance = async (
  id: string,
  payload: { structure: string; theme_id: string },
): Promise<{ structure: string; theme_id: string }> => {
  const response = await requestResponse(`/api/resources/${id}/mindmap/appearance`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }, { timeoutMs: 10_000 })
  await handleUnauthorized(response)
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as { detail?: string }
    throw new MindmapSaveError(response.status, body?.detail || `保存外观失败：${response.status}`)
  }
  return (await response.json()) as { structure: string; theme_id: string }
}
