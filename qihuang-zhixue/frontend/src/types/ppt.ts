import type { ResourceSource } from './resource'
import type { Archetype, BlockType } from './design'
import type { Scene } from '../render/scene'

/** 版式目的就是设计单里的 `archetype`：生成期由设计单决定，运行期写在每一页上。 */
export type SlidePurpose = Archetype

/** 渲染层认识的六种版式，与后端 `app/ppt/schemas.py` 的 SlideKind 一一对应。 */
export type SlideKind = 'cover' | 'agenda' | 'section' | 'bullets' | 'compare' | 'summary'

export interface SlideColumn {
  heading: string
  items: string[]
}

/**
 * 一页里的一个内容块。
 *
 * 七种类型共用一组字段（不是七个类）：渲染层要的是「给我块，我按 type 画」。
 * 语义按类型约定：`point/alert/quote/def` 用 `text`；`steps` 用 `items`；
 * `matrix` 用 `columns`（两栏=对照、三栏=矩阵）；`figures` 用 `value` + `heading`。
 */
export interface SlideBlock {
  type: BlockType
  text?: string
  heading?: string
  items?: string[]
  columns?: SlideColumn[]
  value?: string
  source?: string
}

/**
 * 一页用到的素材。**由选材步产出，不是模型写的**——
 * 模型只写需求（槽位），资产 id 由选型器给，避免引用不存在的文件。
 */
export interface AssetRef {
  slot: string
  asset_id: string
  result: 'parametric' | 'hit' | 'substitute' | 'generated' | 'none'
  provenance: 'parametric' | 'library' | 'generated' | 'none'
  license: string
  attribution: string
  file: string
  /** 远程直链（已合成的 wsrv 代理地址）。素材不落盘，稿子里只钉 URL。 */
  url?: string
  /** 400px 缩略图，确认卡用。 */
  thumb?: string
  /** 来源渠道 id：cleveland / iconify / wanx / local.herbs … */
  source?: string
  /** 检索固化时间。冻结后不再重检索。 */
  pinned_at?: string
  /** 角标文案：`substitute` 标「示意」、`generated` 标「AI 生成示意」。 */
  note: string
}

/**
 * 一页幻灯。
 *
 * 字段上限与后端严格模型保持一致（标题 40 / 要点 60 / 备注 200），
 * 前端输入框也按这个卡，避免「编辑能打进去、保存被 422 打回来」。
 *
 * `purpose` / `blocks` / `asset_refs` 是 v3 新增的结构化内容。**保存时必须原样回传**：
 * 后端 `SlideNode` 是 `extra="forbid"` 且这三项有默认值，漏传等于把一页数据卡
 * 静默降级成要点页。
 */
export interface SlideNode {
  id: string
  kind: SlideKind
  title: string
  subtitle: string
  bullets: string[]
  columns: SlideColumn[]
  note: string
  source_indexes: number[]
  /** 版式目的（十值）。缺省时渲染层按 `kind` 回落。 */
  purpose?: SlidePurpose
  /** 结构化内容。为空时渲染层用 `bullets` / `columns`。 */
  blocks?: SlideBlock[]
  asset_refs?: AssetRef[]
  /**
   * 视觉方案。有它就走 `SceneSurface`（模型自己排的那一版），没有就回落旧渲染器。
   *
   * 为什么挂在页上而不是挂在设计单上：设计单是「整份的方向」，场景是「这一页怎么画」。
   * 一份稿里十二页各有各的几何，只有页知道自己的。落库后随页冻结。
   */
  scene?: Scene
}

export interface DeckContent {
  theme_id: string
  slides: SlideNode[]
  sources?: ResourceSource[]
}

/** 大纲页：谋篇的产物，也是「先定大纲」模式下用户唯一能改的东西。 */
export interface OutlineSlide {
  id: string
  title: string
  points: string[]
  source_indexes: number[]
}

export type PptMode = 'one_shot' | 'outline'

export interface PptSaveResult {
  version: number
  slide_count: number
}

export interface PptStats {
  slides: number
  bullets: number
  grounded: number
}

export const SLIDE_LIMITS = {
  title: 40,
  subtitle: 40,
  bullet: 60,
  columnHeading: 24,
  columnItem: 30,
  note: 200,
  slides: 20,
} as const

/** 版式的中文名，用于左栏「当前页」与胶片条的辅助说明。 */
export const SLIDE_KIND_LABELS: Record<SlideKind, string> = {
  cover: '封面',
  agenda: '导览',
  section: '章节',
  bullets: '要点',
  compare: '对比',
  summary: '总结',
}

/** 十种版式目的的中文名（与设计单同一套说法，避免左栏与确认单叫法不同）。 */
export const SLIDE_PURPOSE_LABELS: Record<SlidePurpose, string> = {
  cover: '封面',
  agenda: '导览',
  section: '章节',
  points: '要点',
  compare: '对照',
  matrix: '矩阵',
  process: '流程',
  data: '数据',
  warning: '警示',
  summary: '收束',
}

/** 左栏用：这一页实际按什么版式排。有 purpose 就以它为准，旧稿回落到 kind。 */
export const slideLayoutLabel = (slide: SlideNode): string =>
  slide.purpose ? SLIDE_PURPOSE_LABELS[slide.purpose] : SLIDE_KIND_LABELS[slide.kind]
