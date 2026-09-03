import { requestJson } from './request'
import type { GraphNodeDetail } from '../types/graph'

export const getGraphNodeDetail = (nodeId: string) => requestJson<GraphNodeDetail>(`/api/graph/node/${encodeURIComponent(nodeId)}/detail`)
