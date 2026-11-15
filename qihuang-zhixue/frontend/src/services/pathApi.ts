import { requestJson, type RequestOptions } from './request'
import type { ClarifyResult, CompleteResponse, CurrentPathResponse, DraftOp, ExpandResponse, FinalizeResult, KnowledgeTreeResponse, PlanDraft, PlanResponse } from '../types/path'

const post = <T>(url: string, payload: unknown, options?: RequestOptions) => requestJson<T>(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }, options)
export const getKnowledgeTree = () => requestJson<KnowledgeTreeResponse>('/api/path/knowledge-tree')
export const getCurrentPath = () => requestJson<CurrentPathResponse>('/api/path/current')
export const planPath = () => post<PlanResponse>('/api/path/plan', {})
export const expandPath = (domain_id: string) => post<ExpandResponse>('/api/path/expand', { domain_id })
export const reorderPath = (node_ids: string[]) => post<{ version: number; ordered_node_ids: string[]; states: unknown[] }>('/api/path/reorder', { node_ids })
export const createCustomNode = (text: string) => post<{ accepted: boolean; suggestion?: string; node?: any; state?: any }>('/api/path/custom-node', { text })
export const deferPathNode = (node_id: string, reason: string, reason_text?: string) => post<{ state: any }>('/api/path/defer', { node_id, reason, reason_text })
export const completePathNode = (node_id: string) => post<CompleteResponse>('/api/path/complete-node', { node_id, bkt_snapshot: {} })

// ── 两段式草稿（P5）：草稿只追加，确认才进 current_path ───────────────────
const patch = <T>(url: string, payload: unknown) => requestJson<T>(url, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })

/**
 * 排之前的追问检查。**只读**：不落库、不调模型，判定是确定性的。
 *
 * 为什么要先问：期限 / 范围 / 起点决定同一份画像排出完全不同的路线。以前这三样
 * 全靠关键词表猜，猜不到就按默认值直接排——用户既没被问过，也不知道默认值从哪来。
 */
export const clarifyPlan = (payload: { goal?: string; answers?: Record<string, string> } = {}) =>
  post<ClarifyResult>('/api/path/plan/clarify', payload, { timeoutMs: 20_000 })

/** 骨架草稿。goal 会翻成 breadth/pace/depth，不只是写进摘要。 */
export const createPlanDraft = (payload: { goal?: string; reuse?: boolean; answers?: Record<string, string> } = {}) =>
  post<PlanDraft>('/api/path/plan/draft', payload, { timeoutMs: 60_000 })
export const getPlanDraft = (version?: number) =>
  requestJson<PlanDraft>(`/api/path/plan/draft${version ? `?version=${version}` : ''}`)
export const patchPlanDraft = (version: number, ops: DraftOp[]) =>
  patch<PlanDraft>(`/api/path/plan/draft/${version}`, { ops })
export const confirmPlanDraft = (version: number) =>
  post<{ version: number; stage: string; versions: number[] }>(`/api/path/plan/draft/${version}/confirm`, {})

/**
 * 一步定稿：确认取舍 → 展开全部单元 → 确认明细。
 *
 * 向导只有三步（说目标 / 定取舍 / 确认）。独立的「看细节 + 调掌握度」已经取消：
 * 把 0.70~0.95 的滑杆摆给还没开始学的人，他能做的判断只有"这俩数字差不多"。
 */
export const finalizePlanDraft = (version: number) =>
  post<FinalizeResult>(`/api/path/plan/draft/${version}/finalize`, {}, { timeoutMs: 180_000 })

export const createDetailDraft = (payload: { version?: number; domains?: string[] } = {}) =>
  post<PlanDraft>('/api/path/detail/draft', payload, { timeoutMs: 120_000 })

// ── 呈现（P7）：同一版同一体裁只产一份，body.reused 为 true 即复用 ─────────
/**
 * 一种文稿体裁的「说明书」。
 *
 * 四条缺一不可：只给名字的话用户没法判断该选哪个，选择器就退化成"随便点一个"。
 * 这四条由后端 `DOC_ARCHETYPE_SPECS` 统一给出，前端不另写一份文案。
 */
export type PresentationOption = {
  /** 形态名：mindmap / route_guide / task_cards / syllabus_map。 */
  value: string
  label: string
  /** 什么时候该选它。 */
  when: string
  /** 应含内容。 */
  contents: string
  /** 展现形式。 */
  form: string
  /** 创作原理：为什么这样组织就更好用。 */
  principle: string
  /** 适用价值：什么场合拿得出手。叫 merit 而不是 value，避免和形态名撞车。 */
  merit: string
}

export type PlanPresentation = {
  kind: 'mindmap' | 'doc'
  /** 文稿体裁：route_guide / task_cards / syllabus_map。导图时为空串。 */
  archetype?: string
  archetype_label?: string
  presentation_options?: PresentationOption[]
  resource_id: string
  open_url: string
  title: string
  reason: string
  node_count: number
  reused: boolean
  /** 实际导出的那一版。复述或再点一次时带上它，别让"最近一版"在半路前进。 */
  plan_version: number
}

/**
 * 把已确认的路线导成导图或文稿。**不重新谋篇**：结构就是确认过的结构。
 *
 * `docArchetype` 只在 present='doc' 时有意义：说明书 / 任务卡 / 考纲对照表，
 * 分别回答"为什么这么排""今天做什么""每节在教材哪一章"。
 */
export const presentPlan = (
  payload: { present?: 'auto' | 'mindmap' | 'doc'; version?: number; docArchetype?: string } = {},
) =>
  post<PlanPresentation>(
    '/api/capabilities/plan.present',
    {
      present: payload.present ?? 'auto',
      ...(payload.version ? { version: payload.version } : {}),
      ...(payload.docArchetype ? { doc_archetype: payload.docArchetype } : {}),
    },
    { timeoutMs: 30_000 },
  )

