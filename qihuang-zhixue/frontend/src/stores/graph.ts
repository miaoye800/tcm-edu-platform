import { defineStore } from 'pinia'
import { getDefaultCenter, getGraph, getGraphNodeDetail } from '../services/graphApi'
import type { GraphContent, GraphNodeDetail } from '../types/graph'

const source = (出处: string, 原文摘录: string, 定位: string) => ({ 出处, 原文摘录, 定位 })
export const mockMahuangGraph: GraphContent = {
  center_id: 'herb-mahuang',
  nodes: [
    { id: 'herb-gancao', name: '甘草', type: 'herb', brief: '调和诸药，益气缓急' },
    { id: 'herb-banxia', name: '半夏', type: 'herb', brief: '燥湿化痰，降逆止呕' },
    { id: 'herb-ganjiang', name: '干姜', type: 'herb', brief: '温中散寒，温肺化饮' },
    { id: 'herb-mahuang', name: '麻黄', type: 'herb', brief: '发汗解表，宣肺平喘，利水消肿' },
    { id: 'herb-guizhi', name: '桂枝', type: 'herb', brief: '解肌发表，温通经脉' },
    { id: 'herb-xixin', name: '细辛', type: 'herb', brief: '祛风散寒，温肺化饮' },
    { id: 'herb-xingren', name: '杏仁', type: 'herb', brief: '降气止咳平喘' },
    { id: 'herb-shaoyao', name: '芍药', type: 'herb', brief: '养血调营，敛阴和营' },
    { id: 'herb-wuweizi', name: '五味子', type: 'herb', brief: '敛肺滋肾，收敛止咳' },
    { id: 'syn-fenghan', name: '风寒表实', type: 'syndrome', brief: '恶寒发热，无汗而喘' },
    { id: 'syn-hanyin', name: '外寒内饮', type: 'syndrome', brief: '恶寒发热，咳喘痰稀，干呕' },
    { id: 'formula-mahuang', name: '麻黄汤', type: 'formula', brief: '发汗解表，宣肺平喘' },
    { id: 'formula-xiaoqinglong', name: '小青龙汤', type: 'formula', brief: '解表散寒，温肺化饮' },
    { id: 'cat-jiebiao', name: '发散风寒药', type: 'category', brief: '解表药类属' },
    { id: 'cat-fei', name: '肺经', type: 'category', brief: '麻黄的归经之一' },
  ],
  edges: [
    { source: 'herb-mahuang', target: 'formula-mahuang', relation_type: '组成', label: '方中组成·麻黄汤', source_ref: 2 },
    { source: 'herb-guizhi', target: 'formula-mahuang', relation_type: '组成', label: '方中组成·麻黄汤', source_ref: 2 },
    { source: 'herb-xingren', target: 'formula-mahuang', relation_type: '组成', label: '方中组成·麻黄汤', source_ref: 2 },
    { source: 'herb-gancao', target: 'formula-mahuang', relation_type: '组成', label: '方中组成·麻黄汤', source_ref: 2 },
    { source: 'herb-mahuang', target: 'formula-xiaoqinglong', relation_type: '组成', label: '方中组成·小青龙汤', source_ref: 3 },
    { source: 'herb-guizhi', target: 'formula-xiaoqinglong', relation_type: '组成', label: '方中组成·小青龙汤', source_ref: 3 },
    { source: 'herb-banxia', target: 'formula-xiaoqinglong', relation_type: '组成', label: '方中组成·小青龙汤', source_ref: 3 },
    { source: 'herb-xixin', target: 'formula-xiaoqinglong', relation_type: '组成', label: '方中组成·小青龙汤', source_ref: 3 },
    { source: 'herb-ganjiang', target: 'formula-xiaoqinglong', relation_type: '组成', label: '方中组成·小青龙汤', source_ref: 3 },
    { source: 'herb-shaoyao', target: 'formula-xiaoqinglong', relation_type: '组成', label: '方中组成·小青龙汤', source_ref: 3 },
    { source: 'herb-wuweizi', target: 'formula-xiaoqinglong', relation_type: '组成', label: '方中组成·小青龙汤', source_ref: 3 },
    { source: 'herb-gancao', target: 'formula-xiaoqinglong', relation_type: '组成', label: '方中组成·小青龙汤', source_ref: 3 },
    { source: 'herb-mahuang', target: 'syn-fenghan', relation_type: '药证链', label: '主治证候·风寒表实', source_ref: 1 },
    { source: 'syn-fenghan', target: 'formula-mahuang', relation_type: '证方链', label: '常用方剂·麻黄汤', source_ref: 1 },
    { source: 'herb-mahuang', target: 'syn-hanyin', relation_type: '药证链', label: '主治证候·外寒内饮', source_ref: 4 },
    { source: 'syn-hanyin', target: 'formula-xiaoqinglong', relation_type: '证方链', label: '常用方剂·小青龙汤', source_ref: 4 },
    { source: 'herb-mahuang', target: 'cat-jiebiao', relation_type: '归经聚类', label: '药类归属·发散风寒药', source_ref: 5 },
    { source: 'herb-mahuang', target: 'cat-fei', relation_type: '归经聚类', label: '归经·肺经', source_ref: 6 },
  ],
  sources: [
    source('《神农本草经》', '主中风伤寒头痛，温疟。发汗。', '上品'),
    source('《伤寒论》', '太阳病，头痛发热，身疼腰痛，骨节疼痛，恶风，无汗而喘者，麻黄汤主之。', '辨太阳病脉证并治'),
    source('《伤寒论》', '麻黄汤方：麻黄、桂枝、杏仁、甘草。', '辨太阳病脉证并治'),
    source('《伤寒论》', '小青龙汤方：麻黄、芍药、细辛、干姜、甘草、桂枝、五味子、半夏。', '辨太阳病脉证并治'),
    source('《伤寒论》', '伤寒表不解，心下有水气，干呕发热而咳，小青龙汤主之。', '辨太阳病脉证并治'),
    source('《中药学》', '麻黄属发散风寒药。', '解表药'),
    source('《中药学》', '归肺、膀胱经。', '性味归经'),
  ],
}

export const useGraphStore = defineStore('graph', {
  state: () => ({ content: mockMahuangGraph as GraphContent, usingMock: true, loading: false, detail: null as GraphNodeDetail | null, detailLoading: false, detailRequestVersion: 0 }),
  actions: {
    setContent(content?: GraphContent) { this.content = content?.nodes?.length ? content : mockMahuangGraph },
    /** 按中心节点拉取真实子图；失败或为空时返回 false，由调用方决定是否回落 mock。 */
    async loadCenter(centerId: string): Promise<boolean> {
      if (!centerId) return false
      this.loading = true
      try {
        const content = await getGraph(centerId)
        if (!content?.nodes?.length) return false
        this.content = content
        this.usingMock = false
        return true
      } catch {
        return false
      } finally {
        this.loading = false
      }
    },
    /** 优先用学习路径当前节点，其次退到任一有缓存的药材。 */
    async loadDefaultCenter(): Promise<boolean> {
      try {
        const { center_id: centerId } = await getDefaultCenter()
        if (centerId && (await this.loadCenter(centerId))) return true
      } catch {
        /* 未登录或路径为空时静默回落 */
      }
      return false
    },
    async loadDetail(nodeId: string) {
      const requestVersion = ++this.detailRequestVersion
      this.detailLoading = true
      try {
        const detail = await getGraphNodeDetail(nodeId)
        if (requestVersion !== this.detailRequestVersion) return
        this.detail = detail; this.usingMock = false
      } catch {
        if (requestVersion !== this.detailRequestVersion) return
        this.detail = fallbackDetail(this.content, nodeId); this.usingMock = true
      } finally {
        if (requestVersion === this.detailRequestVersion) this.detailLoading = false
      }
    },
  },
})
const mockDetail: GraphNodeDetail = { 性味归经: '辛、微苦，温。归肺、膀胱经。', 功效主治: { 功效: ['发汗解表', '宣肺平喘', '利水消肿'], 主治: ['风寒表实证', '胸闷喘咳', '风水浮肿'] }, 常用剂量与炮制: { 剂量: '2-10g，煎服', 炮制: '生用发汗力强；蜜炙偏于润肺平喘。' }, 使用禁忌: ['表虚自汗、阴虚盗汗者慎用', '高血压及失眠患者慎用'], 经典出处: ['《神农本草经》', '《伤寒论》'], 关联考点: { exam_frequency: .92, 说明: '麻黄汤组成与麻桂配伍为高频辨析点。' }, sources: mockMahuangGraph.sources.slice(0, 3) }

const fallbackDetail = (content: GraphContent, nodeId: string): GraphNodeDetail => {
  if (nodeId === 'herb-mahuang') return mockDetail
  const relatedSources = [...new Set(content.edges.filter(edge => edge.source === nodeId || edge.target === nodeId).map(edge => edge.source_ref))]
    .map(index => content.sources[index]).filter((source): source is GraphContent['sources'][number] => Boolean(source)).slice(0, 4)
  const node = content.nodes.find(item => item.id === nodeId)
  return {
    功效主治: { 功效: node?.brief ? [node.brief] : [], 主治: [] },
    经典出处: [...new Set(relatedSources.map(source => source.出处))],
    sources: relatedSources,
  }
}
