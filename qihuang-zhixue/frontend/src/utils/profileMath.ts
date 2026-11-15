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

// 未配置模型密钥时两条链路都不出分，后端写的 provenance 是 `no_key`；
// 早期落库的数据里是 `no_key_rule_fallback`。两者含义相同：没有模型，无法评分。
export const isNoKeyProvenance = (provenance?: string | null): boolean =>
  provenance === 'no_key' || provenance === 'no_key_rule_fallback'

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

/** 这一维算出来的分。分类维度只有 radar_score，所以两个入口都要看。 */
export const dimensionScore = (profile: ProfileResponse | null, key: DimensionKey): number | null => {
  const dimension = profile?.[key]
  const raw = dimension?.score ?? dimension?.radar_score
  if (raw === null || raw === undefined) return null
  const parsed = Number(raw)
  return Number.isFinite(parsed) ? clampScore(parsed) : null
}

export const numericScore = (profile: ProfileResponse | null, key: DimensionKey): number =>
  dimensionScore(profile, key) ?? 0

export const distributionEntries = (distribution?: Record<string, number>) =>
  Object.entries(distribution ?? {})
    .map(([label, ratio]) => ({ label, ratio: clampRatio(ratio) }))
    .sort((left, right) => right.ratio - left.ratio)

/**
 * 某一维是否真的取过证。
 *
 * 空维度的 `label` 为 null、`score` 为 null、`distribution` 为空、`evidence` 为空；
 * 只要有一项非空就说明有证据。没证据的维度不能借用 0 分或默认文案冒充结论
 * ——「只有目标锚定度」的时候，弟子档案里就不该出现知识基础的建议。
 */
export const dimensionHasEvidence = (profile: ProfileResponse | null, key: DimensionKey): boolean => {
  const dimension = profile?.[key]
  if (!dimension) return false
  if ((dimension.evidence ?? []).length > 0) return true
  if (dimension.score !== null && dimension.score !== undefined) return true
  if (Object.keys(dimension.distribution ?? {}).length > 0) return true
  return Boolean(dimension.label)
}

export const categoryConcentration = (
  profile: ProfileResponse | null,
  key: 'learning_goal' | 'learning_style' | 'resource_preference',
): number => {
  const dimension = profile?.[key]
  const ratios = Object.values(dimension?.distribution ?? {}).map(clampRatio)
  // 没有分布就没有"集中度"。旧实现在只有 label 时返回 100，等于给一个毫无
  // 证据的维度画出满格尖角。
  if (ratios.length === 0) return 0
  return Math.round(Math.max(...ratios) * 100)
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
  if (!dimensionHasEvidence(profile, 'weak_points')) return 0
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
    const evidenced = dimensionHasEvidence(profile, key)
    const confidence = confidenceFor(profile, narrative, key)
    const pendingVerification = Boolean(profile?.[key].evidence?.some((card) => card.claim_status === 'pending'))
    // 有分才有结论。`dimensionHasEvidence` 只说明"收过材料"，材料不等于结论：
    // 目标维度聊出 3/5 个组件、分已经算出来时，档案必须画出来；反过来只有
    // 线索没有分时，也不能借 0 分冒充一个结论。
    const conclusion = dimensionScore(profile, key)
    const scored = conclusion !== null || (key === 'weak_points' && weakItems.length > 0)
    const componentScores = Object.keys(dimension?.component_scores ?? {})
    const componentTotal = Object.keys(dimension?.component_matrix ?? {})
    const componentCollected = componentScores.length
    const total = componentTotal.length || componentScores.length || undefined
    // 无结论的维度一律标 dark：雷达图不画尖角、档案不给建议、也不显示 0 分。
    // 有结论但置信度不足才用虚线（"已收集但仍待确认"），与对话牌面同口径。
    const status: RadarAxis['status'] = !scored
      ? 'dark'
      : confidence >= lowConfidenceThreshold ? 'solid' : 'dashed'
    const progressText = total ? `${componentCollected}/${total} 项已收集` : '线索已收下'
    return {
      key,
      label: radarLabels[key],
      sourceLabel: dimensionLabels[key],
      metric: scored ? metricText(key) : '进度',
      value: scored ? (conclusion ?? derivedValues[key]) : 0,
      raw: scored ? rawText(key, dimension, derivedValues[key], weakItems, conclusion) : (evidenced ? progressText : '待补充'),
      confidence,
      confidenceStatus: scored && confidence >= lowConfidenceThreshold
        ? '已取证'
        : (scored || evidenced ? '已收集到' : '待取证'),
      lowConfidence: confidence < lowConfidenceThreshold,
      anchor: scored
        ? anchorText(key, conclusion ?? derivedValues[key])
        : (evidenced ? '评分暂缓：证据已收下，继续补证就会出分' : '该维度尚未取证'),
      kind: axisKind(key),
      status,
      lit: scored,
      placeholder: !scored,
      pending_verification: pendingVerification,
      scoring_phase: dimension?.scoring_phase,
      component_collected: componentCollected,
      component_total: total,
      pending_components: dimension?.pending_components ?? [],
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
  if (key === 'learning_goal') return '清晰度'
  if (key === 'learning_style') return '风格集中度'
  if (key === 'resource_preference') return '资源集中度'
  return '数值分'
}

const rawText = (
  key: DimensionKey,
  dimension: DimensionPayload | undefined,
  value: number,
  weakItems: WeakPoint[],
  score?: number | null,
): string => {
  if (key === 'weak_points') return weakItems.length ? `${weakItems.length} 个标签` : '暂无高频薄弱点'
  if (key === 'learning_goal' || key === 'learning_style' || key === 'resource_preference') {
    // 分类维度有分就先显示分：模型没给牌名时，不能退回"待补充"把结论藏掉。
    return dimension?.label || (score != null ? formatScore(score) : '待补充')
  }
  if (key === 'motivation') {
    return `${formatScore(score ?? value)}${dimension?.label ? ` · ${dimension.label}` : ''}`
  }
  return formatScore(score ?? value)
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

  // 分类维度也必须按分数说话。以前这里对所有分类维度返回同一句
  // "由交互选择与资源使用分布派生"——用户刚聊出 53 分的清晰度，档案里
  // 读到的却是一句描述数据来源的话，看起来就像"什么结论都没有"。
  if (key === 'learning_goal') {
    if (score <= 30) return '方向还比较模糊：先把目标说成一句能验收的话'
    if (score <= 55) return '方向清楚，但还缺期限与验收标准'
    if (score <= 75) return '目标较明确，可以拆成阶段里程碑'
    return '目标清晰，可直接进入路径规划'
  }
  if (key === 'learning_style') {
    if (score <= 30) return '学习方式还很分散：先用一种主方式稳住'
    if (score <= 60) return '已有偏好方式，可再补一种互补方式'
    return '学习方式集中，可按主方式编排资源'
  }
  if (key === 'resource_preference') {
    if (score <= 30) return '资源偏好还不明显：多看几种再定'
    if (score <= 60) return '已有主用资源，可少量补充其他形态'
    return '资源偏好明确，可直接按此出材'
  }
  return '由交互选择与资源使用分布派生'
}
