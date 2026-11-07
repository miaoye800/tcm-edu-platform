import { requestJson } from './request'
import type { GraphContent, GraphNodeDetail } from '../types/graph'

// A graph node can still be inspected while the detail endpoint is unavailable
// (for example in the public preview or during a short session refresh). Let
// the store show its local evidence fallback instead of navigating away from
// the graph as a side effect of selecting a node.
export const getGraphNodeDetail = (nodeId: string) => requestJson<GraphNodeDetail>(`/api/graph/node/${encodeURIComponent(nodeId)}/detail`, undefined, { skipAuthRedirect: true })

/**
 * 按中心节点取局部子图。
 *
 * **P7 只改了这一行指向的端点。** 原来走 `/api/graph/{center_id}`，那是「旧前端
 * 契约」的入口；现在走 `/api/kg/neighbors/{id}`——方案 §3.5 定义的图谱服务入口。
 * 返回结构由后端 `adapters.to_graph_content` 适配回原来的
 * `center_id/nodes/edges/sources`，所以 `GraphView.vue`、`config/graph.ts`、
 * `stores/graph.ts` 一行都没动——这正是方案把 P7 限定为「只改一个文件」的前提。
 *
 * 中心节点用 `encodeURIComponent` 包起来：这里传进来的常是中文药名（麻黄）或
 * 带点的树节点 id（d17.s1.p1），裸拼进 URL 会在部分代理上被截断。
 *
 * `tiers` 默认 `1,2`：T1 是十八反/十九畏这类硬约束，T2 是教材明文记载的事实关系。
 * T3（按属性算出来的相似聚类）按设计不落库，服务端即使收到 `3` 也会剔掉，
 * 所以这里不提供「看 T3」的开关——不是漏了，是它本来就不在该被看到的地方。
 */
export const getGraph = (centerId: string, options: { hops?: number; limit?: number; tiers?: string } = {}) => {
  const params = new URLSearchParams()
  if (options.hops) params.set('hops', String(options.hops))
  if (options.limit) params.set('limit', String(options.limit))
  if (options.tiers) params.set('tiers', options.tiers)
  const query = params.toString()
  const path = `/api/kg/neighbors/${encodeURIComponent(centerId)}`
  return requestJson<GraphContent>(query ? `${path}?${query}` : path, undefined, { skipAuthRedirect: true })
}

// 学习路径上当前节点，作为图谱默认入口。
// 仍然留在 /api/graph：它答的是「我该看哪」，属于学习状态，不是图谱查询。
export const getDefaultCenter = () => requestJson<{ center_id: string | null }>('/api/graph/default-center', undefined, { skipAuthRedirect: true })
