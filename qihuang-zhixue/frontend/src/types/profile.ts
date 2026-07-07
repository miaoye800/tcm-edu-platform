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

export type ConfidenceStatus = '待取证' | '已取证'

export type EvidenceCard = {
  dimension: DimensionKey
  source: string
  fact: string
  objective_signal: Record<string, unknown>
  weight: number
  timestamp: string
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
  label?: string | null
  distribution?: Record<string, number>
  confidence?: number
  evidence?: EvidenceCard[]
  narrative?: string
  items?: WeakPoint[]
  jury?: Partial<Record<TeacherName | string, JuryPayload>>
}

export type ProfileResponse = {
  user_id: string
  created_at?: string
  updated_at?: string
  confidence?: Partial<Record<DimensionKey, number>>
  radar?: {
    dimensions: string[]
    values: number[]
  }
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
  raw: string
  confidence: number
  confidenceStatus: ConfidenceStatus
  lowConfidence: boolean
  anchor: string
  kind: 'numeric' | 'derived' | 'hotspot'
}

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
}

export type ProfileSessionStep = {
  id: string
  title: string
  kind: 'identity' | 'identity_quiz' | 'diagnostic_quiz' | 'prescription' | 'resource_choice' | 'learning_rule' | 'events' | string
  prompt: string
  payload: {
    identity_cards?: string[]
    questions?: ProfileSessionQuestion[]
    time_limit_seconds?: number
    resource_types?: string[]
    fields?: string[]
    case?: string
    objective_signal_fields?: string[]
    event_types?: string[]
    quick_replies?: string[]
    card_scores?: Record<string, number>
    inferred_card?: string | null
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
  }
  turn_index?: number
  converged?: boolean
  next_question?: string | null
}
