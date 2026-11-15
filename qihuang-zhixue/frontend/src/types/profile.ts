export const dimensionKeys = [
  'knowledge_base',
  'learning_goal',
  'learning_style',
  'weak_points',
  'pace',
  'motivation',
  'resource_preference',
] as const

export type DimensionKey = (typeof dimensionKeys)[number]

export type ConfidenceStatus = '待取证' | '已收集到' | '已取证'

export type EvidenceCard = {
  evidence_id: string
  dimension: DimensionKey
  source: string
  fact: string
  objective_signal: Record<string, unknown>
  weight: number
  timestamp: string
  tier?: string
  claim_status?: 'pending' | 'verified' | 'refuted' | string | null
  quote?: string | null
  basis?: 'explicit' | 'inferred'
  extraction_confidence?: number
  extraction_method?: 'llm' | 'no_key_rule_fallback' | 'rule_fallback' | 'legacy' | string
  knowledge_base_ref?: string | null
  component?: string | null
  evidence_group_id?: string | null
  collection_status?: 'scorable' | 'pending' | 'conflict' | 'invalid' | string | null
  normalized_claim?: string | null
}

export type WeakPoint = {
  label: string
  strength: number
}

export type TeacherName = '严师' | '宽师' | '中立'

export type JuryPayload = {
  score?: number | null
  comment?: string
  reasoning_chain?: string[]
}

export type DimensionPayload = {
  score?: number | null
  /** 兼容别名：分类维度在模型里只有 radar_score，接口会把分同时写到 score。 */
  radar_score?: number | null
  label?: string | null
  distribution?: Record<string, number>
  confidence?: number
  evidence?: EvidenceCard[]
  narrative?: string
  items?: WeakPoint[]
  jury?: Partial<Record<TeacherName | string, JuryPayload>>
  provenance?: string
  component_matrix?: Record<string, { status?: string; evidence_ids?: string[]; missing_information?: string[] }>
  component_scores?: Record<string, number>
  scoring_phase?: 'collecting' | 'scored' | string
  pending_components?: string[]
  score_summary?: string
}

export type PlanningParams = {
  depth: '浅' | '中' | '深' | string
  breadth: '考点导向' | '全面' | '兴趣优先' | string
  pace: '松' | '中' | '紧' | string
  fun_preference: number
  assessment_strictness: '宽松' | '标准' | '严格' | string
  time_budget: { total_days: number; daily_minutes: number } | null
}

export type ProfileGoal = {
  raw_text: string
  normalized: string | null
  confirmed_by_user: boolean
  confidence: number
}

export type ProfileTrust = {
  trust: number
  tier: '眼见为实' | '半信半疑' | '深信不疑' | string
}

export type ProfileChange = {
  ts: string
  dim: string
  old_value: unknown
  new_value: unknown
  reason: string
  source_event: string
  evidence_ids: string[]
}

export type ProfileAbility = {
  theta?: number
  level?: string
  attempts?: number
  [key: string]: unknown
}

export type GoalCandidate = {
  restatement: string
  goal: ProfileGoal
  planning_params: PlanningParams
}

export type ProfileResponse = {
  user_id: string
  created_at?: string
  updated_at?: string
  confidence?: Partial<Record<DimensionKey, number>>
  provenance?: Partial<Record<DimensionKey, string>>
  radar?: {
    dimensions: string[]
    values: number[]
    provenance?: Partial<Record<DimensionKey, string>>
    axes?: RadarAxis[]
  }
  planning_params?: PlanningParams
  goal?: ProfileGoal
  change_log?: ProfileChange[]
  change_log_pagination?: { limit: number; offset: number }
  ability?: ProfileAbility
} & Record<DimensionKey, DimensionPayload>

export type NarrativeFact = {
  key: DimensionKey
  name: string
  score?: number | null
  anchor?: string | null
  label?: string | null
  distribution?: Record<string, number>
  weak_points?: WeakPoint[]
  confidence: number
  status: 'ready' | '待补充' | string
  evidence_count: number
  evidence_refs?: string[]
  narrative?: string | null
}

export type NarrativeItem = {
  dimension_key: DimensionKey
  dimension: string
  summary: string
  confidence: number
  status: 'ready' | '待补充' | string
  score?: number | null
  label?: string | null
  weak_points?: WeakPoint[]
  evidence_count: number
  evidence_refs?: string[]
}

export type ProfileNarrative = {
  user_id: string
  method: 'llm' | 'template'
  description: string
  overview: string
  advantages: NarrativeItem[]
  weak_points: NarrativeItem[]
  advice_intro: string
  dimensions: NarrativeFact[]
  low_confidence_dimensions: NarrativeFact[]
  guardrails: string[]
  fallback_reason?: string | null
}

export type RadarAxis = {
  key: DimensionKey
  label: string
  sourceLabel: string
  metric: string
  value: number
  status?: 'dark' | 'dashed' | 'solid' | string
  lit?: boolean
  placeholder?: boolean
  raw: string
  confidence: number
  confidenceStatus: ConfidenceStatus
  lowConfidence: boolean
  anchor: string
  kind: 'numeric' | 'derived' | 'hotspot' | 'agent_estimate'
  pending_verification?: boolean
  provenance?: string
  scoring_phase?: 'collecting' | 'scored' | string
  component_collected?: number
  component_total?: number
  pending_components?: string[]
}

export type ProfileProgressDimension = {
  key: DimensionKey
  label: string
  confidence: number
  collected: boolean
  status: ConfidenceStatus | '已收集到'
}

export type ProfileProgress = {
  collected: number
  total: number
  threshold: number
  dimensions: ProfileProgressDimension[]
}

export type EvidenceQuote = {
  text: string
  fact: string
  source: string
  ref: string
}

export type EvidencePanelDimension = {
  key: DimensionKey
  label: string
  score?: number | null
  value: string
  tag?: string | null
  distribution?: Record<string, number>
  confidence: number
  status: ConfidenceStatus | '已收集到'
  evidence_refs: string[]
  narrative?: string
  quotes: EvidenceQuote[]
}

export type IdentityCardKind = 'distribution' | 'level' | 'tags'

export type IdentityCardProvenance = 'fusion' | 'rule_fallback' | 'no_key' | 'no_key_rule_fallback' | string

/** A server-authored item displayed inside one seven-dimension identity card. */
export type IdentityCardItem = {
  canonical_label?: string
  display_name?: string
  meaning?: string
  ratio?: number
  level_name?: string
  score?: number | null
  label?: string
  strength?: number | null
  daily_minutes?: number | null
  target_days?: number | null
  quotes?: string[]
  evidence_refs?: string[]
  placeholder?: boolean
}

/**
 * Dialogue identity-card view model. Values are deliberately not recomputed in
 * the client: `items` is the canonical payload and `cards` is legacy support.
 */
export type IdentityCardView = {
  key: DimensionKey
  label: string
  kind: IdentityCardKind
  revealed: boolean
  revealable: boolean
  revealed_now: boolean
  confidence: number
  confidence_status: ConfidenceStatus
  score?: number | null
  radar_score?: number | null
  coverage?: number
  unknown_reasons?: string[]
  confidence_breakdown?: Record<string, number>
  display_value: string
  distribution: Record<string, number>
  distribution_scope?: 'observed_evidence' | 'complete_profile' | string
  /** 由已观察组成推导出的牌名（预设之外的组合也不留空）。 */
  card_name?: string
  composition_basis?: 'labels' | 'components' | 'none' | string
  composition_summary?: string
  items: IdentityCardItem[]
  cards?: IdentityCardItem[]
  evidence_refs: string[]
  quotes: string[]
  explanation: string
  provenance: IdentityCardProvenance
  pending_reason?: string | null
  target_label?: string | null
  quick_replies?: string[]
  pending_verification?: boolean
  claim_status?: 'pending' | 'verified' | 'refuted' | string | null
}

/** @deprecated Use IdentityCardItem. */
export type IdentityDimensionCard = IdentityCardItem
/** @deprecated Use IdentityCardView. */
export type IdentityCardsDimensionView = IdentityCardView

export type DimensionRow = {
  key: DimensionKey
  label: string
  metric: string
  value: number
  raw: string
  confidence: number
  status: ConfidenceStatus
  anchor: string
}

export type ProfileStat = {
  label: string
  value: string
  tone: 'jade' | 'blue' | 'gold' | 'coral'
}

export type ProfileBuildMode = 'apprentice' | 'quickcard' | 'practice' | 'passive' | 'dialogue'

export type ProfileSessionQuestion = {
  id: string
  question: string
  options: string[]
  topic?: string
  skill?: string
  /** 剧情题面：不含药名，靠照片认药时用。 */
  story_prompt?: string
  herb_id?: string
  herb_name?: string
  herb_hint?: string
  image_url?: string
  image_caption?: string
}

// 剧情里每幕追加的追问。组件标签留在服务端，前端只拿到题干和选项。
export type ProfileSessionStoryQuestion = {
  id: string
  kind: 'text' | 'choice' | string
  prompt: string
  options: string[]
}

export type ProfileSessionStep = {
  id: string
  title: string
  kind: 'identity' | 'identity_quiz' | 'diagnostic_quiz' | 'prescription' | 'resource_choice' | 'learning_rule' | 'events' | string
  prompt: string
  payload: {
    identity_cards?: string[]
    questions?: ProfileSessionQuestion[]
    story_questions?: ProfileSessionStoryQuestion[]
    time_limit_seconds?: number
    resource_types?: string[]
    fields?: string[]
    case?: string
    objective_signal_fields?: string[]
    event_types?: string[]
    quick_replies?: string[]
    card_scores?: Record<string, number>
    inferred_card?: string | null
    target?: DimensionKey | string | null
    target_label?: string | null
  }
  started_at?: string
}

export type ProfileSessionResponse = {
  session_id: string
  user_id: string
  mode: ProfileBuildMode
  status: 'start' | 'advanced' | 'finished' | string
  step: ProfileSessionStep | null
  current_stage?: string | null
  finished: boolean
  evidence_count: number
  evidence_cards?: EvidenceCard[]
  card_scores?: Record<string, number>
  inferred_card?: string | null
  radar?: {
    axes?: RadarAxis[]
    dimensions?: string[]
    values?: number[]
    confidences?: number[]
    provenance?: Partial<Record<DimensionKey, string>>
  }
  profile_progress?: ProfileProgress
  evidence_panel?: EvidencePanelDimension[]
  identity_cards?: IdentityCardsDimensionView[]
  target?: DimensionKey | null
  target_label?: string | null
  quick_replies?: string[]
  turn_index?: number
  converged?: boolean
  next_question?: string | null
  radar_changes?: RadarChange[]
  summary_text?: string
  chat_history?: Array<{ role: 'assistant' | 'user'; content: string }>
}

export type RadarChange = {
  key: string
  label: string
  newly_lit: boolean
  from_score: number
  to_score: number
  delta: number
  from_status: string
  to_status: string
  evidence_count: number
}

export type ProfileDialogueChatMessage = {
  id: number
  role: 'assistant' | 'user'
  text: string
  typing?: boolean
  reasoning: string
  reasoningStreaming: boolean
  reasoningCollapsed: boolean
  reasoningStartedAt?: number
  reasoningDurationMs?: number
}

export type ProfileDialogueStreamUiEvent = {
  id: number
  // `replace` 用于事后纠偏：漂移的追问已经在流里显示出来了，后端给出整段
  // 修正文本时把它整段换掉，而不是再追加一个问句。
  type: 'thinking' | 'message' | 'replace' | 'message_done' | 'done' | 'reset'
  delta?: string
}
