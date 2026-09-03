import { defineStore } from 'pinia'
import layout from '../config/meridian-layout.json'
import { completePathNode, createCustomNode, deferPathNode, expandPath, getCurrentPath, getKnowledgeTree, planPath, reorderPath } from '../services/pathApi'
import type { BackendPathState, CurrentPathResponse, MapNode, NodePlan, PathState, PlanResponse } from '../types/path'

const mockPlan = (): CurrentPathResponse => ({
  plan: { version: 1, stages: layout.directions.map((direction, index) => ({ domain_id: direction.id, order: index + 1, order_locked: direction.id === 'general', reason: index ? '按教材章节递进，可在同组内调整。' : '总论是后续学习的共同基础。' })), domains: { general: { nodes: [{ node_id: 'general.p1', stage: 1, seq: 1, target_mastery: .8, resource_hint: '图文讲解', highlight: 'normal', calibration: false, reason: '建立共同基础。' }] }, d01: { nodes: [{ node_id: 'd01.s1.p1', stage: 2, seq: 1, target_mastery: .8, resource_hint: '随堂练习', highlight: 'exam_hot', calibration: false, reason: '高频重点。' }, { node_id: 'd01.s1.p2', stage: 2, seq: 2, target_mastery: .8, resource_hint: '对比记忆', highlight: 'normal', calibration: false, reason: '巩固辨析。' }] } }, adjustments: [] },
  states: [{ node_id: 'general', status: 'completed', updated_at: '', meta: {} }, { node_id: 'd01', status: 'current', updated_at: '', meta: {} }, { node_id: 'd02', status: 'available', updated_at: '', meta: {} }, { node_id: 'd03', status: 'paused', updated_at: '', meta: {} }], suggestions: [],
})

export const toMeridianStatus = (state: BackendPathState | undefined, highlight = 'normal', free = false): MapNode['status'] => {
  if (state === 'completed') return 'mastered'
  if (state === 'current') return 'current'
  if (state === 'paused') return 'deferred'
  if (state === 'custom') return 'custom'
  if (highlight === 'exam_hot') return 'exam_hot'
  if (free) return 'free'
  return 'recommended'
}

export const usePathStore = defineStore('path', {
  state: () => ({ tree: [] as any[], current: null as CurrentPathResponse | null, loading: false, planning: false, error: '', fallback: false, usingMock: false, expandedDomains: [] as string[], latestPlan: null as PlanResponse | null }),
  getters: {
    hasPlan: (state) => Boolean(state.current?.plan.stages.length),
    nodes(state): MapNode[] {
      const states = new Map((state.current?.states ?? []).map((item) => [item.node_id, item]))
      const stageById = new Map((state.current?.plan.stages ?? []).map((item) => [item.domain_id, item]))
      const baseNodes = layout.nodes.map((entry) => {
        const stage = stageById.get(entry.id)
        const pathState = states.get(entry.id)?.status ?? (entry.id === 'general' ? 'available' : 'locked')
        return { id: entry.id, label: stage ? (state.tree.find((node) => node.id === entry.id)?.name ?? entry.id) : entry.id === 'general' ? '中药学总论' : entry.id, detail: stage?.reason ?? entry.segment, domainId: entry.id, x: entry.x, y: entry.y, status: toMeridianStatus(pathState, 'normal', !stage?.order_locked), state: pathState, free: Boolean(stage && !stage.order_locked), expanded: state.expandedDomains.includes(entry.id) }
      })
      const customNodes = Object.entries(state.current?.plan.domains ?? {}).flatMap(([domainId, domain]) => domain.nodes.filter((node) => node.custom || states.get(node.node_id)?.status === 'custom').map((node, index) => {
        const anchor = layout.nodes.find((item) => item.id === domainId) ?? layout.nodes[0]
        const itemState = states.get(node.node_id)?.status ?? 'custom'
        return { id: node.node_id, label: node.name ?? '自定义节点', detail: node.reason ?? '用户自定义', domainId, x: anchor.x + 52 + index * 22, y: anchor.y - 82 - index * 28, status: toMeridianStatus(itemState, node.highlight, false), state: itemState, free: false, expanded: true, custom: true } as MapNode
      }))
      return [...baseNodes, ...customNodes]
    },
  },
  actions: {
    async load() {
      this.loading = true; this.error = ''
      if (window.sessionStorage.getItem('qihuang-e2e-force-mock') === '1' || window.localStorage.getItem('qihuang-e2e-force-mock') === '1') { this.current = mockPlan(); this.tree = layout.directions.map((item, index) => ({ id: item.id, name: item.label, level: 'domain', sort_hint: index })); this.usingMock = true; this.loading = false; return }
      try { const [tree, current] = await Promise.all([getKnowledgeTree(), getCurrentPath()]); this.tree = tree.domains; this.current = current; this.usingMock = false } catch (error) { this.current = mockPlan(); this.tree = layout.directions.map((item, index) => ({ id: item.id, name: item.label, level: 'domain', sort_hint: index })); this.usingMock = true; this.error = error instanceof Error ? error.message : '路线加载失败，已切换示例路线。' } finally { this.loading = false }
    },
    async plan() {
      this.planning = true; this.error = ''
      try { this.latestPlan = await planPath(); this.fallback = this.latestPlan.fallback; await this.load() } catch (error) { this.error = error instanceof Error ? error.message : '规划失败'; this.fallback = true; if (!this.current) this.current = mockPlan() } finally { this.planning = false }
    },
    async expand(domainId: string) {
      if (this.expandedDomains.includes(domainId)) return
      try { await expandPath(domainId); this.expandedDomains.push(domainId); if (!this.usingMock) await this.load() } catch (error) { this.error = error instanceof Error ? error.message : '展开失败' }
    },
    async reorder(nodeIds: string[]) { if (!this.usingMock) await reorderPath(nodeIds); if (!this.usingMock) await this.load() },
    async defer(nodeId: string, reason: string, reasonText?: string) { const result = this.usingMock ? { state: { node_id: nodeId, status: 'paused' as const, updated_at: '', meta: { reason, reason_text: reasonText } } } : await deferPathNode(nodeId, reason, reasonText); this.applyStates([result.state]); return result },
    async addCustom(text: string) { if (this.usingMock && this.current) { const node_id = `custom:${Date.now()}`; const node = { node_id, name: text, stage: 1, seq: 99, target_mastery: .8, resource_hint: '自定义主题', highlight: 'normal' as const, calibration: false, reason: '用户自定义节点', custom: true, domain_id: 'd01' }; this.current.plan.domains.d01.nodes.push(node); const state = { node_id, status: 'custom' as const, updated_at: '', meta: {} }; this.applyStates([state]); return { accepted: true, node, state } } const result = await createCustomNode(text); if (result.accepted && result.state) this.applyStates([result.state]); return result },
    async complete(nodeId: string) { if (this.usingMock) { const next = this.nodes.find((node) => node.id !== nodeId && node.state !== 'completed' && node.state !== 'paused')?.id ?? null; const changes = [{ node_id: nodeId, status: 'completed' as const, updated_at: '', meta: {} }, ...(next ? [{ node_id: next, status: 'current' as const, updated_at: '', meta: {} }] : [])]; this.applyStates(changes); return { version: 1, next_node: next, changes, diff: { node_id: nodeId, action: 'normal' as const, skip_node_ids: [], updates: [], inserted_nodes: [], reason: '' } } } const result = await completePathNode(nodeId); this.applyStates(result.changes); return result },
    applyStates(changes: PathState[]) { if (!this.current) return; const byId = new Map(this.current.states.map((item) => [item.node_id, item])); changes.forEach((item) => byId.set(item.node_id, item)); this.current.states = [...byId.values()] },
  },
})
