import { defineStore } from 'pinia'
import { getGraphNodeDetail } from '../services/graphApi'
import type { GraphContent, GraphNodeDetail } from '../types/graph'

const source = (出处: string, 原文摘录: string, 定位: string) => ({ 出处, 原文摘录, 定位 })
export const mockMahuangGraph: GraphContent = {
  center_id: 'herb-mahuang',
  nodes: [
    { id: 'herb-gancao', name: '甘草', type: 'herb', brief: '调和诸药，益气缓急' },
    { id: 'herb-banxia', name: '半夏', type: 'herb', brief: '燥湿化痰，降逆止呕' },
    { id: 'herb-fuling', name: '茯苓', type: 'herb', brief: '渗湿利水，健脾宁心' },
    { id: 'syn-phlegm', name: '痰湿内停', type: 'syndrome', brief: '痰多胸闷，脾运不健' },
    { id: 'herb-mahuang', name: '麻黄', type: 'herb', brief: '发汗解表，宣肺平喘，利水消肿' },
    { id: 'herb-guizhi', name: '桂枝', type: 'herb', brief: '助麻黄发汗解肌' }, { id: 'herb-xixin', name: '细辛', type: 'herb', brief: '温肺化饮' },
    { id: 'herb-shigao', name: '石膏', type: 'herb', brief: '清泄肺热' }, { id: 'herb-xingren', name: '杏仁', type: 'herb', brief: '降气平喘' },
    { id: 'herb-jiegeng', name: '桔梗', type: 'herb', brief: '宣肺利咽' }, { id: 'syn-fenghan', name: '风寒表实', type: 'syndrome', brief: '恶寒发热，无汗喘咳' },
    { id: 'syn-shuiyin', name: '寒饮喘咳', type: 'syndrome', brief: '外寒内饮，喘咳痰稀' }, { id: 'formula-mahuang', name: '麻黄汤', type: 'formula', brief: '发汗解表，宣肺平喘' },
    { id: 'formula-xiaoqinglong', name: '小青龙汤', type: 'formula', brief: '解表化饮，止咳平喘' }, { id: 'cat-jiebiao', name: '发散风寒药', type: 'category', brief: '解表药类属' },
    { id: 'cat-fei', name: '肺经', type: 'category', brief: '归经聚类' },
  ],
  edges: [
    { source: 'herb-mahuang', target: 'herb-gancao', relation_type: '相畏', label: '相畏·缓和峻烈', source_ref: 0 },
    { source: 'herb-mahuang', target: 'herb-banxia', relation_type: '相杀', label: '相杀·制约药性', source_ref: 1 },
    { source: 'herb-mahuang', target: 'herb-fuling', relation_type: '相恶', label: '相恶·避免相碍', source_ref: 2 },
    { source: 'herb-mahuang', target: 'herb-banxia', relation_type: '相反', label: '相反·禁忌辨识', source_ref: 3 },
    { source: 'herb-mahuang', target: 'herb-fuling', relation_type: '性味相同', label: '性味相同·温化水湿', source_ref: 4 },
    { source: 'herb-mahuang', target: 'syn-phlegm', relation_type: '功效相通', label: '功效相通·宣肺化饮', source_ref: 5 },
    { source: 'herb-mahuang', target: 'herb-guizhi', relation_type: '相须', label: '相须·助发汗', source_ref: 0 },
    { source: 'herb-mahuang', target: 'herb-xingren', relation_type: '相使', label: '相使·宣降肺气', source_ref: 1 },
    { source: 'herb-mahuang', target: 'herb-xixin', relation_type: '功效相通', label: '功效相通·温肺化饮', source_ref: 2 },
    { source: 'herb-mahuang', target: 'herb-shigao', relation_type: '同类鉴别', label: '同类鉴别·寒热有别', source_ref: 3 },
    { source: 'herb-mahuang', target: 'herb-jiegeng', relation_type: '性味相反', label: '性味相反·升降互参', source_ref: 4 },
    { source: 'herb-mahuang', target: 'syn-fenghan', relation_type: '药证链', label: '药证链·风寒表实', source_ref: 5 },
    { source: 'syn-fenghan', target: 'formula-mahuang', relation_type: '证方链', label: '证方链·麻黄汤', source_ref: 6 },
    { source: 'herb-mahuang', target: 'syn-shuiyin', relation_type: '药证链', label: '药证链·寒饮喘咳', source_ref: 7 },
    { source: 'syn-shuiyin', target: 'formula-xiaoqinglong', relation_type: '证方链', label: '证方链·小青龙汤', source_ref: 8 },
    { source: 'herb-mahuang', target: 'cat-jiebiao', relation_type: '归经聚类', label: '药类聚类·解表', source_ref: 9 },
    { source: 'herb-mahuang', target: 'cat-fei', relation_type: '归经聚类', label: '归经聚类·肺经', source_ref: 10 },
  ],
  sources: [source('《神农本草经》', '主中风伤寒头痛，温疟。', '上品'), source('《本草纲目》', '麻黄乃肺经专药。', '草部'), source('《伤寒论》', '麻黄汤主之。', '辨太阳病脉证并治'), source('《本草求真》', '石膏与麻黄，一寒一热。', '卷上'), source('《本草备要》', '桔梗升，杏仁降。', '草部'), source('《伤寒论》', '太阳病，头痛发热，身疼腰痛，骨节疼痛，恶风，无汗而喘者。', '辨太阳病'), source('《伤寒论》', '麻黄汤：麻黄、桂枝、杏仁、甘草。', '方剂'), source('《伤寒论》', '伤寒表不解，心下有水气，干呕发热而咳。', '辨太阳病'), source('《伤寒论》', '小青龙汤主之。', '方剂'), source('《中药学》', '麻黄属发散风寒药。', '解表药'), source('《中药学》', '归肺、膀胱经。', '性味归经')],
}

export const useGraphStore = defineStore('graph', {
  state: () => ({ content: mockMahuangGraph as GraphContent, usingMock: true, loading: false, detail: null as GraphNodeDetail | null, detailLoading: false }),
  actions: {
    setContent(content?: GraphContent) { this.content = content?.nodes?.length ? content : mockMahuangGraph },
    async loadDetail(nodeId: string) { this.detailLoading = true; try { this.detail = await getGraphNodeDetail(nodeId); this.usingMock = false } catch { this.detail = mockDetail; this.usingMock = true } finally { this.detailLoading = false } },
  },
})
const mockDetail: GraphNodeDetail = { 性味归经: '辛、微苦，温。归肺、膀胱经。', 功效主治: { 功效: ['发汗解表', '宣肺平喘', '利水消肿'], 主治: ['风寒表实证', '胸闷喘咳', '风水浮肿'] }, 常用剂量与炮制: { 剂量: '2-10g，煎服', 炮制: '生用发汗力强；蜜炙偏于润肺平喘。' }, 使用禁忌: ['表虚自汗、阴虚盗汗者慎用', '高血压及失眠患者慎用'], 经典出处: ['《神农本草经》', '《伤寒论》'], 关联考点: { exam_frequency: .92, 说明: '麻黄汤组成与麻桂配伍为高频辨析点。' }, sources: mockMahuangGraph.sources.slice(0, 3) }
