import { requestJson } from './request'
import type { CompleteResponse, CurrentPathResponse, ExpandResponse, KnowledgeTreeResponse, PlanResponse } from '../types/path'

const post = <T>(url: string, payload: unknown) => requestJson<T>(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
export const getKnowledgeTree = () => requestJson<KnowledgeTreeResponse>('/api/path/knowledge-tree')
export const getCurrentPath = () => requestJson<CurrentPathResponse>('/api/path/current')
export const planPath = () => post<PlanResponse>('/api/path/plan', {})
export const expandPath = (domain_id: string) => post<ExpandResponse>('/api/path/expand', { domain_id })
export const reorderPath = (node_ids: string[]) => post<{ version: number; ordered_node_ids: string[]; states: unknown[] }>('/api/path/reorder', { node_ids })
export const createCustomNode = (text: string) => post<{ accepted: boolean; suggestion?: string; node?: any; state?: any }>('/api/path/custom-node', { text })
export const deferPathNode = (node_id: string, reason: string, reason_text?: string) => post<{ state: any }>('/api/path/defer', { node_id, reason, reason_text })
export const completePathNode = (node_id: string) => post<CompleteResponse>('/api/path/complete-node', { node_id, bkt_snapshot: {} })
