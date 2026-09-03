export type ResourceType = 'explain' | 'graph' | 'mindmap' | 'quiz' | 'reading'

export type SourceRef = {
  name: string
  type: string
  ref: string
  quote?: string
}

export type GroundingReport = {
  score: number
  supported_claims: string[]
  unsupported_claims: string[]
  note: string
}

export type ExplainSection = {
  title: string
  content: string
  sources: SourceRef[]
  claims: string[]
}

export type ExplainPayload = {
  markdown: string
  sections: ExplainSection[]
}

export type GraphNodeCategory = 'herb' | 'syndrome' | 'formula'

export type GraphNode = {
  id: string
  name: string
  category: GraphNodeCategory
  symbolSize: number
  draggable: boolean
}

export type GraphLink = {
  source: string
  target: string
  relation: string
  evidence: string
}

export type GraphPayload = {
  nodes: GraphNode[]
  links: GraphLink[]
}

export type MindmapPayload = {
  markdown: string
  root: string
  node_names: string[]
}

export type QuizOption = {
  key: string
  text: string
}

export type QuizQuestion = {
  stem: string
  type: '单选' | '多选' | '填空' | '简答' | '病例分析'
  options: QuizOption[]
  answer: string[]
  explanation: string
  source: SourceRef
  bloom_level: string
  difficulty: number
  distractor_rationale: Record<string, string>
  claims: string[]
  calibration?: { claimId: string; questionId: string }
}

export type CalibrationAnswer = { claimId: string; questionId: string; answer: string[] }

export type QuizPayload = {
  questions: QuizQuestion[]
}

export type ReadingItem = {
  title: string
  reason: string
  sources: SourceRef[]
  claims: string[]
}

export type ReadingPayload = {
  items: ReadingItem[]
}

export type ResourcePayloadByType = {
  explain: ExplainPayload
  graph: GraphPayload
  mindmap: MindmapPayload
  quiz: QuizPayload
  reading: ReadingPayload
}

export type ResourceEnvelope<T extends ResourceType = ResourceType> = {
  type: T
  topic: string
  payload: ResourcePayloadByType[T]
  sources: SourceRef[]
  grounding: GroundingReport
  trace_node: string
  generated_by: string
  cache_key: string
}

export type SkillStatus = 'locked' | 'unlocked' | 'in_progress' | 'mastered'

export type SkillNode = {
  id: string
  title: string
  kind: 'category' | 'subcategory' | 'herb' | 'differentiation'
  status: SkillStatus
  prerequisites: string[]
  recommended_resources: ResourceType[]
  reason: string
  mastery: number
  confidence: number
}

export type Recommendation = {
  kc_id: string
  title: string
  status: SkillStatus
  score: number
  reason: string
  mastery: number
  confidence: number
  recommended_resources: ResourceType[]
}

export type ReviewItem = {
  kc_id: string
  due_at: string
  difficulty: number
  stability: number
  retrievability: number
  last_rating: string
  attempts: number
  urgency: number
  evidence: Record<string, unknown>
}

export type ReviewsResponse = {
  user_id: string
  items: ReviewItem[]
  suggested_count: number
}

export type PathResponse = {
  user_id: string
  skill_tree: SkillNode[]
  recommendations: Recommendation[]
  reviews: Omit<ReviewsResponse, 'user_id'>
  summary: string
  trace: Array<{ node: string; summary: string }>
}

export type QuizSubmitItem = {
  question_id: string
  kc_id: string
  answer: string[]
  correct_answer: string[]
  elapsed_seconds?: number
  difficulty: number
  bloom_level: string
  topic: string
}

export type QuizSubmitResponse = {
  user_id: string
  results: Array<{
    question_id: string
    kc_id: string
    correct: boolean
    review: Record<string, unknown>
  }>
  mastery: Record<string, { p_mastery: number; attempts: number; last_seen?: string | null }>
  reviews: Omit<ReviewsResponse, 'user_id'>
}

export type ResourceStreamHandlers = {
  nodeStart?: (payload: { node: string; label?: string; summary?: string }) => void
  token?: (payload: { type?: ResourceType; text: string }) => void
  resourceDone?: (payload: { resource: ResourceEnvelope }) => void
  done?: (payload: { topic: string; types: ResourceType[]; trace: Array<{ node: string; summary: string }> }) => void
}
