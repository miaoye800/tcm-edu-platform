import type { GraphEntityType, GraphRelationType } from '../types/graph'

export const graphVisual = {
  entity: {
    herb: { label: '药材', color: 'var(--qh-graph-node-herb)' },
    syndrome: { label: '证候', color: 'var(--qh-graph-node-syndrome)' },
    formula: { label: '方剂', color: 'var(--qh-graph-node-formula)' },
    category: { label: '类属', color: 'var(--qh-graph-node-category)' },
  } satisfies Record<GraphEntityType, { label: string; color: string }>,
  relation: {
    配伍协同: { color: 'var(--qh-graph-edge-compatible)', group: '配伍协同', description: '相须、相使：共同增强功效', dash: 'solid' },
    配伍制约: { color: 'var(--qh-graph-edge-restraint)', group: '配伍制约', description: '相畏、相杀、相恶：说明制约关系', dash: 'dash' },
    配伍禁忌: { color: 'var(--qh-graph-edge-forbidden)', group: '配伍禁忌', description: '相反：重要用药禁忌', dash: 'dash' },
    功效互参: { color: 'var(--qh-graph-edge-benefit)', group: '功效互参', description: '功效相通、性味相合', dash: 'solid' },
    方药组成: { color: 'var(--qh-graph-edge-composition)', group: '方药组成', description: '方剂中包含的药材', dash: 'arrow' },
    药证方链: { color: 'var(--qh-graph-node-formula)', group: '药证方链', description: '药材关联证候，证候关联方剂', dash: 'arrow' },
    鉴别对照: { color: 'var(--qh-graph-edge-contrast)', group: '鉴别对照', description: '功效或性味差异，帮助辨析', dash: 'dot' },
    归属聚类: { color: 'var(--qh-graph-edge-cluster)', group: '归属聚类', description: '归经、药类等所属关系', dash: 'dot' },
  },
  maxVisibleNodes: 60,
  motion: { introMs: 900, pulseMs: 3200, cameraMs: 420, panelMs: 260, reducedFrameMs: 50 },
  space: { sphereRadius: 285, centerRadius: 500, cameraDistance: 665, minCameraDistance: 340, maxCameraDistance: 1300, autoRotateSpeed: 0.018 },
  canvas: {
    fallbackColor: 'var(--qh-graph-node-herb)',
    labelFill: 'var(--qh-graph-ink)',
    labelMuted: 'var(--qh-graph-muted)',
    labelBackground: 'rgba(255,255,255,0.72)',
    line: 'var(--qh-graph-border)',
  },
} as const

export type GraphRelationGroup = keyof typeof graphVisual.relation

export const relationGroup = (relation: string): GraphRelationGroup => {
  if (relation === '相须' || relation === '相使') return '配伍协同'
  if (['相畏', '相杀', '相恶'].includes(relation)) return '配伍制约'
  if (relation === '相反') return '配伍禁忌'
  if (relation === '组成') return '方药组成'
  if (['药证链', '证方链'].includes(relation)) return '药证方链'
  if (['同类鉴别', '性味相反'].includes(relation)) return '鉴别对照'
  if (['归经聚类', '归属', '类属'].includes(relation)) return '归属聚类'
  return '功效互参'
}

export const relationStyle = (relation: string) => graphVisual.relation[relationGroup(relation)]
export const relationLabel = (relation: string) => relation as GraphRelationType
