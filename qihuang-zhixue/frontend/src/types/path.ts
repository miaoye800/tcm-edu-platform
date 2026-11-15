export type BackendPathState = 'locked' | 'available' | 'current' | 'completed' | 'paused' | 'custom'
export type MeridianStatus = 'recommended' | 'current' | 'mastered' | 'free' | 'deferred' | 'exam_hot' | 'custom'

export interface KnowledgeNode { id: string; name: string; level: 'domain' | 'subtype' | 'point'; parent_id?: string | null; description?: string; exam_frequency?: number; sort_hint?: number; extra?: Record<string, unknown>; children?: KnowledgeNode[] }
export interface KnowledgeTreeResponse { domains: KnowledgeNode[] }
export interface PathState { node_id: string; status: BackendPathState; updated_at: string; meta: Record<string, unknown> }
export interface StagePlan {
  domain_id: string
  order: number
  order_locked: boolean
  reason: string
  subject_id?: string
  subject_name?: string
  /** 确认后的路线是**单元**序列：一章可能拆成多行，所以 `stage_id` 才是行的键。 */
  stage_id?: string
  unit_id?: string
  unit_kind?: UnitKind
  unit_title?: string
  chapter_title?: string
  point_ids?: string[]
}
export interface NodePlan { node_id: string; stage: number; seq: number; target_mastery: number; resource_hint: string; highlight: 'normal' | 'exam_hot' | 'remedial'; calibration: boolean; reason: string; name?: string; custom?: boolean; parent_id?: string; domain_id?: string }
export interface PlanResponse { version: number; fallback: boolean; suggestion: string; stages: StagePlan[]; nodes: NodePlan[]; verify_attempts: number; verification_issues: Array<{ code: string; message: string; node_ids: string[] }> }
export interface ExpandResponse extends Omit<PlanResponse, 'suggestion'> { domain_id: string }
export interface CurrentPathResponse { plan: { version: number | null; stages: StagePlan[]; domains: Record<string, { domain_id?: string; nodes: NodePlan[] }>; adjustments: Array<{ version: number; diff: AdjustmentDiff }>; progress?: PathProgress }; states: PathState[]; suggestions: string[] }
export interface AdjustmentDiff { node_id: string; action: 'skip' | 'normal' | 'reinforce' | 'remedial'; skip_node_ids: string[]; updates: Array<Partial<NodePlan> & { node_id: string }>; inserted_nodes: NodePlan[]; reason: string }
export interface CompleteResponse { version: number; next_node: string | null; changes: PathState[]; diff: AdjustmentDiff }
export interface MapNode { id: string; label: string; detail: string; domainId: string; x: number; y: number; status: MeridianStatus; state: BackendPathState; free: boolean; expanded: boolean; custom?: boolean }

// ── 已确认路线的进度（地图底部步骤条）─────────────────────────────────────
/**
 * 一步 = 一个教材单元（一穴一节），**不是一章**。
 *
 * 地图上 293 个穴位与 293 个单元一一对应，但用户的进度是按**点集**算的：
 * 一个穴位挂着半节时，"学了 3/16 味药"不能被算成这一节完成。所以 `done` /
 * `point_count` 只能由后端数（前端只有一张扁平状态表，数不出单元边界）。
 */
export type PathStepState = 'done' | 'current' | 'next' | 'later' | 'pending'

export interface PathStep {
  seq: number
  /** 行的键（一章可能有多行，`unit_id` 只到章）。 */
  stage_id: string
  unit_id: string
  /** 只写节/小节名，不带章名。 */
  title: string
  chapter_title: string
  subject_id: string
  subject_name: string
  unit_kind: UnitKind
  point_count: number
  done: number
  minutes: number
  point_ids: string[]
  state: PathStepState
}

export interface PathProgress {
  steps: PathStep[]
  current: PathStep | null
  next: PathStep | null
  total: number
  total_points: number
  done_points: number
  /** 0–1。 */
  percent: number
}

// ── 两段式草稿（P5）─────────────────────────────────────────────────────
export type DraftStage = 'skeleton' | 'detail'

/** 单元层级：有小节用小节、无小节但有节用节、都没有才用章。 */
export type UnitKind = 'chapter' | 'section' | 'subsection'

/**
 * 一行安排表 = **一个教材单元**（一穴一节），不是一章。
 *
 * `unit_title` 只写节/小节名，**不带章名**；章在 `chapter_title` 里，
 * 界面用分组表达层级。`stage_id` 是这一行的键（同一章可能有多行）。
 */
export interface DraftStageRow {
  /** 行键：地图上那个穴位。跟 `domain_id` 不同——一章可能拆成多行。 */
  stage_id?: string
  unit_id?: string
  unit_kind?: UnitKind
  unit_title?: string
  chapter_title?: string
  chapter_no?: number
  section_no?: number
  part?: string
  point_ids?: string[]
  virtual?: boolean
  domain_id: string
  name: string
  order: number
  order_locked: boolean
  point_count: number
  estimated_minutes: number
  reason: string
  /** 章所属学科。图谱事实，不依赖模型有没有答出取舍。 */
  subject_id?: string
  subject_name?: string
}

export interface DraftExcludedRow {
  stage_id?: string
  unit_title?: string
  chapter_title?: string
  domain_id: string
  name: string
  point_count: number
  estimated_minutes: number
  why: string
  subject_id?: string
  subject_name?: string
}

/** 整科跳过。用户说过"已掌握中药学"，这里就是他应得的那句交代。 */
export interface DraftExemptSubject {
  subject_id: string
  name: string
}

export interface DraftSkippedPoint {
  node_id: string
  name: string
  reason: string
  domain_id?: string
}

export interface DraftRejected {
  code: string
  target: string
  message: string
}

export type PlanDraft = {
  version: number
  stage: DraftStage
  parent_version?: number | null
  fallback: boolean
  suggestion: string
  stages: DraftStageRow[]
  nodes: NodePlan[]
  excluded: DraftExcludedRow[]
  /** 汇总：节数 / 章数 / 知识点 / 分钟。表头与地图路线条读它。 */
  totals?: { units: number; chapters: number; points: number; minutes: number }
  /**
   * 本次规划的学科范围（后端按学科裁的域清单）。空 = 没点名，六门全参与。
   * 向导要拿它说"本次只排中药学"，不能让用户从 265 节里自己推断范围没生效。
   */
  scope_subjects?: DraftExemptSubject[]
  /** 模型判为「已有基础、整科跳过」的学科；顺序即建议顺序。 */
  exempt_subjects?: DraftExemptSubject[]
  /** 章 → 跳过理由。 */
  skipped_chapters?: Record<string, string>
  /** 明细阶段才有：判定免修、不进路线的知识点。 */
  skipped?: DraftSkippedPoint[]
  rejected: DraftRejected[]
  pending_domains?: string[]
  budget?: { total_days: number; daily_minutes: number } | null
  goal?: string
  reused_from?: number
  attach?: { layer_id: string; mapped: Record<string, Record<string, unknown>>; unmapped: string[]; coverage: number }
}

/** 追问的一题。缺什么问什么，最多三题。 */
export interface ClarifyOption {
  value: string
  label: string
  detail?: string
  /** 选中后追加到诉求里的那句话。后端拼回 goal，别在前端另抄一份。 */
  phrase?: string
  /** 从用户原话里读到的答案，前端默认选中。 */
  default?: boolean
}

export interface ClarifyQuestion {
  id: 'deadline' | 'scope' | 'baseline' | string
  question: string
  hint: string
  multi: boolean
  allow_text: boolean
  /**
   * 自由输入框的示例。**每题不同**，由后端给（模型会按用户原话改写）。
   * 前端不要写死"例如明年 3 月"——问到"什么水平"时那个例子是错的。
   */
  placeholder?: string
  options: ClarifyOption[]
}

export interface ClarifyResult {
  needs: boolean
  missing: string[]
  questions: ClarifyQuestion[]
  /** 用户不答时系统会用的默认值。必须显示出来，不能让它悄悄生效。 */
  assumptions: string[]
  /** 已经能确定的事实，用于回显"我理解你要……"。 */
  understood: string[]
  /** 从原话里点名的学科。它就是本次规划的范围（后端硬约束，不是给模型的建议）。 */
  subjects?: Array<{ subject_id: string; name: string }>
}

/** 一步定稿的结果：一条 l1 + 每个被选中域一条 l2。 */
export interface FinalizeResult {
  finalized: boolean
  skeleton_version: number
  detail_version: number
  versions: number[]
  node_count: number
  totals?: { units: number; chapters: number; points: number; minutes: number }
  attach?: PlanDraft['attach']
  confirmed_at?: string
}

export type DraftOp =
  | { op: 'reorder'; ids: string[] }
  | { op: 'remove'; ids: string[] }
  | { op: 'include'; domain_id: string; after?: string }
  | { op: 'set_budget'; total_days?: number; daily_minutes?: number }
  | { op: 'set_node'; node_id: string; target_mastery?: number; resource_hint?: string }
  | { op: 'drop_node'; node_id: string }
  | { op: 'add_custom'; text: string; domain_id?: string }
  | { op: 'defer'; node_id: string; reason?: string }
