export type GraphEntityType = 'herb' | 'syndrome' | 'formula' | 'category'

export type GraphRelationType =
  | '功效相通' | '相须' | '相使' | '相畏' | '相杀' | '相恶' | '相反'
  | '性味相同' | '性味相反' | '归经聚类' | '药证链' | '证方链' | '同类鉴别'
  | '组成'

export interface GraphSource { 出处: string; 原文摘录: string; 定位: string }
export interface GraphNode { id: string; name: string; type: GraphEntityType; brief: string }
export interface GraphEdge { source: string; target: string; relation_type: GraphRelationType | string; label: string; source_ref: number; confidence?: number }
export interface GraphContent { center_id: string; nodes: GraphNode[]; edges: GraphEdge[]; sources: GraphSource[] }

export interface GraphNodeDetail {
  性味归经?: string
  功效主治?: { 功效?: string[]; 主治?: Array<string | Record<string, unknown>> }
  常用剂量与炮制?: { 剂量?: string; 炮制?: string }
  使用禁忌?: string[]
  经典出处?: string[]
  关联考点?: { exam_frequency?: number; 说明?: string }
  sources?: GraphSource[]
  [key: string]: unknown
}
