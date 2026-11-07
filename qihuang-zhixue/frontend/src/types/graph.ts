export type GraphEntityType = 'herb' | 'syndrome' | 'formula' | 'category'

export type GraphRelationType =
  | '功效相通' | '相须' | '相使' | '相畏' | '相杀' | '相恶' | '相反'
  // P2 重建后的正式类型：十八反/十九畏 = T1 硬约束，配伍 = 教材未标七情的配伍
  | '十八反' | '十九畏' | '配伍'
  | '性味相同' | '性味相反' | '归经聚类' | '药证链' | '证方链' | '同类鉴别'
  | '组成'

export interface GraphSource { 出处: string; 原文摘录: string; 定位: string }
export interface GraphNode { id: string; name: string; type: GraphEntityType; brief: string }
export interface GraphEdge {
  source: string
  target: string
  relation_type: GraphRelationType | string
  label: string
  source_ref: number
  confidence?: number
  /**
   * 边所属的可信层。1 = 硬约束（十八反/十九畏，错一次就是教学事故，只能人工校订）；
   * 2 = 教材明文记载的事实关系（配伍、方剂组成、药证链）；3 = 按属性算出的相似聚类，
   * 按设计**不落库**，所以在线接口里不会出现。
   * 早期数据没有这个字段，因此可选。
   */
  tier?: 1 | 2 | 3
}
export interface GraphContent {
  center_id: string
  nodes: GraphNode[]
  edges: GraphEdge[]
  sources: GraphSource[]
  /** `/api/kg/neighbors` 附带的自述信息：这条内容是不是真数据、按哪几层取的边。 */
  meta?: {
    using_mock: boolean
    center_resolved: boolean
    tiers: number[]
    tier_counts: Record<string, number>
    /**
     * 后端是否因为 `limit` 砍过节点。只有 `truncated` 说不出「砍了几条」——
     * 前端要显示「已隐藏 N 条」必须同时拿到 `total_nodes`，靠 limit 反推是错的
     * （邻域本来就不够 limit 时，一条都没隐藏）。
     */
    truncated?: boolean
    total_nodes?: number
  }
}

export interface GraphNodeDetail {
  性味归经?: string
  功效主治?: { 功效?: string[]; 主治?: Array<string | Record<string, unknown>> }
  // 剂量可能是纯文本（早期数据），也可能是抽取管线产出的结构化对象。
  // 类型写成 string 会让人以为能直接插值，实际渲染出 [object Object]。
  常用剂量与炮制?: { 剂量?: string | { text?: string; min_g?: number; max_g?: number; usage_notes?: string }; 炮制?: string }
  使用禁忌?: string[]
  经典出处?: string[]
  关联考点?: { exam_frequency?: number; 说明?: string }
  sources?: GraphSource[]
  [key: string]: unknown
}
