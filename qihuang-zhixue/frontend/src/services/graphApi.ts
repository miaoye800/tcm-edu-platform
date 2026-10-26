import { requestJson } from './request'
import type { GraphContent, GraphNodeDetail } from '../types/graph'

// A graph node can still be inspected while the detail endpoint is unavailable
// (for example in the public preview or during a short session refresh). Let
// the store show its local evidence fallback instead of navigating away from
// the graph as a side effect of selecting a node.
export const getGraphNodeDetail = (nodeId: string) => requestJson<GraphNodeDetail>(`/api/graph/node/${encodeURIComponent(nodeId)}/detail`, undefined, { skipAuthRedirect: true })

// 离线预计算（backend/scripts/build_graph.py）写入的按中心节点子图。
export const getGraph = (centerId: string) => requestJson<GraphContent>(`/api/graph/${encodeURIComponent(centerId)}`, undefined, { skipAuthRedirect: true })

// 学习路径上当前节点，作为图谱默认入口。
export const getDefaultCenter = () => requestJson<{ center_id: string | null }>('/api/graph/default-center', undefined, { skipAuthRedirect: true })
