import { requestJson } from './request'
import type {
  AttributionResponse,
  CompareResult,
  KgAskResult,
  KgHealth,
  KgPathResult,
  KgReverseResult,
  KgSearchHit,
  MistakeAttribution,
  MistakeRecord,
  PrescriptionReviewResult,
} from '../types/kg'

/**
 * 知识图谱服务客户端（``/api/kg``）与错题归因（``/api/mistakes``）。
 *
 * 这些端点都要登录，所以**不传** ``skipAuthRedirect``——401 时该回登录页
 * 就回登录页。旧图谱接口（``graphApi.ts``）允许匿名预览，两者策略不同。
 */

// ---------------------------------------------------------------------------
// 图谱
// ---------------------------------------------------------------------------

/** 实体搜索。``kind`` 限定 herb/formula/syndrome，不传则全收。 */
export const searchKgEntities = (q: string, kind?: string, limit = 12) => {
  const params = new URLSearchParams({ q, limit: String(limit) })
  if (kind) params.set('kind', kind)
  return requestJson<{ query: string; items: KgSearchHit[] }>(`/api/kg/search?${params.toString()}`)
}

/**
 * 两药对比。**不调模型**——逐项结构差异 + 教材明文鉴别句 + T1 约束。
 *
 * 返回里 ``ok:false`` 不是错误：可能是「这味药没进图谱」（只有身份没有性味功效），
 * 这时 ``reason`` 会说清楚，``constraints`` 仍然会照答能不能一起用。
 */
export const compareHerbs = (left: string, right: string) => {
  const params = new URLSearchParams({ left, right })
  return requestJson<CompareResult>(`/api/kg/compare?${params.toString()}`)
}

/** 两实体最短关系链。回答「这两味药是怎么扯上关系的」。 */
export const findKgPath = (fromId: string, toId: string, maxHops = 4) => {
  const params = new URLSearchParams({ from_id: fromId, to_id: toId, max_hops: String(maxHops) })
  return requestJson<KgPathResult>(`/api/kg/path?${params.toString()}`)
}

/**
 * GraphRAG 问答（P5）。**默认不调模型**：先认出问题里的实体（种子），
 * 再沿 T1/T2 边扩一跳，把「相连的事实」和教材原文一起返回。
 *
 * 和普通检索的区别：普通检索只给「相似的条目」，这里给的是「这两味药在图里
 * 怎么连着的」——所以 ``citations`` 里每条都能按 ``edge_id`` 追回图里那条边。
 *
 * ``seedIds`` 是给上游已经做过向量检索的调用方用的：传了 id 就不再解析问题文本，
 * 避免「检索出来的是 A，摘要说的是 B」。
 */
export const askKgGraph = (
  question: string,
  options: { hops?: number; limit?: number; seedIds?: string[]; expand?: boolean } = {},
) =>
  requestJson<KgAskResult>(
    '/api/kg/ask',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question,
        hops: options.hops ?? 1,
        limit: options.limit ?? 40,
        seed_ids: options.seedIds,
        expand: options.expand ?? true,
      }),
    },
    { timeoutMs: 20_000 },
  )

/**
 * 反向检索：症状 → 证 → 方 → 药（方案 §0 第 2 类问题）。
 *
 * **全程查表，不调模型。** 三段都是机械动作：症状短语走教材倒排索引找证候，
 * 证候走「证方链」找方，方走「组成」、证走「药证链」找药。
 *
 * 为什么不能用模型兜底：口语说法是开放的（「嗓子疼」），教材词表是封闭的
 * （「咽喉肿痛」），缺口永远补不完。模型遇到缺口不会说「不知道」，它会就近编一个证——
 * 那个证一旦进到处方审查，错的就不是一条展示，是一次开方。所以 `ok:false` 时
 * 页面显示 `reason` + `hint`（该怎么问），而不是一批「看着像」的结果。
 */
export const reverseLookup = (target: string, topK = 3) => {
  const params = new URLSearchParams({ target, top_k: String(topK) })
  return requestJson<KgReverseResult>(`/api/kg/reverse?${params.toString()}`)
}

/** 图谱健康度：实体/边按类型计数。用于「这个能力是不是真的在跑」的自检。 */
export const getKgHealth = () => requestJson<KgHealth>('/api/kg/health')

/**
 * 处方审查（P3）。**同步规则 + 图遍历，不调模型**（除非四级字面映射全落空，
 * 后端才会做一次闭集映射；没有 key 时直接跳过）。
 *
 * 返回的每条 ``findings`` 都带 ``evidence``（教材页码 + 原文）和 ``edge_ids``
 * （指回图谱那条边）。``verdict`` 三态：``block`` 不可开、``warn`` 有疑点、
 * ``pass`` 未发现问题。
 */
export const reviewPrescription = (input: { case?: string; prescription?: string }) =>
  requestJson<PrescriptionReviewResult>(
    '/api/kg/prescription/review',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ case: input.case ?? '', prescription: input.prescription ?? '' }),
    },
    {
      // 后端在字面映射全落空时会补一次模型调用，默认 10s 不够。
      timeoutMs: 25_000,
      timeoutMessage: '审查超时：后端可能正在做证候映射，稍后重试',
    },
  )

// ---------------------------------------------------------------------------
// 错题归因
// ---------------------------------------------------------------------------

export const listMyMistakes = (limit = 20) =>
  // `count` 是本次返回的条数，`total` 才是错题总数（不受 limit 截断）。侧栏角标用 total。
  requestJson<{ count: number; total: number; items: MistakeRecord[] }>(`/api/mistakes?limit=${limit}`)

/**
 * 错题归因 + 汇总。
 *
 * 每条错题**至少有一条** ``causes``：能归因的给具体类型和图上那条边，
 * 归不了的给 ``unknown`` 并写清卡在哪一步。``summary.unattributed`` 单独计数。
 */
export const getMyAttribution = (limit = 20, refresh = true) =>
  requestJson<AttributionResponse>(`/api/mistakes/attribution?limit=${limit}&refresh=${refresh}`)

/** 单条重算。错题详情页打开时调。 */
export const reattributeMistake = (mistakeId: number | string) =>
  requestJson<MistakeAttribution>(`/api/mistakes/${encodeURIComponent(String(mistakeId))}/attribution`, {
    method: 'POST',
  })
