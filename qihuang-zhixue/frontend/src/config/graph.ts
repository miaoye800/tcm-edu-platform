import type { GraphEntityType, GraphRelationType } from '../types/graph'

export const graphVisual = {
  entity: {
    herb: { label: '药材', color: 'var(--qh-color-jade)', glow: 'var(--qh-color-jade-line)' },
    syndrome: { label: '证候', color: 'var(--qh-color-blue)', glow: 'var(--qh-color-jade-line)' },
    formula: { label: '方剂', color: 'var(--qh-color-gold)', glow: 'var(--qh-color-jade-line)' },
    category: { label: '类属', color: 'var(--qh-color-cream)', glow: 'var(--qh-color-cream-line)' },
  } satisfies Record<GraphEntityType, { label: string; color: string; glow: string }>,
  relation: {
    配伍: { color: 'var(--qh-color-jade)', dash: [], arrow: false, group: '配伍' },
    鉴别: { color: 'var(--qh-color-coral)', dash: [6, 5], arrow: false, group: '鉴别' },
    链路: { color: 'var(--qh-color-gold)', dash: [], arrow: true, group: '药证方链' },
    聚类: { color: 'var(--qh-color-cream-line)', dash: [2, 5], arrow: false, group: '聚类' },
  },
  maxVisibleNodes: 60,
  motionMs: 320,
  canvas: {
    fallbackColor: 'var(--qh-color-jade)',
    nodeStroke: 'var(--qh-color-cream)',
    nodeStrokeMuted: 'var(--qh-color-text-soft)',
    labelFill: 'var(--qh-text)',
    labelMuted: 'var(--qh-color-text-faint)',
    labelBackground: 'var(--qh-color-night-overlay)',
  },
} as const

export const relationStyle = (relation: string) => {
  if (['相须', '相使', '相畏', '相杀', '相恶', '相反'].includes(relation)) return graphVisual.relation.配伍
  if (relation === '同类鉴别' || relation === '性味相反') return graphVisual.relation.鉴别
  if (relation === '药证链' || relation === '证方链') return graphVisual.relation.链路
  return graphVisual.relation.聚类
}

export const relationLabel = (relation: string) => relation as GraphRelationType
