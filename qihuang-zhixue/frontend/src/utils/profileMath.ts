import type {
  DimensionKey,
  DimensionPayload,
  ProfileNarrative,
  ProfileResponse,
  RadarAxis,
  WeakPoint,
} from '../types/profile'
import { dimensionKeys } from '../types/profile'

export const lowConfidenceThreshold = 0.5

export const dimensionLabels: Record<DimensionKey, string> = {
  knowledge_base: '知识基础',
  learning_goal: '学习目标',
  learning_style: '认知/学习风格',
  weak_points: '薄弱知识点',
  pace: '学习节奏',
  motivation: '动机强度',
  resource_preference: '资源偏好',
}

const radarLabels: Record<DimensionKey, string> = {
  knowledge_base: '知识基础',
  learning_goal: '目标清晰度',
  learning_style: '风格集中度',
  weak_points: '薄弱点',
  pace: '学习节奏',
  motivation: '动机强度',
  resource_preference: '资源集中度',
}

export const clampRatio = (value: unknown): number => {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 0
  return Math.min(1, Math.max(0, parsed))
}

export const clampScore = (value: unknown): number => {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 0
  return Math.min(100, Math.max(0, parsed))
}

export const formatScore = (value: number): string => `${Math.round(clampScore(value))} 分`

export const formatPercent = (value: number): string => `${Math.round(clampRatio(value) * 100)}%`

export const formatDateTime = (value?: string): string => {
  if (!value) return '尚无画像快照'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

export const formatSignalValue = (value: unknown): string => {
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (Array.isArray(value)) return value.join('、')
  if (value && typeof value === 'object') return JSON.stringify(value)
  return String(value ?? '空')
}

export const confidenceFor = (
  profile: ProfileResponse | null,
  narrative: ProfileNarrative | null,
  key: DimensionKey,
): number => {
  const fact = narrative?.dimensions.find((item) => item.key === key)
  return clampRatio(profile?.confidence?.[key] ?? profile?.[key].confidence ?? fact?.confidence ?? 0)
}

export const numericScore = (profile: ProfileResponse | null, key: DimensionKey): number =>
  clampScore(profile?.[key].score ?? 0)

export const distributionEntries = (distribution?: Record<string, number>) =>
  Object.entries(distribution ?? {})
    .map(([label, ratio]) => ({ label, ratio: clampRatio(ratio) }))
    .sort((left, right) => right.ratio - left.ratio)

export const categoryConcentration = (
  profile: ProfileResponse | null,
  key: 'learning_goal' | 'learning_style' | 'resource_preference',
): number => {
  const dimension = profile?.[key]
  const ratios = Object.values(dimension?.distribution ?? {}).map(clampRatio)
  if (ratios.length > 0) return Math.round(Math.max(...ratios) * 100)
  return dimension?.label ? 100 : 0
}

export const weakPointItems = (
  profile: ProfileResponse | null,
  narrative: ProfileNarrative | null,
): WeakPoint[] => {
  const fromProfile = profile?.weak_points.items ?? []
  if (fromProfile.length > 0) return fromProfile
  return narrative?.dimensions.find((item) => item.key === 'weak_points')?.weak_points ?? []
}

export const weakPointHeat = (profile: ProfileResponse | null, items: WeakPoint[]): number => {
  const totalStrength = items.reduce((sum, item) => sum + Math.max(0, item.strength || 0), 0)
  if (totalStrength > 0) return clampScore(totalStrength * 25)
  return profile?.weak_points.label ? 35 : 0
}

export const buildRadarAxes = (
  profile: ProfileResponse | null,
  narrative: ProfileNarrative | null,
): RadarAxis[] => {
  const weakItems = weakPointItems(profile, narrative)
  const derivedValues: Record<DimensionKey, number> = {
    knowledge_base: numericScore(profile, 'knowledge_base'),
    learning_goal: categoryConcentration(profile, 'learning_goal'),
    learning_style: categoryConcentration(profile, 'learning_style'),
    weak_points: weakPointHeat(profile, weakItems),
    pace: numericScore(profile, 'pace'),
    motivation: numericScore(profile, 'motivation'),
    resource_preference: categoryConcentration(profile, 'resource_preference'),
  }

  return dimensionKeys.map((key) => {
    const dimension = profile?.[key]
    const confidence = confidenceFor(profile, narrative, key)
    return {
      key,
      label: radarLabels[key],
      sourceLabel: dimensionLabels[key],
      metric: metricText(key),
      value: derivedValues[key],
      raw: rawText(key, dimension, derivedValues[key], weakItems),
      confidence,
      confidenceStatus: confidence >= lowConfidenceThreshold ? '已取证' : '待取证',
      lowConfidence: confidence < lowConfidenceThreshold,
      anchor: anchorText(key, derivedValues[key]),
      kind: axisKind(key),
    }
  })
}

export const profileEvidenceCards = (profile: ProfileResponse | null) =>
  dimensionKeys.flatMap((key) =>
    (profile?.[key].evidence ?? []).map((card) => ({
      ...card,
      dimensionName: dimensionLabels[key],
      timestampText: formatDateTime(card.timestamp),
      signals: Object.entries(card.objective_signal ?? {})
        .slice(0, 4)
        .map(([signalKey, signalValue]) => `${signalKey}: ${formatSignalValue(signalValue)}`),
    })),
  )

export const dimensionPayload = (
  profile: ProfileResponse | null,
  key: DimensionKey,
): DimensionPayload => profile?.[key] ?? {}

const axisKind = (key: DimensionKey): RadarAxis['kind'] => {
  if (key === 'weak_points') return 'hotspot'
  if (key === 'learning_goal' || key === 'learning_style' || key === 'resource_preference') return 'derived'
  return 'numeric'
}

const metricText = (key: DimensionKey): string => {
  if (key === 'weak_points') return '热区值'
  if (key === 'learning_goal' || key === 'learning_style' || key === 'resource_preference') return '派生值'
  return '数值分'
}

const rawText = (
  key: DimensionKey,
  dimension: DimensionPayload | undefined,
  value: number,
  weakItems: WeakPoint[],
): string => {
  if (key === 'weak_points') return weakItems.length ? `${weakItems.length} 个标签` : '暂无高频薄弱点'
  if (key === 'learning_goal' || key === 'learning_style' || key === 'resource_preference') {
    return dimension?.label || '待补充'
  }
  if (key === 'motivation') {
    return `${formatScore(value)}${dimension?.label ? ` · ${dimension.label}` : ''}`
  }
  return formatScore(value)
}

export const anchorText = (key: DimensionKey, score: number): string => {
  if (key === 'knowledge_base') {
    if (score <= 20) return '药名、性味与归经线索仍待建立'
    if (score <= 40) return '能识别常见概念，但功效辨析还需补证'
    if (score <= 60) return '常用药功效已有基础，配伍辨析待强化'
    if (score <= 80) return '功效与配伍较熟，可进入病例迁移'
    return '配伍加减与剂量判断已较稳定'
  }

  if (key === 'motivation') {
    if (score <= 20) return '当前动机线索较少，适合先用短目标牵引'
    if (score <= 40) return '需要外部节奏推动与即时反馈'
    if (score <= 60) return '有学习意愿，可通过阶段目标稳定投入'
    if (score <= 80) return '主动性较强，适合安排挑战任务'
    return '高度自驱，可进入拓展型任务'
  }

  if (key === 'pace') {
    if (score <= 30) return '慢节奏：适合小步复盘'
    if (score <= 70) return '中节奏：可保持日常练习'
    return '快节奏：适合密集冲刺与回顾'
  }

  if (key === 'weak_points') return '由错题、问答和开药模拟共同取证'
  return '由交互选择与资源使用分布派生'
}
