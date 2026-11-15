/**
 * 设计单的前端镜像。
 *
 * 与后端 `backend/app/ppt/design.py` 的 pydantic 模型**逐字段对应**。两边的用途
 * 不同但不能分叉：后端用它校验与落库，前端用它渲染确认单与线框。
 * 字段全部带默认值，旧资源读不到这些键时退化成「要点版式」，不会白屏。
 */

/** 十种版式原型。与后端 `ARCHETYPES` 同序。 */
export type Archetype =
  | 'cover'
  | 'agenda'
  | 'section'
  | 'points'
  | 'compare'
  | 'matrix'
  | 'process'
  | 'data'
  | 'warning'
  | 'summary'

/** 内容块七类型。与后端 `BLOCK_TYPES` 同源。 */
export type BlockType = 'point' | 'steps' | 'matrix' | 'figures' | 'alert' | 'quote' | 'def'

export type Density = 'airy' | 'normal' | 'dense'
export type SlotKind = 'figure' | 'evidence' | 'ornament' | 'ground'
export type SlotStyle = 'plate' | 'line' | 'photo' | 'motif' | 'none'

export interface DesignSlot {
  slot: SlotKind
  role?: string
  /** 要找什么（人参 / 阴阳 / 通用）。**不是** asset id。 */
  subject?: string
  style?: SlotStyle
  priority?: 'required' | 'optional'
  aspect?: 'portrait' | 'square' | 'landscape' | 'any'
  slot_class?: 'decorative' | 'evidence'
  /** 检索短词（「黄芪」「饮片」）。给库检索用，不是给人看的描述。 */
  keywords?: string[]
  /** 这一页的构图，见 spec.COMPOSITIONS。 */
  composition?: string
}

export interface PageDesign {
  unit_id: string
  archetype: Archetype
  density: Density
  slots: DesignSlot[]
  /** 一句话：为什么这一页这样排。只给人看，不参与渲染。 */
  note?: string
  /** 这一页怎么摆：text-only / text-image-right / full-bleed / centered / split。 */
  composition?: string
}

export interface DeckDesign {
  theme_id: string
  paper: string
  type_pair: string
  ornament_set: string
  /** 版式母版 id（`xuan-songkai` 等）。空 = 老方案，走 scene_art 自由排版。 */
  master_id?: string
  /** 母版色板变体序号。 */
  accent_variant?: number
  /** 视觉方案：模型读用户的话定的，确认后才执行。 */
  palette?: Record<string, string>
  /** 底纹母题 id（`paper-plain` 等）。没有则按 `paper` 处方推。 */
  paper_motif?: string
  /** 角饰母题 id，或 `none`＝不要角花。没有则按 `ornament_set` 处方推。 */
  ornament_motif?: string
  /** {kind, motif, subject, keywords, veil, tone} */
  background?: Record<string, unknown>
  /** {motif, subject, keywords, corners, ask} */
  ornament?: Record<string, unknown>
  image_treatment?: string
  mood?: string
  rhythm?: string
  pages: PageDesign[]
  rationale: string[]
  assumptions: string[]
  revision?: number
}

/** 选材结果。`substitute` 要标「示意」，`generated` 要标「AI 生成示意」。 */
export interface SlotResolution {
  unit_id: string
  slot: string
  requested: Record<string, string>
  result: 'parametric' | 'hit' | 'substitute' | 'generated' | 'none'
  asset_id: string
  provenance: 'parametric' | 'library' | 'generated' | 'none'
  score: number
  license: string
  attribution: string
  file: string
  fallback_reason: string
}

/** 文稿的设计单：体裁、版式、引用体例、篇幅目标。 */
export interface DocDesign {
  genre: 'explain' | 'compare' | 'guide' | 'plan' | 'review' | 'case' | 'qa' | 'minutes' | 'cheatsheet'
  layout: 'lecture' | 'report' | 'manual' | 'minutes'
  citation: 'footnote' | 'endnote' | 'none'
  charts: 'table' | 'flow' | 'text'
  target_words: number
  rationale: string[]
  assumptions: string[]
}

/** 导图的设计单：结构与视觉语法，其中分支预算与深度上限会参与生成。 */
export interface MindmapDesign {
  structure: string
  theme: string
  node_shape: 'tag' | 'text' | 'outline'
  edge: 'straight' | 'curve' | 'bracket'
  fold: 'auto' | 'depth3' | 'all'
  branch_budget: number
  max_depth: number
  rationale: string[]
  assumptions: string[]
}

export type AnyDesign = DeckDesign | DocDesign | MindmapDesign

/** 版式的中文名与一句说明。确认单与线框都用它，避免两处文案分叉。 */
export const ARCHETYPE_META: Record<Archetype, { label: string; hint: string }> = {
  cover: { label: '封面', hint: '界格 / 满版 / 极简三选一，无图时走界格' },
  agenda: { label: '导览', hint: '序号 + 横线，最多六条' },
  section: { label: '章节', hint: '巨号汉字序号做水印' },
  points: { label: '要点', hint: '单栏左齐，五条以内' },
  compare: { label: '对照', hint: '双栏 + 中线，栏题不超过 10 字' },
  matrix: { label: '矩阵', hint: '斑马格，3×3 以内' },
  process: { label: '流程', hint: '连接线 + 大号数字，四步以内' },
  data: { label: '数据', hint: '数字卡，数值不超过 6 字符' },
  warning: { label: '警示', hint: '朱砂竖条，最多两条' },
  summary: { label: '收束', hint: '要点回顾 + 一句定论' },
}

export const ARCHETYPE_ORDER: Archetype[] = [
  'cover',
  'agenda',
  'section',
  'points',
  'compare',
  'matrix',
  'process',
  'data',
  'warning',
  'summary',
]

/** 密度三档与内容块上限。与后端 `DENSITY_CAPS` 同数。 */
export const DENSITY_META: Record<Density, { label: string; maxBlocks: number }> = {
  airy: { label: '疏', maxBlocks: 3 },
  normal: { label: '常', maxBlocks: 5 },
  dense: { label: '密', maxBlocks: 7 },
}

export const GENRE_LABELS: Record<DocDesign['genre'], string> = {
  explain: '讲解',
  compare: '对比分析',
  guide: '操作指南',
  plan: '计划',
  review: '综述',
  case: '案例',
  qa: '答疑',
  minutes: '纪要',
  cheatsheet: '速查表',
}

export const isDeckDesign = (value: unknown): value is DeckDesign =>
  Boolean(value && typeof value === 'object' && Array.isArray((value as DeckDesign).pages))
