import type { DimensionKey, ProfileNarrative, ProfileResponse } from '../types/profile'
import { dimensionKeys } from '../types/profile'

const blankDimension = () => ({
  score: null,
  label: null,
  distribution: {},
  confidence: 0,
  evidence: [],
  narrative: '',
  items: [],
  jury: {},
})

export const createMockProfile = (userId = 'student-demo'): ProfileResponse => {
  const dimensions = Object.fromEntries(dimensionKeys.map((key) => [key, blankDimension()])) as Record<
    DimensionKey,
    ReturnType<typeof blankDimension>
  >

  return {
    user_id: userId,
    created_at: '',
    updated_at: '',
    confidence: Object.fromEntries(dimensionKeys.map((key) => [key, 0])) as Partial<Record<DimensionKey, number>>,
    ...dimensions,
  }
}

export const createMockNarrative = (userId = 'student-demo'): ProfileNarrative => ({
  user_id: userId,
  method: 'template',
  description:
    '当前暂无高置信维度。小岐会先陪你完成一次轻量问诊与诊断小测，再把学习目标、基础水平、资源偏好和薄弱点逐步点亮。',
  overview: '当前画像暂无高置信维度。',
  advantages: [],
  weak_points: [],
  advice_intro: '学习建议引子：先完成一轮冷启动问诊，系统会把证据卡写入画像后再生成个性化路径。',
  dimensions: dimensionKeys.map((key) => ({
    key,
    name: key,
    score: null,
    anchor: null,
    label: null,
    distribution: {},
    weak_points: [],
    confidence: 0,
    status: '待补充',
    evidence_count: 0,
    evidence_refs: [],
    narrative: null,
  })),
  low_confidence_dimensions: [],
  guardrails: ['只引用七维画像快照中的 score、label、weak_points、confidence 与 evidence_count。'],
})
