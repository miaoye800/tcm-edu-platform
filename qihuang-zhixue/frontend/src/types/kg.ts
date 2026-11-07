/**
 * 知识图谱服务（``/api/kg``）与错题归因（``/api/mistakes``）的前端契约。
 *
 * 与 ``types/graph.ts`` 的区别：那份是旧「按中心节点取一张子图」的契约
 * （节点 id 是 ``d01.s1.p1`` 这种知识点 id）；这份是新图谱服务的契约，
 * 实体 id 是 ``herb_mahuang`` 这种。**两套 id 不通用**，混用会查不到东西。
 */

export type KgEntityKind = 'herb' | 'formula' | 'syndrome'

export type KgEntityBrief = {
  id: string
  name: string
  kind: KgEntityKind
  status?: string
  /**
   * 教材出处（仅对比接口给）。性味/归经/功效/剂量这些字段都取自该药的教材条目，
   * 所以出处挂在**一侧**——同一味药的字段集只有一个来源，逐行重复只会让表变脏。
   */
  source?: { ref: string; quote: string }
}

export type KgSearchHit = {
  id: string
  kind: KgEntityKind
  name: string
  status: string
  /** ``name`` > ``alias`` > ``pinyin`` > ``id`` > ``fuzzy`` */
  matched_by: string
}

/** 对比表的一行。``left``/``right`` 是原值，``only_*`` 是差集。 */
export type CompareDifference = {
  field: string
  label: string
  left: string[]
  right: string[]
  only_left: string[]
  only_right: string[]
  shared: string[]
}

/** 教材明文鉴别句。**优先用它**，不是模型重新组织的说法。 */
export type TextbookDifferentiation = {
  edge_id: number
  quote: string
  ref: string
  page: number | null
}

export type CompareResult = {
  ok: boolean
  reason?: string
  left: KgEntityBrief | null
  right: KgEntityBrief | null
  differences?: CompareDifference[]
  textbook_differentiation?: TextbookDifferentiation | null
  shared_relations?: { id: string; name: string; relation_type: string; edge_id: number }[]
  /** T1 硬约束。「能不能一起用」比「差在哪」更急，前端要显眼。 */
  constraints?: { edge_id: number; relation_type: string; page: number | null; quote: string }[]
  summary?: string
}

export type KgPathStep = {
  edge_id: number
  from: { id: string; name: string }
  to: { id: string; name: string }
  relation_type: string
  tier: number
  label: string
  evidence: { page?: number | null; quote?: string; ref?: string }[]
}

export type KgPathResult = {
  ok: boolean
  found?: boolean
  reason?: string
  from?: KgEntityBrief
  to?: KgEntityBrief
  hops?: number
  path: KgPathStep[]
  sentence?: string
}

export type KgHealth = {
  entities?: Record<string, number>
  edges?: Record<string, number>
  [key: string]: unknown
}

// ---------------------------------------------------------------------------
// 处方审查（P3）
// ---------------------------------------------------------------------------

/** 一条检查结论。``quote`` 是**用户原文**的片段（用来高亮），教材原文在 ``evidence``。 */
export type ReviewFinding = {
  kind: string
  severity: 'block' | 'warn' | 'info'
  quote: string
  reason: string
  rule: string
  knowledge_base_refs: string[]
  /** 指回 ``kg_edge`` 的边 id，错题归因时从这条结论跳到图上那条边。 */
  edge_ids: number[]
  evidence: { ref: string; page: number | null; quote: string }[]
}

/**
 * 归一化后的一味药。
 *
 * ``dose_status`` 是四态而不是布尔：``unchecked``（没结构化条目，判不了）和
 * ``missing``（有条目，但处方里没写剂量）必须分开——把「判不了」显示成
 * 「没问题」是这个项目反复踩的坑。
 */
export type ReviewedHerb = {
  name: string
  raw_name: string
  dose_g: number | null
  dose_range: [number | null, number | null] | null
  dose_status: 'unchecked' | 'missing' | 'ok' | 'below' | 'above'
  preparation: string | null
  knowledge_base_refs: string[]
  has_monograph: boolean
}

/** 对症追溯：命中走的是哪一层，以及教材里真正主治该证的药。 */
export type PrescriptionTrace = {
  layer: 'syndrome' | 'effect_overlap' | 'effect_tag' | null
  syndromes: { id: string; name: string }[]
  candidates: { name: string; layer: string; score?: number }[]
  matched_herbs: string[]
  effect_tags: string[]
  expected_effects?: string[]
  shared_effects?: Record<string, string[]>
  edges?: { edge_id: number; herb: string; syndrome: string; page: number | null; ref: string; quote: string }[]
  syndrome_alternatives?: { syndrome: string; herbs: string[] }[]
}

export type PrescriptionCheckState = 'pass' | 'fail' | 'unknown'

export type PrescriptionReviewResult = {
  verdict: 'pass' | 'warn' | 'block'
  symptom_match: boolean
  symptom_match_status: 'matched' | 'mismatched' | 'uncertain'
  compatible: boolean
  dosage_ok: boolean
  toxic_misuse: boolean
  /**
   * 四项检查的三态结论。**``unknown`` 是一等公民**：
   * 草乌在图谱里只有身份没有性味功效，剂量和毒性根本没查过，
   * 用 ``dosage_ok: true`` 表达就成了「合规」——「不知道」不能伪装成「没问题」。
   * 老接口没有这个字段时，前端退回布尔值。
   */
  checks?: {
    compatible?: PrescriptionCheckState
    dosage?: PrescriptionCheckState
    toxicity?: PrescriptionCheckState
    symptom?: 'matched' | 'mismatched' | 'uncertain'
  }
  findings: ReviewFinding[]
  herbs: ReviewedHerb[]
  knowledge_base_refs: string[]
  quote: string
  trace: PrescriptionTrace
  blocking: ReviewFinding[]
}


// ---------------------------------------------------------------------------
// 错题归因
// ---------------------------------------------------------------------------

/**
 * 归因类型是**闭集**。``unknown`` 是一等公民——图谱没有依据时就说没有依据，
 * 不许拿一个听起来合理的说法顶上。
 *
 * ``wrong_herb`` 是「选了另一味有实体的药，但教材没写这两味的鉴别点」。它不带边，
 * 依据是两味药各自的教材【功效】原文——不是猜的，只是没有鉴别句可引。
 */
export type CauseType = 'confused_with' | 'forbidden_pair' | 'answer_incomplete' | 'wrong_herb' | 'unknown'

export type AttributionCause = {
  cause_type: CauseType
  label: string
  entity_id: string | null
  entity_name: string
  /**
   * 依据的那条边。``null`` 表示这条结论不靠边：``answer_incomplete`` /
   * ``wrong_herb``（改用功效原文）/ ``unknown``。
   */
  edge_id: number | null
  relation_type: string
  score: number
  quote: string
  page: number | null
  ref: string
  explanation: string
}

export type RemediationNode = { id: string; name: string; level: 'domain' | 'subtype' | 'point' }

export type MistakeRemediation = {
  entity_id: string
  entity_name: string
  /** 知识树祖先链，按 domain → point 排列（学习顺序）。 */
  node_path: RemediationNode[]
  /** 章节名。汇总「薄弱章节」用它，不是药名。 */
  domain: string
  distinguish_from: { entity_id: string; name: string; edge_id: number; quote: string; page: number | null }[]
  entry: string
  focus_node_id: string
}

export type MistakeAttribution = {
  ok: boolean
  reason?: string
  mistake_id: string
  question_id?: string
  exam_point: { id: string; name: string; node_id: string }
  entity: { id: string; name: string; kind: KgEntityKind } | null
  causes: AttributionCause[]
  remediation: MistakeRemediation | null
  /** 数据不一致等「不影响结论但必须让人看见」的提示。 */
  notes: string[]
}

export type AttributionSummary = {
  by_cause: Record<string, number>
  by_domain: Record<string, number>
  /** 至少归上一条原因的错题数。 */
  attributed: number
  /** 只落了 ``unknown`` 的错题数。**不摊进任何章节**。 */
  unattributed: number
}

export type AttributionResponse = {
  ok: boolean
  user_id: string
  count: number
  items: MistakeAttribution[]
  summary: AttributionSummary
  cause_labels: Record<string, string>
  cause_types: string[]
}

export type MistakeRecord = {
  id: number
  user_id: string
  question_id: string
  exam_point_id: string
  answer: string[]
  expected: string[]
  created_at: string
}

// ---------------------------------------------------------------------------
// GraphRAG 问答（P5，``POST /api/kg/ask``）
// ---------------------------------------------------------------------------

/** 种子：从问题里认出来的实体。``matched_by=retrieval`` 表示来自向量检索的 id。 */
export type KgAskSeed = {
  id: string
  name: string
  kind: KgEntityKind
  /** ``mention``（原文出现）| ``alias`` | ``name`` | ``fuzzy``（弱命中）| ``retrieval`` */
  matched_by: string
  score: number
}

export type KgAskRelation = {
  edge_id: number | null
  source: { id: string; name: string }
  target: { id: string; name: string }
  relation_type: string
  /** 1 = 硬约束（不能同用），2 = 事实关系（配伍/组成/药证链），3 = 相似（**不会出现**）。 */
  tier: number
  label: string
  derived: boolean
  confidence: number
  evidence: { page?: number | null; quote?: string; ref?: string }[]
}

export type KgAskContext = {
  center: { id: string; name: string; kind: KgEntityKind }[]
  herbs: Record<string, unknown>[]
  syndromes: Record<string, unknown>[]
  formulas: Record<string, unknown>[]
  relations: KgAskRelation[]
  constraints: KgAskRelation[]
  seed_ids: string[]
  truncated: boolean
}

export type KgAskCitation = {
  edge_id: number | null
  relation_type: string
  tier: number
  ref: string
  page: number | null
  quote: string
}

export type KgAskResult = {
  question: string
  seeds: KgAskSeed[]
  answer: string
  context: KgAskContext
  citations: KgAskCitation[]
  graph: {
    center_ids: string[]
    nodes: { id: string; name: string; kind: KgEntityKind }[]
    edges: { id: number | null; source: string; target: string; relation_type: string; tier: number; label: string }[]
    truncated: boolean
  }
  /** ``kg_expand_v1``：名单式版本号，便于验收时确认跑的是哪套实现。 */
  method: string
}

// ---------------------------------------------------------------------------
// 反向检索（症状 → 证 → 方 → 药）
// ---------------------------------------------------------------------------

/** 一跳：图里的一条边，连同它的教材依据。 */
export type KgHop = {
  id: string
  name: string
  relation_type: string
  label: string
  tier: number
  confidence: number
  /** 指回 ``kg_edge`` 的那一行。没有它，这条结论只是一句话，不是一条证据。 */
  edge_id: number | null
  evidence: { page?: number | null; quote?: string; ref?: string }[]
  /** 仅方剂有：方子的组成药。 */
  composition?: KgHop[]
  composition_total?: number
}

/** 一个候选证候。``in_graph:false`` 表示教材词表命中但图谱里没有这个实体。 */
export type KgReverseSyndrome = {
  id: string
  name: string
  score: number
  /** ``name`` > ``alias`` > ``phrase`` > ``fuzzy``，标明是哪一级命中的。 */
  layer: string
  matched_terms: string[]
  in_graph: boolean
  note?: string
  herbs?: KgHop[]
  herbs_total?: number
  formulas?: KgHop[]
  formulas_total?: number
  /** 有证无药或有证无方时是 false。空数组和「本来就没有」不能混为一谈。 */
  complete?: boolean
}

export type KgReverseResult = {
  ok: boolean
  query: string
  reason: string | null
  /** 空候选时不道歉，直接给「怎么说才对」。 */
  hint?: string
  syndromes: KgReverseSyndrome[]
  chain?: string[]
  tiers_used?: number[]
}
