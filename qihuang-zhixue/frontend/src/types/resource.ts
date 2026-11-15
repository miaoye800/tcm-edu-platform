import type { DeckDesign, DocDesign, MindmapDesign, SlotResolution } from './design'
export type ResourceType = 'doc' | 'graph' | 'mindmap' | 'quiz' | 'story' | 'ppt'
export type ResourceStatus = 'generating' | 'ready' | 'failed'

export interface ResourceSource { [key: string]: string | undefined; source?: string; title?: string; quote?: string; ref?: string; '出处'?: string; '原文摘录'?: string; '定位'?: string }
export interface ResourceItem { id: string; node_id: string; type: ResourceType; title: string; content?: Record<string, unknown>; sources?: ResourceSource[]; notes?: string; status: ResourceStatus; created_at: string; favorited?: boolean; meta?: Record<string, unknown>; node_name?: string }
export interface ResourceListResponse { items: ResourceItem[]; total: number; offset: number; limit: number }
export type DocBlockType = 'heading' | 'paragraph' | 'bullet' | 'table' | 'callout' | 'quote'
export interface ResourceDocBlock { type: DocBlockType | string; text: string }
/** 依据等级：教材实证 / 图谱关系 / 模型推断。 */
export type MindmapEvidence = 'textbook' | 'graph' | 'model'
/** 三档依据的节点数。三数之和恒等于节点总数，底栏占比直接除它。 */
export interface MindmapEvidenceCounts { textbook: number; graph: number; model: number }
/** 节点在树里的身份。根与一级分支画法不同，缩略图也靠它取舍。 */
export type MindmapNodeRole = 'root' | 'branch' | 'leaf'
/** 导图逐节点事件：父节点必定先于子节点到达，前端靠 id 去重。 */
export interface ResourceMindmapNode {
  id: string
  parent_id: string | null
  topic: string
  depth: number
  order: number
  direction: number
  source_indexes: number[]
  node_total: number
  /** 后端 9 步流水线开始下发；旧任务没有这个字段。 */
  evidence?: MindmapEvidence
  role?: MindmapNodeRole
}
/** 校验步的统计：只裁剪越界依据，不删节点。 */
export interface ResourceMindmapStats { node_count: number; grounded: number; trimmed: number }
/** 校色步的统计：只统计，不改内容。 */
export interface ResourceDeckStats { slides: number; bullets: number; columns: number; grounded: number; notes: number }
/**
 * 逐页核对统计（后端 `_deck_verify`）。只报问题，不改内容——
 * 「校验完偷偷重写」会让用户对成品失去信任。
 */
export interface DeckVerifyStats {
  slides: number
  planned: number
  matched: number
  missing: string[]
  duplicates: string[]
  empty: number
  bad_sources: number
  grounded: number
  /** 版式多样性：用了几种、最长连续同类几页、方案要的版式缺了哪些。 */
  variety?: DeckVariety
}

/** 后端 `deck_conformance` 的形状。「排版单一」靠这几个数说得清。 */
export interface DeckVariety {
  pages: number
  expected_pages: number
  roles_used: string[]
  role_variety: number
  longest_run: number
  missing_roles: string[]
  thin_pages: string[]
  with_figure: number
  figure_pages: number
  monotone: boolean
}
/** 逐节核对统计（后端 `_doc_verify`）。同样只报问题。 */
export interface DocVerifyStats {
  sections: number
  planned: number
  matched: number
  missing: string[]
  bad_citations: number
  grounded: number
  /** 实际字数与方案目标。差得远要告诉读者，不要假装写满了。 */
  words?: number
  expected_words?: number
  /** 整节只有大段落、没有任何表格/清单/步骤的章节标题。 */
  dense?: string[]
  /** 单段超过 200 字的段数。 */
  long_paragraphs?: number
  /** 实际出现的排版要素与方案要求却缺失的。 */
  devices?: string[]
  devices_missing?: string[]
}
/** Back end emits body citations as zero-based indexes, such as [0] for sources[0]. */
export interface ResourceTaskPayload {
  step_id?: string
  resource_id?: string
  message?: string
  detail?: string
  index?: number
  block?: ResourceDocBlock
  node?: ResourceMindmapNode
  stats?: ResourceMindmapStats | ResourceDeckStats | DeckVerifyStats | DocVerifyStats
  /** 导图校验步：三档依据的节点数，与 stats 同一时刻算出。 */
  evidence?: MindmapEvidenceCounts
  /** 问法判定结果。`intent_note` 是后端写好的一句话，前端不自己拼中文。 */
  intent?: string
  intent_label?: string
  intent_note?: string
  degraded?: boolean
  template_id?: string
  /** 幻灯谋篇的大纲页与总页数。 */
  outline?: ResourceOutlineSlide[]
  total?: number
  deck_title?: string
  /** 敷演步逐页推送的整页内容。 */
  slide?: ResourceSlide
  /** 暂停原因：`await_outline` 等确认大纲，`await_plan` 等确认制作方案。 */
  reason?: string
  /** 方案事件里的完整方案；确认之后生成阶段不会重拟。 */
  plan?: ResourcePlan
  /** 设计事件里的设计单：版式/密度/槽位（幻灯）或体裁/篇幅（文稿）。 */
  design?: DeckDesign | DocDesign | MindmapDesign
  /** 选材事件的逐槽解析结果：这一页的图为什么有、为什么没有。 */
  slots?: SlotResolution[]
  summary?: { total?: number; counts?: Record<string, number>; unfilled?: string[] }
  /** 逐页设计/资产校验的问题清单。 */
  assets?: { issues?: string[]; slot_unfilled?: string[] }
  capacity?: { issues?: string[] }
  resource_type?: ResourceType
  /** 方案是否已被确认（重连时服务端会带上这个标记）。 */
  confirmed?: boolean
  /** 本次任务的工序表，前端据此渲染步骤条。 */
  steps?: ResourcePlanStep[]
  theme_id?: string
  /** 逐页核对的结果：缺页、重复、空页、引用越界。 */
  pages?: DeckPageCheck[]
  /**
   * 校验后重写过的页。校验本身只报不改；「装不下 / 空页 / 引用越界」是这一页
   * 没写对，由单页重写定点修掉，其余页一个字不动。
   */
  revised?: { page: number; issues: string[]; notes?: string[] }[]
  /** 逐节依据：哪一节用了教材、哪一节是模型补充。 */
  source_map?: SectionSource[]
  /** 方案分支在树里落地了几支。 */
  plan_coverage?: { planned: number; matched: number; missing: string[] }
  /** 成品与方案的差距：层数差多少、哪些支偏浅、哪些支没落地。 */
  conformance?: { depth?: number; expected_depth?: number; missing?: string[]; shallow?: string[]; note?: string }
}

export interface ResourcePlanStep {
  id: string
  label: string
}

/** 依据三档：教材知识库/图谱、用户材料、通用模型补充。 */
export type PlanSource = 'knowledge_base' | 'user_material' | 'model'

export interface ResourcePlanUnit {
  id?: string
  /** 一页幻灯 / 一节文稿 / 一支导图分支的标题。 */
  label: string
  /** 这个单元要达成什么。 */
  intent?: string
  /** 要点，可能用「；」连写。导图里它就是「这一支必须覆盖什么」。 */
  detail?: string
  source?: PlanSource
  evidence?: string[]
  gap?: string
}

/**
 * 导图的施工图。面板上能手改的每一项都在这里，生成阶段读的也是这一份——
 * 以前层数只活在「呈现方式」那行字里，改了没人读。
 */
export interface ResourcePlanSpec {
  root_topic?: string
  audience?: string
  organization?: string
  organization_kind?: string
  /** 最终会用到的层数（已按结构上限夹过）。 */
  depth?: number
  /** 用户原本要的层数；与 depth 不同说明被结构上限削过。 */
  depth_requested?: number | null
  leaves_min?: number
  structure?: string
  branches?: { label: string; purpose?: string; must_cover?: string[] }[]
  /** 用户明确提出的要求，一条一句。 */
  emphasis?: string[]
  /** 这张图必须回答的问题。 */
  must_answer?: string[]
  /** 「我这样理解你的要求」。面板直接展示这几句，别再自己拼文案。 */
  interpretation?: string[]
  /** rules = 按规则拟的（本机没有解读模型）；model = 模型读出来的。 */
  basis?: 'model' | 'rules'
  notes?: string[]
  // —— 文稿施工图。与导图共用同一个 spec 键，靠字段区分 ——
  /** 文档标题。方案卡上可改，生成时逐字照用。 */
  title?: string
  subtitle?: string
  /** 文稿是体裁（explain/plan/…），讲稿是场合（lecture/orientation/…）。 */
  genre?: DocGenre | DeckGenre
  /** 这份文档要交付的判断，也就是方案卡上「目标」那一栏。 */
  thesis?: string
  target_words?: number
  sections?: ResourcePlanSection[]
  devices?: string[]
  // —— 讲稿施工图。与上面两类共用同一个 spec 键，靠 pages 区分 ——
  /** 场合一句话（「新生入学第一周的入门试讲」）。 */
  occasion?: string
  /** 叙事弧：先勾住人 → 说清是什么 → …… */
  arc?: string[]
  target_pages?: number
  duration_min?: number
  pages?: ResourcePlanPage[]
  /** 讲稿的「做什么用」——与 thesis（讲完记住的那句）分开。 */
  goal?: string
  /** 视觉方案：气质、主题、配色、背景、角饰、字体、配图处理、版式节奏。 */
  look?: DeckLook
}

/** 讲稿的视觉方案。字段名与后端 `ppt.spec.DeckLook` 逐项对应。 */
export interface DeckLook {
  /** 版式母版 id（`xuan-songkai` 等）。空 = 走旧 LLM 自由排版。 */
  master_id?: string
  /** 母版色板变体序号（0 起）。 */
  accent_variant?: number
  mood?: string
  theme?: DeckTheme
  palette?: DeckPalette
  background?: {
    kind?: 'motif' | 'asset' | 'solid'
    motif?: string
    subject?: string
    keywords?: string[]
    veil?: number
    tone?: string
  }
  ornament?: {
    motif?: string
    subject?: string
    keywords?: string[]
    corners?: string[]
    ask?: string
  }
  type_pair?: string
  image_treatment?: 'plate' | 'photo' | 'line' | 'collage' | 'none'
  rhythm?: string
}

export interface DeckPalette {
  bg?: string
  fg?: string
  muted?: string
  accent?: string
  line?: string
}

export type DeckTheme = 'paper' | 'ink' | 'celadon' | 'cinnabar'

/** 讲稿施工图里的一页：什么版式、解决什么、必须讲到什么、要什么素材。 */
export interface ResourcePlanPage {
  heading: string
  goal?: string
  /** 与 design.ARCHETYPES 同一套 id：cover/agenda/section/points/compare/... */
  role?: string
  must_cover?: string[]
  density?: string
  material?: {
    slot?: string
    subject?: string
    style?: string
    priority?: string
    why?: string
    keywords?: string[]
  }
  /** 这一页怎么摆：text-only / text-image-right / full-bleed / centered / split。 */
  composition?: string
}

/** 文稿施工图里的一节：叫什么、解决什么、必须覆盖什么、该长什么样。 */
export interface ResourcePlanSection {
  heading: string
  goal?: string
  must_cover?: string[]
  /** prose / table / steps / checklist / qa / mixed —— 决定这一节有没有表格与清单。 */
  form?: string
}

export type DocGenre =
  | 'explain'
  | 'compare'
  | 'guide'
  | 'plan'
  | 'review'
  | 'case'
  | 'qa'
  | 'minutes'
  | 'cheatsheet'

/** 讲稿的场合。与后端 `ppt.spec.GENRE_LABEL` 同源。 */
export type DeckGenre = 'lecture' | 'orientation' | 'brief' | 'training' | 'defense' | 'pitch'

export interface ResourcePlan {
  kind: ResourceType
  title?: string
  goal: string
  audience: string
  organization: string
  presentation: string
  units: ResourcePlanUnit[]
  gaps: string[]
  evidence: Record<PlanSource, string[]>
  /** 导图专有：这次怎么排的完整说明。其他资源类型为空。 */
  spec?: ResourcePlanSpec
  clarification?: string
  revision?: number
  updated_at?: string
  /** 修订是否真的生效。false 表示模型不可用，方案一字未动。 */
  applied?: boolean
  overturned?: boolean
  notice?: string
  steps?: ResourcePlanStep[]
}

export interface ResourceTaskSnapshot {
  task_id: string
  resource_type: ResourceType
  status: string
  current_step: number
  resource_id?: string | null
  plan: ResourcePlan | null
  /** 刷新后设计单也要在：否则用户改完版式一刷新就丢了。 */
  design?: DeckDesign | DocDesign | MindmapDesign | null
  plan_confirmed: boolean
  awaiting_plan: boolean
  pipeline_version: number
  steps: ResourcePlanStep[]
}

export interface DeckPageCheck {
  index: number
  title: string
  planned: boolean
  source: PlanSource
  issues: string[]
}

export interface SectionSource {
  heading: string
  citations: number
  source: PlanSource
}

/** 幻灯事件里的页数据，结构直接复用 `types/ppt.ts` 的 SlideNode。 */
export interface ResourceSlide {
  id: string
  kind: string
  title: string
  subtitle: string
  bullets: string[]
  columns: Array<{ heading: string; items: string[] }>
  note: string
  source_indexes: number[]
  /** 下面三项是 v3 的结构化内容，旧任务没有。 */
  purpose?: string
  blocks?: Array<{
    type: string
    text?: string
    heading?: string
    items?: string[]
    columns?: Array<{ heading: string; items: string[] }>
    value?: string
    source?: string
  }>
  asset_refs?: Array<{
    slot: string
    asset_id: string
    result: 'parametric' | 'hit' | 'substitute' | 'generated' | 'none'
    provenance: 'parametric' | 'library' | 'generated' | 'none'
    license: string
    attribution: string
    file: string
    note: string
  }>
}

export interface ResourceOutlineSlide {
  id: string
  title: string
  points: string[]
  source_indexes: number[]
}

export interface ResourceTaskEvent {
  /**
   * `pause` 是幻灯「先定大纲」模式的终止事件：服务端推完大纲就断流，
   * 前端必须把它当成一次正常结束，否则会重连风暴（重连后又收一遍 outline）。
   */
  event:
    | 'step_start'
    | 'step_done'
    | 'block'
    | 'token'
    | 'done'
    | 'error'
    | 'node'
    | 'mindmap_verify'
    | 'doc_verify'
    | 'outline'
    | 'slide'
    | 'deck_verify'
    | 'plan'
    /** v3 设计步：版式/密度/槽位（幻灯）或体裁/篇幅（文稿）。与 plan 同一道闸门确认。 */
    | 'design'
    /** 选材步：逐槽解析结果。只解释「这张图为什么有 / 为什么没有」，不需要确认。 */
    | 'slot_resolution'
    | 'pause'
  step_index: number
  total_steps: number
  step_label: string
  payload: ResourceTaskPayload
}
export interface ResourceFilters { type?: ResourceType; nodeId?: string; favorited?: boolean; q?: string; offset?: number; limit?: number }

export const displayCitation = (zeroBased: number) => zeroBased + 1
/** Table blocks arrive as Markdown text; malformed or non-table text intentionally falls back to a paragraph. */
export const parseMarkdownTable = (text: string): string[][] => text.split(/\r?\n/).map(line => line.trim()).filter(line => line.startsWith('|')).filter((line, index) => index !== 1 || !/^\|?\s*:?-{3,}/.test(line.split('|').join('').trim())).map(line => line.replace(/^\||\|$/g, '').split('|').map(cell => cell.trim()))
