import { handleUnauthorized, requestJson, requestResponse } from './request'
import type { ResourceItem, ResourceListResponse } from '../types/resource'
import type { OutlineSlide, PptSaveResult, SlideNode } from '../types/ppt'

/**
 * 保存失败一律带状态码。
 *
 * 三种处置完全不同：409（别人改过，要用户选「重新载入 / 保留我的版本」）、
 * 422（结构非法，提示具体原因）、其它（网络/服务端，可重试）。
 */
export class PptSaveError extends Error {
  readonly status: number
  readonly detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'PptSaveError'
    this.status = status
    this.detail = detail
  }
}

export interface SaveDeckPayload {
  version: number
  theme_id: string
  slides: SlideNode[]
}

export const saveDeck = async (id: string, payload: SaveDeckPayload): Promise<PptSaveResult> => {
  const response = await requestResponse(
    `/api/resources/${id}/ppt/deck`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
    { timeoutMs: 15_000 },
  )
  await handleUnauthorized(response)
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new PptSaveError(response.status, body?.detail || `保存失败：${response.status}`)
  }
  return (await response.json()) as PptSaveResult
}

/**
 * 确认大纲：把（可能被改过的）页序写回任务，任务推进到敷演步。
 *
 * 这里只发 id/title/points/source_indexes，不发明细字段——后端 OutlineSlide
 * 是 `extra="forbid"`，多带一个键就是 422。
 */
export const confirmOutline = async (taskId: string, slides: OutlineSlide[]): Promise<{ ok: boolean; current_step: number }> => {
  const response = await requestResponse(
    `/api/resource/tasks/${taskId}/ppt/outline`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        slides: slides.map((slide) => ({
          id: slide.id,
          title: slide.title,
          points: slide.points,
          source_indexes: slide.source_indexes,
        })),
      }),
    },
    { timeoutMs: 15_000 },
  )
  await handleUnauthorized(response)
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as { detail?: string }
    throw new PptSaveError(response.status, body?.detail || `确认大纲失败：${response.status}`)
  }
  return (await response.json()) as { ok: boolean; current_step: number }
}

/** 左栏「最近讲稿」：只取前几条，用来提供「继续编辑」的入口。 */
export const listRecentDecks = async (limit = 3): Promise<ResourceItem[]> => {
  const response = await requestJson<ResourceListResponse>(`/api/resources?type=ppt&limit=${limit}`)
  return response.items ?? []
}
