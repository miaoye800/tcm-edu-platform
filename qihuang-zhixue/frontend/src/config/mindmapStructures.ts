/**
 * 四种导图结构。
 *
 * 这里是「视图属性」而非内容属性：同一棵树可以随时在四种结构间切换，
 * 不重新生成、不改数据。因此切换是瞬时的，也不需要调后端。
 *
 * 库的能力边界（已核实 simple-mind-map@0.14.0）：
 *   src/constants/constant.js → CONSTANTS.LAYOUT
 *   src/core/render/Render.js → 布局注册表
 * 只有 logicalStructure / mindMap / organizationStructure / fishbone 这四种
 * 与我们对外承诺的名字一一对应；timeLine 等留作后续，不接 UI。
 */

export type StructureId = 'logic-right' | 'mind-both' | 'org-down' | 'fishbone'

export interface MindmapStructure {
  id: StructureId
  name: string
  /** 选择器上的短标签，两字以内 */
  short: string
  layout: string
  /** 该结构能清楚表达的层数上限，超过就折叠 */
  maxDepth: number
  lineStyle: 'curve' | 'straight' | 'direct'
  when: string
  /**
   * 层间距。横向树靠 marginX 拉开、纵向靠 marginY，
   * 一套间距套到四种结构上，向下和鱼骨会被压成一条横带（已实测）。
   */
  gaps: { parentX: number; parentY: number; childX: number; childY: number }
}

export const mindmapStructures: MindmapStructure[] = [
  {
    id: 'logic-right',
    name: '向右分支',
    short: '向右',
    layout: 'logicalStructure',
    maxDepth: 5,
    lineStyle: 'curve',
    when: '单味药详解、属性罗列、大纲展开——最通用的默认形态',
    gaps: { parentX: 48, parentY: 10, childX: 28, childY: 4 },
  },
  {
    id: 'mind-both',
    name: '四面发散',
    short: '发散',
    layout: 'mindMap',
    maxDepth: 5,
    lineStyle: 'curve',
    when: '概念全景、总论、一个中心多个维度——左右均衡铺开',
    gaps: { parentX: 52, parentY: 14, childX: 30, childY: 6 },
  },
  {
    id: 'org-down',
    name: '向下分支',
    short: '向下',
    layout: 'organizationStructure',
    maxDepth: 4,
    lineStyle: 'direct',
    when: '分类层级、章节结构、证治分层——自上而下的从属关系',
    // 纵向结构靠 Y 拉开，横向反而要收紧，否则一张 6 支的图会宽到 6000px。
    // 实测：childX 18 时 36 节点的「解表药」被 fit 压到 0.376（字号剩 5px），
    // 收到 13 后约 0.43；再小就会撞字，这个值是「不重叠」与「看得清」的交点。
    gaps: { parentX: 64, parentY: 10, childX: 30, childY: 4 },
  },
  {
    id: 'fishbone',
    name: '鱼骨图',
    short: '鱼骨',
    layout: 'fishbone',
    maxDepth: 3,
    lineStyle: 'straight',
    when: '因果归因、病因病机、鉴别要点归因——主脊加上下斜刺',
    gaps: { parentX: 40, parentY: 44, childX: 24, childY: 14 },
  },
]

export const defaultStructureId: StructureId = 'logic-right'

export const getStructure = (id?: string | null): MindmapStructure =>
  mindmapStructures.find(item => item.id === id) ?? mindmapStructures[0]

/**
 * 结构切换时的降级说明。
 *
 * 这条文案是刻意存在的：v1 里后端接受 fishbone 却渲染成树，用户点了没反应。
 * 宁可明说「已折叠 12 个节点」，也不要静默丢内容。
 */
export const describeOverflow = (structure: MindmapStructure, actualDepth: number, folded: number): string => {
  if (actualDepth <= structure.maxDepth || folded <= 0) return ''
  return `${structure.name}最多清楚呈现 ${structure.maxDepth} 层，已折叠 ${folded} 个更深节点`
}
